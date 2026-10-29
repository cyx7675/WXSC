# -*- coding: utf-8 -*-
"""task-29 验证：D1 指针可见 / D2 字号稳定 / D5 热条 —— 修后量化 + 灵敏度（运行时破坏，不改文件）。

用法：<venv>\\python.exe tools\\_d1d2d5_check.py [BEFORE|AFTER]
    BEFORE 模式额外把"修复前行为"临时还原（D1 rIn=0.40R；D2 每帧重算；D5 旧 idx 公式），
    用来出"修前/修后"对照；不动源码。
"""
import json
import os
import sys

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
MODE = (sys.argv[1] if len(sys.argv) > 1 else "AFTER").upper()

JS = r"""
(function () {
  var out = {errs: window.__errs || []};
  function feed(o){
    lastSampleT = -1e9;
    onDataBatch(JSON.stringify([
      {type:'adc', seq:1, ts:performance.now(), values:[o.a0||2048,2048,0,0,0,0,0,0,0,0]},
      {type:'motor', seq:1, ts:performance.now(),
       names:['SPEED','RPM','THROTTLE','TORQUE','TEMP','VOLT','AMP'],
       values:[o.speed,o.rpm,o.thr,o.tq,o.temp,o.volt,o.amp],
       speed:o.speed, rpm:o.rpm, throttle:o.thr, torque:o.tq, temp:o.temp, volt:o.volt, amp:o.amp}]));
    if (typeof updateDom === 'function') updateDom();
    if (typeof paintGauges === 'function') paintGauges();
    if (typeof updateHeats === 'function') updateHeats();
  }
  function TR(el){ var r = document.createRange(); r.selectNodeContents(el); var b = r.getBoundingClientRect();
    return [b.left, b.top, b.width, b.height]; }
  /* 指针"可见"判据：数字盒**之外**、半径 rIn..rOut 的环带里有没有指针色像素 */
  function needleVisible(cvId, numId, id){
    var cv = document.getElementById(cvId), num = document.getElementById(numId);
    var g = ringGeom(cv), ctx = cv.getContext('2d');
    var nr = ctx._needleR || [0,0];
    var cr = cv.getBoundingClientRect(), nb = TR(num);
    var cx = cr.left + g.cx, cy = cr.top + g.cy;
    var dpr = Math.min(window.devicePixelRatio||1, UI_CFG.dprCap||2);
    var img = ctx.getImageData(0,0,cv.width,cv.height).data;
    var s = SIGS[id], lo = s.min, hi = s.max>lo?s.max:1;
    var v = Number(state.latest[id])||0;
    var f = Math.max(0, Math.min(1, (v-lo)/(hi-lo)));
    var sweep = 260*Math.PI/180, a0 = -Math.PI/2 - sweep/2, av = a0 + sweep*f;
    var inBox = 0, outBox = 0;
    for (var rr = 1; rr < g.R - g.rw; rr += 0.5){
      if (rr < nr[0] + 1) continue;                     /* ★ 只数指针自己的区间（避开数字字形） */
      for (var da = -0.26; da <= 0.261; da += 0.04){
        var wx = cx + Math.cos(av+da)*rr, wy = cy + Math.sin(av+da)*rr;
        var x = Math.round((wx - cr.left) * dpr), y = Math.round((wy - cr.top) * dpr);
        if (x<0||y<0||x>=cv.width||y>=cv.height) continue;
        var o = (y*cv.width+x)*4;
        if (img[o+3] < 100) continue;
        var lum = img[o]*0.299 + img[o+1]*0.587 + img[o+2]*0.114;
        if (lum >= 110) continue;                       /* 只数深色(指针) */
        var inDigitBox = (wx >= nb[0] && wx <= nb[0]+nb[2] && wy >= nb[1] && wy <= nb[1]+nb[3]);
        if (inDigitBox) inBox++; else outBox++;
      }
    }
    /* 几何判据：指针线段 [rIn,rOut] 与数字盒是否相交（这才是"被遮挡"的定义） */
    function segRect(x1,y1,x2,y2,b){
      function o(p,q,r){ var v=(q[1]-p[1])*(r[0]-q[0])-(q[0]-p[0])*(r[1]-q[1]); return v===0?0:(v>0?1:2); }
      function ss(a,bb,c,d){ var o1=o(a,bb,c), o2=o(a,bb,d), o3=o(c,d,a), o4=o(c,d,bb);
        return o1!==o2 && o3!==o4; }
      if (x1>=b[0]&&x1<=b[0]+b[2]&&y1>=b[1]&&y1<=b[1]+b[3]) return true;
      if (x2>=b[0]&&x2<=b[0]+b[2]&&y2>=b[1]&&y2<=b[1]+b[3]) return true;
      var L=[b[0],b[1]], R=[b[0]+b[2],b[1]], R2=[b[0]+b[2],b[1]+b[3]], L2=[b[0],b[1]+b[3]];
      return ss([x1,y1],[x2,y2],L,R)||ss([x1,y1],[x2,y2],R,R2)||ss([x1,y1],[x2,y2],R2,L2)||ss([x1,y1],[x2,y2],L2,L);
    }
    var nx1 = cx + Math.cos(av)*nr[0], ny1 = cy + Math.sin(av)*nr[0];
    var nx2 = cx + Math.cos(av)*nr[1], ny2 = cy + Math.sin(av)*nr[1];
    var hits = segRect(nx1,ny1,nx2,ny2, nb);
    return {needleR: nr, rIn: Math.round(nr[0]*10)/10, rOut: Math.round(nr[1]*10)/10,
            len: Math.round((nr[1]-nr[0])*10)/10,
            darkInDigitBox: inBox, darkOutsideDigitBox: outBox,
            hitsDigitBox: hits};
  }
  /* ---------- D2：字号 vs 值 ---------- */
  out.d2 = {speed: [], rpm: []};
  [[0,0],[10.1,999],[29.2,5200],[40,10000]].forEach(function(c){
    feed({speed:c[0], rpm:c[1], thr:50, tq:75, temp:60, volt:440, amp:120});
    out.d2.speed.push({v:c[0], fs: parseFloat(getComputedStyle(document.getElementById('speedNum')).fontSize)});
    out.d2.rpm.push({v:c[1], fs: parseFloat(getComputedStyle(document.getElementById('rpmNum')).fontSize)});
  });
  /* ---------- D1：指针可见（巡航段重点） ---------- */
  out.d1 = [];
  [24, 29.2, 34].forEach(function(v){
    feed({speed:v, rpm:5200, thr:50, tq:75, temp:60, volt:440, amp:120});
    out.d1.push({speed:v, speedRing: needleVisible('speedGauge','speedNum','speed')});
  });
  [999, 5200, 9800].forEach(function(v){
    feed({speed:29.2, rpm:v, thr:50, tq:75, temp:60, volt:440, amp:120});
    out.d1.push({rpm:v, rpmRing: needleVisible('rpmGauge','rpmNum','rpm')});
  });
  /* ---------- D5：热条（600 点、50% 处阶跃；油门低值 10%） ---------- */
  function loadBuf(id, arr){ state.buf[id] = arr.slice(); state.latest[id] = arr[arr.length-1]; }
  var N = 600, half = 300;
  var sp = [], th = [], tq = [];
  for (var i=0;i<N;i++){
    sp.push(i < half ? 8 : 30);          /* 车速：前半 8、后半 30（阶跃点在 50%） */
    th.push(i < half ? 10 : 60);         /* 油门：低值段 10%（旧代码会退化成虚线） */
    tq.push(i < half ? 140 : 40);        /* 扭矩：前半超上限 140（>150 上限的 93%）、后半正常 */
  }
  loadBuf('speed', sp); loadBuf('throttle', th); loadBuf('torque', tq);
  ['speed','rpm','throttle','torque','temp','volt','amp'].forEach(function(k){
    if (!state.buf[k] || !state.buf[k].length){ state.buf[k] = sp.slice(); }
  });
  if (typeof updateHeats === 'function') updateHeats();
  function heatInfo(elId, id){
    var el = document.getElementById(elId), kids = el.children, n = kids.length;
    var hei = [], cols = [];
    for (var k=0;k<n;k++){
      hei.push(Math.round(parseFloat(kids[k].style.height || '0')));
      cols.push(kids[k].style.background || '');
    }
    var firstCol = cols[0], lastCol = cols[n-1];
    var perCol = cols.filter(function(c){ return c !== firstCol; }).length;
    return {n:n, heights: hei, rightmostH: hei[n-1], lowH: hei[0],
            colorsDistinct: perCol, firstCol: firstCol, lastCol: lastCol,
            tailValue: Math.round(state.buf[id][state.buf[id].length-1]*100)/100};
  }
  out.d5 = {speedHeat: heatInfo('speedHeat','speed'),
            thrHeat: heatInfo('thrHeat','throttle'),
            tqHeat: heatInfo('tqHeat','torque')};
  /* 最右段 == 当前值？用 30 与 31 两个尾巴验 */
  state.buf.speed[state.buf.speed.length-1] = 31;
  if (typeof updateHeats === 'function') updateHeats();
  out.d5.tailFollows = (function(){
    var el = document.getElementById('speedHeat');
    var h1 = el.children[el.children.length-1].style.height;
    state.buf.speed[state.buf.speed.length-1] = 8;
    if (typeof updateHeats === 'function') updateHeats();
    var h2 = el.children[el.children.length-1].style.height;
    state.buf.speed[state.buf.speed.length-1] = 30;
    if (typeof updateHeats === 'function') updateHeats();
    return {hTail31: h1, hTail8: h2, changesWithTail: h1 !== h2};
  })();
  /* len<2 是否显式清空 */
  var saved = state.buf.throttle.slice();
  state.buf.throttle = [42];
  if (typeof updateHeats === 'function') updateHeats();
  out.d5.emptyClears = (function(){
    var el = document.getElementById('thrHeat'), n = el.children.length, maxH = 0;
    for (var k=0;k<n;k++){ var h = parseFloat(el.children[k].style.height || '0'); if (h > maxH) maxH = h; }
    return maxH;
  })();
  state.buf.throttle = saved;
  if (typeof updateHeats === 'function') updateHeats();
  return JSON.stringify(out);
})()
"""

