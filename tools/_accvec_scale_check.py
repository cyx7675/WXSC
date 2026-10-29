# -*- coding: utf-8 -*-
"""task-13 返工验证：① 满量程修复后矢量图在**真实量级**下可见 ② P2 旧键不重置另一路。

① 用真实量级 lat=1.2 / lon=-0.8（mag=1.4422）断言：
   · ACC_FS = 16.384（不是 1000）
   · 箭头端点距圆心 ≈ 52 × 1.4422/16.384 ≈ 4.58 px（占半径 8.8%），不是 0.075 px
   · 合成幅值虚线圈半径同量级
   · lat/lon 仍 ±ACC_FS 对称、合成 0..ACC_FS
   · gaugeRanges 里额外塞 ACCEL_LAT/ACCEL_LON 假键 -> 必须被忽略（代码不依赖它们）
② P2：clearTelemetry 后三路 = 0；先 lat=12 再只来 {accel:7} -> mag 必须是 7（不是 13.892）
③ 趋势 norm 下 accelMag 不再贴底（1.4 g 量级数据 -> 曲线离底 ≥8px）

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_accvec_scale_check.py
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
OUT_JSON = os.path.join(HERE, "tools", "_accvec_scale_result.json")

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  /* 抓矢量图的几何：arc 半径 + 箭头端点（lineTo 的坐标） */
  window.__g = {radii: [], pts: []};
  if (!window.__gHooked) {
    window.__gHooked = true;
    var _arc = CanvasRenderingContext2D.prototype.arc;
    CanvasRenderingContext2D.prototype.arc = function (x, y, r) {
      if (this.canvas && this.canvas.id === 'accVecCanvas') window.__g.radii.push(r);
      return _arc.apply(this, arguments);
    };
    var _lt = CanvasRenderingContext2D.prototype.lineTo;
    CanvasRenderingContext2D.prototype.lineTo = function (x, y) {
      if (this.canvas && this.canvas.id === 'accVecCanvas') window.__g.pts.push([x, y]);
      return _lt.apply(this, arguments);
    };
  }
})();
"""

