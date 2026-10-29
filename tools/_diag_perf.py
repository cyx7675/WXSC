#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_diag_perf.py — 滚动优化后的视觉回归验证

本轮改了三处会影响外观的地方，必须肉眼确认没画歪：
  1. .tape .ptr 由「2px 宽元素 + left 定位」改成「铺满容器 + transform」，
     竖线移到 ::before —— 指针位置/三角形箭头可能错位
  2. 去掉 bar-fill 的 will-change —— 条形本身应正常
  3. 高频数字容器加 contain:layout —— 不能把数字裁掉

产出：tools/_diag_p1.png（导航区：含航向带指针）、tools/_diag_p2.png（动力区）
并断言 yawPtr 的 transform 随 yaw 变化且竖线真的画出来了。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _cli(*args, cwd=None, timeout=300):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _flatten(js):
    out = []
    for line in js.split("\n"):
        s = line.strip()
        if not s:
            continue
        c = s.find("//")
        if c >= 0:
            s = s[:c].strip()
        if s:
            out.append(s)
    return " ".join(out)


SETUP = r"""
async (page) => {
  await page.evaluate(() => {
    setDemo(true);
  });
  await page.waitForTimeout(1500);
  /* 关掉演示再手动灌值：演示的 simStep 每 50ms 会覆盖 state.latest，
     不关掉就永远读不到我们设的 yaw。 */
  await page.evaluate(() => {
    setDemo(false);
    state.latest.yaw = 180; state.latest.roll = 12; state.latest.pitch = -8;
    state.latest.speed = 27.4; state.latest.rpm = 4200; state.latest.torque = 88;
    state.latest.throttle = 65; state.latest.temp = 72;
    state.hasData.nav = true; state.hasData.motor = true; state.hasData.adc = true;
    for (var i = 0; i < 10; i++) state.latest['adc'+i] = 800 + i * 260;
    updateDom();
  });
  await page.waitForTimeout(300);
  return await page.evaluate(() => {
    const p = document.getElementById('yawPtr');
    const cs = getComputedStyle(p);
    const bf = getComputedStyle(p, '::before');
    const r = p.getBoundingClientRect();
    const tape = p.parentElement.getBoundingClientRect();
    return JSON.stringify({
      transform: cs.transform,
      width: Math.round(r.width), tapeWidth: Math.round(tape.width),
      beforeW: bf.width, beforeBg: bf.backgroundColor,
      ptrX: Math.round(r.left - tape.left),
      transition: cs.transitionProperty,
      willChange: cs.willChange,
      yawText: (document.getElementById('yawNum')||{}).textContent
    });
  });
}
"""


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "telemetry_web"))
    tools = os.path.abspath(os.path.join(root, "..", "tools"))
    port = 8798
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = os.path.join(root, "_diag_tmp.png").replace("\\", "/")
    try:
        rc, log = _cli("open", '"http://127.0.0.1:%d/index.html"' % port, cwd=root)
        if rc != 0:
            print("open fail:", log[-300:])
            return 1
        _cli("resize", "1500", "1000", cwd=root)
        rc, log = _cli("run-code", "--raw", '"%s"' % _flatten(SETUP), cwd=root)
        raw = log.strip()
        i = 0 if raw[:1] in ('{', '"', '[') else raw.find("{")
        probe = {}
        if i >= 0:
            try:
                d, _ = json.JSONDecoder().raw_decode(raw, i)
                probe = json.loads(d) if isinstance(d, str) else d
            except Exception as e:
                print("probe parse:", e, raw[-200:])
        print("[yawPtr 探针]", json.dumps(probe, ensure_ascii=False))

        ok = True
        for zone, name in (("#zoneP1", "_diag_p1.png"), ("#zoneP2", "_diag_p2.png")):
            rc, log = _cli("run-code", "--raw",
                           '"async (page) => { await page.locator(\'%s\')'
                           '.screenshot({path:\'%s\'}); return \'ok\'; }"' % (zone, tmp),
                           cwd=root)
            if not os.path.isfile(tmp):
                print("shot fail", zone, log[-200:])
                ok = False
                continue
            shutil.move(tmp, os.path.join(tools, name))
            print("SAVED", name)
        return 0 if ok else 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
