#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_diag_shot.py — 开演示模式 + 全开 14 路曲线，截趋势区现场（改造前/后对比用）

产出两张图：
    tools/_diag_still.png   静止态（右侧数值面板显示实时值）
    tools/_diag_hover.png   鼠标悬停在曲线 60% 处（十字光标 + 面板切到游标时刻读数）
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _free_port(preferred=8791):
    for p in (preferred, 8792, 8793):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no port")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _cli(*args, cwd=None, timeout=180):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "telemetry_web"))
    tools = os.path.abspath(os.path.join(root, "..", "tools"))
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port),
                              partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    tmp = os.path.join(root, "_diag_tmp.png").replace("\\", "/")
    try:
        rc, log = _cli("open", '"%s"' % url, cwd=root)
        if rc != 0:
            print("open fail:", log)
            return 1
        _cli("resize", "1500", "1000", cwd=root)

        setup = (
            "async (page) => { await page.evaluate(() => {"
            "  setDemo(true);"
            "  state.visible = new Set(['adc0','adc1','adc2','adc3','adc4',"
            "'adc5','adc6','adc7','adc8','adc9','speed','rpm','throttle','torque']);"
            "  buildChips(); trendLayer.sKey=''; trendDirty=true;"
            "}); return 'ok'; }"
        )
        _cli("run-code", "--raw", '"%s"' % setup, cwd=root)
        time.sleep(10)     # 攒 30s 窗口的大半数据

        def shot(name):
            rc, log = _cli("run-code", "--raw",
                           '"async (page) => { await page.locator(\'#zoneTrend\')'
                           '.screenshot({path:\'%s\'}); return \'ok\'; }"' % tmp, cwd=root)
            if not os.path.isfile(tmp):
                print("shot fail", name, log)
                return False
            shutil.move(tmp, os.path.join(tools, name))
            print("SAVED", os.path.join(tools, name))
            return True

        ok = shot("_diag_still.png")

        # hover 到曲线 60% 处：触发十字光标 + 面板切游标读数
        hv = (
            "async (page) => {"
            "  const b = await page.locator('.trend-wrap .chart-box').boundingBox();"
            "  if (!b) return 'no chart-box';"
            "  await page.mouse.move(b.x + b.width*0.6, b.y + b.height*0.5);"
            "  return 'ok'; }"
        )
        rc, log = _cli("run-code", "--raw", '"%s"' % hv, cwd=root)
        print("hover rc=%d %s" % (rc, log[-200:]))
        time.sleep(1)
        ok = shot("_diag_hover.png") and ok

        # hover 面板第 3 行：其余曲线应淡出、目标线高亮
        hl = (
            "async (page) => {"
            "  const row = page.locator('.sig-row').nth(2);"
            "  await row.hover();"
            "  return await page.evaluate(() => state.hl); }"
        )
        rc, log = _cli("run-code", "--raw", '"%s"' % hl, cwd=root)
        print("hl rc=%d %s" % (rc, log[-80:]))
        time.sleep(0.6)
        ok = shot("_diag_hl.png") and ok

        # 抓面板首行 DOM 文本：验证单位是否真的渲染出来
        probe = (
            "async (page) => {"
            "  return await page.evaluate(() => {"
            "    var r = document.querySelector('.sig-row');"
            "    var cap = document.getElementById('sigCapT');"
            "    return r ? JSON.stringify({row: r.textContent,"
            "      val: (r.querySelector('[data-v]')||{}).textContent,"
            "      un: (r.querySelector('.sig-un')||{}).textContent,"
            "      cap: cap ? cap.textContent : ''}) : 'no row'; }); }"
        )
        rc, log = _cli("run-code", "--raw", '"%s"' % probe, cwd=root)
        print("probe:", log.strip()[:300])
        return 0 if ok else 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