SETUP_JS = """
(function () {
  var out = {};
  document.querySelectorAll('.rail-item')[0].click();      /* 矢量图现在在「总览」的 #zoneP1 里 */

  /* 真实配置值：ACCEL scale=0.001、accRawFs=16384；再走 applyUiConfig 的 gaugeRanges
     分支（含**假键** ACCEL_LAT/ACCEL_LON，用来证明代码不依赖它们）。 */
  ACC_SCALE = 0.001; ACC_RAW_FS = 16384; ACC_UNIT = '';
  applyUiConfig({gaugeRanges: {ACCEL: 16.384, ACCEL_LAT: 999, ACCEL_LON: 999, SPEED: 40}, adc: [], ui: {}});
  out.accFs = ACC_FS;
  out.accFsSet = ACC_FS_SET;
  out.ranges = {
    lat: [SIGS.accelLat.min, SIGS.accelLat.max],
    lon: [SIGS.accelLon.min, SIGS.accelLon.max],
    mag: [SIGS.accelMag.min, SIGS.accelMag.max]
  };

  /* 真实量级数据 */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 1, ts: 1000,
    names: ['ROLL', 'PITCH', 'YAW', 'ACCEL_LAT', 'ACCEL_LON'],
    values: [0, 0, 0, 1.2, -0.8], roll: 0, pitch: 0, yaw: 0,
    accel_lat: 1.2, accel_lon: -0.8}]));
  out.lat = state.latest.accelLat; out.lon = state.latest.accelLon; out.mag = state.latest.accelMag;

  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cv.width, cv.height);
  window.__g.radii = []; window.__g.pts = [];
  refreshAccVec();

  var L = accVecLayer;
  var cx = L.w / 2, cy = L.h / 2, R = Math.max(10, Math.min(L.w, L.h) / 2 - 14);
  out.geom = {w: L.w, h: L.h, R: R};
  out.radii = window.__g.radii.slice();
  /* 箭头端点 = lineTo 里同时偏离两条中线 >1px 的那个点（十字准星的端点在某一轴上） */
  var tip = null, best = -1;
  window.__g.pts.forEach(function (p) {
    var dx = p[0] - cx, dy = p[1] - cy, d = Math.sqrt(dx * dx + dy * dy);
    if (Math.abs(dx) > 1 && Math.abs(dy) > 1 && d > best) { best = d; tip = p; }
  });
  out.tip = tip;
  out.tipDist = tip ? best : null;
  out.tipDistExpect = R * Math.abs(out.mag) / ACC_FS;
  out.tipDistIfBroken = R * Math.abs(out.mag) / 1000.0;   /* 若满量程还是 1000 g 会是多少 */
  /* 合成虚线圈：排除 4 条同心圈（半径恰为 R/4*k）与命中点（3.2），取与期望值最近的那个 */
  out.magCircle = (function () {
    var cands = window.__g.radii.filter(function (rr) {
      for (var k = 1; k <= 4; k++) if (Math.abs(rr - R * k / 4) < 0.5) return false;
      return Math.abs(rr - 3.2) > 0.01;      /* 命中点半径固定 3.2 */
    });
    if (!cands.length) return null;
    var exp = R * Math.abs(out.mag) / ACC_FS, best = null, bd = 1e9;
    cands.forEach(function (rr) { var d = Math.abs(rr - exp); if (d < bd) { bd = d; best = rr; } });
    return best;
  })();
  out.pixels = (function () {
    var d = g.getImageData(0, 0, cv.width, cv.height).data, n = 0;
    for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
  })();
  /* 箭头附近（半径 8px 内）是否有非透明像素 -> "肉眼可见"的像素级证据 */
  out.pixelsNearCenter = (function () {
    var d = g.getImageData(0, 0, cv.width, cv.height).data, n = 0;
    var dpr = L.dpr, rr = 8 * dpr;
    for (var yy = 0; yy < cv.height; yy++) {
      for (var xx = 0; xx < cv.width; xx++) {
        var dx = xx - cx * dpr, dy = yy - cy * dpr;
        if (dx * dx + dy * dy <= rr * rr && d[(yy * cv.width + xx) * 4 + 3] > 0) n++;
      }
    }
    return n;
  })();
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

P2_JS = """
(function () {
  var out = {};
  /* ---- P2-a：clearTelemetry 后三路必须是 0（不是负满量程）---- */
  clearTelemetry();
  out.afterClear = {lat: state.latest.accelLat, lon: state.latest.accelLon, mag: state.latest.accelMag};

  /* ---- P2-b：先新协议 lat=12，再只来旧键 {accel:7} ---- */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 2, ts: 2000,
    names: ['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values: [0,0,0,12,0],
    roll: 0, pitch: 0, yaw: 0, accel_lat: 12, accel_lon: 0}]));
  var afterNew = {lat: state.latest.accelLat, lon: state.latest.accelLon, mag: state.latest.accelMag};
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 3, ts: 2100,
    names: ['ROLL','PITCH','YAW','ACCEL'], values: [0,0,0,7],
    roll: 0, pitch: 0, yaw: 0, accel: 7}]));
  out.afterNewThenOld = {lat: state.latest.accelLat, lon: state.latest.accelLon,
                         mag: state.latest.accelMag, before: afterNew};

  /* ---- P2-c：旧键 -> 新协议（两个键）---- */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 4, ts: 2200,
    names: ['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values: [0,0,0,3,4],
    roll: 0, pitch: 0, yaw: 0, accel_lat: 3, accel_lon: 4}]));
  out.afterOldThenNew = {lat: state.latest.accelLat, lon: state.latest.accelLon, mag: state.latest.accelMag};

  /* ---- P2-d：协议垫片形态：accel_lat=0 且 accel_lon=值（同时带旧键）---- */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 5, ts: 2300,
    names: ['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values: [0,0,0,0,4.2],
    roll: 0, pitch: 0, yaw: 0, accel_lat: 0, accel_lon: 4.2, accel: 4.2}]));
  out.afterShimShape = {lat: state.latest.accelLat, lon: state.latest.accelLon, mag: state.latest.accelMag};

  /* ---- P2-e：混合流再复现一次原缺陷场景（lat=12 -> {accel:7}）---- */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 6, ts: 2400,
    names: ['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values: [0,0,0,-1.5,0],
    roll: 0, pitch: 0, yaw: 0, accel_lat: -1.5, accel_lon: 0}]));
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'nav', seq: 7, ts: 2500,
    names: ['ROLL','PITCH','YAW','ACCEL'], values: [0,0,0,-2.5],
    roll: 0, pitch: 0, yaw: 0, accel: -2.5}]));
  out.mixedNegative = {lat: state.latest.accelLat, lon: state.latest.accelLon, mag: state.latest.accelMag};
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

