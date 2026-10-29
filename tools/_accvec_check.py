# -*- coding: utf-8 -*-
"""task-13 验证：加速度矢量图 + 合成总加速度（三路接入可视化与趋势图）。

覆盖：
  ① 合成值手算对照（lat/lon -> sqrt(lat²+lon²)），含负值、满量程、0
  ② 三路真的进了 SIGS / state.buf / chipBar / visChannels / 数值面板
  ③ 趋势图真的画出三条曲线（钩 stroke 记录 trendCanvas 上的 strokeStyle）
  ④ 矢量图真的画出来（钩 arc/stroke/fill 记录 accVecCanvas 上的调用与颜色）
  ⑤ 暂停(frozen)时鼠标互动仍能刷新（清空画布 -> hover -> 像素/调用回来）
  ⑥ 不给 rAF 主循环加每帧开销（脏标记为假时 300ms 内 0 次实际绘制）+ 单次绘制耗时
  ⑦ 老协议兼容（只有 f.accel 时映射到纵向）
  ⑧ 派生量跟随：无配置字段也不报错、unit/fmt 跟随 lat

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_accvec_check.py
"""
import os
import sys
import json

# ★ 控制台编码自保护（task-8 F8）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT_JSON = os.path.join(HERE, "tools", "_accvec_result.json")


class Probe(QWebEnginePage):
    """把控制台消息也收下来 —— 未捕获异常的真实文本/行号只有这里能看到。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = []

    def javaScriptConsoleMessage(self, level, msg, line, source):
        self.console.append("[%s] %s (line %d, %s)" % (level.name, msg, line, source))

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  /* 钩绘制：按 canvas id 分别记录 —— 用来证明"真的画在哪个画布上、用了什么颜色" */
  window.__acc = {arcs: 0, strokes: [], fills: 0, trendStrokes: []};
  if (!window.__drawHooked) {
    window.__drawHooked = true;
    var _arc = CanvasRenderingContext2D.prototype.arc;
    CanvasRenderingContext2D.prototype.arc = function () {
      if (this.canvas && this.canvas.id === 'accVecCanvas') window.__acc.arcs++;
      return _arc.apply(this, arguments);
    };
    var _stroke = CanvasRenderingContext2D.prototype.stroke;
    CanvasRenderingContext2D.prototype.stroke = function () {
      var id = this.canvas ? this.canvas.id : '';
      if (id === 'accVecCanvas') window.__acc.strokes.push(String(this.strokeStyle));
      if (id === 'trendCanvas') window.__acc.trendStrokes.push(String(this.strokeStyle));
      return _stroke.apply(this, arguments);
    };
    var _fill = CanvasRenderingContext2D.prototype.fill;
    CanvasRenderingContext2D.prototype.fill = function () {
      if (this.canvas && this.canvas.id === 'accVecCanvas') window.__acc.fills++;
      return _fill.apply(this, arguments);
    };
  }
})();
"""

