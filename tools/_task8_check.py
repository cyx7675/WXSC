# -*- coding: utf-8 -*-
"""task-8 验证：问题五（暂停时可视化悬停数值）+ 问题二（seek 后回填）。

问题三由 tools/_serial_jitter_probe.py 单独验证。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_task8_check.py
"""
import os
import sys
import json

# ★ 控制台编码自保护（task-8 F8）：Windows GBK 控制台下，print 里出现 ⏸/▶/★ 这类字符会
# 抛 UnicodeEncodeError 直接崩进程；而且崩在打印阶段会让“后面才写 JSON”的取证代码根本执行不到。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  window.__ft = [];
  if (!window.__ftHooked) {
    window.__ftHooked = true;
    var _ft = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (t) {
      window.__ft.push({t: String(t), cv: (this.canvas && (this.canvas.id || this.canvas.className)) || '?'});
      return _ft.apply(this, arguments);
    };
  }
})();
"""

SETUP_JS = """
(function () {
  /* ★ drawVChart 的计数包装必须放在 app.js **执行之后**：
     若在 DocumentCreation 注入，函数声明会在 app.js 求值时把我的包装覆盖掉
     （第一版探针就踩了这个坑，__dvcCalls 恒为 0）。 */
  if (!window.__dvcWrapped) {
    window.__dvcWrapped = true;
    var _dvc = drawVChart;
    window.__dvcCalls = 0;
    drawVChart = function (c) { window.__dvcCalls++; return _dvc(c); };
  }
  var ids = Object.keys(SIGS);
  state.hasData = {adc: true, nav: true, motor: true};
  ids.forEach(function (k) {
    state.buf[k] = [];
    for (var i = 0; i < 60; i++) state.buf[k].push(Math.round(Math.sin(i / 7 + ids.indexOf(k)) * 30 + 100));
  });
  state.visible = new Set(['adc0', 'adc1', 'roll', 'speed']);
  buildChips();
  // 把 adc0 / roll 拖进第一张图表（走真实拖拽路径）
  var chart = vcharts[0];
  chart.sigs = ['adc0', 'roll'];
  updateVChartSigs(chart);
  chart.layer.sKey = ''; chart.layer.dirty = true;
  chart.hover = null;
  state.frozen = false;
  state.replaying = false;
  state._replayStateWired = true;
  state._replayPausedWired = true;
  var rail = document.querySelectorAll('.rail-item');
  if (rail.length > 1) rail[1].click();          // 进入「数据可视化」
  var box = document.querySelector('.trend-wrap .chart-box');
  window.__box = box;
  drawVChart(chart);
  window.__dvcCalls = 0;      /* 上面这次是初始化用的，不计入"冻结期间有没有偷画" */
  var d = document.getElementById('demoMode');
  return JSON.stringify({
    vchartSigs: chart.sigs,
    canvasClass: chart.canvas.className,
    chartRect: (function(){ var r = chart.canvas.getBoundingClientRect();
                            return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; })(),
    errors: window.__errs
  });
})()
"""

FREEZE_JS = """
(function () {
  state.frozen = true;
  trendDirty = true;
  window.__dvcCalls = 0;
  window.__ft = [];
  return JSON.stringify({frozen: state.frozen});
})()
"""

HOVER_JS = """
(function () {
  var chart = vcharts[0];
  var wrap = chart.card.querySelector('.vc-canvas-wrap');
  var r = wrap.getBoundingClientRect();
  var cx = r.left + r.width * 0.6, cy = r.top + r.height * 0.5;
  var dvcBefore = window.__dvcCalls;
  window.__ft = [];
  wrap.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
  var fills = window.__ft.filter(function (f) { return f.cv === 'vc-canvas'; }).map(function (f) { return f.t; });
  // 光标处期望值（drawVChart 的 hover 段用 curvePts 找最近点，这里按 frac 估一个索引做对照）
  var len = state.buf['adc0'].length;
  return JSON.stringify({
    frozen: state.frozen,
    dvcCallsDuringFrozenFrames: dvcBefore,      /* 冻结期间主循环有没有偷画 */
    dvcCallsAfterHover: window.__dvcCalls,
    hoverSet: chart.hover !== null,
    chartCanvasFills: fills,
    hasValueWithUnit: fills.some(function (t) { return /[0-9]/.test(t) && /[a-zA-Z\u00b0%]/.test(t); }),
    bufLen: len
  });
})()
"""

PERF_JS = """
(function () {
  var chart = vcharts[0];
  chart.hover = 0.5; chart.layer.dirty = true;
  drawVChart(chart);
  var t0 = performance.now();
  for (var i = 0; i < 200; i++){ chart.hover = 0.2 + (i % 50) * 0.01; chart.layer.dirty = true; drawVChart(chart); }
  var dvcMs = (performance.now() - t0) / 200;

  /* 回填性能：600 槽 × 3 items，字段与后端一致（含 names），量 JSON.parse 与整条处理链 */
  function buildPayload(nSlots){
    var adcNames = [], navNames = ['ROLL','PITCH','YAW','ACCEL'], motNames = ['SPEED','MOTOR_RPM','TORQUE','THROTTLE','MOTOR_TEMP','VOLTAGE','CURRENT'];
    for (var i = 0; i < 10; i++) adcNames.push('CH' + (i + 1) + '_悬架线位移' + i);
    var slots = [];
    for (var s = 0; s < nSlots; s++){
      var ts = 100000 + s * 50;
      slots.push({ts: ts, items: [
        {type: 'adc', names: adcNames, values: [100 + s, 200 + s, 300 + s, 400 + s, 500 + s, 600 + s, 700 + s, 800 + s, 900 + s, 1000 + s]},
        {type: 'nav', names: navNames, values: [s * 0.1, s * 0.2, s * 0.3, 16384 + s]},
        {type: 'motor', names: motNames, values: [s * 0.01, 5000 + s, 12.5, 40 + (s % 60), 55 + (s % 20), 48.2, 12.3]}
      ]});
    }
    return JSON.stringify({sampleMs: 50, slots: slots});
  }
  var payloadStr = buildPayload(600);
  var t1 = performance.now();
  var parsed = JSON.parse(payloadStr);
  var t2 = performance.now();
  var parseMs = t2 - t1;
  var t3 = performance.now();
  onReplayBackfill(payloadStr);
  var t4 = performance.now();
  return JSON.stringify({
    dvcMsPerCall: dvcMs,
    payloadChars: payloadStr.length,
    slots: parsed.slots.length,
    jsonParseMs: parseMs,
    handlerTotalMs: t4 - t3,
    errors: window.__errs
  });
})()
"""

BACKFILL_JS = """
(function () {
  var out = {};
  var chart = vcharts[0];

  /* --- ① 先灌"旧数据"（全为 0），再回填。拼接 bug 的表现就是缓冲里还留着这些 0 --- */
  function seedOld(){
    Object.keys(SIGS).forEach(function (k) {
      state.buf[k] = [];
      for (var i = 0; i < 60; i++) state.buf[k].push(0);
    });
    state.latest.adc0 = 0; state.latest.roll = 0; state.latest.speed = 0;
  }
  seedOld();
  var SLOTS = 120;
  var slots = [];
  for (var s = 0; s < SLOTS; s++){
    var items = [];
    /* 故意让 items 数量不齐：前 20 槽只有 adc（"某类型到该时刻还没出现"），之后才有 nav/motor */
    items.push({type: 'adc', values: [1000 + s, 2000 + s, 3000 + s, 4000 + s, 5000 + s, 6000 + s, 7000 + s, 8000 + s, 9000 + s, 10000 + s]});
    if (s >= 20){
      items.push({type: 'nav', roll: s * 1.5, pitch: s * 2.5, yaw: s * 3.5, accel: 16384 + s});
      items.push({type: 'motor', speed: s * 0.1, rpm: 4000 + s, throttle: 30 + s, torque: 11.1, temp: 50, volt: 48, amp: 12});
    }
    slots.push({ts: 500000 + s * 50, items: items});
  }
  onReplayBackfill(JSON.stringify({sampleMs: 50, slots: slots}));

  function last(a){ return a[a.length - 1]; }
  var bAdc0 = state.buf['adc0'], bRoll = state.buf['roll'], bSpeed = state.buf['speed'], bAdc9 = state.buf['adc9'];
  out.slotCount = SLOTS;
  out.lens = {adc0: bAdc0.length, adc9: bAdc9.length, roll: bRoll.length, speed: bSpeed.length};
  out.lastValues = {adc0: last(bAdc0), roll: last(bRoll), speed: last(bSpeed)};
  out.expectedLast = {adc0: 1000 + (SLOTS - 1), roll: (SLOTS - 1) * 1.5, speed: (SLOTS - 1) * 0.1};
  /* 拼接检测：旧数据全是 0；若缓冲里还残留 0 -> 说明没清干净 */
  out.oldValueLeaked = {adc0: bAdc0.indexOf(0) >= 0, roll: bRoll.indexOf(0) >= 0};
  /* 每个槽只推一个点：点数必须恰好等于槽数（若按 payload 推点，adc 会是 3 倍） */
  out.onePointPerSlot = (bAdc0.length === SLOTS && bRoll.length === SLOTS && bSpeed.length === SLOTS);
  out.latest = {adc0: state.latest.adc0, roll: state.latest.roll, speed: state.latest.speed};
  out.trendDirty = trendDirty;

  /* --- ② 可视化区看到的是不是跳转点的数据（hover 标注读数） --- */
  window.__ft = [];
  chart.hover = 0.999; chart.layer.dirty = true;
  drawVChart(chart);
  var fills = window.__ft.filter(function (f) { return f.cv === 'vc-canvas'; }).map(function (f) { return f.t; });
  out.vchartHoverFills = fills;

  /* --- ③ 趋势区与可视化区共用 state.buf：趋势层重画一次，确认读的是同一份数据 --- */
  $('trendCanvas');
  paintLayer(trendLayer, drawTrend);
  out.trendRedrawOk = true;

  /* --- ④ 格式不符必须"什么都不做"（F1：删掉了兼容旧扁平数组那条死代码路径）---
     旧扁平数组 / 缺 slots / slots 类型不对 -> 一律不刷新，绝不静默走另一套语义 */
  seedOld();
  var beforeLen = state.buf['adc0'].length;
  onReplayBackfill(JSON.stringify([{type: 'adc', values: [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]},
                                   {type: 'adc', values: [21, 22, 23, 24, 25, 26, 27, 28, 29, 30]}]));
  onReplayBackfill(JSON.stringify({sampleMs: 50}));
  onReplayBackfill(JSON.stringify({slots: 'nope'}));
  out.rejectedFormats = {
    lenUnchanged: state.buf['adc0'].length === beforeLen,
    untouched: state.buf['adc0'][0] === 0 && state.buf['adc0'][beforeLen - 1] === 0,
    latestUnchanged: state.latest.adc0 === 0
  };

  /* --- ⑤ 回放开始要清空缓冲（否则新文件接在旧文件后面） --- */
  onReplayState(true, 'newfile.bin');
  out.afterReplayStart = {adc0Len: state.buf['adc0'].length, rollLen: state.buf['roll'].length};
  onReplayState(false, '');

  /* --- ⑥ 空/异常输入不能抛 --- */
  var errs = [];
  try { onReplayBackfill('not json'); } catch (e) { errs.push('bad json: ' + e.message); }
  try { onReplayBackfill('{}'); } catch (e) { errs.push('empty obj: ' + e.message); }
  try { onReplayBackfill(JSON.stringify({slots: []})); } catch (e) { errs.push('empty slots: ' + e.message); }
  try { onReplayBackfill(JSON.stringify({slots: [{ts: 1, items: []}]})); } catch (e) { errs.push('empty items: ' + e.message); }
  out.exceptionFree = errs.length === 0;
  out.exceptions = errs;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


DATABATCH_JS = """
(function () {
  /* 回归：onDataBatch 拆出 applyPayload() 之后，真实数据入口必须行为不变 */
  var out = {};
  state.hasData = {adc: false, nav: false, motor: false};
  Object.keys(SIGS).forEach(function (k) { state.buf[k] = []; state.latest[k] = 0; });
  state.frozen = false;
  lastSampleT = -1e9;                     /* 解除取样限频，保证这次一定推点 */
  onDataBatch(JSON.stringify([
    {type: 'adc', values: [11, 12, 13, 14, 15, 16, 17, 18, 19, 110]},
    {type: 'nav', roll: 1.5, pitch: 2.5, yaw: 3.5, accel: 16384},
    {type: 'motor', speed: 7.5, rpm: 4444, throttle: 33, torque: 11, temp: 50, volt: 48, amp: 12}
  ]));
  out.applied = {adc0: state.latest.adc0, adc9: state.latest.adc9, roll: state.latest.roll,
                 speed: state.latest.speed, rpm: state.latest.rpm};
  out.hasData = {adc: state.hasData.adc, nav: state.hasData.nav, motor: state.hasData.motor};
  out.bufLen = state.buf['adc0'].length;
  out.tail = state.buf['adc0'][state.buf['adc0'].length - 1];
  /* null 字段不能把已有值冲成 null（原实现的语义） */
  onDataBatch(JSON.stringify([{type: 'adc', values: [null, null, 3, null, null, null, null, null, null, null]}]));
  out.afterNulls = {adc0: state.latest.adc0, adc2: state.latest.adc2};
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


F2F4_JS = """
(function () {
  var out = {};

  /* 调用计数器（必须在 app.js 执行之后包装，否则会被函数声明覆盖） */
  if (!window.__f2wrapped) {
    window.__f2wrapped = true;
    window.__trendPaints = 0; window.__domUpdates = 0; window.__cursorPaints = 0;
    var _dt = drawTrend;
    drawTrend = function () { window.__trendPaints++; return _dt.apply(this, arguments); };
    var _ud = updateDom;
    updateDom = function () { window.__domUpdates++; return _ud.apply(this, arguments); };
    var _dc = drawCursorLayer;
    drawCursorLayer = function () { window.__cursorPaints++; return _dc.apply(this, arguments); };
  }

  /* ---------- F2：暂停 + seek（回填）必须立刻刷新趋势区与数值面板 ---------- */
  state.frozen = true;
  trendDirty = true;
  window.__trendPaints = 0; window.__domUpdates = 0; window.__cursorPaints = 0;
  /* 先擦干净趋势画布，模拟"暂停时什么都看不到" */
  (function () {
    var c = document.getElementById('trendCanvas');
    var g = c.getContext('2d'); g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, c.width, c.height);
  })();

  var SLOTS = 60, slots = [];
  for (var s = 0; s < SLOTS; s++) {
    slots.push({ts: 900000 + s * 50, items: [
      {type: 'adc', values: [500 + s, 600 + s, 700 + s, 800 + s, 900 + s, 1000 + s, 1100 + s, 1200 + s, 1300 + s, 1400 + s]},
      {type: 'nav', roll: s * 0.5, pitch: s * 0.1, yaw: s * 0.2, accel: 16384 + s},
      {type: 'motor', speed: s * 0.1, rpm: 3000 + s, throttle: 20 + s, torque: 9.9, temp: 50, volt: 48, amp: 12}
    ]});
  }
  var _toast = toast;
  window.__toasts = [];
  toast = function (m, k, ms) { window.__toasts.push({m: String(m), k: k || 'info'}); return _toast(m, k, ms); };

  onReplayBackfill(JSON.stringify({sampleMs: 50, slots: slots}));

  out.f2 = {
    frozen: state.frozen,
    trendPaints: window.__trendPaints,
    cursorPaints: window.__cursorPaints,
    domUpdates: window.__domUpdates,
    bufLen: state.buf['adc0'].length,
    lastAdc0: state.buf['adc0'][state.buf['adc0'].length - 1],
    expectedLast: 500 + SLOTS - 1,
    toasts: window.__toasts.slice(),
    foundPauseToast: window.__toasts.some(function (t) { return t.m.indexOf('暂停') >= 0; }),
    /* 趋势画布是否真被画上了像素（清空后又被画回来） */
    trendPixels: (function () {
      var c = document.getElementById('trendCanvas');
      var g = c.getContext('2d');
      var d = g.getImageData(0, 0, c.width, c.height).data, n = 0;
      for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
      return n;
    })()
  };
  toast = _toast;

  /* ---------- F4：seek 到最左端（后端只回填 1 槽）提示语必须分语境 ---------- */
  state.replaying = true;
  onReplayBackfill(JSON.stringify({sampleMs: 50,
    slots: [{ts: 1000, items: [{type: 'adc', values: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}]}]}));
  window.__ft = [];
  paintLayer(trendLayer, drawTrend);
  var fillsReplay = window.__ft.filter(function (f) { return f.cv === 'trendCanvas'; }).map(function (f) { return f.t; });
  out.f4_replay = {
    bufLen: state.buf['adc0'].length,
    fills: fillsReplay,
    hasReplayMsg: fillsReplay.some(function (t) { return t.indexOf('回放位置太靠前') >= 0; }),
    hasSerialMsg: fillsReplay.some(function (t) { return t.indexOf('连接串口') >= 0; })
  };

  /* 非回放 + 无数据 -> 仍应是串口语境文案（别把回放文案漏到常态里） */
  state.replaying = false;
  Object.keys(SIGS).forEach(function (k) { state.buf[k].length = 0; });
  window.__ft = [];
  paintLayer(trendLayer, drawTrend);
  var fillsIdle = window.__ft.filter(function (f) { return f.cv === 'trendCanvas'; }).map(function (f) { return f.t; });
  out.f4_idle = {
    fills: fillsIdle,
    hasSerialMsg: fillsIdle.some(function (t) { return t.indexOf('连接串口') >= 0; }),
    hasReplayMsg: fillsIdle.some(function (t) { return t.indexOf('回放位置太靠前') >= 0; })
  };

  state.frozen = false;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


F3_JS = """
(function () {
  /* F3：暂停(冻结)状态下，不靠悬停也要能刷新「数据可视化」图表。
     覆盖三个离散交互：切回可视化区 / 拖入信号 / 移除信号。 */
  var out = {};
  var chart = vcharts[0];
  state.frozen = true;

  /* F3-a：把画布擦干净模拟"暂停时进来是空白"，再从总览切回可视化 */
  var ctx = chart.layer.cv.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, chart.layer.cv.width, chart.layer.cv.height);
  window.__ft = [];
  window.__dvcCalls = 0;
  document.querySelectorAll('.rail-item')[0].click();      /* 切到总览（可视化隐藏） */
  document.querySelectorAll('.rail-item')[1].click();      /* 再切回可视化 */
  out.f3_zoneSwitch = {
    dvcCalls: window.__dvcCalls,
    fills: window.__ft.filter(function (f) { return f.cv === 'vc-canvas'; }).map(function (f) { return f.t; })
  };

  /* F3-b：冻结 + 拖入新信号 -> 立刻重画（不靠主循环） */
  window.__ft = [];
  var sigsBefore = chart.sigs.slice();
  try {
    var item = document.querySelector('#visChannels .vc-item[data-sig="speed"]');
    var dt = new DataTransfer();
    item.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));
    chart.card.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true}));
    chart.card.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
  } catch (e) { out.dropErr = String(e && e.message); }
  out.f3_drop = {
    sigsBefore: sigsBefore, sigsAfter: chart.sigs.slice(),
    fills: window.__ft.filter(function (f) { return f.cv === 'vc-canvas'; }).map(function (f) { return f.t; })
  };

  /* F3-c：冻结 + 点掉信号 chip -> 立刻重画 */
  window.__ft = [];
  var chip = chart.card.querySelector('.vc-sig[data-sig="speed"]');
  if (chip) chip.click();
  out.f3_remove = {
    sigsAfter: chart.sigs.slice(),
    fills: window.__ft.filter(function (f) { return f.cv === 'vc-canvas'; }).map(function (f) { return f.t; })
  };

  state.frozen = false;
  out.frozenAtEnd = state.frozen;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
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
        def cb_backfill(v):
            res["backfill"] = v
            page.runJavaScript(PERF_JS, cb_perf)

        def cb_perf(v):
            res["perf"] = v
            page.runJavaScript(F2F4_JS, cb_f2f4)

        def cb_f2f4(v):
            res["f2f4"] = v
            app.quit()

        def cb_hover(v):
            res["hover"] = v
            page.runJavaScript(F3_JS, cb_f3)

        def cb_f3(v):
            res["f3"] = v
            page.runJavaScript(DATABATCH_JS, cb_databatch)

        def cb_databatch(v):
            res["databatch"] = v
            page.runJavaScript(BACKFILL_JS, cb_backfill)

        def cb_frozen(v):
            QTimer.singleShot(400, lambda: page.runJavaScript(HOVER_JS, cb_hover))

        def cb_setup(v):
            res["setup"] = v
            page.runJavaScript(FREEZE_JS, cb_frozen)

        QTimer.singleShot(3200, lambda: page.runJavaScript(SETUP_JS, cb_setup))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    setup = json.loads(res.get("setup", "{}"))
    hover = json.loads(res.get("hover", "{}"))
    f3 = json.loads(res.get("f3", "{}"))
    dbatch = json.loads(res.get("databatch", "{}"))
    backfill = json.loads(res.get("backfill", "{}"))
    perf = json.loads(res.get("perf", "{}"))
    f2f4 = json.loads(res.get("f2f4", "{}"))

    print("=== 问题五：暂停时「数据可视化」悬停数值 ===")
    print("  图表信号=%s  canvas=%s  rect=%s" % (setup.get("vchartSigs"), setup.get("canvasClass"), setup.get("chartRect")))
    print("  冻结期间主循环调用 drawVChart 次数 = %s（应为 0，证明 frozen 门禁挡住了）"
          % hover.get("dvcCallsDuringFrozenFrames"))
    print("  悬停一次后 drawVChart 调用总数 = %s（交互自己补画）" % hover.get("dvcCallsAfterHover"))
    print("  frozen=%s hover=%s" % (hover.get("frozen"), hover.get("hoverSet")))
    print("  图表画布上画出的文字: %s" % hover.get("chartCanvasFills"))

    print()
    print("=== 问题二：seek 回填（%s 个槽）===" % backfill.get("slotCount"))
    print("  各缓冲长度: %s   （恰等于槽数 = 每槽只推一个点；按 payload 推会变成 3 倍）"
          % backfill.get("lens"))
    print("  末值: %s" % backfill.get("lastValues"))
    print("  期望末值: %s" % backfill.get("expectedLast"))
    print("  旧数据(0)是否残留: %s   （true = 曲线拼接没修掉）" % backfill.get("oldValueLeaked"))
    print("  state.latest: %s" % backfill.get("latest"))
    print("  trendDirty=%s  可视化悬停读数=%s" % (backfill.get("trendDirty"), backfill.get("vchartHoverFills")))
    print("  格式不符(旧扁平数组/缺slots/slots类型错)一律不刷新: %s" % backfill.get("rejectedFormats"))
    print("  回放开始后缓冲: %s（应为 0）" % backfill.get("afterReplayStart"))
    print("  异常输入不抛异常: %s %s" % (backfill.get("exceptionFree"), backfill.get("exceptions")))
    print()
    print("各阶段 JS 错误: setup=%s hover=%s backfill=%s perf=%s"
          % (setup.get("errors"), hover.get("errors"), backfill.get("errors"), perf.get("errors")))

    print()
    print("=== F3：暂停状态下不靠悬停也能刷新可视化图表 ===")
    print("  切回可视化区: drawVChart 调用=%s 画布文字=%s"
          % ((f3.get("f3_zoneSwitch") or {}).get("dvcCalls"), (f3.get("f3_zoneSwitch") or {}).get("fills")))
    print("  冻结中拖入 speed: sigs %s -> %s  画布文字=%s"
          % ((f3.get("f3_drop") or {}).get("sigsBefore"), (f3.get("f3_drop") or {}).get("sigsAfter"),
             (f3.get("f3_drop") or {}).get("fills")))
    print("  冻结中点掉 speed: sigs=%s  画布文字=%s"
          % ((f3.get("f3_remove") or {}).get("sigsAfter"), (f3.get("f3_remove") or {}).get("fills")))

    print()
    print("=== 回归：真实数据入口 onDataBatch（applyPayload 抽出后）===")
    print("  latest: %s" % dbatch.get("applied"))
    print("  hasData=%s  缓冲长度=%s  末点=%s" % (dbatch.get("hasData"), dbatch.get("bufLen"), dbatch.get("tail")))
    print("  null 字段不覆盖已有值: %s" % dbatch.get("afterNulls"))

    print()
    print("=== F2：暂停 + seek（回填）立刻刷新趋势区/光标层/数值面板 ===")
    print("  %s" % (f2f4.get("f2") or {}))
    print("=== F4：seek 到最左端（只回填 1 槽）提示语分语境 ===")
    print("  回放中: %s" % (f2f4.get("f4_replay") or {}))
    print("  非回放: %s" % (f2f4.get("f4_idle") or {}))
    print("  JS 错误:", f2f4.get("errors"))

    print()
    print("=== 性能 ===")
    print("  单张图表 drawVChart 平均 %.3f ms（200 次）" % (perf.get("dvcMsPerCall") or 0))
    print("  回填 payload: %s 个字符 / %s 槽" % (perf.get("payloadChars"), perf.get("slots")))
    print("  JSON.parse 耗时 %.1f ms" % (perf.get("jsonParseMs") or 0))
    print("  onReplayBackfill 全程（含 parse + 逐槽填充）%.1f ms" % (perf.get("handlerTotalMs") or 0))

    def close(a, b, tol=1e-6):
        return a is not None and b is not None and abs(a - b) <= tol

    checks = [
        ("问题五：冻结期间主循环不重画（门禁未被我破坏）", hover.get("dvcCallsDuringFrozenFrames") == 0),
        ("问题五：冻结时悬停触发了一次重绘", (hover.get("dvcCallsAfterHover") or 0) >= 1),
        ("问题五：画布上出现了带单位的数值标注",
         hover.get("hasValueWithUnit") is True and len(hover.get("chartCanvasFills") or []) >= 1),
        ("回归：onDataBatch 三类型 latest 正确",
         dbatch.get("applied") == {"adc0": 11, "adc9": 110, "roll": 1.5, "speed": 7.5, "rpm": 4444}),
        ("回归：hasData 三类型全置位", dbatch.get("hasData") == {"adc": True, "nav": True, "motor": True}),
        ("回归：pushSample 正常推点（末点=adc0 最新值）",
         (dbatch.get("bufLen") or 0) >= 1 and dbatch.get("tail") == 11),
        ("回归：null 字段不覆盖已有值", dbatch.get("afterNulls") == {"adc0": 11, "adc2": 3}),
        # ---- F3：暂停时不靠悬停也能刷新 ----
        ("F3：冻结中切回可视化区 -> 图表立刻重画（画布出现图例/数值）",
         ((f3.get("f3_zoneSwitch") or {}).get("dvcCalls") or 0) >= 1
         and len((f3.get("f3_zoneSwitch") or {}).get("fills") or []) >= 1),
        ("F3：冻结中拖入信号 -> 立刻重画",
         "speed" in ((f3.get("f3_drop") or {}).get("sigsAfter") or [])
         and len((f3.get("f3_drop") or {}).get("fills") or []) >= 1),
        ("F3：冻结中点掉信号 -> 立刻重画",
         "speed" not in ((f3.get("f3_remove") or {}).get("sigsAfter") or [])
         and len((f3.get("f3_remove") or {}).get("fills") or []) >= 1),
        ("问题二：每槽只推一个点（缓冲长度 == 槽数）", backfill.get("onePointPerSlot") is True),
        ("问题二：末值与最后一槽一致",
         close(backfill.get("lastValues", {}).get("adc0"), backfill.get("expectedLast", {}).get("adc0"))
         and close(backfill.get("lastValues", {}).get("roll"), backfill.get("expectedLast", {}).get("roll"))
         and close(backfill.get("lastValues", {}).get("speed"), backfill.get("expectedLast", {}).get("speed"))),
        ("问题二：旧数据已被清空（无拼接残留）",
         backfill.get("oldValueLeaked") == {"adc0": False, "roll": False}),
        ("问题二：state.latest 停在最后一槽", close(backfill.get("latest", {}).get("adc0"), 1000 + 119)),
        ("问题二：回填后置脏 + 可视化区读数来自新数据",
         backfill.get("trendDirty") is True and bool(backfill.get("vchartHoverFills"))),
        ("问题二：趋势层重画无异常", backfill.get("trendRedrawOk") is True),
        ("问题二 F1：格式不符（旧扁平数组/缺 slots/slots 类型错）一律不刷新、不报错",
         backfill.get("rejectedFormats") == {"lenUnchanged": True, "untouched": True, "latestUnchanged": True}),
        ("问题二：回放开始清空缓冲", backfill.get("afterReplayStart") == {"adc0Len": 0, "rollLen": 0}),
        # ---- F2：暂停中 seek 必须立刻刷新（不改主循环门禁，走离散补画）----
        ("F2 暂停中回填后：趋势层被直接补画一次",
         ((f2f4.get("f2") or {}).get("trendPaints") or 0) >= 1),
        ("F2 暂停中回填后：光标层也刷一次", ((f2f4.get("f2") or {}).get("cursorPaints") or 0) >= 1),
        ("F2 暂停中回填后：updateDom 被调用（数值面板/卡片跟着换）",
         ((f2f4.get("f2") or {}).get("domUpdates") or 0) >= 1),
        ("F2 趋势画布确实被画上像素（先清空过）",
         ((f2f4.get("f2") or {}).get("trendPixels") or 0) > 1000),
        ("F2 缓冲内容 = 跳转点数据（长度=槽数、末值正确）",
         (f2f4.get("f2") or {}).get("bufLen") == 60
         and (f2f4.get("f2") or {}).get("lastAdc0") == (f2f4.get("f2") or {}).get("expectedLast")),
        ("F2 暂停中 seek 有提示（含\"暂停\"）",
         ((f2f4.get("f2") or {}).get("foundPauseToast")) is True),
        # ---- F4：最左端提示语分语境 ----
        ("F4 回放中 seek 到最左端（1 槽）：显示回放语境文案、不出现\"连接串口\"",
         (f2f4.get("f4_replay") or {}).get("bufLen") == 1
         and (f2f4.get("f4_replay") or {}).get("hasReplayMsg") is True
         and (f2f4.get("f4_replay") or {}).get("hasSerialMsg") is False),
        ("F4 非回放无数据时仍是串口语境文案（逐字回归）",
         (f2f4.get("f4_idle") or {}).get("hasSerialMsg") is True
         and (f2f4.get("f4_idle") or {}).get("hasReplayMsg") is False),
        ("问题二：空/坏输入不抛异常", backfill.get("exceptionFree") is True),
        ("无 JS 运行时错误",
         not setup.get("errors") and not hover.get("errors") and not f3.get("errors")
         and not backfill.get("errors") and not perf.get("errors") and not f2f4.get("errors")),
    ]
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
