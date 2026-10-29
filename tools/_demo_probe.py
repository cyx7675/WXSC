# -*- coding: utf-8 -*-
"""一次性验证（缺陷3）：无后端打开 -> 全 "—"；手动开演示 -> 数据起来；关演示 -> 回 "—"。
用法： cd F:\\上位机\\v2 && <venv>\\python.exe tools\\_demo_probe.py
"""
import os, shutil, socket, subprocess, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telemetry_web")


def _free_port(preferred=8801):
    for p in (preferred, 8802, 8803, 8804, 8805):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p)); return p
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


SAMPLE = ("async (page) => JSON.stringify(await page.evaluate(() => ({"
          "demo: document.getElementById('demoMode').checked,"
          "live: document.getElementById('liveText').textContent,"
          "speed: document.getElementById('speedNum').textContent,"
          "roll: document.getElementById('rollNum').textContent,"
          "adc: document.querySelector('#adcGrid [data-val]').textContent,"
          "tempState: document.getElementById('tempState').textContent,"
          "errs: window.__errs || []})))")

CLICK_DEMO = "async (page) => { await page.evaluate(() => document.getElementById('demoMode').click()); return 'ok'; }"


def shot(tmp):
    rc, log = _cli('screenshot', '--filename="%s"' % tmp.replace("\\", "/"))
    return os.path.isfile(tmp)


def main():
    port = _free_port()
    srv = threading.Thread()
    handler = partial(_Quiet, directory=ROOT)
    from http.server import ThreadingHTTPServer as T
    server = T(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    tmp = os.path.join(ROOT, "_demo_tmp.png")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_demo_shot.png")
    try:
        rc, log = _cli("open", '"%s"' % url)
        if rc != 0:
            print("open failed:\n" + log); return 1
        _cli("resize", "1600", "1400")
        time.sleep(3)
        _, s1 = _cli("run-code", "--raw", '"%s"' % SAMPLE)
        print("PHASE1 (initial):", s1.strip().splitlines()[-1] if s1.strip() else s1)

        _cli("run-code", "--raw", '"%s"' % CLICK_DEMO)
        time.sleep(4)
        _, s2 = _cli("run-code", "--raw", '"%s"' % SAMPLE)
        print("PHASE2 (demo on):", s2.strip().splitlines()[-1] if s2.strip() else s2)
        if os.path.isfile(tmp):
            os.remove(tmp)
        if shot(tmp):
            shutil.move(tmp, out)
            print("SAVED", out)

        _cli("run-code", "--raw", '"%s"' % CLICK_DEMO)
        time.sleep(1.5)
        _, s3 = _cli("run-code", "--raw", '"%s"' % SAMPLE)
        print("PHASE3 (demo off):", s3.strip().splitlines()[-1] if s3.strip() else s3)
        return 0
    finally:
        server.shutdown()
        if os.path.isfile(tmp):
            try: os.remove(tmp)
            except OSError: pass


if __name__ == "__main__":
    sys.exit(main())