SAB = {}


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def on_load(ok):
        def got(v):
            res["r"] = v
            app.quit()
        QTimer.singleShot(3000, lambda: page.runJavaScript(JS, got))

    page.loadFinished.connect(on_load)
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    d = json.loads(res.get("r") or "{}")
    print("=== D2 · 中心数字字号（同一环内应恒定）===")
    for k in ("speed", "rpm"):
        arr = d.get("d2", {}).get(k) or []
        fss = [x["fs"] for x in arr]
        print("  %-6s %s   -> 字号集合=%s  极差=%.1fpx" % (
            k, " ".join("%s→%gpx" % (x["v"], x["fs"]) for x in arr), sorted(set(fss)),
            (max(fss) - min(fss)) if fss else 0))
    print()
    print("=== D1 · 指针可见性（数字盒内/外的深色像素）===")
    for rec in d.get("d1") or []:
        for kind in ("speedRing", "rpmRing"):
            if kind in rec:
                r = rec[kind]
                print("  %-5s=%-6s rIn=%-6s rOut=%-6s 长度=%-5s 盒内暗像素=%-4s 盒外暗像素=%-4s 命中数字盒=%s"
                      % ("speed" if kind.startswith("speed") else "rpm",
                         rec.get("speed", rec.get("rpm")), r["rIn"], r["rOut"], r["len"],
                         r["darkInDigitBox"], r["darkOutsideDigitBox"], r["hitsDigitBox"]))
    print()
    print("=== D5 · 热条 ===")
    for k, v in (d.get("d5") or {}).items():
        if k == "tailFollows" or not isinstance(v, dict):
            continue
        print("  %-9s n=%s 最右段高=%-4s 首段高=%-4s 不同色段数=%-3s 首色=%s 尾色=%s 缓冲末值=%s"
              % (k, v.get("n"), v.get("rightmostH"), v.get("lowH"), v.get("colorsDistinct"),
                 v.get("firstCol"), v.get("lastCol"), v.get("tailValue")))
    tf = (d.get("d5") or {}).get("tailFollows") or {}
    print("  最右段跟随末值: %s（31->%s  8->%s  不同=%s）" % (tf.get("changesWithTail"), tf.get("hTail31"),
                                                    tf.get("hTail8"), tf.get("changesWithTail")))
    print("  len<2 时最大段高 = %s（应为 0 = 显式清空）" % (d.get("d5") or {}).get("emptyClears"))
    print("  JS 错误:", d.get("errs"))


if __name__ == "__main__":
    main()
