# -*- coding: utf-8 -*-
"""task-20：① 布局几何（zoneP1/zoneP2 空白）② resize 闪屏**先测量**。

用法：
    <venv>\\python.exe tools\\_layout_flash_probe.py --save   # 改前存基线
    <venv>\\python.exe tools\\_layout_flash_probe.py          # 改后对比

闪屏测量方法（不猜，只看可观测事实）：
  · 给每个 canvas 的 width/height 属性装 setter 钩子（记录"什么时候被改尺寸"——
    改 canvas 尺寸会**清空**画布，这是最可疑的裸帧来源）
  · 拖动期间用 rAF 连续采样每个 canvas 是否有非透明像素（空白帧 = 闪）
  · 记录 paintLayer/drawVChart/paintAccVec 的调用次数与时刻（"有没有及时补画"）
  · 每个区各拖一次（zoneP2 / zoneP1 / zoneSerial / zoneTrend），比较差异：
    - 同栅格行的联动（拖一个区另一个区高度变不变）
    - 串口区 hexbox 解锁（#hexBox 高度突变）
"""
import os
import sys
import json

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
BASELINE = os.path.join(HERE, "tools", "_layout_flash_baseline.json")
OUT_JSON = os.path.join(HERE, "tools", "_layout_flash_result.json")
SAVE = "--save" in sys.argv

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
  window.__P = {geo: {}, drags: {}, t0: performance.now()};
  /* --- 钩子：canvas 尺寸写入（= 清空画布）+ 绘制调用 --- */
  window.__sizes = [];        /* [{t, id, w, h}] */
  window.__paints = [];       /* [{t, what}] */
  function hookCanvas(id) {
    var cv = document.getElementById(id);
    if (!cv || cv.__hooked) return;
    cv.__hooked = true;
    ['width', 'height'].forEach(function (prop) {
      var d = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, prop);
      try {
        Object.defineProperty(cv, prop, {
          get: function () { return d.get.call(this); },
          set: function (v) {
            window.__sizes.push({t: performance.now(), id: id, prop: prop, v: v});
            return d.set.call(this, v);
          }
        });
      } catch (e) { window.__errs.push('hook ' + id + '.' + prop + ': ' + e.message); }
    });
  }
  window.__hookCanvas = hookCanvas;
  ['trendCanvas', 'cursorCanvas', 'accVecCanvas'].forEach(hookCanvas);
  /* --- 空白采样：stride 抽样，任一非透明像素即认为"有内容" --- */
  window.__blank = function (id) {
    var cv = document.getElementById(id);
    if (!cv || !cv.width || !cv.height) return null;
    var ctx = cv.getContext('2d');
    var w = cv.width, h = cv.height;
    var d;
    try { d = ctx.getImageData(0, 0, w, h).data; } catch (e) { return null; }
    for (var i = 3; i < d.length; i += 4 * 7) if (d[i] > 0) return false;
    return true;
  };
  /* ★ 关键指标：位图"陈旧"= 画布 backing store 尺寸 ≠ CSS 尺寸×dpr。
     此时浏览器会把旧位图**缩放**贴到新的 CSS 盒子上（拖高时每帧都在变），
     而重绘只有 30Hz —— 这就是"调大小时画面发虚/抖动（闪）"的可见来源。
     注意：改 canvas 尺寸会清空它，所以只有"重绘时"才会写尺寸；
     写尺寸 → 立刻重画（paintLayer 内同步完成），因此真正的空白帧应当为 0。 */
  window.__stat = function (id) {
    var cv = document.getElementById(id);
    if (!cv || !cv.clientWidth) return null;
    var dpr = Math.min(window.devicePixelRatio || 1, (window.UI_CFG && UI_CFG.dprCap) || 2);
    var ew = Math.round(cv.clientWidth * dpr), eh = Math.round(cv.clientHeight * dpr);
    return {stale: (cv.width !== ew || cv.height !== eh),
            bw: cv.width, bh: cv.height, cw: cv.clientWidth, ch: cv.clientHeight};
  };
})();
"""

START_JS = """
(function () {
  /* 钩 drawTrend / drawVChart / paintAccVec 计数（必须在 app.js 之后包装） */
  if (!window.__paintWrapped) {
    window.__paintWrapped = true;
    var _dt = drawTrend, _dv = drawVChart, _pa = paintAccVec, _vl = paintVCharts;
    drawTrend = function () { window.__paints.push({t: performance.now(), what: 'drawTrend'}); return _dt.apply(this, arguments); };
    drawVChart = function () { window.__paints.push({t: performance.now(), what: 'drawVChart'}); return _dv.apply(this, arguments); };
    paintAccVec = function () { window.__paints.push({t: performance.now(), what: 'paintAccVec'}); return _pa.apply(this, arguments); };
    paintVCharts = function () { window.__paints.push({t: performance.now(), what: 'paintVCharts'}); return _vl.apply(this, arguments); };
  }
  ['trendCanvas', 'cursorCanvas', 'accVecCanvas'].forEach(window.__hookCanvas);
  var vcs = document.querySelectorAll('.vis-grid canvas');
  for (var vi = 0; vi < vcs.length; vi++) if (vcs[vi].id) window.__hookCanvas(vcs[vi].id);
  window.__vecCanvases = Array.prototype.map.call(
    document.querySelectorAll('#visGrid canvas'), function (c) { return c.id || '(no-id)'; });

  function rect(el) {
    var b = el.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)];
  }
  /* 一个分区里"内容底部到分区底部"的空白 */
  function innerBlank(zoneId) {
    var z = document.getElementById(zoneId);
    if (!z) return null;
    var zb = z.getBoundingClientRect(), bottom = -1e9;
    Array.prototype.forEach.call(z.children, function (ch) {
      if (ch.classList.contains('zone-resize')) return;
      var b = ch.getBoundingClientRect();
      if (b.height > 0 && b.bottom > bottom) bottom = b.bottom;
    });
    return {zone: rect(z), contentBottom: Math.round(bottom - zb.top),
            blankPx: Math.round(zb.bottom - bottom)};
  }
  window.__geo = function () {
    var g = {zones: {}, blanks: {}};
    ['zoneP2', 'zoneP1', 'zoneP0', 'zoneTrend', 'zoneSerial', 'zoneRecord', 'zoneSettings'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) { g.zones[id] = null; return; }
      g.zones[id] = rect(el);
      g.blanks[id] = innerBlank(id);
    });
    var hex = document.getElementById('hexBox');
    g.hexBox = hex ? rect(hex) : null;
    g.stage = (function () { var s = document.getElementById('stage'); return s ? rect(s) : null; })();
    /* ★ 有没有被挤坏/裁掉：scrollHeight > clientHeight 说明内容被裁 */
    g.overflow = [];
    ['#zoneP2 .hcard', '#zoneP1 .dcard', '#zoneP1 .accvec', '#zoneP2 .hero-grid',
     '#zoneSerial .hexbox', '#zoneRecord .loglist'].forEach(function (sel) {
      Array.prototype.forEach.call(document.querySelectorAll(sel), function (el) {
        g.overflow.push({sel: sel, rect: rect(el),
                         oy: el.scrollHeight - el.clientHeight, ox: el.scrollWidth - el.clientWidth});
      });
    });
    /* 同一栅格行：P1/P2 的 top/bottom 关系 */
    g.row = {p1: g.zones.zoneP1, p2: g.zones.zoneP2};
    return g;
  };

  /* ---- 合成拖动：mousedown 在手柄上，然后逐帧 mousemove ---- */
  /* ★ 拖动期间必须**有数据在流**：否则主循环不重绘，测不出真实表现。
     每 50ms 推一批（与后端 20Hz 推送同形）。 */
  window.__feed = setInterval(function () {
    lastSampleT = -1e9;
    onDataBatch(JSON.stringify([
      {type: 'adc', seq: 1, ts: performance.now(), values: [2000, 1500, 1400, 1300, 1200, 1100, 1000, 900, 800, 700]},
      {type: 'nav', seq: 1, ts: performance.now(), names: ['ROLL', 'PITCH', 'YAW', 'ACCEL_LAT', 'ACCEL_LON'],
       values: [2, 1, 30, 1.2, -0.8], roll: 2, pitch: 1, yaw: 30, accel_lat: 1.2, accel_lon: -0.8},
      {type: 'motor', seq: 1, ts: performance.now(),
       names: ['SPEED', 'RPM', 'THROTTLE', 'TORQUE', 'TEMP', 'VOLT', 'AMP'],
       values: [12, 3000, 40, 20, 45, 480, 30], speed: 12, rpm: 3000, throttle: 40,
       torque: 20, temp: 45, volt: 480, amp: 30}
    ]));
  }, 50);

  window.__drag = function (zoneId, dyTotal, steps, done) {
    var zone = document.getElementById(zoneId);
    var h = zone && zone.querySelector('.zone-resize');
    if (!h) { done({error: 'no handle in ' + zoneId}); return; }
    var hb = h.getBoundingClientRect();
    var hx = hb.left + Math.min(40, hb.width / 2), hy = hb.top + hb.height / 2;
    var sizes = [], paints = [], blankFrames = {}, staleFrames = {}, initial = {};
    var canvases = ['trendCanvas', 'accVecCanvas'].concat(window.__vecCanvases || []);
    var t0 = performance.now();
    var other = zoneId === 'zoneP1' ? 'zoneP2' : (zoneId === 'zoneP2' ? 'zoneP1' : null);
    var g0 = {self: rect(zone), other: other ? rect(document.getElementById(other)) : null,
              hex: document.getElementById('hexBox') ? rect(document.getElementById('hexBox')) : null};
    var sIdx0 = window.__sizes.length, pIdx0 = window.__paints.length;
    var sampling = true, frames = 0;
    function sample() {
      if (!sampling) return;
      frames++;
      canvases.forEach(function (id) {
        if (!document.getElementById(id)) return;
        var st = window.__stat(id);
        if (!st) return;
        if (st.stale) { (staleFrames[id] = staleFrames[id] || []).push(Math.round(performance.now() - t0)); }
        var b = window.__blank(id);
        if (initial[id] === undefined) initial[id] = b;
        if (b === true && initial[id] === false) {
          (blankFrames[id] = blankFrames[id] || []).push(Math.round(performance.now() - t0));
        }
      });
      requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
    h.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: hx, clientY: hy}));
    var i = 0;
    function stepMove() {
      i++;
      var y = hy + dyTotal * i / steps;
      document.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: hx, clientY: y}));
      if (i < steps) { setTimeout(stepMove, 16); return; }
      setTimeout(function () {
        document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: hx, clientY: y}));
        setTimeout(function () {
          sampling = false;
          sizes = window.__sizes.slice(sIdx0);
          paints = window.__paints.slice(pIdx0);
          var g1 = {self: rect(zone), other: other ? rect(document.getElementById(other)) : null,
                    hex: document.getElementById('hexBox') ? rect(document.getElementById('hexBox')) : null};
          done({
            zone: zoneId, dy: dyTotal, frames: frames,
            selfBefore: g0.self, selfAfter: g1.self,
            otherId: other, otherBefore: g0.other, otherAfter: g1.other,
            hexBefore: g0.hex, hexAfter: g1.hex,
            sizes: sizes.map(function (s) { return {t: Math.round(s.t - t0), id: s.id, prop: s.prop, v: s.v}; }),
            paints: paints.map(function (p) { return {t: Math.round(p.t - t0), what: p.what}; }),
            blankFrames: blankFrames, staleFrames: staleFrames, initialBlank: initial
          });
        }, 120);
      }, 40);
    }
    setTimeout(stepMove, 30);
  };

  var SEQ = [['zoneTrend', 60], ['zoneP2', 40], ['zoneP1', 40], ['zoneSerial', 40]];
  window.__P.geo0 = window.__geo();
  function next(k) {
    if (k >= SEQ.length) {
      /* 最后再测一次「可视化区」里那些 vchart 画布（切区后它们才会被建出来/可见） */
      document.querySelectorAll('.rail-item')[1].click();
      setTimeout(function () {
        window.__vecCanvases = Array.prototype.map.call(
          document.querySelectorAll('#visGrid canvas'), function (c) { return c.id; }).filter(Boolean);
        window.__P.vecCanvases = window.__vecCanvases;
        window.__drag('zoneVisual', 60, 10, function (r) {
          window.__P.drags['zoneVisual'] = r;
          document.querySelectorAll('.rail-item')[0].click();
          window.__P.done = true;
        });
      }, 400);
      return;
    }
    window.__drag(SEQ[k][0], SEQ[k][1], 10, function (r) {
      window.__P.drags[SEQ[k][0]] = r;
      var z = document.getElementById(SEQ[k][0]);
      z.style.height = '';
      setTimeout(function () { next(k + 1); }, 200);
    });
  }
  next(0);
  return 'started';
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    s = QWebEngineScript()
    s.setName("probe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)
    res = {}

    def on_load(ok):
        def read():
            page.runJavaScript("JSON.stringify(window.__P)", cb_read)
        def cb_read(v):
            res["p"] = v
            app.quit()
        QTimer.singleShot(3200, lambda: page.runJavaScript(START_JS, lambda _: QTimer.singleShot(6000, read)))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    p = json.loads(res.get("p") or "{}")
    geo, drags = p.get("geo0") or {}, p.get("drags") or {}
    if SAVE:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump({"geo": geo, "drags": drags}, f, ensure_ascii=False, indent=2)
        print("已存基线 WROTE", BASELINE)

    print("=== ① 布局几何（分区 rect 与区内空白）===")
    print("  %-14s %-26s %-22s %s" % ("分区", "[x,y,w,h]", "内容底部/空白", "备注"))
    for zid, r in (geo.get("zones") or {}).items():
        bl = (geo.get("blanks") or {}).get(zid) or {}
        print("  %-14s %-26s 底=%s 空白=%spx" % (zid, r, bl.get("contentBottom"), bl.get("blankPx")))
    print("  hexBox=%s" % geo.get("hexBox"))
    print("  --- 内容是否被挤坏（oy/ox = scrollH-clientH，>1px 表示有裁切/溢出）---")
    bad = 0
    for o in (geo.get("overflow") or []):
        flag = "" if (o["oy"] <= 1 and o["ox"] <= 1) else "  ★被裁!"
        if flag:
            bad += 1
        print("      %-24s %-24s oy=%-5s ox=%-5s%s" % (o["sel"], o["rect"], o["oy"], o["ox"], flag))
    print("      溢出/裁切元素数 = %d" % bad)

    print()
    print("=== ② 拖动各区的测量（有数据在流；每帧采样『位图陈旧』与『画布空白』）===")
    for zid in ("zoneTrend", "zoneP2", "zoneP1", "zoneSerial", "zoneVisual"):
        d = drags.get(zid) or {}
        if not d:
            print("  %-12s （无数据）" % zid)
            continue
        print("  --- 拖 %s +%spx ---" % (zid, d.get("dy")))
        print("      自身 %s -> %s" % (d.get("selfBefore"), d.get("selfAfter")))
        if d.get("otherId"):
            print("      同栅格行 %s: %s -> %s" % (d.get("otherId"), d.get("otherBefore"), d.get("otherAfter")))
        print("      hexBox: %s -> %s" % (d.get("hexBefore"), d.get("hexAfter")))
        print("      采样帧数=%s  canvas 尺寸写入=%s 次  绘制调用=%s 次"
              % (d.get("frames"), len(d.get("sizes") or []), len(d.get("paints") or [])))
        st = d.get("staleFrames") or {}
        if st:
            for cid, ts in st.items():
                print("      ★ 位图陈旧(被缩放) %-14s %d 帧 / 共 %s 帧  首次 t=%sms"
                      % (cid, len(ts), d.get("frames"), ts[0]))
        else:
            print("      位图陈旧: 无")
        bf = d.get("blankFrames") or {}
        if bf:
            for cid, ts in bf.items():
                print("      ★★ 空白帧 %-14s %d 帧（拖前有内容）  首次 t=%sms" % (cid, len(ts), ts[0]))
        else:
            print("      空白帧: 无（拖前有内容的画布都没出现过空白）")
        pt = d.get("paints") or []
        cnt = {}
        for q in pt:
            cnt[q["what"]] = cnt.get(q["what"], 0) + 1
        print("      绘制明细: %s" % cnt)

    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
        print("WROTE", OUT_JSON)
    except Exception as e:
        print("JSON 写入失败:", e)


if __name__ == "__main__":
    main()
