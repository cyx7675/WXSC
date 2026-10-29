# -*- coding: utf-8 -*-
"""scroll_bench.py —— 主舞台滚动专项基准（定位「滚动不丝滑」）

为什么单独做这个
----------------
perf_bench.py 只测「静止时的主线程占用」，测不出滚动。
用户反馈「交互界面滚动不丝滑」，而滚动卡顿的成因和静态渲染完全不同：
滚动时浏览器要不断重算**可见区**的合成层、重绘阴影/动画元素，
再叠加 20Hz 的 DOM 更新（每 50ms 改一批数字/条形），两者会互相放大。

测什么
------
1. **跟手性**：scrollTop 赋值后**立刻**读回的值 ——
   scroll-behavior:smooth 下赋值不会立即生效（浏览器要平滑滑过去），
   表现就是"滚轮停了页面还在飘"。赋值立即生效才是跟手。
2. **滚动期间的主线程开销**：一边滚一边跑真实数据管线（演示模式），
   测每个调度 step 的耗时，与「不滚动」基线对比。
3. **静态风险扫描**：滚动容器里有多少带 box-shadow / 无限 animation /
   transition 的元素 —— 这些是滚动重绘的成本大头。

用法
----
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\Scripts\\python.exe tools\\scroll_bench.py [--seconds 6]
"""
import os
import sys
import json
import argparse

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --no-sandbox --disable-software-rasterizer "
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling "
    "--disable-renderer-backgrounding --disable-background-timer-throttling")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QUrl, QTimer                      # noqa: E402
