# -*- coding: utf-8 -*-
"""task-25 测量：演示模式加速度量纲 —— 分清"无后端的静态路径"与"带真配置的路径"。

两段测量（同一页面）：
  ① 静态 demo（没有后端，= ux 截图那种加载方式）：读 ACC_SCALE/ACC_FS/单位，跑演示 5s 采样读数
  ② 灌**真实 uiConfig**（telemetry_config.ui_config()，等价于后端下发）后再跑 5s
→ 用同一套采样对比，判断 ±16384/RAW 到底是"截图方法的假象"还是真机也错。

采样内容：ACC_SCALE/ACC_RAW_FS/ACC_FS_SET/ACC_FS/ACC_UNIT、矢量图三路读数、
lat/lon/mag 的 min/max、|mag|>ACC_FS 的帧数（是否钉死/超量程）、sim.a / sim.speed / sim.roll 范围、
以及"箭头端点距圆心"的推算像素（R × mag/ACC_FS）。

用法：<venv>\\python.exe tools\\_demo_accel_check.py
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
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
})();
"""

# 打开演示 + 开始采样
START_JS = """
(function () {
  if (!window.__demoHooked) {
    window.__demoHooked = 1;
  }
  window.__st = {on: true, lat: [], lon: [], mag: [], sa: [], sp: [], ro: [], over: 0, frames: 0};
  window.__sample = function () {
    if (!window.__st || !window.__st.on) return;
    var st = window.__st;
    st.frames++;
    var L = state.latest;
    st.lat.push(L.accelLat); st.lon.push(L.accelLon); st.mag.push(L.accelMag);
    st.sa.push(sim.a); st.sp.push(sim.speed); st.ro.push(sim.roll);
    if (Math.abs(L.accelMag) > ACC_FS) st.over++;
    requestAnimationFrame(window.__sample);
  };
  requestAnimationFrame(window.__sample);
  var d = document.getElementById('demoMode');
  if (d) { d.checked = true; }
  setDemo(true);
  return 'ok';
})()
"""

READ_JS = """
(function () {
  function mm(a) {
    if (!a.length) return null;
    var mn = a[0], mx = a[0];
    for (var i = 1; i < a.length; i++) { if (a[i] < mn) mn = a[i]; if (a[i] > mx) mx = a[i]; }
    return [Math.round(mn * 1000) / 1000, Math.round(mx * 1000) / 1000];
  }
  var st = window.__st;
  var out = {
    accScale: ACC_SCALE, accRawFs: ACC_RAW_FS, accFsSet: ACC_FS_SET, accFs: ACC_FS,
    accUnit: ACC_UNIT, dec: ACC_DEC,
    sigMinMax: [SIGS.accelLat.min, SIGS.accelLat.max, SIGS.accelMag.max],
    domChip: (document.getElementById('accChip') || {}).textContent,
    domUnit: (document.getElementById('accUnit') || {}).textContent,
    domNum: (document.getElementById('accNum') || {}).textContent,
    vecChip: (document.getElementById('accVecChip') || {}).textContent,
    vecNote: (document.getElementById('accVecNote') || {}).textContent,
    vecLat: (document.getElementById('accVecLat') || {}).textContent,
    vecLon: (document.getElementById('accVecLon') || {}).textContent,
    vecMag: (document.getElementById('accVecMag') || {}).textContent,
    frames: st.frames,
    lat: mm(st.lat), lon: mm(st.lon), mag: mm(st.mag),
    simA: mm(st.sa), simSpeed: mm(st.sp), simRoll: mm(st.ro),
    over: st.over,
    /* 箭头端点推算像素：R × max|mag|/ACC_FS（R=画布半径），以及"是否钉死"（over>0 或 mag≡满量程） */
    arrowPxAtMax: (function () {
      var cv = document.getElementById('accVecCanvas');
      var R = cv ? Math.max(10, Math.min(cv.clientWidth, cv.clientHeight) / 2 - 14) : 48;
      var mx = 0;
      for (var i = 0; i < st.mag.length; i++) mx = Math.max(mx, Math.abs(st.mag[i]));
      return Math.round(R * Math.min(1, mx / ACC_FS) * 100) / 100;
    })(),
    errors: window.__errs
  };
  return JSON.stringify(out);
})()
"""

