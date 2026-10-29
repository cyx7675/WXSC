# -*- coding: utf-8 -*-
"""perf_bench.py -- v2 前端性能基准（离屏 + 强制真实布局尺寸）

两个坑，先说清楚
----------------
1) 不能用 requestAnimationFrame 采样：离屏没有合成器，rAF 不触发（实测帧样本
   恒为 0）。所以这里用 setTimeout 复刻 app.js 主循环的**调度节奏**
   （趋势 30fps / DOM 20Hz / HEX 200ms），逐次测量同步计算耗时。
   同步耗时 = 主线程被 UI 占住的时间，正是决定"卡不卡"的量。

2) 离屏下页面布局会被压扁（实测趋势画布只有 61px 宽），直接测会低估一个量级。
   所以本脚本把 #app 强制成 1440×860 —— 与 exe 默认窗口一致 —— 让画布拿到
   真实尺寸，canvas 光栅化成本才是真的。

测什么
------
* 打开演示模式，走 app.js 真实数据管线（simStep -> pushSample -> 缓冲）
* UI 主线程占用率 uiDutyPct（同步耗时 / 墙钟时间）
* 分步耗时：整步 / 趋势绘制 / DOM 更新 / HEX 刷新（avg + p95 + max）
* 分项微基准：paintLayer+drawTrend、清屏、空绘制（衡量双缓冲 blit 成本）

用法
----
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\Scripts\\python.exe tools\\perf_bench.py [--seconds 6] [--out _perf_bench_before.json]
"""
import os
import sys
import json
import argparse

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --no-sandbox --disable-software-rasterizer "
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling "
    "--disable-renderer-backgrounding --disable-background-timer-throttling"
)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = r"""
(function (OPTS) {
  var SEC = OPTS.seconds;
  window.__errs = [];
  window.addEventListener('error', function (e) { window.__errs.push(e.message); });

  // ---- 强制真实布局尺寸（离屏下布局会被压扁，画布只有 61px 宽）----
  var appEl = document.getElementById('app');
  appEl.style.width = '1440px';
  appEl.style.height = '860px';

  var longTasks = [];
  try {
    new PerformanceObserver(function (l) {
      for (var e of l.getEntries()) longTasks.push(Math.round(e.duration));
    }).observe({ entryTypes: ['longtask'] });
  } catch (e) {}

  // ---- 打开演示模式：走 app.js 真实数据管线 ----
  document.getElementById('demoMode').checked = true;
  setDemo(true);
  if (trendLayer) trendLayer.dirty = true;

  var STEP = 16, DRAW_MS = 1000 / 30, DOM_MS = 50, HEX_MS = 200;
  var stepCost = [], domCost = [], paintCost = [], hexCost = [];
  var lastDraw = 0, lastDom = 0, lastHex = 0, nDraw = 0, nDom = 0, nHex = 0;
  var t0wall = performance.now();
  var running = true;

  function frame() {
    var now = performance.now(), t0 = now;

    if (!state.frozen && trendDirty && now - lastDraw >= DRAW_MS) {
      var p0 = performance.now();
      paintLayer(trendLayer, drawTrend);
      paintCost.push(performance.now() - p0);
      lastDraw = now; nDraw++; trendDirty = false;
    }
    if (domDirty && now - lastDom >= DOM_MS) {
      var d0 = performance.now();
      updateDom();
      domCost.push(performance.now() - d0);
      lastDom = now; nDom++; domDirty = false;
    }
    if (now - lastHex >= HEX_MS) {
      var h0 = performance.now();
      pushHexLines('AA 55 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 55 AA');
      flushHex();
      hexCost.push(performance.now() - h0);
      lastHex = now; nHex++;
    }
    stepCost.push(performance.now() - t0);
    if (running) setTimeout(frame, STEP);
  }
  setTimeout(frame, STEP);

  function stat(arr) {
    var a = arr.slice(1).sort(function (x, y) { return x - y; });
    if (!a.length) return { n: 0, avg: -1, p50: -1, p95: -1, max: -1 };
    var s = 0; for (var i = 0; i < a.length; i++) s += a[i];
    return {
      n: a.length,
      avg: +(s / a.length).toFixed(2),
      p50: +a[Math.floor(a.length * 0.5)].toFixed(2),
      p95: +a[Math.floor(a.length * 0.95)].toFixed(2),
      max: +a[a.length - 1].toFixed(2)
    };
  }

  setTimeout(function () {
    running = false;
    var wall = performance.now() - t0wall;
    var sum = 0, over16 = 0, over33 = 0;
    stepCost.forEach(function (x) { sum += x; if (x > 16) over16++; if (x > 33) over33++; });

    function bench(fn, reps) {
      fn();
      var t = performance.now();
      for (var i = 0; i < reps; i++) fn();
      return +((performance.now() - t) / reps).toFixed(3);
    }
    var sub = {
      canvasCss: Math.round(trendLayer.w) + 'x' + Math.round(trendLayer.h),
      dpr: trendLayer.dpr,
      trendPaintMs: bench(function () { paintLayer(trendLayer, drawTrend); }, 30),
      domUpdateMs: bench(function () { updateDom(); }, 30),
      hexFlushMs: bench(function () {
        pushHexLines('AA 55 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11 12 13 14 15 16 17 55 AA');
        flushHex();
      }, 20),
      blitOnlyMs: bench(function () { paintLayer(trendLayer, function () {}); }, 30)
    };

    window.__bench = {
      wallMs: Math.round(wall),
      uiDutyPct: +((sum / wall) * 100).toFixed(1),
      stepsOver16: over16, stepsOver33: over33,
      step: stat(stepCost),
      domUpdate: stat(domCost),
      trendPaint: stat(paintCost),
      hexFlush: stat(hexCost),
      draws: nDraw, domRuns: nDom, hexRuns: nHex,
      sub: sub,
      longTasks: longTasks.length,
      longTaskMaxMs: longTasks.length ? Math.max.apply(null, longTasks) : 0,
      bufLen: state.buf.speed ? state.buf.speed.length : -1,
      errors: window.__errs.slice(0, 8)
    };
  }, SEC * 1000);
})(__OPTS__);
"""