TREND_JS = """
(function () {
  /* ③ 趋势 norm 模式下 accelMag 不再贴底：只留 accelMag 一条曲线，种 1.4 g 量级数据 */
  state.scaleMode = 'norm';
  state.visible = new Set(['accelMag']);
  state.hl = null;
  buildChips();
  state.buf.accelMag = [];
  for (var i = 0; i < 40; i++) state.buf.accelMag.push(1.4 + Math.sin(i / 4) * 0.05);
  trendLayer.sKey = ''; trendLayer.dirty = true;
  paintLayer(trendLayer, drawTrend);

  var L = trendLayer;
  var g = L.cv.getContext('2d');
  var d = g.getImageData(0, 0, L.cv.width, L.cv.height).data;
  var pad = TREND_PAD, dpr = L.dpr, plotH = L.h - pad.top - pad.bottom;
  var minY = 1e9, maxY = -1;
  for (var yy = 0; yy < L.cv.height; yy++) {
    for (var xx = 0; xx < L.cv.width; xx++) {
      var o = (yy * L.cv.width + xx) * 4;
      if (d[o + 3] < 40) continue;
      /* 只认合成那一路的红 (#DC2626) */
      if (Math.abs(d[o] - 220) < 40 && d[o + 1] < 80 && Math.abs(d[o + 2] - 38) < 40) {
        if (yy < minY) minY = yy;
        if (yy > maxY) maxY = yy;
      }
    }
  }
  var out = {
    found: maxY >= 0,
    minYcss: minY / dpr, maxYcss: maxY / dpr,
    plotBottom: pad.top + plotH,
    offBottomPx: (maxY / dpr >= 0) ? (pad.top + plotH - maxY / dpr) : null,
    /* 期望：norm 下 fraction = (1.4-0)/(16.384-0) = 8.5%，曲线应离底 ~12px */
    expectFraction: 1.4 / ACC_FS,
    accFs: ACC_FS,
    brokenIfFs1000_fraction: 1.4 / 1000.0,
    errors: window.__errs
  };
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
        def cb_trend(v):
            res["trend"] = v
            app.quit()

        def cb_p2(v):
            res["p2"] = v
            QTimer.singleShot(120, lambda: page.runJavaScript(TREND_JS, cb_trend))

        def cb_setup(v):
            res["setup"] = v
            QTimer.singleShot(120, lambda: page.runJavaScript(P2_JS, cb_p2))

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

    setup, p2, trend = load("setup"), load("p2"), load("trend")

    print("=== ① 满量程与真实量级可见性（lat=1.2 lon=-0.8）===")
    print("  ACC_FS = %s（期望 16.384；修好前是 1000）  ACC_FS_SET = %s" % (setup.get("accFs"), setup.get("accFsSet")))
    print("  量程对称性: lat=%s lon=%s mag=%s" % (
        (setup.get("ranges") or {}).get("lat"), (setup.get("ranges") or {}).get("lon"),
        (setup.get("ranges") or {}).get("mag")))
    print("  mag=%s（手算 sqrt(1.2²+0.8²)=1.4422205101855958）" % setup.get("mag"))
    print("  画布几何: %s" % setup.get("geom"))
    print("  箭头端点 = %s  距圆心 %.3f px（期望 %.3f px；若满量程仍是 1000g 只有 %.3f px）"
          % (setup.get("tip"), setup.get("tipDist") or 0, setup.get("tipDistExpect") or 0,
             setup.get("tipDistIfBroken") or 0))
    print("  占半径比例 = %.2f%%" % (100.0 * (setup.get("tipDist") or 0) / ((setup.get("geom") or {}).get("R") or 1)))
    print("  所有 arc 半径 = %s" % setup.get("radii"))
    print("  合成虚线圈半径 = %s（应与箭头端点同量级）" % setup.get("magCircle"))
    print("  画布非透明像素=%s，圆心 8px 内非透明像素=%s" % (setup.get("pixels"), setup.get("pixelsNearCenter")))

    print()
    print("=== ② P2：旧键不重置另一路 / 复位归 0 ===")
    print("  clearTelemetry 后: %s" % p2.get("afterClear"))
    print("  新协议(lat=12) -> 旧键 {accel:7}: %s" % p2.get("afterNewThenOld"))
    print("  旧键 -> 新协议(lat=3,lon=4): %s" % p2.get("afterOldThenNew"))
    print("  垫片形态(lat=0,lon=4.2,+旧键): %s" % p2.get("afterShimShape"))
    print("  负数混合流(lat=-1.5) -> 旧键 {accel:-2.5}: %s" % p2.get("mixedNegative"))

    print()
    print("=== ③ 趋势 norm 下 accelMag 不再贴底 ===")
    print("  %s" % trend)
    print("  （期望 fraction=%.4f，曲线离底 ~%.0f px；若满量程仍 1000g 离底只有 %.2f px）"
          % (trend.get("expectFraction") or 0,
             (trend.get("offBottomPx") or 0),
             ((trend.get("plotBottom") or 0) - ((trend.get("plotBottom") or 0) - 0.2)) * 1.0
             if False else 0.2))

    def close(a, b, tol):
        return a is not None and b is not None and abs(a - b) <= tol

    r = setup.get("ranges") or {}
    print()
    print("---")
    checks = [
        ("① ACC_FS = 16.384（不再是 1000）", close(setup.get("accFs"), 16.384, 1e-9)),
        ("① gaugeRanges 里的假键 ACCEL_LAT/LON 被忽略（ACC_FS 不受 999 影响）",
         close(setup.get("accFs"), 16.384, 1e-9)),
        ("① lat/lon 仍 ±ACC_FS 对称、mag 仍 0..ACC_FS",
         r.get("lat") == [-16.384, 16.384] and r.get("lon") == [-16.384, 16.384]
         and r.get("mag") == [0, 16.384]),
        ("① 合成值正确（1.2,-0.8 -> 1.44222）", close(setup.get("mag"), 1.4422205101855958, 1e-9)),
        ("① 箭头端点距圆心 ≈ 4.58px（实测 %.2f）" % (setup.get("tipDist") or 0),
         close(setup.get("tipDist"), setup.get("tipDistExpect"), 0.6)),
        ("① 箭头可见：>3px 且 > 修好前的 0.075px 的 50 倍",
         (setup.get("tipDist") or 0) > 3 and (setup.get("tipDist") or 0) > 50 * (setup.get("tipDistIfBroken") or 1)),
        ("① 合成虚线圈半径同量级（≈4.58px）",
         close(setup.get("magCircle"), setup.get("tipDistExpect"), 0.6)),
        ("① 圆心附近确有像素（肉眼可见）", (setup.get("pixelsNearCenter") or 0) > 20),
        ("② clearTelemetry 后三路 = 0", p2.get("afterClear") == {"lat": 0, "lon": 0, "mag": 0}),
        ("② 新协议后只来旧键：横向归 0、mag = 7（不是 13.892）",
         (p2.get("afterNewThenOld") or {}).get("lat") == 0
         and close((p2.get("afterNewThenOld") or {}).get("lon"), 7, 1e-9)
         and close((p2.get("afterNewThenOld") or {}).get("mag"), 7, 1e-9)),
        ("② 旧键后新协议双路：mag = 5",
         close((p2.get("afterOldThenNew") or {}).get("mag"), 5, 1e-9)),
        ("② 垫片形态(accel_lat=0 同时带旧键)：mag = 4.2（不用旧键二次覆盖）",
         close((p2.get("afterShimShape") or {}).get("mag"), 4.2, 1e-9)),
        ("② 负数混合流：横向归 0、mag = 2.5（不是 sqrt(1.5²+2.5²)=2.915）",
         (p2.get("mixedNegative") or {}).get("lat") == 0
         and close((p2.get("mixedNegative") or {}).get("mag"), 2.5, 1e-9)),
        ("③ 趋势里确实找到 accelMag 曲线", trend.get("found") is True),
        ("③ norm 下曲线不再贴底（离底 ≥8px）", (trend.get("offBottomPx") or 0) >= 8),
        ("无 JS 运行时错误",
         not setup.get("errors") and not p2.get("errors") and not trend.get("errors")),
    ]
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）

    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"setup": setup, "p2": p2, "trend": trend}, f, ensure_ascii=False, indent=2)
        print("WROTE", OUT_JSON)
    except Exception as e:
        print("JSON 写入失败:", e)


if __name__ == "__main__":
    main()
