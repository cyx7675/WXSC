# -*- coding: utf-8 -*-
"""把设计稿 / 前端页面用真实浏览器渲染并截图。

为什么不用 QWebEngineView.grab()：
    网页内容在独立的渲染进程里合成，Widget.grab() 只能拿到空白图；
    改用屏幕级抓屏同样拿不到（窗口表面不进入截图）。
    所以这里走 Playwright（真实浏览器，布局与合成都是真的）。

用法：
    python tools/preview_screenshot.py <html路径> [输出png] [宽] [高] [--sel 选择器] [--wait 秒]

    --sel  只截某个元素，放大看细节时很有用，例如 --sel "#zoneP2"
    --wait 等页面渲染 / 模拟数据累积的秒数，默认 8

关键坑（踩过的）：
    playwright-cli 的浏览器会话依附于「调用它的那个进程」。
    同一条 shell 命令里连续 open/resize/screenshot 没问题；
    但拆成两次独立调用（中间 shell 退出）就会报
        Browser 'default' is not open.
    所以本脚本用同一个 Python 进程把全部调用串起来，不要拆开跑。
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote


def _free_port(preferred=8791):
    for p in (preferred, 8792, 8793, 8794, 8795):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):        # 静音，避免刷屏
        pass


def _serve(root, port):
    handler = partial(_Quiet, directory=root)
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _cli(*args, cwd=None, timeout=180):
    """调用 playwright-cli。返回 (returncode, stdout+stderr)。

    注意：screenshot / run-code 的 locator.screenshot 会把输出路径限制在
    「playwright 进程的 cwd」这棵目录树内（allowed roots）。
    所以统一把 cwd 设成页面根目录，先截到根内临时文件，再移动到目标位置。
    """
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2

    sel, wait = None, 8.0
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--sel":
            sel = argv[i + 1]; i += 2
        elif argv[i] == "--wait":
            wait = float(argv[i + 1]); i += 2
        else:
            rest.append(argv[i]); i += 1

    html = os.path.abspath(rest[0])
    if not os.path.isfile(html):
        print("找不到文件:", html)
        return 2
    root = os.path.dirname(html)
    name = os.path.basename(html)

    out = os.path.abspath(rest[1]) if len(rest) > 1 else \
        os.path.join(root, "_preview_shot.png")
    w = int(rest[2]) if len(rest) > 2 else 1600
    h = int(rest[3]) if len(rest) > 3 else 1000

    port = _free_port()
    srv = _serve(root, port)
    url = "http://127.0.0.1:%d/%s" % (port, quote(name))
    # playwright 的截图输出被限制在其 cwd 目录树内，先落到根内临时文件再移动
    tmp = os.path.join(root, "_preview_tmp_shot.png")

    try:
        rc, log = _cli("open", '"%s"' % url, cwd=root)
        if rc != 0 or "is not open" in log:
            print("打开浏览器失败:\n" + log)
            return 1

        _cli("resize", str(w), str(h), cwd=root)
        time.sleep(wait)                       # 等布局稳定 + 模拟数据累积

        if os.path.isfile(tmp):
            os.remove(tmp)
        if sel:
            code = ("async (page) => { await page.locator('%s')"
                    ".screenshot({path:'%s'}); return 'ok'; }"
                    % (sel, tmp.replace("\\", "/")))
            rc, log = _cli("run-code", "--raw", '"%s"' % code, cwd=root)
            if not os.path.isfile(tmp):
                print("元素截图失败:\n" + log)
                return 1
        else:
            rc, log = _cli("screenshot", '--filename="%s"' % tmp.replace("\\", "/"), cwd=root)
            if not os.path.isfile(tmp):
                print("截图失败:\n" + log)
                return 1

        shutil.move(tmp, out)
        rc, log = _cli("run-code", "--raw",
                       "\"async (page) => JSON.stringify(await page.evaluate("
                       "() => window.__errs || []))\"", cwd=root)
        errs = log.strip()
        print("SAVED", out)
        print("PAGE_ERRORS", errs)
        return 0
    finally:
        srv.shutdown()
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