DONE_JS = "JSON.stringify(window.__bench || {})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=6)
    ap.add_argument("--out", default="_perf_bench.json")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    view = QWebEngineView()

    class Probe(QWebEnginePage):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.console = []

        def javaScriptConsoleMessage(self, level, msg, line, source):
            self.console.append("[%s] %s (line %s)" % (level.name, msg, line))

    page = Probe(view)
    view.setPage(page)
    view.resize(1440, 860)

    s = QWebEngineScript()
    s.setName("errprobe")
    s.setSourceCode(
        "window.__errs=window.__errs||[];"
        "window.addEventListener('error',function(e){window.__errs.push(e.message)});")
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    result = {}

    def on_load(ok):
        js = DRIVER.replace("__OPTS__", "{seconds:%d}" % args.seconds)
        QTimer.singleShot(2000, lambda: page.runJavaScript(js, lambda v: None))
        QTimer.singleShot((args.seconds + 3) * 1000,
                          lambda: page.runJavaScript(DONE_JS, grab))

    def grab(v):
        result["bench"] = v
        sys.stderr.write("DEBUG grab=%s\n" % (str(v)[:400],))
        app.quit()

    page.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot((args.seconds + 20) * 1000, app.quit)
    app.exec()

    try:
        bench = json.loads(result.get("bench") or "{}")
    except Exception:
        bench = {"raw": result.get("bench")}

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out),
              "w", encoding="utf-8") as f:
        json.dump({"seconds": args.seconds, "bench": bench}, f, ensure_ascii=False, indent=2)

    print("=" * 62)
    print("v2 前端性能基准 · 离屏（#app 强制 1440x860）· 采样 %d 秒" % args.seconds)
    print("=" * 62)
    for k in ("wallMs", "uiDutyPct", "stepsOver16", "stepsOver33", "draws",
              "domRuns", "hexRuns", "step", "domUpdate", "trendPaint",
              "hexFlush", "sub", "longTasks", "longTaskMaxMs", "bufLen"):
        if k in bench:
            print("  %-14s %s" % (k, bench[k]))
    if bench.get("errors"):
        print("  errors:", bench["errors"])
    print("-> tools/%s" % args.out)


if __name__ == "__main__":
    main()
