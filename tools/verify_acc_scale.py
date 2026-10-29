# -*- coding: utf-8 -*-
"""
verify_acc_scale.py —— 验证「改了 ACCEL 的 scale，界面不会显示成 0」
======================================================================
起因：app.js 里曾写死 `Math.round(accelRaw)` 和满量程 ±16384。
     一旦把 telemetry_config.py 里 ACCEL 的 scale 改成 1e-05 这类小系数，
      真实值约 0.02，四舍五入后界面**永远显示 0**，加速度条也不动 ——
      而自检、录制全都绿的，只有肉眼看界面才发现。

本脚本把配置和界面串起来验证：
    取 telemetry_config.py 里真实的 ACCEL scale
      → 造一个典型原始码
      → 喂进界面
      → 读回 accNum 显示的文字和加速度条长度
      → 断言：不是 "0"、不是 "—"、条长不为 0

用法：
    set QT_QPA_PLATFORM=offscreen
    python tools\\verify_acc_scale.py
"""
import os
import sys
import json

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --no-sandbox --disable-software-rasterizer")

from PyQt6.QtCore import QUrl, QTimer                       # noqa: E402
from PyQt6.QtWidgets import QApplication                    # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView         # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import telemetry_config as CFG                              # noqa: E402

PAGE = os.path.join(HERE, "telemetry_web", "index.html")

# 在页面里注入：套用真实配置 + 一个典型原始码，然后读回显示结果
JS = """
(function (cfg) {
  try {
    applyUiConfig(cfg);
    var raw = cfg.__testRaw;                       // 原始码（固件发的那个整数）
    var disp = raw * cfg.__testScale;              // 显示值 = 原始码 × scale
    state.latest.accelRaw = disp;
    state.hasData.nav = true;
    updateDom();
    var numEl = document.getElementById('accNum');
    var posEl = document.getElementById('accPos');
    var negEl = document.getElementById('accNeg');
    function txt(id){ var e = document.getElementById(id); return e ? e.textContent : null; }
    return JSON.stringify({
      scale: cfg.__testScale,
      raw: raw,
      display: disp,
      accDec: ACC_DEC,
      accFs: ACC_FS,
      accNumText: numEl ? numEl.textContent : null,
      posTransform: posEl ? (posEl.style.transform || '') : null,
      negTransform: negEl ? (negEl.style.transform || '') : null,
      /* ↓ 这三样是 v2.4 才加检查的：以前只验了数值，量程标签写死没人管 */
      accUnitText: txt('accUnit'),
      accLoText: txt('accScaleLo'),
      accHiText: txt('accScaleHi'),
      accChipText: txt('accChip'),
      speedSubText: txt('speedSub'),
      spdFs: SPD_FS
    });
  } catch (e) {
    return JSON.stringify({ error: String(e) });
  }
})(__CFG__);
"""