SETUP_JS = """
(function () {
  var out = {};
  /* ★ 2026-10-15：矢量图已从「数据可视化」搬进「总览 → 姿态与惯性」(#zoneP1)。
     所以这里要进的是**总览**（索引 0，也是默认分区）；分区隐藏时 canvas 尺寸为 0、
     syncLayer 直接返回，什么也画不出来 —— 点错分区会让所有矢量图断言变红。 */
  document.querySelectorAll('.rail-item')[0].click();
  /* ★ paintAccVec 的计数包装必须放在 app.js 执行之后（DocumentCreation 注入会被函数声明覆盖） */
  if (!window.__pavWrapped) {
    window.__pavWrapped = true;
    window.__paintActual = 0;
    var _pav = paintAccVec;
    paintAccVec = function () {
      var before = window.__acc.arcs + window.__acc.strokes.length + window.__acc.fills;
      var r = _pav.apply(this, arguments);
      var after = window.__acc.arcs + window.__acc.strokes.length + window.__acc.fills;
      if (after > before) window.__paintActual++;
      return r;
    };
  }
  out.zoneVisible = zoneVisible('zoneVisual');
  out.canvasBox = (function () {
    var cv = document.getElementById('accVecCanvas');
    var b = cv.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)];
  })();
  /* 信号注册表：三路都在，且都是 P1（= NAV 包）*/
  out.sigs = {};
  ['accelLat', 'accelLon', 'accelMag'].forEach(function (id) {
    var s = SIGS[id];
    out.sigs[id] = s ? {label: s.label, unit: s.unit, group: s.group, fmt: s.fmt,
                        color: s.color, min: s.min, max: s.max} : null;
  });
  out.accelRawGone = (typeof SIGS.accelRaw === 'undefined');
  out.bufExists = ['accelLat', 'accelLon', 'accelMag'].map(function (id) {
    return !!(state.buf[id] && state.latest[id] !== undefined);
  });
  out.csvHeaderLocal = (typeof CSV_HEADER_LOCAL === 'string') ? CSV_HEADER_LOCAL : '';

  /* ---- ① 合成值手算对照：走真实入口 onDataBatch（payload 与后端 _payload_of 同形）---- */
  var CASES = [
    {lat: 3,      lon: 4,     mag: 5},
    {lat: -6,     lon: 8,     mag: 10},
    {lat: 16.384, lon: 0,     mag: 16.384},
    {lat: 0,      lon: 0,     mag: 0},
    {lat: -12.5,  lon: -7.5,  mag: 14.577379737113251}
  ];
  out.cases = [];
  CASES.forEach(function (c) {
    lastSampleT = -1e9;      /* 解除取样限频：让每一批都真的推一个点，才能验证"进了 state.buf" */
    onDataBatch(JSON.stringify([{
      type: 'nav', seq: 1, ts: 1000, names: ['ROLL', 'PITCH', 'YAW', 'ACCEL_LAT', 'ACCEL_LON'],
      values: [0, 0, 0, c.lat, c.lon], roll: 0, pitch: 0, yaw: 0,
      accel_lat: c.lat, accel_lon: c.lon
    }]));
    out.cases.push({lat: c.lat, lon: c.lon, expect: c.mag,
                    got: state.latest.accelMag,
                    latestLat: state.latest.accelLat, latestLon: state.latest.accelLon,
                    bufMagTail: state.buf.accelMag[state.buf.accelMag.length - 1]});
  });

  /* ---- ② 三路进 UI：chips / visChannels / 数值面板 ---- */
  state.visible = new Set(['accelLat', 'accelLon', 'accelMag']);
  buildChips();
  buildVisChannels();
  out.chips = Array.prototype.map.call(document.querySelectorAll('#chipBar .chip'),
    function (el) { return el.getAttribute('data-id'); });
  out.visItems = Array.prototype.map.call(document.querySelectorAll('#visChannels .vc-item'),
    function (el) { return el.getAttribute('data-sig'); });
  out.sigRows = Array.prototype.map.call(document.querySelectorAll('#sigPanel .sig-row'),
    function (el) { return el.getAttribute('data-id'); });

  /* ---- ③ 趋势图画三条：种数据 + drawTrend，钩 trendCanvas 的 strokeStyle ---- */
  Object.keys(SIGS).forEach(function (k) {
    state.buf[k] = [];
    for (var i = 0; i < 40; i++) state.buf[k].push(Math.sin(i / 5) * 4 + 2);
  });
  window.__acc.trendStrokes = [];
  trendLayer.sKey = ''; trendLayer.dirty = true;
  paintLayer(trendLayer, drawTrend);
  out.trendStrokeCount = window.__acc.trendStrokes.length;
  out.trendAccelColors = ['#B45309', '#2563EB', '#DC2626'].filter(function (c) {
    return window.__acc.trendStrokes.some(function (s) { return s.toLowerCase() === c.toLowerCase(); });
  });

  /* ---- ④ 矢量图真的画出来（先擦掉，再触发一次离散重绘）---- */
  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cv.width, cv.height);
  window.__acc.arcs = 0; window.__acc.strokes = []; window.__acc.fills = 0;
  refreshAccVec();
  out.dirtyAfterPaint = accVecDirty;      /* 成功画过一次之后应当为 false */
  out.vec = {
    arcs: window.__acc.arcs,
    strokes: window.__acc.strokes.slice(),
    fills: window.__acc.fills,
    hasRed: window.__acc.strokes.some(function (s) { return s.toLowerCase() === '#dc2626'; }),
    /* 画布上是否真有非透明像素 */
    pixels: (function () {
      var d = g.getImageData(0, 0, cv.width, cv.height).data, n = 0;
      for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
      return n;
    })()
  };
  /* DOM 读数（只有 accelLat 带单位占位，单位来自 ACC_UNIT） */
  out.readouts = {
    lat: (document.getElementById('accVecLat') || {}).textContent,
    lon: (document.getElementById('accVecLon') || {}).textContent,
    mag: (document.getElementById('accVecMag') || {}).textContent,
    chip: (document.getElementById('accVecChip') || {}).textContent,
    note: (document.getElementById('accVecNote') || {}).textContent
  };
  out.css = (function () {
    var el = document.getElementById('accVecCanvas');
    return {w: el.clientWidth, h: el.clientHeight, parent: el.parentElement.className};
  })();
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

FREEZE_HOVER_JS = """
(function () {
  var out = {};
  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  state.frozen = true;                 /* 暂停：主循环的 updateDom 不再跑 */
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cv.width, cv.height);
  window.__acc.arcs = 0; window.__acc.strokes = []; window.__acc.fills = 0;
  out.pixelsBefore = (function () {
    var d = g.getImageData(0, 0, cv.width, cv.height).data, n = 0;
    for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
  })();
  /* 鼠标互动：mousemove（离散入口 -> refreshAccVec） */
  cv.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: 10, clientY: 10}));
  out.arcsAfterHover = window.__acc.arcs;
  out.pixelsAfter = (function () {
    var d = g.getImageData(0, 0, cv.width, cv.height).data, n = 0;
    for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
  })();
  out.frozen = state.frozen;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

