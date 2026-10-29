# -*- coding: utf-8 -*-
"""task-28 追加：环形表中心读数**溢出环**的量化测量（修前 vs 修后）。

量什么（每个环各一组）：
  · 画布/环的几何：clientWidth/Height、外径 R、弧宽 rw、**内缘半径 rin**、圆心 (cx,cy)
    —— 用的是 app.js 里同一套 `ringGeom()`，保证"量的就是画的"
  · 数字与单位的**实测包围盒**（getBoundingClientRect）+ measureText 出来的文字宽/高
  · 判定：数字宽 vs 环内径(2·rin)、数字高+单位高 vs 环内高(2·rin)、
    以及"内接区域（留 ≥6px 内边距）"是否完整包含二者包围盒
用法：<venv>\\python.exe tools\\_ring_fit_check.py
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
TAG = sys.argv[1] if len(sys.argv) > 1 else "before"

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
})();
"""

MEASURE_JS = """
(function () {
  var PAD = 6;                       /* 要求的内边距 */
  /* 文字包围盒：必须用 Range（紧凑到文字），不能用 getBoundingClientRect ——
     单位现在是 display:block，元素框会横跨整行（455px），量出来的"溢出"是假象。 */
  function tbox(el){
    var rng = document.createRange(); rng.selectNodeContents(el);
    var b = rng.getBoundingClientRect();
    return {l: Math.round(b.left*10)/10, t: Math.round(b.top*10)/10,
            w: Math.round(b.width*10)/10, h: Math.round(b.height*10)/10,
            r: Math.round(b.right*10)/10, b: Math.round(b.bottom*10)/10};
  }
  function box(el){ return tbox(el); }
  function one(cvId, numId, unitId){
    var cv = document.getElementById(cvId), num = document.getElementById(numId), un = document.getElementById(unitId);
    if (!cv || !num) return null;
    var g = ringGeom(cv);                       /* ← app.js 里与 drawRingGauge 同一套几何 */
    var cr = cv.getBoundingClientRect();
    var cx = cr.left + g.cx, cy = cr.top + g.cy;
    var nb = box(num), ub = un ? box(un) : null;
    /* 数字 + 单位（可能换行）的并集包围盒 */
    var l = Math.min(nb.l, ub ? ub.l : nb.l), r = Math.max(nb.r, ub ? ub.r : nb.r);
    var t = Math.min(nb.t, ub ? ub.t : nb.t), btm = Math.max(nb.b, ub ? ub.b : nb.b);
    var cs = getComputedStyle(num);
    var ctx = cv.getContext('2d');
    ctx.font = (cs.fontWeight || '700') + ' ' + parseFloat(cs.fontSize) + 'px ' + (cs.fontFamily || 'monospace');
    var mw = ctx.measureText(num.textContent || '0').width;
    /* 内接区域（留 PAD） */
    var inL = cx - g.rin + PAD, inR = cx + g.rin - PAD;
    var inT = cy - g.rin + PAD, inB = cy + g.rin - PAD;
    return {
      text: num.textContent, unit: un ? un.textContent : '',
      fontSize: parseFloat(cs.fontSize), unitFontSize: un ? parseFloat(getComputedStyle(un).fontSize) : 0,
      ring: {w: cv.clientWidth, h: cv.clientHeight, R: Math.round(g.R*10)/10, rw: Math.round(g.rw*10)/10,
             rin: Math.round(g.rin*10)/10, dOuter: Math.round(g.R*2*10)/10, dInner: Math.round(g.rin*2*10)/10,
             cx: Math.round(cx*10)/10, cy: Math.round(cy*10)/10},
      numBox: nb, unitBox: ub,
      unionBox: {l: Math.round(l*10)/10, t: Math.round(t*10)/10, r: Math.round(r*10)/10, b: Math.round(btm*10)/10,
                 w: Math.round((r-l)*10)/10, h: Math.round((btm-t)*10)/10},
      measureTextW: Math.round(mw*10)/10,
      inset: {l: inL, t: inT, r: inR, b: inB},
      /* 关键对照 */
      numW_vs_dInner: [Math.round(mw*10)/10, Math.round(g.rin*2*10)/10],
      numH_plus_unitH_vs_dInner: [Math.round((nb.h + (ub?ub.h:0))*10)/10, Math.round(g.rin*2*10)/10],
      overflow: {left: Math.round((inL - l)*10)/10, right: Math.round((r - inR)*10)/10,
                 top: Math.round((inT - t)*10)/10, bottom: Math.round((btm - inB)*10)/10},
      fits: (l >= inL && r <= inR && t >= inT && btm <= inB)
    };
  }
  return JSON.stringify({
    speed: one('speedGauge', 'speedNum', 'speedUnit'),
    rpm: one('rpmGauge', 'rpmNum', 'rpmUnit'),
    errors: window.__errs
  });
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    s = QWebEngineScript()
    s.setName("err")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)
    res = {}

    def on_load(ok):
        def cb2(v):
            res["m"] = v
            app.quit()
        def cb1(_):
            QTimer.singleShot(1500, lambda: page.runJavaScript(MEASURE_JS, cb2))
        QTimer.singleShot(3200, lambda: page.runJavaScript(
            "(function(){var d=document.getElementById('demoMode'); if(d){d.checked=true;} "
            "setDemo(true); return 'ok';})()", cb1))

    page.loadFinished.connect(on_load)
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(40000, app.quit)
    app.exec()

    m = json.loads(res.get("m") or "{}")
    for key, name in (("speed", "车速环形表"), ("rpm", "转速环形表")):
        d = m.get(key)
        print("=== %s（%s）===" % (name, TAG))
        if not d:
            print("  （量不到）")
            continue
        g = d["ring"]
        print("  环几何: 画布 %sx%s  外径=%.1fpx(rw=%.1f)  **内径=%.1fpx**  圆心=(%.1f,%.1f)"
              % (g["w"], g["h"], g["dOuter"], g["rw"], g["dInner"], g["cx"], g["cy"]))
        print("  读数: %r 字号=%spx   单位: %r 字号=%spx"
              % (d["text"], d["fontSize"], d["unit"], d["unitFontSize"]))
        print("  数字包围盒 w=%.1f h=%.1f   单位包围盒 w=%.1f h=%.1f   并集 w=%.1f h=%.1f"
              % (d["numBox"]["w"], d["numBox"]["h"],
                 (d["unitBox"] or {}).get("w", 0), (d["unitBox"] or {}).get("h", 0),
                 d["unionBox"]["w"], d["unionBox"]["h"]))
        print("  measureText 数字宽 = %.1fpx（包围盒宽 %.1fpx）" % (d["measureTextW"], d["numBox"]["w"]))
        print("  ★ 数字宽 vs 环内径: %.1f vs %.1f  (%s)"
              % (d["numW_vs_dInner"][0], d["numW_vs_dInner"][1],
                 "超宽 %.1fpx" % (d["numW_vs_dInner"][0] - d["numW_vs_dInner"][1])
                 if d["numW_vs_dInner"][0] > d["numW_vs_dInner"][1] else "放得下"))
        print("  ★ 数字高+单位高 vs 环内高: %.1f vs %.1f" % tuple(d["numH_plus_unitH_vs_dInner"]))
        ov = d["overflow"]
        print("  ★ 相对环内接区（留 6px）的溢出: 左 %.1f 右 %.1f 上 %.1f 下 %.1f   -> %s"
              % (ov["left"], ov["right"], ov["top"], ov["bottom"],
                 "**溢出**" if (ov["left"] > 0 or ov["right"] > 0 or ov["top"] > 0 or ov["bottom"] > 0) else "在环内 ✔"))
        print("  并集完全落环内 = %s" % d["fits"])
    print("  JS 错误: %s" % m.get("errors"))


if __name__ == "__main__":
    main()
