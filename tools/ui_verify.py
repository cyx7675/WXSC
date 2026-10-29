# -*- coding: utf-8 -*-
"""ui_verify.py -- 真实浏览器（Playwright）验证 v2.1 前端：功能 + 响应式 + 视觉。

为什么还要跑一次真实浏览器
--------------------------
离屏 Qt 自检只能证明"JS 不报错、DOM 节点数对"，证明不了**画面对不对**：
canvas 里到底有没有曲线、窄屏下布局会不会挤爆、Toast 会不会挡住内容，
这些都要真实合成 + 截图才能确认。

做什么
------
1. 打开 index.html，开演示模式，等数据累积；
2. 采样趋势画布的像素，确认**曲线真的画出来了**（不是空白）；
3. 在 1440 / 1280 / 1024 / 820 / 520 五种宽度下截图，验证响应式断点；
4. 触发一条 Toast 并截图，确认提示层位置与样式；
5. 收集 window.__errs，任何运行时错误都会被报出来。

用法
----
    cd F:\\上位机\\v2 && <venv>\\Scripts\\python.exe tools\\ui_verify.py
产物：tools/_verify/*.png
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "telemetry_web")
OUT = os.path.join(HERE, "_verify")


def _free_port(preferred=8811):
    for p in range(preferred, preferred + 8):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _cli(*args, timeout=180):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=ROOT)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _code(js, timeout=180):
    return _cli("run-code", "--raw", '"%s"' % js, timeout=timeout)


def _shot(tmp, w, h, wait):
    _cli("resize", str(w), str(h))
    time.sleep(wait)
    if os.path.isfile(tmp):
        os.remove(tmp)
    rc, log = _cli('screenshot', '--filename="%s"' % tmp.replace("\\", "/"))
    return os.path.isfile(tmp), log


# 画布非空判定：统计非透明像素占比 + 出现的不同颜色数（曲线是多色的）
CANVAS_PROBE = ("async (page) => JSON.stringify(await page.evaluate(() => {"
                "var c = document.getElementById('trendCanvas');"
                "var g = c.getContext('2d');"
                "var d = g.getImageData(0, 0, c.width, c.height).data;"
                "var n = 0, cols = {};"
                "for (var i = 0; i < d.length; i += 4) {"
                "  if (d[i+3] > 8) { n++;"
                "    var k = (d[i]>>5) + ',' + (d[i+1]>>5) + ',' + (d[i+2]>>5);"
                "    cols[k] = 1; } }"
                "return {w:c.width, h:c.height, inkPx:n, inkPct:+(100*n/(c.width*c.height)).toFixed(2),"
                "        colors:Object.keys(cols).length};}))")

DOM_PROBE = ("async (page) => JSON.stringify(await page.evaluate(() => ({"
             "live: document.getElementById('liveText').textContent,"
             "speed: document.getElementById('speedNum').textContent,"
             "rpm: document.getElementById('rpmNum').textContent,"
             "adc0: document.querySelector('#adcGrid [data-val]').textContent,"
             "hexLines: document.querySelectorAll('#hexBox .hex-line').length,"
             "fps: document.getElementById('fpsTag').textContent,"
             "errs: window.__errs || []})))")

TOAST_JS = ("async (page) => { await page.evaluate(() => {"
            "toast('演示提示：这是非阻塞 Toast，替代了会卡住界面的 alert', 'ok');"
            "toast('保存目录不可用：磁盘已满', 'err'); }); return 'ok'; }")

CLICK_DEMO = "async (page) => { await page.evaluate(() => document.getElementById('demoMode').click()); return 'ok'; }"


def main():
    os.makedirs(OUT, exist_ok=True)
    port = _free_port()
    handler = partial(_Quiet, directory=ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    tmp = os.path.join(ROOT, "_verify_tmp.png")

    try:
        rc, log = _cli("open", '"%s"' % url)
        if rc != 0:
            print("打开浏览器失败:\n", log)
            return 1

        # ---- 1) 桌面尺寸 + 开演示 ----
        _cli("resize", "1440", "860")
        time.sleep(1.5)
        _code(CLICK_DEMO)
        time.sleep(6)                       # 等模拟数据把 600 点缓冲灌起来

        _, dom = _code(DOM_PROBE)
        print("DOM  :", dom.strip().splitlines()[-1] if dom.strip() else dom)
        _, cvs = _code(CANVAS_PROBE)
        print("CANVAS:", cvs.strip().splitlines()[-1] if cvs.strip() else cvs)

        ok, log = _shot(tmp, 1440, 860, 0.5)
        if ok:
            shutil.move(tmp, os.path.join(OUT, "01_desktop_1440.png"))
            print("SAVED 01_desktop_1440.png")
        else:
            print("截图失败:", log)

        # ---- 2) Toast ----
        _code(TOAST_JS)
        ok, log = _shot(tmp, 1440, 860, 1.0)
        if ok:
            shutil.move(tmp, os.path.join(OUT, "02_toast.png"))
            print("SAVED 02_toast.png")

        # ---- 2.5) HEX 监视器：验证节点池 + column-reverse（最新一行贴底）----
        HEX_JS = ("async (page) => { await page.evaluate(() => {"
                  "for (var i = 0; i < 30; i++)"
                  "  pushHexLines('AA 55 ' + ('0' + i.toString(16)).slice(-2).toUpperCase() +"
                  " ' 01 02 03 04 05 06 07 08 09 0A 0B 0C 55 AA', i % 7 === 0);"
                  "}); return 'ok'; }")
        _code(HEX_JS)
        time.sleep(0.5)
        _, hp = _code("async (page) => JSON.stringify(await page.evaluate(() => {"
                      "var b = document.getElementById('hexBox');"
                      "var n = b._nodes || [];"
                      "var vis = n.filter(function(x){return x.style.display !== 'none';});"
                      "return {pool:n.length, visible:vis.length,"
                      "        bottom:vis.length ? vis[0].textContent : '',"
                      "        top:vis.length ? vis[vis.length-1].textContent : '',"
                      "        idleHidden:(document.getElementById('hexIdle')||{}).style ?"
                      "          document.getElementById('hexIdle').style.display : 'missing'};}))")
        print("HEX  :", hp.strip().splitlines()[-1] if hp.strip() else hp)
        ok, log = _shot(tmp, 1440, 860, 0.3)
        if ok:
            shutil.move(tmp, os.path.join(OUT, "02b_hex.png"))
            print("SAVED 02b_hex.png")

        # ---- 3) 响应式断点 ----
        for name, w, h in [("03_laptop_1280", 1280, 800),
                           ("04_small_1024", 1024, 760),
                           ("05_tablet_820", 820, 1000),
                           ("06_phone_520", 520, 900)]:
            ok, log = _shot(tmp, w, h, 1.2)
            if ok:
                shutil.move(tmp, os.path.join(OUT, name + ".png"))
                print("SAVED", name + ".png")
            else:
                print("截图失败", name, log)

        _, errs = _code("async (page) => JSON.stringify(await page.evaluate(() => window.__errs || []))")
        print("PAGE_ERRORS:", errs.strip().splitlines()[-1] if errs.strip() else errs)
        print("产物目录:", OUT)
        return 0
    finally:
        server.shutdown()
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