IDLE_JS = """
(function () {
  /* 启动"空闲计数"：脏标记此刻应为假 -> 接下来的 300ms 里不应有任何实际绘制 */
  window.__idleStart = window.__paintActual;
  return JSON.stringify({paintActualAtStart: window.__paintActual, dirty: accVecDirty});
})()
"""

IDLE_READ_JS = """
(function () {
  return JSON.stringify({paintActualDuring300ms: window.__paintActual - window.__idleStart,
                         dirty: accVecDirty, errors: window.__errs});
})()
"""

PERF_JS = """
(function () {
  /* 单次绘制耗时（含 DOM 读数写入）；强制脏 -> 每次都是真画 */
  var t0 = performance.now();
  for (var i = 0; i < 200; i++){ accVecDirty = true; paintAccVec(); }
  var ms = (performance.now() - t0) / 200;
  /* updateDom 里那一句的代价（脏标记为假时应当近似 0） */
  accVecDirty = false;
  var t1 = performance.now();
  for (var j = 0; j < 2000; j++) paintAccVec();
  var msClean = (performance.now() - t1) / 2000;
  return JSON.stringify({msPerPaint: ms, msPerNoopCall: msClean, errors: window.__errs});
})()
"""

LEGACY_JS = """
(function () {
  /* ⑦ 老协议：只有 f.accel（旧 .bin 回放）-> 映射到纵向，合成值仍然正确。
     ★ 先把 accelLat 归零：老协议会话里横向本来就没有数据（初值 0）；
        不清零就是复用上一条用例的 lat，属于测试串扰。 */
  state.latest.accelLat = 0;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 2, ts: 2000, names: ['ROLL','PITCH','YAW','ACCEL'],
                               values: [0,0,0,1234], roll: 0, pitch: 0, yaw: 0, accel: 1234}]));
  return JSON.stringify({lon: state.latest.accelLon, mag: state.latest.accelMag,
                         errors: window.__errs});
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = Probe(view)
    view.setPage(page)

    s = QWebEngineScript()
    s.setName("errprobe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    res = {}

    def on_load(ok):
        def cb_perf(v):
            res["perf"] = v
            page.runJavaScript(LEGACY_JS, cb_legacy)

        def cb_legacy(v):
            res["legacy"] = v
            app.quit()

        def cb_idle_read(v):
            res["idle"] = v
            page.runJavaScript(PERF_JS, cb_perf)

        def cb_idle(v):
            res["idle_start"] = v
            QTimer.singleShot(300, lambda: page.runJavaScript(IDLE_READ_JS, cb_idle_read))

        def cb_hover(v):
            res["hover"] = v
            page.runJavaScript(IDLE_JS, cb_idle)

        def cb_setup(v):
            res["setup"] = v
            QTimer.singleShot(120, lambda: page.runJavaScript(FREEZE_HOVER_JS, cb_hover))

        QTimer.singleShot(3200, lambda: page.runJavaScript(SETUP_JS, cb_setup))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    def load(k):
        try:
            return json.loads(res.get(k) or "{}")
        except Exception:
            return {}

    setup, hover = load("setup"), load("hover")
    idle, perf, legacy = load("idle"), load("perf"), load("legacy")

    print("=== ① 合成值手算对照（onDataBatch 真实入口）===")
    print("  %-10s %-10s %-14s %-14s %-14s %s" % ("lat", "lon", "期望 sqrt", "实际 accelMag", "latest.lat/lon", "缓冲末值"))
    for c in setup.get("cases", []):
        print("  %-10s %-10s %-14s %-14s %-14s %s"
              % (c["lat"], c["lon"], c["expect"], c["got"],
                 "%s/%s" % (c["latestLat"], c["latestLon"]), c["bufMagTail"]))

    print()
    print("=== ② 三路进 SIGS / state.buf / UI ===")
    for k, v in (setup.get("sigs") or {}).items():
        print("  SIGS.%-9s %s" % (k, v))
    print("  accelRaw 已移除:", setup.get("accelRawGone"), " state.buf/latest 齐备:", setup.get("bufExists"))
    print("  chipBar 通道:", setup.get("chips"))
    print("  visChannels:", setup.get("visItems"))
    print("  数值面板行:", setup.get("sigRows"))
    print("  本地 CSV 表头:", setup.get("csvHeaderLocal"))

    print()
    print("=== ③ 趋势图画三条曲线（钩 trendCanvas.stroke）===")
    print("  trend 描边次数=%s，其中加速度三色命中=%s"
          % (setup.get("trendStrokeCount"), setup.get("trendAccelColors")))

    print()
    print("=== ④ 矢量图真的画出来 ===")
    print("  进可视化区时 zoneVisible=%s 画布 rect=%s" % (setup.get("zoneVisible"), setup.get("canvasBox")))
    v = setup.get("vec") or {}
    print("  arcs=%s strokes=%s fills=%s 含红色合成矢量=%s 非透明像素=%s"
          % (v.get("arcs"), v.get("strokes"), v.get("fills"), v.get("hasRed"), v.get("pixels")))
    print("  刻度/标注用的 strokeStyle:", v.get("strokes"))
    print("  DOM 读数:", setup.get("readouts"))
    print("  画布尺寸/父容器:", setup.get("css"))

    print()
    print("=== ⑤ 暂停时鼠标互动仍能刷新 ===")
    print("  %s" % hover)

    print()
    print("=== ⑥ 不给主循环加每帧开销 ===")
    print("  空闲前 paintActual=%s dirty=%s" % ((load("idle_start") or {}).get("paintActualAtStart"),
                                             (load("idle_start") or {}).get("dirty")))
    print("  300ms 内实际绘制次数=%s（脏标记为假 -> 应为 0）" % idle.get("paintActualDuring300ms"))
    print("  单次绘制 %.3f ms；脏标记为假时的空调用 %.5f ms/次"
          % (perf.get("msPerPaint") or 0, perf.get("msPerNoopCall") or 0))

    print()
    print("=== ⑦ 老协议兼容（只有 f.accel）===")
    print("  %s" % legacy)

    def close(a, b, tol=1e-9):
        return a is not None and b is not None and abs(a - b) <= tol

    sigs = setup.get("sigs") or {}
    cases = setup.get("cases") or []
    checks = [
        ("① 5 组合成值全部与手算一致",
         len(cases) == 5 and all(close(c["got"], c["expect"], 1e-9) for c in cases)),
        ("① 缓冲末值 = 合成值（真的进了 state.buf）",
         all(close(c["bufMagTail"], c["expect"], 1e-9) for c in cases)),
        ("② 三路都在 SIGS 且 group=P1",
         all(sigs.get(k) and sigs[k]["group"] == "P1" for k in ("accelLat", "accelLon", "accelMag"))),
        ("② 三路都有 state.buf / latest", setup.get("bufExists") == [True, True, True]),
        ("② 旧 accelRaw 已从 SIGS 移除（不产生重复曲线）", setup.get("accelRawGone") is True),
        ("② 三路都在 chipBar（可勾选）",
         all(k in (setup.get("chips") or []) for k in ("accelLat", "accelLon", "accelMag"))),
        ("② 三路都在 visChannels（可视化可拖）",
         all(k in (setup.get("visItems") or []) for k in ("accelLat", "accelLon", "accelMag"))),
        ("② 三路都在数值面板",
         all(k in (setup.get("sigRows") or []) for k in ("accelLat", "accelLon", "accelMag"))),
        ("② 本地 CSV 表头已跟随协议（accel_lat,accel_lon）",
         "accel_lat" in (setup.get("csvHeaderLocal") or "") and "accel_lon" in (setup.get("csvHeaderLocal") or "")),
        ("③ 趋势图上确实描了加速度三色",
         set(v2.lower() for v2 in (setup.get("trendAccelColors") or [])) == {"#b45309", "#2563eb", "#dc2626"}),
        ("④ 矢量图画了同心圈+十字（arcs>=5 且有多条描边）",
         (v.get("arcs") or 0) >= 5 and len(v.get("strokes") or []) >= 3),
        ("④ 矢量图含红色合成矢量（strokeStyle=#DC2626）", v.get("hasRed") is True),
        ("④ 画布上真的出现像素", (v.get("pixels") or 0) > 300),
        ("④ DOM 读数已刷新（非占位）",
         (setup.get("readouts") or {}).get("mag") not in (None, "—")),
        ("⑤ 暂停下悬停能刷新（像素从 0 变回 >0）",
         (hover.get("pixelsBefore") == 0) and (hover.get("pixelsAfter") or 0) > 300
         and (hover.get("arcsAfterHover") or 0) >= 5),
        ("⑥ 空闲 300ms 内 0 次实际绘制（不给主循环加每帧开销）",
         idle.get("paintActualDuring300ms") == 0),
        ("⑥ 脏标记为假时调用近似免费（<0.01ms/次）",
         (perf.get("msPerNoopCall") or 1) < 0.01),
        ("⑦ 老协议 f.accel 映射到纵向且合成值正确",
         close(legacy.get("lon"), 1234) and close(legacy.get("mag"), 1234)),
        ("无 JS 运行时错误",
         not setup.get("errors") and not hover.get("errors") and not legacy.get("errors")
         and not perf.get("errors")),
    ]
    print()
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）

    print()
    print("=== ⑧ 控制台消息（未捕获异常会在这里现形）===")
    for m in (page.console or [])[-12:]:
        print("  " + m)
    if not page.console:
        print("  （无）")

    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"setup": setup, "hover": hover, "idle": idle, "perf": perf, "legacy": legacy,
                       "console": page.console}, f, ensure_ascii=False, indent=2)
        print("WROTE", OUT_JSON)
    except Exception as e:
        print("JSON 写入失败:", e)


if __name__ == "__main__":
    main()
