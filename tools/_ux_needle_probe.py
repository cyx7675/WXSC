# -*- coding: utf-8 -*-
"""_ux_needle_probe.py —— 表盘"图形到底画出来没有"（车速/转速=canvas 环形表，横滚=SVG 指针）

★ 历史（2026-10-16 产品体验复核）：车速/横滚两个 SVG 表盘的指针**从来没被绘制过** ——
  根因是 `transform="translate(a b) rotate(deg)"` 这种写法会把指针画到 SVG 视口外面，
  被根元素默认的 `overflow:hidden` 裁掉（QtWebEngine 里 rect 落到盒子外；Chrome 像素计数 0）。
  改成**中心式** `rotate(角度 cx cy)` 后可见。本脚本当时固定了"不含 translate("的回归断言。

★ task-27 落地（用户裁定"总体的 B + 车速/转速用 A 的环形表"）：
  · 车速、转速不再用 SVG，改成 **canvas 环形表**（drawRingGauge）——canvas 没有坐标系映射坑；
  · 横滚仍是 SVG，**中心式 rotate 那条回归断言必须继续守住**（本脚本因此保留它）。
  所以本脚本现在验证三件事：
    ① 车速/转速是 canvas 且**真的有像素**（弧/刻度/指针都画上了），中心读数是 DOM 且落在环内；
    ② 告警态下环形表**变红**（>85% 量程）——证明 canvas 也吃告警语义；
    ③ 横滚指针仍是中心式 rotate 且落在 SVG 盒子内（历史坑不再复发）。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_ux_needle_probe.py
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer          # noqa: E402
from PyQt6.QtWidgets import QApplication       # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JS = r"""
(function(){
  function box(e){ var b = e.getBoundingClientRect();
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; }
  /* 置数据 + 关演示：环形表要有真值才画得出弧与指针 */
  try{ setDemo(false); }catch(e){}
  clearTelemetry();
  /* 本探针只关心"绘制"，把可见性门控放开（分区在离屏/总览滚动位置下 IO 还没回调） */
  zoneVisible = function(){ return true; };
  var out = {errs: []};
  window.onerror = function(m){ out.errs.push(String(m)); };

  function feed(speed, rpm){
    onDataBatch(JSON.stringify([{type:'motor',seq:1,ts:1,speed:speed,rpm:rpm,throttle:88,
      torque:96.4,temp:63,volt:443.9,amp:150.0}]));
    updateDom();
    refreshMini();          /* 离散补画一次（与切区/回填同一入口），拿到同步结果 */
  }
  function pixels(cv){
    var g = cv.getContext('2d');
    var d = g.getImageData(0, 0, cv.width, cv.height).data;
    var n = 0, red = 0;
    for (var i = 0; i < d.length; i += 4){
      if (d[i+3] > 12){
        n++;
        if (d[i] > 140 && d[i+1] < 100 && d[i+2] < 100) red++;   /* 告警红 #C8393B 附近 */
      }
    }
    return {total: n, red: red};
  }

  /* ---- 正常态 ---- */
  feed(31.2, 5200);
  var sg = document.getElementById('speedGauge');
  var rg = document.getElementById('rpmGauge');
  out.hasSpeedCanvas = !!(sg && sg.tagName === 'CANVAS');
  out.hasRpmCanvas = !!(rg && rg.tagName === 'CANVAS');
  out.oldSpeedNeedleGone = !document.getElementById('speedNeedle');
  out.spdPx = pixels(sg);
  out.rpmPx = pixels(rg);
  out.spdRect = box(sg);
  out.rpmRect = box(rg);
  out.spdNumRect = box(document.getElementById('speedNum'));
  out.spdNumInside = (function(){
    var s = out.spdRect, n = out.spdNumRect;
    var cx = n[0] + n[2]/2, cy = n[1] + n[3]/2;
    return cx > s[0] && cx < s[0]+s[2] && cy > s[1] && cy < s[1]+s[3];
  })();
  out.spdUnitInside = (function(){
    var e = document.getElementById('speedUnit'); if (!e) return false;
    var r = box(e);
    return r[0] >= out.spdRect[0] && r[0]+r[2] <= out.spdRect[0]+out.spdRect[2];
  })();

  /* ---- 告警态：车速拉到 39 m/s（量程 40 的 97.5% > 报警线 85%）---- */
  feed(39.0, 9800);
  out.spdAlarmPx = pixels(sg);
  out.rpmAlarmPx = pixels(rg);
  out.spdState = (document.getElementById('speedState') || {}).textContent;

  /* ---- 横滚仍是 SVG：中心式 rotate 的回归（历史坑） ---- */
  var rs = document.getElementById('rollNeedle');
  out.rollExists = !!rs;
  out.rollAttr = rs ? rs.getAttribute('transform') : null;
  var svgR = rs ? rs.closest('svg') : null;
  out.svgRoll = svgR ? box(svgR) : null;
  out.rollRect = rs ? box(rs) : null;
  if (rs){
    var keep = rs.getAttribute('transform');
    rs.removeAttribute('transform');
    out.rollNoTf = box(rs);
    rs.setAttribute('transform', 'rotate(-90 60 56)');
    out.rollCenterForm = box(rs);
    rs.setAttribute('transform', keep);
    function inside(r, b){
      return r && b && r[0] >= b[0]-1 && r[1] >= b[1]-1 &&
             r[0]+r[2] <= b[0]+b[2]+1 && r[1]+r[3] <= b[1]+b[3]+1;
    }
    out.rollInSvg = inside(out.rollRect, out.svgRoll);
    out.rollCenterInSvg = inside(out.rollCenterForm, out.svgRoll);
  }
  /* ★ task-28 新增：中心读数必须落在**环内接区**（内径四周留 6px），
     而不只是"在画布矩形内" —— 旧断言 `spdNumInside` 只比 canvas 的 rect，
     "数字比环还宽/单位骑在弧上"照样能过（这次就是这么漏掉的）。
     几何用 app.js 的 ringGeom()，保证"断言算的"就是"drawRingGauge 画的"。 */
  function ringFit(cvId, numId, unitId, pad){
    var cv = document.getElementById(cvId);
    var num = document.getElementById(numId);
    var un = document.getElementById(unitId);
    if (!cv || !num || typeof ringGeom !== 'function') return null;
    var g = ringGeom(cv), cr = cv.getBoundingClientRect();
    var cx = cr.left + g.cx, cy = cr.top + g.cy;
    function t(el){
      var rg = document.createRange(); rg.selectNodeContents(el);
      var b = rg.getBoundingClientRect();
      return {l: b.left, t: b.top, r: b.right, b: b.bottom};
    }
    var a = t(num), u = un ? t(un) : a;
    var L = Math.min(a.l, u.l), R = Math.max(a.r, u.r);
    var T = Math.min(a.t, u.t), B = Math.max(a.b, u.b);
    var p = pad || 6, avail = g.rin * 2 - p * 2;
    return {rin: Math.round(g.rin * 10) / 10, dInner: Math.round(g.rin * 2 * 10) / 10,
            w: Math.round((R - L) * 10) / 10, h: Math.round((B - T) * 10) / 10, pad: p, avail: Math.round(avail * 10) / 10,
            overL: Math.round((cx - g.rin + p - L) * 10) / 10,
            overR: Math.round((R - (cx + g.rin - p)) * 10) / 10,
            overT: Math.round((cy - g.rin + p - T) * 10) / 10,
            overB: Math.round((B - (cy + g.rin - p)) * 10) / 10,
            wOK: (R - L) <= avail, hOK: (B - T) <= avail,
            fits: (R <= cx + g.rin - p) && (L >= cx - g.rin + p) &&
                  (B <= cy + g.rin - p) && (T >= cy - g.rin + p)};
  }
  out.spdRingFit = ringFit('speedGauge', 'speedNum', 'speedUnit', 6);
  out.rpmRingFit = ringFit('rpmGauge', 'rpmNum', 'rpmUnit', 6);
  /* ★ task-28 新增：**指针判据**。旧断言只查"环画出了图形(像素>2000)"，
     指针即使完全不画/画成背景色，弧和刻度照样有 3000+ 像素 -> 照样是绿的
     （灵敏度实测：把指针色改成卡片底色后 环总像素仍 3804）。
     这里沿"当前值对应的射线"（半径 rIn..rOut，±0.20rad）数深色像素：
     指针在 -> 40+；指针不在 -> 0~1（残余抗锯齿）。 */
  function needlePix(cvId, id){
    var cv = document.getElementById(cvId); if (!cv) return null;
    var s = SIGS[id]; if (!s) return null;
    var g = ringGeom(cv);
    var lo = s.min, hi = s.max > lo ? s.max : 1;
    var v = Number(state.latest[id]) || 0;
    var f = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    var dpr = Math.min(window.devicePixelRatio || 1, UI_CFG.dprCap || 2);
    var img = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    var sweep = 260 * Math.PI / 180, a0 = -Math.PI / 2 - sweep / 2, av = a0 + sweep * f;
    var rIn = Math.max(6, g.R * 0.40), rOut = g.R - g.rw - 1, n = 0;
    for (var rr = rIn; rr <= rOut; rr += 1){
      for (var da = -0.20; da <= 0.201; da += 0.05){
        var x = Math.round((g.cx + Math.cos(av + da) * rr) * dpr);
        var y = Math.round((g.cy + Math.sin(av + da) * rr) * dpr);
        if (x < 0 || y < 0 || x >= cv.width || y >= cv.height) continue;
        var o = (y * cv.width + x) * 4;
        var lum = img[o] * 0.299 + img[o + 1] * 0.587 + img[o + 2] * 0.114;
        if (img[o + 3] > 128 && lum < 120) n++;
      }
    }
    return n;
  }
  out.spdNeedle = needlePix('speedGauge', 'speed');
  out.rpmNeedle = needlePix('rpmGauge', 'rpm');
  return JSON.stringify(out);
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def cb(v):
        res["r"] = v
        app.quit()

    page.loadFinished.connect(lambda ok: page.runJavaScript(JS, cb))
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    raw = res.get("r")
    if not raw:
        print("FAIL 页面无返回")
        return 1
    d = json.loads(raw)

    def pc(k):
        v = d.get(k) or {}
        return "%s 像素（其中红 %s）" % (v.get("total"), v.get("red"))

    print("=== 车速 / 转速 环形表（canvas）===")
    print("  #speedGauge 是 canvas      :", d.get("hasSpeedCanvas"), " rect=", d.get("spdRect"))
    print("  #rpmGauge   是 canvas      :", d.get("hasRpmCanvas"), " rect=", d.get("rpmRect"))
    print("  旧 SVG 指针 #speedNeedle 已移除:", d.get("oldSpeedNeedleGone"))
    print("  正常态 车速环 :", pc("spdPx"))
    print("  正常态 转速环 :", pc("rpmPx"))
    print("  告警态 车速环 :", pc("spdAlarmPx"), " 状态徽标=", d.get("spdState"))
    print("  中心读数 #speedNum 在环内  :", d.get("spdNumInside"), " 单位在环内 =", d.get("spdUnitInside"))
    print("  ★ 车速环内接区: %s" % d.get("spdRingFit"))
    print("  ★ 转速环内接区: %s" % d.get("rpmRingFit"))
    print("=== 横滚（仍是 SVG，中心式 rotate 回归）===")
    print("  transform       :", d.get("rollAttr"))
    print("  指针 rect       :", d.get("rollRect"), " 在 SVG 内 =", d.get("rollInSvg"))
    print("  去掉 transform  :", d.get("rollNoTf"))
    print("  改中心式 rotate :", d.get("rollCenterForm"), " 在 SVG 内 =", d.get("rollCenterInSvg"))
    print("  SVG 盒子        :", d.get("svgRoll"))

    px = lambda k: ((d.get(k) or {}).get("total") or 0)
    red = lambda k: ((d.get(k) or {}).get("red") or 0)
    checks = [
        ("① 车速表是 canvas（不再用 SVG 指针）", d.get("hasSpeedCanvas") is True),
        ("① 转速表是 canvas（不再用 SVG 指针）", d.get("hasRpmCanvas") is True),
        ("① 旧 SVG 指针节点已彻底移除（不会再有人以为它在画）", d.get("oldSpeedNeedleGone") is True),
        ("② 车速环真的画出了图形（像素 > 2000）", px("spdPx") > 2000),
        ("② 转速环真的画出了图形（像素 > 2000）", px("rpmPx") > 2000),
        ("② 中心读数 #speedNum 落在环内（不是被弧挡住/跑到框外）", d.get("spdNumInside") is True),
        ("② 单位跟着读数在同一环内", d.get("spdUnitInside") is True),
        # ★ task-28 新增两条：真判据是"落在**环内接区**（内径四周留 6px）"，
        #   而不是"落在画布矩形内" —— 用户放大截图抓到的"数字比环还宽、单位骑弧"
        #   在旧判据下是绿的（这次缺陷漏检的根因）。
        ("② 车速 数字+单位 完全落在环内接区（内径 %s，四周留 6px）" % (d.get("spdRingFit") or {}).get("dInner"),
         (d.get("spdRingFit") or {}).get("fits") is True),
        ("② 转速 数字+单位 完全落在环内接区（内径 %s，四周留 6px）" % (d.get("rpmRingFit") or {}).get("dInner"),
         (d.get("rpmRingFit") or {}).get("fits") is True),
        # ★ task-28 新增：指针必须**真的画出来**（沿当前值的射线数深色像素）。
        #   旧判据"环总像素>2000"对"指针不画"完全无感（实测把指针改成底色后仍 3804）。
        ("② 车速指针真的画在应有的射线上（深色像素 %s）" % d.get("spdNeedle"),
         (d.get("spdNeedle") or 0) >= 20),
        ("② 转速指针真的画在应有的射线上（深色像素 %s）" % d.get("rpmNeedle"),
         (d.get("rpmNeedle") or 0) >= 20),
        ("③ 告警态环形表变红（车速 39/40 触发；红像素 > 300）",
         red("spdAlarmPx") > 300 and str(d.get("spdState") or "").find("超限") >= 0),
        ("③ 横滚指针仍是中心式 rotate（不含 translate(）",
         ("translate(" not in (d.get("rollAttr") or ""))),
        ("③ 横滚指针落在 SVG 盒子内（历史坑不再复发）", d.get("rollInSvg") is True),
        ("③ 横滚指针改中心式 rotate 也落在 SVG 内（对照）", d.get("rollCenterInSvg") is True),
        ("无 JS 运行时错误", not (d.get("errs") or [])),
    ]
    print("---")
    ok = True
    for n, g in checks:
        print(("  OK   " if g else "  FAIL ") + n)
        ok = ok and bool(g)
    print("结果:", "全部通过" if ok else "存在失败（见上面 rect/像素）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
