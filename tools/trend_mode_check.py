# -*- coding: utf-8 -*-
"""trend_mode_check.py —— 验证趋势图三档量程（归一化 / 自适应 / 分组分带）

为什么要有这个
--------------
趋势图的量程映射是最容易「画得出来但画错」的地方：
* 归一化：每条按注册量程映射到 0–100%，注册量程不准时曲线会贴底变平线；
* 自适应：每条按窗口内自身 min/max 独立缩放，细节最大；
* 分组分带：P0/P1/P2 各占一条带，量纲差一个数量级也不会互相压扁。

这里直接读 canvas 像素来断言「三种模式都真的画出了曲线」，
并且断言分带模式确实规划出了多条带 —— 只看 JS 不报错是不够的。

用法
----
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\Scripts\\python.exe tools\\trend_mode_check.py
"""
import os
import sys
import json

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --no-sandbox --disable-software-rasterizer")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QUrl, QTimer                      # noqa: E402
from PyQt6.QtWidgets import QApplication                   # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView        # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JS = r"""
(function () {
  window.__errs = [];
  window.addEventListener('error', function (e) { window.__errs.push(e.message); });

  var appEl = document.getElementById('app');
  appEl.style.width = '1440px';
  appEl.style.height = '860px';

  document.getElementById('demoMode').checked = true;
  setDemo(true);
  /* 离屏 rAF 不跑，手动推进模拟数据把 30s 缓冲填满 */
  for (var i = 0; i < 260; i++) simStep(0.05);

  /* 默认只开了 4 条 P2 曲线，分带就只有一条带；这里补开 P0/P1 各一条。
     ★ 加速度信号 id 随协议变过：老协议叫 accelRaw，2026-10-15 起拆成
       accelLat（横向）/ accelLon（纵向）/ accelMag（合成模）。
       这里按**当前实际注册的信号**挑一个，避免把不存在的 id 塞进 state.visible ——
       悬空 id 会让主循环在 state.buf[id].length 上抛异常，整块画布直接什么都不画
       （那会表现成"量程模式坏了"，其实是被测试脚本的旧 id 带崩的）。 */
  var want = ['adc0', 'roll'];
  ['accelMag', 'accelLat', 'accelLon', 'accelRaw'].forEach(function (k) {
    if (typeof SIGS !== 'undefined' && SIGS[k] && want.indexOf(k) < 0) want.push(k);
  });
  want.forEach(function (k) { state.visible.add(k); });

  var out = {modes: {}, errors: []};
  ['norm', 'auto', 'band'].forEach(function (m) {
    state.scaleMode = m;
    trendLayer.sKey = '';
    paintLayer(trendLayer, drawTrend);
    var cv = trendLayer.cv, ctx = cv.getContext('2d');
    var d = ctx.getImageData(0, 0, cv.width, cv.height).data;
    var n = 0;
    for (var p = 3; p < d.length; p += 4) if (d[p] > 8) n++;
    out.modes[m] = {pixels: n, w: cv.width, h: cv.height};
  });

  var ids = Array.from(state.visible);
  out.bands = bandPlan(ids).map(function (b) {
    return {group: b.group, n: b.ids.length, lo: +b.lo.toFixed(3), hi: +b.hi.toFixed(3)};
  });
  out.perCurveKeys = Object.keys(perCurveRange(ids)).length;
  out.visible = ids.length;

  /* 按钮三档循环 */
  var btn = document.getElementById('normBtn');
  var labels = [];
  for (var b2 = 0; b2 < 4; b2++) { btn.click(); labels.push(btn.textContent); }
  out.btnCycle = labels;
  out.errors = window.__errs.slice(0, 6);
  return JSON.stringify(out);
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1440, 860)
    view.show()          # ★ 不 show 的话 WebEngine 不合成，画布尺寸永远是 0 -> 假故障
    result = {}

    def on_load(ok):
        if not ok:
            result["load"] = False
            app.quit()
            return
        QTimer.singleShot(1500, lambda: view.page().runJavaScript(JS, grab))

    def grab(v):
        result["data"] = v
        app.quit()

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(40000, app.quit)
    app.exec()

    try:
        d = json.loads(result.get("data") or "{}")
    except Exception:
        d = {"raw": result.get("data")}

    print("=" * 62)
    print("趋势图三档量程 · 离屏（#app 强制 1440x860）")
    print("=" * 62)

    problems = []
    if d.get("errors"):
        problems.append("页面报错: %s" % d["errors"])

    modes = d.get("modes") or {}
    for m in ("norm", "auto", "band"):
        info = modes.get(m) or {}
        px = info.get("pixels", 0)
        print("  %-5s 画布 %sx%s  非透明像素 %s" %
              (m, info.get("w"), info.get("h"), px))
        if px < 2000:
            problems.append("%s 模式几乎没画出东西（像素 %s）" % (m, px))

    bands = d.get("bands") or []
    print("\n  分带规划：")
    for b in bands:
        print("    %-3s %s 条曲线  量程 %s ~ %s" %
              (b.get("group"), b.get("n"), b.get("lo"), b.get("hi")))
    if len(bands) < 2:
        problems.append("分带模式只规划出 %d 条带（应 >=2，否则等于没分）" % len(bands))
    if d.get("perCurveKeys", 0) < 2:
        problems.append("自适应模式没有为每条曲线算出独立量程")

    print("\n  量程按钮循环：%s" % " → ".join(d.get("btnCycle") or []))
    cyc = d.get("btnCycle") or []
    if len(set(cyc)) < 3:
        problems.append("按钮没有在三个档位之间循环：%s" % cyc)

    print()
    if problems:
        for p in problems:
            print("  [FAIL]", p)
        return 1
    print("  [OK] 三档量程都能正常绘制，分带规划与按钮循环正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
