# -*- coding: utf-8 -*-
"""task-23 验收：decimals / bufPoints 防御性夹紧的"修前 vs 修后"实测。

★ 这个探针**不 import 任何后端模块**（直接加载 telemetry_web/index.html），
  所以在 telemetry_config.py 被编辑坏的时候也能跑，专门验前端的防御。

用法：<venv>\\python.exe tools\\_clamp_check.py
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

JS = """
(function () {
  var out = {};

  /* ---------- F1：原始 API 与"修前写法"确实会抛 ---------- */
  try { (1.234).toFixed(200); out.rawThrows = null; }
  catch (e) { out.rawThrows = e.name + ': ' + e.message; }
  var oldFmtSigVal = function (v, sig) { return v.toFixed(sig.fmt); };   /* 修前实现 */
  try { oldFmtSigVal(1.234, {fmt: 200}); out.oldThrows = null; }
  catch (e) { out.oldThrows = e.name + ': ' + e.message; }

  /* ---------- F1：修后 fmtSigVal 正常返回且位数被夹到 20 ---------- */
  out.newSig200 = fmtSigVal(1.234, {fmt: 200});
  out.newSig200Dec = (out.newSig200.split('.')[1] || '').length;
  out.newSigNaN = fmtSigVal(1.234, {fmt: 'abc'});      /* 非数字 -> 回退默认 2 */
  out.newSigNeg = fmtSigVal(1.234, {fmt: -7});         /* 负数 -> 0 */
  out.newSigBig = fmtSigVal(1.5, {fmt: 1e9});          /* 巨值 -> 20 */

  /* ---------- F1：灌一份 decimals=200 的 uiConfig，看面板/悬停/矢量图是否还更新 ---------- */
  var cfg = {
    version: 2, accRawFs: 16384,
    adc: [], nav: [], motor: [],
    gaugeRanges: {ACCEL: 16.384},
    ui: {}, csvHeader: '',
    serial: {baud_choices: [115200], default_baud: 115200}
  };
  for (var i = 0; i < 10; i++)
    cfg.adc.push({short: 'CH' + (i + 1), name: 'CH' + (i + 1), sensor: 'ADC',
                  unit: 'V', rng: 5, color: '#123456', min: 0, max: 4095, dec: 200});
  cfg.nav = [{name: 'ROLL', unit: '°', scale: 0.01, dec: 200},
             {name: 'PITCH', unit: '°', scale: 0.01, dec: 200},
             {name: 'YAW', unit: '°', scale: 0.01, dec: 200},
             {name: 'ACCEL_LAT', unit: '', scale: 0.001, dec: 200},
             {name: 'ACCEL_LON', unit: '', scale: 0.001, dec: 200}];
  cfg.motor = [{name: 'SPEED', unit: 'm/s', scale: 0.01, dec: 200},
               {name: 'MOTOR_RPM', unit: 'RPM', scale: 1, dec: 200},
               {name: 'TORQUE', unit: 'N·m', scale: 0.1, dec: 200}];
  window.__errs = [];
  try { applyUiConfig(cfg); } catch (e) { window.__errs.push('applyUiConfig: ' + e.message); }
  out.fmtAfterCfg = {roll: SIGS.roll.fmt, adc0: SIGS.adc0.fmt, accelLat: SIGS.accelLat.fmt};

  /* 真跑一遍三条读取路径：数值面板 / 数值格式化 / 矢量图 */
  state.visible = new Set(['roll', 'accelLat', 'accelMag']);
  buildChips();
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([
    {type: 'adc', seq: 1, ts: 1, values: [2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048]},
    {type: 'nav', seq: 1, ts: 1, names: ['ROLL', 'PITCH', 'YAW', 'ACCEL_LAT', 'ACCEL_LON'],
     values: [2.5, 1, 30, 1.2, -0.8], roll: 2.5, pitch: 1, yaw: 30, accel_lat: 1.2, accel_lon: -0.8}
  ]));
  var panelBefore = (document.querySelector('#sigPanel .sig-row .sig-val') || {}).textContent;
  try { updateDom(); } catch (e) { window.__errs.push('updateDom: ' + e.message); }
  try { updateSigPanel(); } catch (e) { window.__errs.push('updateSigPanel: ' + e.message); }
  try { accVecDirty = true; paintAccVec(); } catch (e) { window.__errs.push('paintAccVec: ' + e.message); }
  out.panelVal = (document.querySelector('#sigPanel .sig-row .sig-val') || {}).textContent;
  out.vecReadouts = [ (document.getElementById('accVecLat') || {}).textContent,
                      (document.getElementById('accVecMag') || {}).textContent ];
  out.adcCardVal = (document.querySelector('#adcGrid .acard [data-val]') || {}).textContent;
  out.fmtNumRoll = fmtNum('roll', 2.5);
  out.fmtNumAdc0 = fmtNum('adc0', 2048);
  out.fmtNumRollDec = (out.fmtNumRoll.split('.')[1] || '').length;
  out.errorsAfterDecimals200 = window.__errs.slice();

  /* ---------- F2：bufPoints 夹紧 ---------- */
  function bufAfter(v) {
    applyUiConfig({version: 2, accRawFs: 16384, adc: [], nav: [], motor: [],
                   gaugeRanges: {}, ui: {bufPoints: v}, csvHeader: '',
                   serial: {baud_choices: [115200], default_baud: 115200}});
    return window.BUF;
  }
  out.buf = {e8: bufAfter(1e8), huge: bufAfter(999999), zero: bufAfter(0),
             neg: bufAfter(-5), tiny: bufAfter(5), nan: bufAfter('abc'),
             normal: bufAfter(600), min: bufAfter(10), max: bufAfter(20000),
             over: bufAfter(20001)};
  /* 尾巴：夹紧后再灌一次 20000 并推 3 个点验证 splice 逻辑没被破坏 */
  out.bufAfterFeed = state.buf.roll ? state.buf.roll.length : -1;
  out.errors = window.__errs;
  return JSON.stringify(out);
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
        QTimer.singleShot(3200, lambda: page.runJavaScript(JS, cb))

    def cb(v):
        res["r"] = v
        app.quit()

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    r = json.loads(res.get("r") or "{}")
    print("=== F1 · 小数位夹紧 ===")
    print("  修前：原始 API (1.234).toFixed(200)      -> %s" % r.get("rawThrows"))
    print("  修前：老实现 v.toFixed(sig.fmt)          -> %s" % r.get("oldThrows"))
    print("  修后：fmtSigVal(1.234,{fmt:200})         -> %r（小数位 %s）"
          % (r.get("newSig200"), r.get("newSig200Dec")))
    print("  修后：fmt='abc' -> %r   fmt=-7 -> %r   fmt=1e9 -> %r"
          % (r.get("newSigNaN"), r.get("newSigNeg"), r.get("newSigBig")))
    print("  灌 decimals=200 的 uiConfig 后 SIGS.fmt: %s（已被夹到 20）" % r.get("fmtAfterCfg"))
    print("  三条读取路径仍工作: 面板=%r 矢量图读数=%s ADC 卡=%r  fmtNum(roll)=%r(位数%s)"
          % (r.get("panelVal"), r.get("vecReadouts"), r.get("adcCardVal"),
             r.get("fmtNumRoll"), r.get("fmtNumRollDec")))
    print("  灌 200 后累计 JS 错误 = %s" % r.get("errorsAfterDecimals200"))

    print()
    print("=== F2 · bufPoints 夹紧（BUF，上下限与后端一致 [10,20000]）===")
    print("  %s" % r.get("buf"))

    print()
    print("=== 全部 JS 错误 ===")
    print("  %s" % r.get("errors"))

    b = r.get("buf") or {}
    checks = [
        ("F1 修前确实会抛 RangeError（证明缺陷真实存在）",
         str(r.get("oldThrows") or "").startswith("RangeError")),
        ("F1 修后 fmtSigVal(200 位) 不抛且正常返回", isinstance(r.get("newSig200"), str)),
        ("F1 修后位数被夹到 20", r.get("newSig200Dec") == 20),
        ("F1 非数字位数回退默认（fmt='abc' -> 2 位）",
         (r.get("newSigNaN") or '').split('.')[-1].__len__() == 2),
        ("F1 负位数 -> 0 位（整数显示）", (r.get("newSigNeg") or '').find('.') < 0),
        ("F1 巨位数 -> 20", (r.get("newSigBig") or '').split('.')[-1].__len__() == 20),
        ("F1 配置里的 200 在 SIGS.fmt 上就被夹成 20",
         (r.get("fmtAfterCfg") or {}).get("roll") == 20
         and (r.get("fmtAfterCfg") or {}).get("adc0") == 20),
        ("F1 灌 200 后 updateDom/updateSigPanel/paintAccVec 全程无异常",
         not (r.get("errorsAfterDecimals200") or [])),
        ("F1 数值面板该帧仍在更新（非占位）",
         r.get("panelVal") not in (None, '', '—')),
        ("F1 矢量图读数仍刷新", all(x not in (None, '', '—') for x in (r.get("vecReadouts") or []))),
        ("F2 bufPoints=1e8 -> BUF=20000", b.get("e8") == 20000),
        ("F2 bufPoints=999999 -> 20000", b.get("huge") == 20000),
        ("F2 bufPoints=20001 -> 20000（上限）", b.get("over") == 20000),
        ("F2 bufPoints=20000 -> 20000（边界保留）", b.get("max") == 20000),
        ("F2 bufPoints=600 -> 600（正常值不动）", b.get("normal") == 600),
        ("F2 bufPoints=0 -> 默认 600", b.get("zero") == 600),
        ("F2 bufPoints=-5 -> 默认 600", b.get("neg") == 600),
        ("F2 bufPoints='abc' -> 默认 600", b.get("nan") == 600),
        ("F2 bufPoints=5 -> 下限 10", b.get("tiny") == 10),
        ("F2 bufPoints=10 -> 10（边界保留）", b.get("min") == 10),
        ("无 JS 运行时错误", not (r.get("errors") or [])),
    ]
    print()
    print("---")
    ok = True
    for n, g in checks:
        print(("  OK   " if g else "  FAIL ") + n)
        ok = ok and bool(g)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
