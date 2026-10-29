# -*- coding: utf-8 -*-
"""task-28：环形表两条断言/判据的**灵敏度证明**（运行时破坏，不改文件）。

三种模式各量一次：
  normal     —— 现状
  fontSab    —— 模拟"字号写死 38px、无自适应"（把 fitRingVal 置空 + 字号复原）
                → `_ux_needle_probe.py` 新增的"落在环内接区"断言必须变红
  needleSab  —— 模拟"指针画不出来/看不见"（把 PAL.ink 改成卡片底色）
                → 旧的"环画出图形(像素>2000)"**看不出来**（弱点），
                  新加的"指针射线上有深色像素"判据必须变红

用法：<venv>\\python.exe tools\\_ring_sensitivity_check.py
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

PROBE_JS = """
(function () {
  window.__errs = [];
  window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
})();
"""

MEASURE = """
window.__measure = function () {
  function t(el){ if(!el) return null; var r = document.createRange(); r.selectNodeContents(el);
                  var b = r.getBoundingClientRect(); return {l:b.left,t:b.top,r:b.right,b:b.bottom}; }
  function ringFit(cvId, numId, unitId, pad){
    var cv = document.getElementById(cvId), num = document.getElementById(numId), un = document.getElementById(unitId);
    if (!cv || !num) return null;
    var g = ringGeom(cv), cr = cv.getBoundingClientRect();
    var cx = cr.left + g.cx, cy = cr.top + g.cy, p = pad || 6;
    var a = t(num), u = un ? t(un) : a;
    var L = Math.min(a.l,u.l), R = Math.max(a.r,u.r), T = Math.min(a.t,u.t), B = Math.max(a.b,u.b);
    return {dInner: Math.round(g.rin*2*10)/10,
            fits: (R <= cx+g.rin-p) && (L >= cx-g.rin+p) && (B <= cy+g.rin-p) && (T >= cy-g.rin+p),
            overL: Math.round((cx-g.rin+p-L)*10)/10, overR: Math.round((R-(cx+g.rin-p))*10)/10};
  }
  /* 指针判据：沿"当前值对应的射线"（半径 rIn..rOut，±0.20rad）数**深色**像素 */
  function needlePix(cvId, id){
    var cv = document.getElementById(cvId); if (!cv) return null;
    var g = ringGeom(cv);
    var s = SIGS[id]; if (!s) return null;
    var lo = s.min, hi = s.max > lo ? s.max : 1;
    var v = Number(state.latest[id]) || 0;
    var f = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    var dpr = Math.min(window.devicePixelRatio || 1, UI_CFG.dprCap || 2);
    var img = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    var sweep = 260*Math.PI/180, a0 = -Math.PI/2 - sweep/2, av = a0 + sweep*f;
    var rIn = Math.max(6, g.R*0.40), rOut = g.R - g.rw - 1, n = 0;
    /* ★ task-29：指针起点已改成"数字块之外"，采样区间取**指针自身** [rIn,rOut]，
       否则采到的是数字字形区、判据失去意义（几何变了，采样口径同步跟随）。 */
    var nr = cv.getContext('2d')._needleR;
    if (nr){ rIn = nr[0]; rOut = nr[1]; }
    for (var rr = rIn; rr <= rOut; rr += 1){
      for (var da = -0.20; da <= 0.201; da += 0.05){
        var x = Math.round((g.cx + Math.cos(av+da)*rr) * dpr);
        var y = Math.round((g.cy + Math.sin(av+da)*rr) * dpr);
        if (x < 0 || y < 0 || x >= cv.width || y >= cv.height) continue;
        var o = (y*cv.width + x)*4;
        var lum = img[o]*0.299 + img[o+1]*0.587 + img[o+2]*0.114;
        if (img[o+3] > 128 && lum < 120) n++;
      }
    }
    return n;
  }
  function pix(id){
    var cv = document.getElementById(id); if (!cv) return 0;
    var d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data, n = 0;
    for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
  }
  return JSON.stringify({
    spdFit: ringFit('speedGauge','speedNum','speedUnit',6),
    rpmFit: ringFit('rpmGauge','rpmNum','rpmUnit',6),
    spdNeedle: needlePix('speedGauge','speed'),
    rpmNeedle: needlePix('rpmGauge','rpm'),
    spdTotal: pix('speedGauge'), rpmTotal: pix('rpmGauge'),
    errs: window.__errs
  });
};
'ok'
"""

SEED = """
(function(){
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type:'motor',seq:1,ts:1,speed:26.3,rpm:5240,throttle:88,
    torque:20,temp:45,volt:443.9,amp:150}]));
  if (typeof updateDom === 'function') updateDom();
  paintGauges();
  return 'ok';
})()
"""

SAB_FONT = """
(function(){
  /* 模拟"自适应被破坏"：关掉 fitRingVal，并把字号设成**必然溢出**的大小。
     ★ 不再写死 38px（task-29 收尾）：环内径从 113.6 涨到 132.1 之后 38px 已放得下，
       探针会"合理地不变红" → 证明失效。这里按当前环径推导：字号 = 内径半径 × 1.15，
       文字宽度必然远超内接区，任何布局下都会溢出。 */
  fitRingVal = function(){};
  [['speedNum','speedGauge'],['rpmNum','rpmGauge']].forEach(function(c){
    var e = document.getElementById(c[0]), cv = document.getElementById(c[1]);
    if (!e || !cv) return;
    var big = Math.max(60, Math.ceil(ringGeom(cv).rin * 1.15));
    e.style.fontSize = big + 'px'; e._fs = big;
  });
  ['speedUnit','rpmUnit'].forEach(function(id){
    var e = document.getElementById(id);
    if (e){ var b2 = Math.max(18, Math.round(parseFloat(e.style.fontSize || '12') )); e.style.fontSize = b2 + 'px'; }
  });
  paintGauges();
  return 'ok';
})()
"""

SAB_NEEDLE = """
(function(){
  /* 模拟"指针看不见"：把指针可能用到的**全部**颜色（ok 用 ink、warn/over 用状态色）
     都设成卡片底色 —— 否则 warn 态下指针仍是琥珀色，遮不住，破坏不成立。 */
  var bg = getComputedStyle(document.getElementById('zoneP2')).backgroundColor || '#FCFCFE';
  PAL.ink = bg; PAL.warn = bg; PAL.over = bg; PAL.ok = bg;
  paintGauges();
  return 'ok';
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

    def meas(tag, then):
        def got(v):
            res[tag] = v
            then()
        QTimer.singleShot(120, lambda: page.runJavaScript("(window.__measure)()", got))

    def on_load(ok):
        page.runJavaScript(MEASURE, lambda _: QTimer.singleShot(400, lambda: step("normal")))

    def step(tag):
        if tag == "normal":
            page.runJavaScript(SEED, lambda _: QTimer.singleShot(150, lambda: meas(
                "normal", lambda: page.runJavaScript(SAB_FONT, lambda _: QTimer.singleShot(150, lambda: step("fontSab"))))))
        elif tag == "fontSab":
            meas("fontSab", lambda: page.runJavaScript(SAB_NEEDLE, lambda _: QTimer.singleShot(150, lambda: step("needleSab"))))
        else:
            meas("needleSab", lambda: app.quit())

    page.loadFinished.connect(on_load)
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(40000, app.quit)
    app.exec()

    for tag in ("normal", "fontSab", "needleSab"):
        raw = res.get(tag)
        d = json.loads(raw) if raw else {}
        print("=== %s ===" % tag)
        sf, rf = d.get("spdFit") or {}, d.get("rpmFit") or {}
        print("  车速 环内接区 fits=%s (内径 %s, 溢出 左%s 右%s)   指针射线深色像素=%s   环总像素=%s"
              % (sf.get("fits"), sf.get("dInner"), sf.get("overL"), sf.get("overR"),
                 d.get("spdNeedle"), d.get("spdTotal")))
        print("  转速 环内接区 fits=%s (内径 %s, 溢出 左%s 右%s)   指针射线深色像素=%s   环总像素=%s"
              % (rf.get("fits"), rf.get("dInner"), rf.get("overL"), rf.get("overR"),
                 d.get("rpmNeedle"), d.get("rpmTotal")))

    n = json.loads(res.get("normal") or "{}")
    f = json.loads(res.get("fontSab") or "{}")
    nd = json.loads(res.get("needleSab") or "{}")
    print()
    print("--- 灵敏度结论 ---")
    checks = [
        ("现状：两条 ring 断言都通过",
         (n.get("spdFit") or {}).get("fits") is True and (n.get("rpmFit") or {}).get("fits") is True),
        ("★ 破坏自适应（字号按环径×1.15 必然溢出）-> ring 断言**变红**",
         (f.get("spdFit") or {}).get("fits") is False or (f.get("rpmFit") or {}).get("fits") is False),
        ("★ 指针看不见 -> 旧的『环画出图形(像素>2000)』**仍是绿的**（判据太弱）",
         (nd.get("spdTotal") or 0) > 2000),
        ("★ 指针看不见 -> 新的『指针射线深色像素』判据**变红**（残余抗锯齿像素 <= 20%）",
         (n.get("spdNeedle") or 0) >= 8
         and (nd.get("spdNeedle") or 0) <= max(1, int(0.2 * (n.get("spdNeedle") or 0)))),
        # ★ 负向对照：注入一条必然失败项，验证"失败 -> 退出码非零"这条线真的接了
        #   （用法：RING_SENS_FORCE_FAIL=1 跑本脚本，必须 exit 1）
        ("★ [负向对照] 故意注入失败 -> 退出码必须非零",
         os.environ.get("RING_SENS_FORCE_FAIL") != "1"),
    ]
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