from PyQt6.QtWidgets import QApplication                   # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView        # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = r"""
(function (OPTS) {
  var SEC = OPTS.seconds;
  window.__errs = [];
  window.addEventListener('error', function (e) { window.__errs.push(e.message); });

  var appEl = document.getElementById('app');
  appEl.style.width = '1440px';
  appEl.style.height = '860px';
  var stage = document.getElementById('stage');

  document.getElementById('demoMode').checked = true;
  setDemo(true);
  if (trendLayer) trendLayer.dirty = true;

  // ---------- 1) 静态风险扫描 ----------
  function countIn(root, sel) {
    try { return root.querySelectorAll(sel).length; } catch (e) { return -1; }
  }
  // 带明确 box-shadow 的元素（离屏下 getComputedStyle 能拿到声明值）
  var shadowN = 0, animN = 0, transN = 0, total = 0;
  var all = stage.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    total++;
    var cs = getComputedStyle(all[i]);
    if (cs.boxShadow && cs.boxShadow !== 'none') shadowN++;
    if (cs.animationName && cs.animationName !== 'none') animN++;
    if (cs.transitionDuration && parseFloat(cs.transitionDuration) > 0) transN++;
  }
  var scan = {
    stageScrollH: stage.scrollHeight,
    stageClientH: stage.clientHeight,
    scrollablePx: stage.scrollHeight - stage.clientHeight,
    domNodesInStage: total,
    boxShadowNodes: shadowN,
    animatingNodes: animN,
    transitionNodes: transN,
    behavior: getComputedStyle(stage).scrollBehavior
  };

  // ---------- 2) 跟手性 A/B ----------
  // smooth: 赋值后浏览器要"平滑滑过去"，立刻读回不是目标值
  // auto  : 赋值即生效
  function followProbe(mode) {
    stage.style.scrollBehavior = mode;
    stage.scrollTop = 0;
    return new Promise(function (res) {
      setTimeout(function () {
        stage.scrollTop = 600;
        var immediate = stage.scrollTop;
        setTimeout(function () {
          res({
            mode: mode,
            target: 600,
            immediate: Math.round(immediate),
            after300ms: Math.round(stage.scrollTop)
          });
        }, 300);
      }, 60);
    });
  }

  // ---------- 3) 滚动期间的主线程开销 ----------
  // 复刻 app.js 的调度节奏（趋势 30fps / DOM 20Hz），每步额外滚动一点
  function runPhase(scroll, seconds) {
    return new Promise(function (res) {
      var STEP = 16, DRAW_MS = 1000 / 30, DOM_MS = 50;
      var stepCost = [], lastDraw = 0, lastDom = 0, t0 = performance.now();
      var y = 0, maxY = Math.max(1, stage.scrollHeight - stage.clientHeight);
      var running = true;

      function frame() {
        var now = performance.now(), s0 = now;
        if (scroll) {
          y += 14; if (y > maxY) y = 0;
          stage.scrollTop = y;                 // 写入
          void stage.scrollTop;                // 读回：强制同步布局，暴露真实成本
        }
        if (!state.frozen && trendDirty && now - lastDraw >= DRAW_MS) {
          paintLayer(trendLayer, drawTrend);
          lastDraw = now; trendDirty = false;
        }
        if (domDirty && now - lastDom >= DOM_MS) {
          updateDom();
          lastDom = now; domDirty = false;
        }
        stepCost.push(performance.now() - s0);
        if (running) setTimeout(frame, STEP);
      }
      setTimeout(frame, STEP);
      setTimeout(function () {
        running = false;
        var a = stepCost.slice(1).sort(function (x, y2) { return x - y2; });
        var sum = 0, over16 = 0, over33 = 0;
        for (var i2 = 0; i2 < a.length; i2++) {
          sum += a[i2];
          if (a[i2] > 16) over16++;
          if (a[i2] > 33) over33++;
        }
        res({
          scroll: scroll,
          wallMs: Math.round(performance.now() - t0),
          samples: a.length,
          avg: +(sum / Math.max(1, a.length)).toFixed(2),
          p50: +(a[Math.floor(a.length * 0.5)] || 0).toFixed(2),
          p95: +(a[Math.floor(a.length * 0.95)] || 0).toFixed(2),
          max: +(a[a.length - 1] || 0).toFixed(2),
          pctOver16: +(over16 / Math.max(1, a.length) * 100).toFixed(1),
          pctOver33: +(over33 / Math.max(1, a.length) * 100).toFixed(1)
        });
      }, seconds * 1000);
    });
  }

  (function () {
    followProbe('smooth')
      .then(function (r1) {
        window.__followSmooth = r1;
        return followProbe('auto');
      })
      .then(function (r2) {
        window.__followAuto = r2;
        stage.style.scrollBehavior = 'smooth';   // 回到现状
        return runPhase(false, SEC);
      })
      .then(function (p1) {
        window.__still = p1;
        return runPhase(true, SEC);
      })
      .then(function (p2) {
        window.__scrolling = p2;
        window.__scan = scan;
        window.__done = true;
      });
  })();
})(__OPTS__);
"""

DONE_JS = """JSON.stringify({
  scan: window.__scan || null,
  followSmooth: window.__followSmooth || null,
  followAuto: window.__followAuto || null,
  still: window.__still || null,
  scrolling: window.__scrolling || null,
  done: !!window.__done,
  errors: (window.__errs || []).slice(0, 8)
})"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=5)
    ap.add_argument("--out", default="_scroll_bench.json")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1440, 860)
    result = {}

    def on_load(ok):
        if not ok:
            result["load"] = False
            app.quit()
            return
        js = DRIVER.replace("__OPTS__", "{seconds:%d}" % args.seconds)
        QTimer.singleShot(1500, lambda: view.page().runJavaScript(js, lambda v: None))
        QTimer.singleShot((args.seconds * 2 + 6) * 1000,
                          lambda: view.page().runJavaScript(DONE_JS, grab))

    def grab(v):
        result["data"] = v
        app.quit()

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot((args.seconds * 2 + 25) * 1000, app.quit)
    app.exec()

    try:
        d = json.loads(result.get("data") or "{}")
    except Exception:
        d = {"raw": result.get("data")}

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out),
              "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print("=" * 66)
    print("主舞台滚动基准 · 离屏（#app 强制 1440x860）· 每档 %d 秒" % args.seconds)
    print("=" * 66)
    sc = d.get("scan") or {}
    if sc:
        print("\n[滚动容器]")
        print("  可滚动距离      : %s px（内容 %s / 视口 %s）"
              % (sc.get("scrollablePx"), sc.get("stageScrollH"), sc.get("stageClientH")))
        print("  scroll-behavior : %s" % sc.get("behavior"))
        print("  滚动区内节点数  : %s" % sc.get("domNodesInStage"))
        print("  其中 box-shadow : %s   无限 animation: %s   transition: %s"
              % (sc.get("boxShadowNodes"), sc.get("animatingNodes"),
                 sc.get("transitionNodes")))

    fs = d.get("followSmooth") or {}
    fa = d.get("followAuto") or {}
    if fs and fa:
        print("\n[跟手性] 赋值 scrollTop=600 后立刻读回")
        print("  smooth(现状): 立即=%s  300ms后=%s" % (fs.get("immediate"), fs.get("after300ms")))
        print("  auto        : 立即=%s  300ms后=%s" % (fa.get("immediate"), fa.get("after300ms")))

    st = d.get("still") or {}
    ro = d.get("scrolling") or {}
    if st and ro:
        print("\n[主线程 step 耗时] 单位 ms")
        print("  %-10s %8s %8s %8s %8s %10s" % ("", "avg", "p50", "p95", "max", ">16ms占比"))
        for tag, p in (("静止", st), ("滚动中", ro)):
            print("  %-10s %8s %8s %8s %8s %9s%%"
                  % (tag, p.get("avg"), p.get("p50"), p.get("p95"),
                     p.get("max"), p.get("pctOver16")))
        try:
            a0, a1 = float(st.get("avg")), float(ro.get("avg"))
            print("  滚动相对静止：%+.1f%%" % ((a1 - a0) / max(0.001, a0) * 100))
        except Exception:
            pass

    if d.get("errors"):
        print("\n  errors:", d["errors"])
    print("\n-> tools/%s" % args.out)


if __name__ == "__main__":
    main()
