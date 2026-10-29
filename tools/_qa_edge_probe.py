# -*- coding: utf-8 -*-
"""边界补测：演示中收到真实连接回调 onStatus(true) -> 应停演示、清场、转 LIVE。"""
import os, socket, subprocess, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telemetry_web")


class _Q(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def cli(*a):
    p = subprocess.run("playwright-cli " + " ".join(a), shell=True,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    return ((p.stdout or "") + (p.stderr or "")).strip().splitlines()


def run(code):
    lines = cli("run-code", "--raw", '"%s"' % code.replace('"', '\\"'))
    return lines[-1] if lines else ""


SAMPLE = ("async (page) => JSON.stringify(await page.evaluate(() => ({"
          "demo: document.getElementById('demoMode').checked,"
          "live: document.getElementById('liveText').textContent,"
          "speed: document.getElementById('speedNum').textContent,"
          "errs: window.__errs || []})))")


def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Q, directory=ROOT))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cli("close")
        cli("open", '"http://127.0.0.1:%d/index.html"' % port)
        time.sleep(2)
        run("async (page) => { await page.evaluate(() => {"
            "window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));"
            "}); await page.click('#demoMode'); return 'ok'; }")
        time.sleep(3)
        print("演示中      :", run(SAMPLE))
        run("async (page) => { await page.evaluate(() => onStatus(true, 'COM5')); return 'ok'; }")
        time.sleep(0.8)
        r1 = run(SAMPLE)
        print("真实连接到达:", r1)
        time.sleep(2)
        r2 = run(SAMPLE)
        print("2s 后       :", r2)
        ok = ('"demo":false' in r2 or '\\"demo\\":false' in r2) and "COM5" in r2 and "—" in r2
        print("EDGE PASS" if ok else "EDGE FAIL")
        return 0 if ok else 1
    finally:
        cli("close")
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
