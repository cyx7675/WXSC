#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_hex_bench.py — HEX 监视器专项压力：演示模式高速写入 + 人工上下拖动，测掉帧。

对照：v2.7 旧实现（60 节点池 + column-reverse + 强制拉底）在这场景下
      step 尖刺数倍、甚至整页交互假死（用户实测"滑动 HEX 会死机"）。
"""
import os
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


def _free_port(preferred=8795):
    for p in (preferred, 8796, 8797):
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


MEASURE_JS = (
    "async (page) => { await page.evaluate(() => {"
    "  window.__frames = [];"
    "  window.__lastT = performance.now();"
    "  window.__rafOn = true;"
    "  (function loop(t){"
    "    if (!window.__rafOn) return;"
    "    window.__frames.push(t - window.__lastT);"
    "    window.__lastT = t;"
    "    requestAnimationFrame(loop);"
    "  })(performance.now());"
    "}); return 'ok'; }"
)

STOP_JS = (
    "async (page) => {"
    "  const r = await page.evaluate(() => {"
    "    window.__rafOn = false;"
    "    const f = window.__frames.slice(2);"
    "    f.sort(function(a,b){return a-b;});"
    "    const q = function(p){ return f[Math.min(f.length-1, Math.floor(p*f.length))]; };"
    "    const avg = f.reduce(function(s,x){return s+x;},0) / f.length;"
    "    const pre = document.querySelector('#hexBox .hexpre');"
    "    return JSON.stringify({n: f.length, avg: +avg.toFixed(2),"
    "      p50: +q(0.5).toFixed(2), p95: +q(0.95).toFixed(2), max: +f[f.length-1].toFixed(1),"
    "      over16: f.filter(function(x){return x>16.7;}).length,"
    "      over33: f.filter(function(x){return x>33.4;}).length,"
    "      hexBuf: hexBuf.length,"
    "      hexLines: pre ? pre.textContent.split('\\n').length - 1 : 0});"
    "  }); return r; }"
)


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "telemetry_web"))
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    try:
        rc, log = _cli("open", '"%s"' % url, cwd=root)
        if rc != 0:
            print("open fail:", log)
            return 1
        _cli("resize", "1500", "1000", cwd=root)
        _cli("run-code", "--raw",
             '"async (page) => { await page.evaluate(() => setDemo(true)); return \'ok\'; }"',
             cwd=root)
        # 演示模式不产生 HEX（HEX 走串口 rawReady），页面内直灌等价流量。
        # ★ 不用 setInterval：离屏页面会被 Chromium 后台节流到 ~1Hz（实测 6s 只
        #   触发 5 次）。rAF 在离屏下不节流（实测 ~156fps），每帧灌 2 行 ≈ 300 行/s，
        #   高于真实 P0@100Hz 的 HEX 口径，属于偏严苛压力。
        feed = (
            "async (page) => { await page.evaluate(() => {"
            "  window.__hexOn = true;"
            "  (function feedLoop(){"
            "    if (!window.__hexOn) return;"
            "    var l1 = [], l2 = [];"
            "    for (var k = 0; k < 27; k++){"
            "      l1.push(('0' + ((Math.random()*256)|0).toString(16)).slice(-2));"
            "      l2.push(('0' + ((Math.random()*256)|0).toString(16)).slice(-2)); }"
            "    pushHexLines(l1.join(' ')); pushHexLines(l2.join(' '));"
            "    requestAnimationFrame(feedLoop);"
            "  })();"
            "}); return 'fed'; }"
        )
        rc, log = _cli("run-code", "--raw", '"%s"' % feed, cwd=root)
        print("feed rc=%d %s" % (rc, log[-120:]))
        time.sleep(3)   # HEX 已经在持续写入
        rc, log = _cli("run-code", "--raw",
                       '"async (page) => { return await page.evaluate(() => hexBuf.length); }"',
                       cwd=root)
        print("hexbuf rc=%d %s" % (rc, log[-120:]))

        print("== 场景 A：HEX 持续写入，页面静止 ==")
        _cli("run-code", "--raw", '"%s"' % MEASURE_JS, cwd=root)
        time.sleep(6)
        rc, log = _cli("run-code", "--raw", '"%s"' % STOP_JS, cwd=root)
        print("  ", log.strip()[:220])

        print("== 场景 B：写入同时快速上下拖动 HEX（死机复现场景）==")
        _cli("run-code", "--raw", '"%s"' % MEASURE_JS, cwd=root)
        # 模拟滚轮：滚到顶再滚到底，来回 12 次（真实手势会触发 wheel + 滚动）
        for i in range(12):
            y = 300 if i % 2 == 0 else 640
            _cli("run-code", "--raw",
                 '"async (page) => { const b = await page.locator(\'#hexBox\').boundingBox();'
                 ' await page.mouse.move(b.x + b.width/2, b.y + b.height/2);'
                 ' for (var k=0;k<10;k++){ await page.mouse.wheel(0, %s); await page.waitForTimeout(16); }'
                 ' return \'ok\'; }"' % ("-600" if i % 2 == 0 else "600"), cwd=root)
        rc, log = _cli("run-code", "--raw", '"%s"' % STOP_JS, cwd=root)
        print("  ", log.strip()[:220])
        return 0
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
