# -*- coding: utf-8 -*-
"""task-3 实测探针：趋势区三件事的「改前/改后」对照。

测什么（全部走真实事件路径，不直接调内部函数）：
  A) 悬停趋势图 -> 光标层(cursorCanvas) 到底画了哪些文字
     （钩 CanvasRenderingContext2D.prototype.fillText，按 this.canvas.id 区分画布）
  B) 暂停(state.frozen) 后悬停 -> 右侧「数值」面板是否跟随游标
  C) 点最后一条可见通道 -> 是否"点了没反应"
  D) hover 面板行 + 点击 -> state.hl 是否悬空 -> 是否导致所有曲线一起淡出

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\python.exe tools\_trend_interact_probe.py
输出：每一幕的原始 JSON。
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
  window.addEventListener('unhandledrejection', function (e) {
    window.__errs.push('promise: ' + e.reason);
  });
  /* fillText 探针：记录「画在哪个 canvas 上」，用来区分趋势层 / 光标层 */
  window.__ft = [];
  if (!window.__ftHooked) {
    window.__ftHooked = true;
    var _ft = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (t) {
      window.__ft.push({t: String(t), cv: (this.canvas && this.canvas.id) || '?'});
      return _ft.apply(this, arguments);
    };
  }
})();
"""

SETUP_JS = """
(function () {
  var ids = Object.keys(SIGS);
  state.hasData = {adc: true, nav: true, motor: true};
  ids.forEach(function (k) {
    state.buf[k] = [];
    for (var i = 0; i < 80; i++) state.buf[k].push(Math.round(Math.sin(i / 9 + ids.indexOf(k)) * 20 + ids.indexOf(k)));
  });
  state.visible = new Set(ids.slice(0, 3));
  state.hl = null;
  state.cur = null;
  state.frozen = false;
  if (typeof BUF === 'number' && BUF > 0) { /* 不改，保持真实窗口参数 */ }
  buildChips();
  trendDirty = true;
  var box = document.querySelector('.trend-wrap .chart-box');
  var r = box.getBoundingClientRect();
  window.__box = {left: r.left, top: r.top, w: box.clientWidth, h: box.clientHeight};
  return JSON.stringify({ids: ids.length, visible: Array.from(state.visible),
                         bufLen: trendBufLen(), box: window.__box, errors: window.__errs});
})()
"""

A_JS = """
(function () {
  var out = {};
  var box = document.querySelector('.trend-wrap .chart-box');
  var r = box.getBoundingClientRect();
  var cx = r.left + box.clientWidth * 0.62, cy = r.top + box.clientHeight * 0.5;
  window.__ft = [];
  box.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
  out.curActive = !!(state.cur && state.cur.active);
  out.curFrac = state.cur ? state.cur.frac : null;
  out.cursorCanvasFills = window.__ft.filter(function (f) { return f.cv === 'cursorCanvas'; }).map(function (f) { return f.t; });
  out.trendCanvasFills = window.__ft.filter(function (f) { return f.cv === 'trendCanvas'; }).map(function (f) { return f.t; });
  out.cursorCanvasHasNumber = window.__ft.some(function (f) {
    return f.cv === 'cursorCanvas' && /[0-9]/.test(f.t) && !/^[-−]?[0-9.]+s$/.test(f.t) && f.t !== 'now';
  });
  var len = trendBufLen();
  var idx = state.cur ? Math.round(state.cur.frac * (len - 1)) : -1;
  out.cursorIdx = idx;
  out.expect = {};
  Array.from(state.visible).forEach(function (id) {
    var s = SIGS[id];
    out.expect[id] = {raw: state.buf[id][idx], fmt: fmtSigVal(state.buf[id][idx], s), unit: s.unit || ''};
  });
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

B_JS = """
(function () {
  var out = {};
  var box = document.querySelector('.trend-wrap .chart-box');
  var r = box.getBoundingClientRect();
  var cx1 = r.left + box.clientWidth * 0.62, cy = r.top + box.clientHeight * 0.5;
  var cx2 = r.left + box.clientWidth * 0.30;
  function snap(tag) {
    var o = {};
    o.tag = tag;
    o.frozen = state.frozen;
    o.cap = document.getElementById('sigCapT') ? document.getElementById('sigCapT').textContent : '';
    o.rows = Array.prototype.map.call(document.querySelectorAll('#sigPanel .sig-row'), function (row) {
      var v = row.querySelector('[data-v]');
      return {id: row.dataset.id, v: v ? v.textContent : '', idx: row.dataset.idx};
    });
    var len = trendBufLen();
    var idx = state.cur ? Math.round(state.cur.frac * (len - 1)) : -1;
    o.curIdx = idx;
    o.expectAtCursor = {};
    Array.from(state.visible).forEach(function (id) {
      if (idx >= 0) o.expectAtCursor[id] = fmtSigVal(state.buf[id][idx], SIGS[id]);
    });
    o.expectLatest = {};
    Array.from(state.visible).forEach(function (id) {
      o.expectLatest[id] = fmtSigVal(state.buf[id][state.buf[id].length - 1], SIGS[id]);
    });
    return o;
  }
  /* 1) 未暂停：悬停到 62% 处 */
  box.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, cancelable: true, clientX: cx1, clientY: cy}));
  out.live = snap('live');
  /* 2) 暂停：先冻结，再悬停到 30% 处（换位置才能触发重算） */
  state.frozen = true;
  window.__ft = [];
  box.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, cancelable: true, clientX: cx2, clientY: cy}));
  out.frozenHover = snap('frozenHover');
  out.frozenHoverCursorFills = window.__ft.filter(function (f) { return f.cv === 'cursorCanvas'; }).map(function (f) { return f.t; });
  /* 3) 暂停下不再动鼠标：面板应保持游标值，不应被"实时值"覆盖 */
  out.frozenIdle = snap('frozenIdle');
  state.frozen = false;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

