#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_freq_probe.py — 验证「滚动期间降频」是否真的生效

数滚动 / 静止两种状态下 updateDom 与 paintLayer 的实际调用次数。
如果滚动时调用次数没降，说明 isScrolling() / scrollUntil 没接上。
"""
import json
import os
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


JS = r"""
async (page) => {
  await page.evaluate(() => { if (typeof setDemo === 'function') setDemo(true); });
  await page.waitForTimeout(3000);

  const r = await page.evaluate(async () => {
    // hook：数真实调用次数
    const od = window.updateDom, pl = window.paintLayer;
    let nDom = 0, nPaint = 0;
    window.updateDom = function(){ nDom++; return od.apply(this, arguments); };
    window.paintLayer = function(){ nPaint++; return pl.apply(this, arguments); };

    const stage = document.getElementById('stage');
    const maxY = Math.max(1, stage.scrollHeight - stage.clientHeight);
    let scrollEvts = 0;
    const onScroll = () => { scrollEvts++; };
    stage.addEventListener('scroll', onScroll, {passive:true});

    async function run(doScroll, sec){
      nDom = 0; nPaint = 0; scrollEvts = 0;
      let y = 0, sawRolling = 0, samples = 0;
      const t0 = performance.now();
      await new Promise(res => {
        (function f(){
          if (doScroll){ y += 9; if (y > maxY) y = 0; stage.scrollTop = y; }
          samples++;
          if (typeof isScrolling === 'function' && isScrolling()) sawRolling++;
          if (performance.now() - t0 < sec*1000) requestAnimationFrame(f); else res();
        })();
      });
      const el = (performance.now() - t0) / 1000;
      return {
        domPerSec: +(nDom/el).toFixed(1),
        paintPerSec: +(nPaint/el).toFixed(1),
        scrollEvtPerSec: +(scrollEvts/el).toFixed(1),
        rollingPct: +(sawRolling/Math.max(1,samples)*100).toFixed(1)
      };
    }

    const cfg = {
      domHz: UI_CFG.domHz, domHzScroll: UI_CFG.domHzScroll,
      trendFps: UI_CFG.trendFps, trendFpsScroll: UI_CFG.trendFpsScroll,
      scrollQuietMs: UI_CFG.scrollQuietMs,
      hasIsScrolling: typeof isScrolling === 'function',
      hasScrollUntil: typeof scrollUntil !== 'undefined'
    };
    const still = await run(false, 3);
    const scroll = await run(true, 3);
    window.updateDom = od; window.paintLayer = pl;
    stage.removeEventListener('scroll', onScroll);
    return JSON.stringify({cfg: cfg, still: still, scroll: scroll});
  });
  return r;
}
"""


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "telemetry_web"))
    port = 8797
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rc, log = _cli("open", '"http://127.0.0.1:%d/index.html"' % port, cwd=root)
        if rc != 0:
            print("open fail:", log[-300:])
            return 1
        _cli("resize", "1500", "1000", cwd=root)
        rc, log = _cli("run-code", "--raw", '"%s"' % _flatten(JS), cwd=root)
        raw = log.strip()
        i = 0 if raw[:1] in ('{', '"', '[') else raw.find("{")
        if i < 0:
            print("no json:", raw[-600:])
            return 1
        d, _ = json.JSONDecoder().raw_decode(raw, i)
        if isinstance(d, str):
            d = json.loads(d)
    finally:
        srv.shutdown()

    print("=" * 62)
    print("降频验证 · 数 updateDom / paintLayer 的真实调用次数")
    print("=" * 62)
    c = d.get("cfg", {})
    print("\n[配置] %s" % json.dumps(c, ensure_ascii=False))
    print("\n%-10s %12s %12s %14s %12s" %
          ("档位", "updateDom/s", "paintLayer/s", "scrollEvt/s", "rolling%"))
    for k in ("still", "scroll"):
        p = d.get(k, {})
        print("%-10s %12s %12s %14s %11s%%" %
              (k, p.get("domPerSec"), p.get("paintPerSec"),
               p.get("scrollEvtPerSec"), p.get("rollingPct")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