STOP_JS = "(function(){ if (window.__st) window.__st.on = false; setDemo(false); return 'ok'; })()"

APPLY_CFG_JS = """
(function () { applyUiConfig(%s); return JSON.stringify({accScale: ACC_SCALE, accFs: ACC_FS}); })()
"""


def main():
    import telemetry_config as CFG
    cfg_json = json.dumps(CFG.ui_config(), ensure_ascii=False)

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
        def cb_read_static(v):
            res["static"] = v
            # ② 停演示 -> 灌真配置 -> 再开演示采样
            page.runJavaScript(STOP_JS, lambda _: QTimer.singleShot(
                400, lambda: page.runJavaScript(APPLY_CFG_JS % cfg_json, cb_cfg)))

        def cb_cfg(v):
            res["cfg_apply"] = v
            QTimer.singleShot(200, lambda: page.runJavaScript(
                START_JS, lambda _: QTimer.singleShot(
                    27000, lambda: page.runJavaScript(READ_JS, cb_read_cfg))))

        def cb_read_cfg(v):
            res["withcfg"] = v
            page.runJavaScript(STOP_JS, lambda _: app.quit())

        # ★ 27s 采样：演示的"单圈"是 36s，转向只在 u∈[20,26]/[30,33] 出现
        #   （前 9s roll=0 → 横向恒 0），只采 5s 会误判"箭头不动"。
        QTimer.singleShot(3200, lambda: page.runJavaScript(
            START_JS, lambda _: QTimer.singleShot(
                27000, lambda: page.runJavaScript(READ_JS, cb_read_static))))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(90000, app.quit)
    app.exec()

    def load(k):
        try:
            return json.loads(res.get(k) or "{}")
        except Exception:
            return {}

    s0, cfg_apply, s1 = load("static"), load("cfg_apply"), load("withcfg")

    def dump(tag, r):
        print("=== %s ===" % tag)
        print("  ACC_SCALE=%s  ACC_RAW_FS=%s  ACC_FS_SET=%s  ACC_FS=%s  ACC_UNIT=%r  ACC_DEC=%s"
              % (r.get("accScale"), r.get("accRawFs"), r.get("accFsSet"), r.get("accFs"),
                 r.get("accUnit"), r.get("dec")))
        print("  SIGS.accelLat min/max=%s  accelMag.max=%s" % (r.get("sigMinMax")[:2], r.get("sigMinMax")[2:]))
        print("  卡片: chip=%r unit=%r accNum=%r   矢量图: chip=%r note=%r"
              % (r.get("domChip"), r.get("domUnit"), r.get("domNum"), r.get("vecChip"), r.get("vecNote")))
        print("  矢量图读数: LAT=%r LON=%r MAG=%r" % (r.get("vecLat"), r.get("vecLon"), r.get("vecMag")))
        print("  采样 %s 帧  lat=%.3f..%.3f  lon=%.3f..%.3f  mag=%.3f..%.3f  |mag|>满量程=%s 帧"
              % (r.get("frames"), (r.get("lat") or [0, 0])[0], (r.get("lat") or [0, 0])[1],
                 (r.get("lon") or [0, 0])[0], (r.get("lon") or [0, 0])[1],
                 (r.get("mag") or [0, 0])[0], (r.get("mag") or [0, 0])[1], r.get("over")))
        print("  仿真物理量: sim.a=%.2f..%.2f m/s²  speed=%.1f..%.1f m/s  roll=%.1f..%.1f °"
              % ((r.get("simA") or [0, 0])[0], (r.get("simA") or [0, 0])[1],
                 (r.get("simSpeed") or [0, 0])[0], (r.get("simSpeed") or [0, 0])[1],
                 (r.get("simRoll") or [0, 0])[0], (r.get("simRoll") or [0, 0])[1]))
        print("  箭头端点推算像素（最大幅值时）= %s px" % r.get("arrowPxAtMax"))

    dump("① 静态 demo（无后端，= ux 截图那种加载）", s0)
    print()
    print("   灌真配置后的即时值: %s" % cfg_apply)
    print()
    dump("② 同一页面 + 真实 uiConfig（等价后端下发）后再跑 demo", s1)


if __name__ == "__main__":
    main()
