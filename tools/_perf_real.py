# -*- coding: utf-8 -*-
"""task-28 任务一：**真实窗口**（qt-opengl，不设 offscreen）性能复测 —— HEAD(改前) vs 现在(改后)。

测什么（每段相位各出一组数）：
  · 主循环帧时间：avg / p95 / max（钩 `render`，直接量主循环自己的 rAF 时间戳）
  · FPS = 帧数 / 窗口秒数
  · **1ms 心跳最大间隔**：页面侧 `setInterval(...,1)` 的 max gap（主线程被占住就会变大）
    + Qt 侧 1ms QTimer 的心跳 max gap（窗口/事件循环是否被阻塞）
  · 各 canvas 图层重绘次数（证明不是每帧无条件全画）
  · **空闲 5s** 的重绘次数（应为 0）

相位：warmup 3s -> 实时喂数据 8s（20Hz，真机量级）-> 空闲 5s

用法（★ 不要设 QT_QPA_PLATFORM）：
  <venv>\\python.exe tools\\_perf_real.py --root F:\\telemetry_v2_head --tag HEAD
  <venv>\\python.exe tools\\_perf_real.py --root F:\\telemetry_v2      --tag NOW
"""
import argparse
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HOOK_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
  window.__perf = {frames: [], work: [], hbMax: 0, hbN: 0, c: {}, running: false, idle: false};
  /* 1ms 心跳：页面主线程只要被长任务占住，gap 就会飙起来（这段不依赖 app.js，DocumentCreation 即可） */
  var last = performance.now();
  setInterval(function () {
    var t = performance.now();
    var gap = t - last; last = t;
    if (window.__perf.running && !window.__perf.idle) {
      window.__perf.hbN++;
      if (gap > window.__perf.hbMax) window.__perf.hbMax = gap;
    }
  }, 1);
})();
"""

# ★ 必须在**页面加载完成之后**跑：DocumentCreation 时 app.js 还没执行，
#   render/drawRingGauge 等全是 undefined，钩子会静默全部落空（第一版就是这样，counts={}）。
HOOK_FNS_JS = """
(function () {
  if (window.__perfFnHooked) return 'already';
  window.__perfFnHooked = 1;
  if (typeof render === 'function') {
    var _r = render;
    render = function (ts) {
      if (window.__perf.running && !window.__perf.idle){
        var t0 = performance.now();
        var out = _r.apply(this, arguments);
        /* ★ 帧**内工作量**（ms）：两台机器都跑满 60fps 时，帧间隔看不出差别，
           真正能回答"新组件加了多少 CPU"的是这个数 */
        window.__perf.work.push(Math.round((performance.now() - t0) * 100) / 100);
        window.__perf.frames.push(ts);
        return out;
      }
      return _r.apply(this, arguments);
    };
  }
  ['paintGauges', 'drawRingGauge', 'drawTrend', 'drawVChart', 'paintVCharts',
   'paintAccVec', 'updateHeats', 'updateDom', 'drawDualAxis', 'drawScatter',
   'paintMiniCharts', 'paintTable', 'drawTable', 'paintSpark', 'updateSigPanel'].forEach(function (name) {
    if (typeof window[name] !== 'function') return;
    var _f = window[name];
    window.__perf.c[name] = 0;
    window[name] = function () {
      if (window.__perf.running && !window.__perf.idle) window.__perf.c[name]++;
      return _f.apply(this, arguments);
    };
  });
  return 'hooked:' + Object.keys(window.__perf.c).join(',');
})();
"""

FEED_JS = """
window.__feed = setInterval(function () {
  var t = performance.now();
  onDataBatch(JSON.stringify([
    {type:'adc', seq:1, ts:t, values:[2048, 1500, 1400, 1300, 1200, 1100, 1000, 900, 800, 700]},
    {type:'nav', seq:1, ts:t, names:['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'],
     values:[2, 1, 30, 1.2, -0.8], roll:2, pitch:1, yaw:30, accel_lat:1.2, accel_lon:-0.8},
    {type:'motor', seq:1, ts:t, names:['SPEED','RPM','THROTTLE','TORQUE','TEMP','VOLT','AMP'],
     values:[31.2, 5200, 62, 128.5, 44.8, 443.9, 150], speed:31.2, rpm:5200, throttle:62,
     torque:128.5, temp:44.8, volt:443.9, amp:150}
  ]));
}, 50);
'ok'
"""

START_JS = "(function(){ window.__perf.frames=[]; window.__perf.hbMax=0; window.__perf.hbN=0; window.__perf.idle=false; window.__perf.running=true; return 'ok'; })()"
IDLE_JS = "(function(){ if (window.__feed) clearInterval(window.__feed); window.__perf.idle=true; window.__perf.cIdle0 = JSON.parse(JSON.stringify(window.__perf.c)); return 'ok'; })()"
READ_JS = """
(function () {
  var p = window.__perf;
  var idle = {};
  Object.keys(p.c).forEach(function (k) { idle[k] = p.c[k] - ((p.cIdle0 && p.cIdle0[k]) || 0); });
  var out = {frames: p.frames.length, hbMax: Math.round(p.hbMax * 100) / 100, hbN: p.hbN,
             deltas: [], counts: p.c, idleCounts: idle, errors: window.__errs};
  for (var i = 1; i < p.frames.length; i++)
    out.deltas.push(Math.round((p.frames[i] - p.frames[i - 1]) * 100) / 100);
  out.work = p.work.slice();
  window.__perf.running = false;
  return JSON.stringify(out);
})()
"""


def pct(a, q):
    if not a:
        return 0.0
    b = sorted(a)
    i = min(len(b) - 1, max(0, int(round(q * (len(b) - 1)))))
    return b[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--tag", default="RUN")
    ap.add_argument("--measure", type=float, default=8.0)
    ap.add_argument("--idle", type=float, default=5.0)
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    res = {}
    hb = {"max": 0.0, "n": 0, "last": time.perf_counter()}

    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    s = QWebEngineScript()
    s.setName("perf")
    s.setSourceCode(HOOK_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    # Qt 侧 1ms 心跳：事件循环/主线程被阻塞 -> gap 变大
    hbt = QTimer()
    hbt.setInterval(1)

    state = {"phase": "warmup"}

    def beat():
        now = time.perf_counter()
        gap = (now - hb["last"]) * 1000.0
        hb["last"] = now
        if state["phase"] == "measure":
            hb["n"] += 1
            if gap > hb["max"]:
                hb["max"] = gap

    hbt.timeout.connect(beat)

    def on_load(ok):
        def do_idle():
            state["phase"] = "idle"
            page.runJavaScript(IDLE_JS, lambda _: QTimer.singleShot(
                int(args.idle * 1000), read))
        def start_measure():
            state["phase"] = "measure"
            page.runJavaScript(FEED_JS, lambda _: QTimer.singleShot(
                int(args.measure * 1000), do_idle))
        def read():
            def got(v):
                res["r"] = v
                app.quit()
            page.runJavaScript(READ_JS, got)
        # 页面加载完成 -> 先装函数钩子（app.js 已执行），再热身
        QTimer.singleShot(3000, lambda: page.runJavaScript(
            HOOK_FNS_JS, lambda hk: (res.__setitem__("hook", hk),
                                     page.runJavaScript(START_JS,
                                                        lambda _: QTimer.singleShot(300, start_measure)))))

    page.loadFinished.connect(on_load)
    view.resize(1600, 1000)
    view.show()
    hbt.start()
    view.load(QUrl.fromLocalFile(os.path.join(root, "telemetry_web", "index.html")))
    QTimer.singleShot(int((3 + args.measure + args.idle + 20) * 1000), app.quit)
    app.exec()
    hbt.stop()

    r = json.loads(res.get("r") or "{}")
    dts = r.get("deltas") or []
    fr = r.get("frames") or 0
    win_s = args.measure
    avg = (sum(dts) / len(dts)) if dts else 0.0
    print("=== %s（%s）===" % (args.tag, root))
    print("  主循环帧数=%s / 窗口 %.1fs  ->  FPS = %.1f" % (fr, win_s, fr / win_s if win_s else 0))
    print("  帧间隔: avg=%.2f ms  p95=%.2f ms  max=%.2f ms  (n=%d)"
          % (avg, pct(dts, 0.95), max(dts) if dts else 0, len(dts)))
    wk = r.get("work") or []
    print("  帧内工作量: avg=%.2f ms  p95=%.2f ms  max=%.2f ms  (预算 16.67ms)"
          % ((sum(wk)/len(wk)) if wk else 0, pct(wk, 0.95), max(wk) if wk else 0))
    print("  页面侧 1ms 心跳: 次数=%s  最大间隔=%.2f ms" % (r.get("hbN"), r.get("hbMax") or 0))
    print("  Qt 侧 1ms 心跳: 次数=%s  最大间隔=%.2f ms" % (hb["n"], hb["max"]))
    print("  各绘制入口调用次数（%.1fs 内）: %s" % (win_s, r.get("counts")))
    print("  **空闲 %.1fs 内重绘次数**（应全 0）: %s" % (args.idle, r.get("idleCounts")))
    print("  JS 错误: %s" % (r.get("errors") or []))
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "_perf_real_%s.json" % args.tag), "w", encoding="utf-8") as f:
            json.dump({"tag": args.tag, "root": root, "frames": fr, "fps": fr / win_s if win_s else 0,
                       "hb_page_max": r.get("hbMax"), "hb_qt_max": hb["max"],
                       "counts": r.get("counts"), "idle": r.get("idleCounts"),
                       "errors": r.get("errors")}, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("  WARN 结果落盘失败:", e)


if __name__ == "__main__":
    main()
