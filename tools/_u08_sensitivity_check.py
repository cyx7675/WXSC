# -*- coding: utf-8 -*-
"""证明 web_ui_selfcheck 新增的 U08 断言**不是空跑**：把 buildVisChannels 换成 no-op
（= 需求3 修复前的行为：applyUiConfig 只重建卡片与 chips，漏掉 #visChannels），
断言必须变红；恢复后再跑必须变绿。

用法：<venv>\\python.exe tools\\_u08_sensitivity_check.py
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
from PyQt6.QtWebEngineCore import QWebEnginePage

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def main():
    import telemetry_config as CFG
    cfg = CFG.ui_config()
    cfg["adc"].append(dict(short="CH%d" % (CFG.ADC_COUNT + 1), name="油温传感器",
                           sensor="温度", unit="°C", rng=150.0, color="#111827",
                           min=0, max=4095))
    apply_js = ("(function(){ applyUiConfig(%s); "
                "return JSON.stringify(Array.prototype.map.call("
                "document.querySelectorAll('#visChannels .vc-item'),"
                "function(el){return {id:el.getAttribute('data-sig'),t:el.textContent.trim()};})); })()"
                % json.dumps(cfg, ensure_ascii=False))

    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def on_load(ok):
        def cb_sabotaged(v):
            res["sabotaged"] = v
            # 恢复真实实现，再走一遍
            page.runJavaScript("(function(){ buildVisChannels = window.__realBVC; "
                               "return 'ok'; })()",
                               lambda _: QTimer.singleShot(
                                   200, lambda: page.runJavaScript(apply_js, cb_normal)))

        def cb_normal(v):
            res["normal"] = v
            app.quit()

        QTimer.singleShot(3200, lambda: page.runJavaScript(
            # ① 模拟修复前：applyUiConfig 不会再调 buildVisChannels
            "(function(){ window.__realBVC = buildVisChannels; buildVisChannels = function(){};"
            " return 'ok'; })()",
            lambda _: QTimer.singleShot(
                200, lambda: page.runJavaScript(apply_js, cb_sabotaged))))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    def adc_items(lst):
        return [x for x in lst if str(x.get("id", "")).startswith("adc")]

    sab, nor = json.loads(res.get("sabotaged") or "[]"), json.loads(res.get("normal") or "[]")
    print("=== U08 断言灵敏度（把 buildVisChannels 变 no-op = 修复前行为）===")
    print("  修复前模拟: 总 %d 条 / ADC 段 %d 条，含「油温传感器」= %s"
          % (len(sab), len(adc_items(sab)), any("油温传感器" in x["t"] for x in sab)))
    print("     前 3 条: %s" % [x["t"] for x in sab[:3]])
    print("  真实代码  : 总 %d 条 / ADC 段 %d 条，含「油温传感器」= %s"
          % (len(nor), len(adc_items(nor)), any("油温传感器" in x["t"] for x in nor)))
    print("     前 3 条: %s" % [x["t"] for x in nor[:3]])
    print("  （#visChannels 列的是**全部**信号：ADC + NAV + MOTOR + 派生 accel_mag，"
          "所以数量看 ADC 段，不看总数）")
    checks = [
        ("模拟修复前 -> 断言会 FAIL（列表停在旧名字、少了新通道）",
         len(adc_items(sab)) == CFG.ADC_COUNT and not any("油温传感器" in x["t"] for x in sab)),
        ("真实代码   -> 断言会 PASS（列表已同步到新配置）",
         len(adc_items(nor)) == CFG.ADC_COUNT + 1 and any("油温传感器" in x["t"] for x in nor)),
    ]
    ok = True
    for n, g in checks:
        print(("  OK   " if g else "  FAIL ") + n)
        ok = ok and bool(g)
    print("结果:", "新断言确实能抓到该 bug（非空跑）" if ok else "灵敏度验证失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须退非零


if __name__ == "__main__":
    main()