C_JS = """
(function () {
  var out = {};
  state.frozen = false;
  state.hl = null;
  state.cur = null;
  /* --- C1: 只剩一条可见通道时点它（改前：被兜底加回来 -> 点了没反应） --- */
  state.visible = new Set(['adc0']);
  buildChips();
  out.c1_beforeSize = state.visible.size;
  out.c1_beforeChipOn = document.querySelectorAll('#chipBar .chip.on').length;
  document.querySelector('#chipBar .chip[data-id="adc0"]').click();
  out.c1_afterChipClickSize = state.visible.size;
  out.c1_afterChipClickOn = document.querySelectorAll('#chipBar .chip.on').length;
  out.c1_chipVisualChanged = (out.c1_beforeChipOn !== out.c1_afterChipClickOn);
  out.c1_panelRows = document.querySelectorAll('#sigPanel .sig-row').length;
  var row = document.querySelector('#sigPanel .sig-row[data-id="adc0"]');
  out.c1_rowExistsAfterHide = !!row;
  /* --- C2: 两条可见时点其中一条（对照组，应正常） --- */
  state.visible = new Set(['adc0', 'adc1']);
  buildChips();
  document.querySelector('#chipBar .chip[data-id="adc0"]').click();
  out.c2_afterSize = state.visible.size;
  out.c2_afterOn = document.querySelectorAll('#chipBar .chip.on').length;
  out.c2_visible = Array.from(state.visible);
  /* --- C3: 把最后一条也点掉 -> 可见集合为空 -> 图上应出现"未选择通道"提示而不是白板 --- */
  state.visible = new Set(['adc1']);
  buildChips();
  document.querySelector('#chipBar .chip[data-id="adc1"]').click();
  out.c3_size = state.visible.size;
  out.c3_chipOn = document.querySelectorAll('#chipBar .chip.on').length;
  out.c3_panelRows = document.querySelectorAll('#sigPanel .sig-row').length;
  /* --- C4: 关掉的那条能再点开（不是单向死路） --- */
  document.querySelector('#chipBar .chip[data-id="adc1"]').click();
  out.c4_size = state.visible.size;
  out.c4_chipOn = document.querySelectorAll('#chipBar .chip.on').length;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

C2_JS = """
(function () {
  /* 清空可见集合，让主循环自然重画一次趋势画布，记录它到底画了什么文字 */
  state.visible = new Set();
  state.hl = null;
  buildChips();
  window.__ft = [];
  trendDirty = true;
  return JSON.stringify({visible: Array.from(state.visible), errors: window.__errs});
})()
"""

E_JS = """
(function () {
  var fills = window.__ft.filter(function (f) { return f.cv === 'trendCanvas'; }).map(function (f) { return f.t; });
  return JSON.stringify({
    trendCanvasFills: fills,
    hasEmptyHint: fills.some(function (t) { return t.indexOf('未选择通道') >= 0; }),
    canvasBlank: fills.length === 0,
    errors: window.__errs
  });
})()
"""

D_JS = """
(function () {
  var out = {};
  var ids = Object.keys(SIGS);
  /* 恢复 3 条，并且 hover 到中间那条的行上，然后点它 */
  state.visible = new Set(ids.slice(0, 3));
  state.hl = null;
  state.cur = null;
  buildChips();
  var row = document.querySelector('#sigPanel .sig-row[data-id="' + ids[1] + '"]');
  // 捕捉点击前的行矩形（行会被 innerHTML 重建，点后再取就没意义了）
  var rectBefore = row.getBoundingClientRect();
  row.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
  out.d_hlAfterHover = state.hl;
  row.click();
  out.d_clickedId = ids[1];
  out.d_visibleAfter = Array.from(state.visible);
  out.d_hlAfterClick = state.hl;
  out.d_hlDangling = (state.hl !== null && !state.visible.has(state.hl));
  /* 复刻 drawTrend 第 994 行附近的逻辑：hl 命中哪条，其余全部 alpha .13 */
  function alphas() {
    var hl = state.hl;
    if (hl && !state.visible.has(hl)) hl = null;   /* 修复后 drawTrend 里的兜底 */
    return Array.from(state.visible).map(function (id) { return {id: id, alpha: hl ? (id === hl ? 1 : 0.13) : 1}; });
  }
  out.d_alphasAfterClick = alphas();
  out.d_allFadedAfterClick = out.d_alphasAfterClick.every(function (x) { return x.alpha < 1; });
  document.getElementById('sigPanel').dispatchEvent(new MouseEvent('mouseleave', {bubbles: false}));
  out.d_alphasAfterLeave = alphas();
  out.d_allFadedAfterLeave = out.d_alphasAfterLeave.every(function (x) { return x.alpha < 1; });
  out.d_hlAfterLeave = state.hl;
  out.d_panelRows = document.querySelectorAll('#sigPanel .sig-row').length;
  out.d_chipOn = document.querySelectorAll('#chipBar .chip.on').length;
  /* 点击前那一行的像素位置，现在落在哪个元素上（判断真实浏览器会不会补发 mouseover） */
  var el = document.elementFromPoint(rectBefore.left + rectBefore.width / 2, rectBefore.top + rectBefore.height / 2);
  out.d_elementFromPointAtRowBefore = el ? (el.id || el.className || el.tagName) : null;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


F_JS = """
(function () {
  /* 浮层成本：1 路 vs 21 路可见，各 200 次 drawCursorLayer() 的平均耗时。
     光标层是独立 canvas，只在 hover 时才会走浮层那段。 */
  var all = Object.keys(SIGS);
  function bench(n) {
    state.visible = new Set(n === 21 ? all : all.slice(0, 1));
    buildChips();
    state.cur = {frac: 0.4, active: true};
    drawCursorLayer();                      /* 先吃掉 syncLayer 的脏标记 */
    var t0 = performance.now();
    for (var i = 0; i < 200; i++){ state.cur.frac = 0.3 + (i % 50) * 0.01; drawCursorLayer(); }
    return (performance.now() - t0) / 200;
  }
  return JSON.stringify({msPerCall_21ch: bench(21), msPerCall_1ch: bench(1), errors: window.__errs});
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

    out = {}
    steps = [("setup", SETUP_JS, 0), ("A", A_JS, 300), ("B", B_JS, 300),
             ("C", C_JS, 300), ("C2", C2_JS, 500), ("E", E_JS, 300), ("D", D_JS, 300), ("F", F_JS, 200)]

    def run(i):
        if i >= len(steps):
            app.quit()
            return
        name = steps[i][0]
        delay_next = steps[i][2]

        def cb(v):
            out[name] = v
            QTimer.singleShot(delay_next, lambda: run(i + 1))
        page.runJavaScript(steps[i][1], cb)

    def on_load(ok):
        QTimer.singleShot(3200, lambda: run(0))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()   # 不 show 画布尺寸恒为 0
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(40000, app.quit)
    app.exec()

    for k in ("setup", "A", "B", "C", "C2", "E", "D", "F"):
        v = out.get(k)
        try:
            parsed = json.loads(v)
        except Exception:
            parsed = v
        print("--- %s ---" % k)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