def main():
    # 从真实配置取加速度字段的 scale，保证测的就是用户当前的配置。
    # ★ 不再写死字段名 ACCEL：协议已把 ACCEL 拆成 ACCEL_LAT / ACCEL_LON，
    #   这里取**第一个** ACCEL* 字段（与 app.js 仪表取量程的规则一致）；老配置里就是 ACCEL。
    acc_name, scale = None, None
    for f in CFG.PROTOCOL[0x01]["fields"]:
        if str(f[0]).startswith("ACCEL"):
            acc_name, scale = f[0], float(f[3])
            break
    if scale is None:
        print("[FAIL] 配置里找不到任何 ACCEL* 字段（NAV 字段表：%s）"
              % [f[0] for f in CFG.PROTOCOL[0x01]["fields"]])
        return 1
    print("    （本次校验的加速度字段：%s，scale=%g）" % (acc_name, scale))

    cfg = CFG.ui_config()
    cfg["__testScale"] = scale
    cfg["__testRaw"] = -2048          # 一个有代表性的原始码（约 -0.6 g）
    js = JS.replace("__CFG__", json.dumps(cfg, ensure_ascii=False))

    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1440, 900)
    result = {}

    def on_load(ok):
        if not ok:
            result["load"] = False
            app.quit()
            return
        QTimer.singleShot(1200, lambda: view.page().runJavaScript(js, grab))

    def grab(v):
        result["probe"] = v
        app.quit()

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(PAGE))
    QTimer.singleShot(25000, app.quit)
    app.exec()

    print("── 加速度显示 · scale 端到端 ──────────────────────")
    print("  配置里的 ACCEL scale :", scale)
    print("  满量程 ACC_RAW_FS    :", CFG.ACC_RAW_FULLSCALE)

    if "probe" not in result or not result["probe"]:
        print("  [FAIL] 页面没返回结果", result.get("load"))
        return 1

    p = json.loads(result["probe"])
    if "error" in p:
        print("  [FAIL] 页面报错:", p["error"])
        return 1

    print("  原始码 %d → 显示值 %g" % (p["raw"], p["display"]))
    print("  小数位 %d，显示满量程 ±%g" % (p["accDec"], p["accFs"]))
    print("  界面 accNum 显示     :", repr(p["accNumText"]))
    print("  加速度条 transform   :", p["posTransform"], "/", p["negTransform"])

    problems = []
    if p["accNumText"] in (None, "—"):
        problems.append("界面没显示数值（可能 hasData 没置位）")
    else:
        try:
            shown = float(str(p["accNumText"]).replace("−", "-"))
        except ValueError:
            problems.append("显示值不是数字：%r" % p["accNumText"])
            shown = None
        if shown is not None:
            if shown == 0 and abs(p["display"]) > 1e-12:
                problems.append("⚠️ 显示成了 0 —— 就是 scale 太小被四舍五入掉的经典 bug")
            elif abs(shown - p["display"]) > abs(p["display"]) * 0.2 + 10 ** (-p["accDec"]):
                problems.append("显示值 %g 与期望 %g 差太多" % (shown, p["display"]))
    if not p["negTransform"] or "scaleX(0)" in (p["negTransform"] or ""):
        problems.append("加速度条没动（负向条 transform 为空或 0）")

    # ---------- 量程标签：以前写死 ±16384，改了 scale 却纹丝不动的那个 bug ----------
    want_fs = CFG.gauge_ranges().get("ACCEL")
    print("  界面加速度刻度      : %r / %r" % (p["accLoText"], p["accHiText"]))
    print("  界面单位后缀        : %r  角标：%r" % (p["accUnitText"], p["accChipText"]))

    for tag in ("accLoText", "accHiText"):
        lbl = str(p[tag] or "")
        num_txt = lbl.replace("−", "-").replace("+", "").strip()
        try:
            num_val = abs(float(num_txt))
        except ValueError:
            problems.append("%s 不是数字：%r" % (tag, lbl))
            continue
        if want_fs and abs(num_val - want_fs) > want_fs * 0.02:
            problems.append(
                "%s 写的是 %r，但按配置应该是 ±%g —— 量程标签没跟着 scale 变"
                % (tag, lbl, want_fs))
    unit_cfg = ""
    for f in CFG.PROTOCOL[0x01]["fields"]:
        if f[0] == "ACCEL":
            unit_cfg = CFG.UNITS.get("ACCEL", "")
    want_unit = " raw" if float(scale) == 1.0 else ((" " + unit_cfg) if unit_cfg else "")
    if (p["accUnitText"] or "").strip() != want_unit.strip():
        problems.append("单位后缀 %r 与配置 %r 不一致" % (p["accUnitText"], want_unit))

    # ---------- 车速量程文案 ----------
    want_spd = CFG.gauge_ranges().get("SPEED")
    print("  界面车速量程文案    : %r" % (p["speedSubText"],))
    if want_spd:
        if abs(float(p["spdFs"]) - float(want_spd)) > 1e-9:
            problems.append("车速表盘量程 %g ≠ 配置 %g" % (p["spdFs"], want_spd))
        # 文案里必须出现配置里的满量程数字，否则就是写死的旧文案
        key = "%g" % want_spd
        if key not in str(p["speedSubText"]):
            problems.append(
                "车速副标题 %r 里找不到量程 %s —— 文案还是写死的"
                % (p["speedSubText"], key))

    print()
    if problems:
        for x in problems:
            print("  [FAIL]", x)
        return 1
    print("  [OK] 加速度在 scale=%g 下正常显示，没有被四舍五入吞掉" % scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
