# -*- coding: utf-8 -*-
"""task-2 专项验证：运行期推送改了通道名的 uiConfig 后，
「数据可视化」区（#visChannels）与已拖入图表（vcharts）的标签是否同步。

验证三条链路（都是真实调用路径，不是直接改 DOM）：
  ① #adcGrid 卡片名        —— 改前就该好（对照组）
  ② #visChannels 通道列表  —— 本次修复点
  ③ 已拖入图表的信号标签    —— 图表信号 chip(DOM) + canvas 左上角图例(绘制)

  ③ 的 canvas 图例无法用 textContent 读，所以钩住
  CanvasRenderingContext2D.prototype.fillText 记录所有绘制文字，再查新名字。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\python.exe tools\_vischan_sync_check.py
"""
import os
import sys
import json

# ★ 控制台编码自保护（task-8 F8）：Windows GBK 控制台下，print 里出现 ⏸/▶/★ 这类字符会
# 抛 UnicodeEncodeError 直接崩进程；而且崩在打印阶段会让“后面才写 JSON”的取证代码根本执行不到。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

MARK = "同步校验通道ZZ"          # 新通道名里塞一个只可能来自配置的标记


class Probe(QWebEnginePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = []

    def javaScriptConsoleMessage(self, level, msg, line, source):
        self.console.append("[%s] %s (line %d)" % (level.name, msg, line))


PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  window.addEventListener('unhandledrejection', function (e) {
    window.__errs.push('promise: ' + e.reason);
  });
})();
"""

# ---- 第一幕：先把 adc0 弄进图表 + 造点数据 + 进入「可视化」页 + 装 fillText 探针
PHASE_A_JS = """
(function () {
  var out = {};
  function itemText(id) {
    var el = document.querySelector('#visChannels .vc-item[data-sig="' + id + '"]');
    return el ? el.querySelectorAll('span')[1].textContent : '(不存在)';
  }
  function firstCard() {
    var c = document.getElementById('adcGrid');
    return (c && c.children.length) ? c.children[0].querySelector('.a-name').textContent : '(无)';
  }
  out.beforeVisChan  = itemText('adc0');
  out.beforeAdcCard  = firstCard();

  // 走真实的拖拽路径把 adc0 拖进第一个图表
  window.__dropMode = 'none';
  try {
    var item = document.querySelector('#visChannels .vc-item[data-sig="adc0"]');
    var card = document.querySelector('#visGrid .vchart');
    var dt = new DataTransfer();
    item.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));
    card.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true, cancelable: true}));
    card.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    window.__dropMode = (vcharts[0].sigs.indexOf('adc0') >= 0) ? 'realdrop' : 'drop-noop';
  } catch (e) {
    window.__dropMode = 'err:' + (e && e.message);
  }
  if (vcharts[0].sigs.indexOf('adc0') < 0) {
    vcharts[0].sigs.push('adc0');
    updateVChartSigs(vcharts[0]);
    window.__dropMode += '+manual';   // 探针降级，不影响待验证的结论
  }

  // 造 ≥2 个点，否则 drawVChart 会提前 return（曲线/图例都不画）
  state.buf['adc0'] = [1, 2, 3, 4, 5, 6, 7, 8];
  state.hasData.adc = true;

  // 进「可视化」页（导航第 2 项），让 zoneVisible('zoneVisual') 为真
  var rail = document.querySelectorAll('.rail-item');
  if (rail.length > 1) rail[1].click();
  out.zoneVisible = zoneVisible('zoneVisual');

  // canvas 绘制文字探针
  window.__fill = [];
  if (!window.__fillHooked) {
    window.__fillHooked = true;
    var _ft = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (t) {
      window.__fill.push(String(t));
      return _ft.apply(this, arguments);
    };
  }
  out.chartSigs = vcharts[0].sigs.slice();
  out.chartChip = (function () {
    var c = document.querySelector('#visGrid .vc-sig[data-sig="adc0"]');
    return c ? c.textContent : '(无)';
  })();
  out.dropMode = window.__dropMode;
  out.layer = {w: vcharts[0].layer.w, h: vcharts[0].layer.h, ok: vcharts[0].layer.ok, dirty: vcharts[0].layer.dirty};
  out.frozen = state.frozen;
  out.errors = window.__errs || [];
  return JSON.stringify(out);
})()
"""

# ---- 第二幕：推送改名后的配置（真实信号处理函数），然后读所有标签
PHASE_B_JS = """
(function () {
  var out = {};
  function itemText(id) {
    var el = document.querySelector('#visChannels .vc-item[data-sig="' + id + '"]');
    return el ? el.querySelectorAll('span')[1].textContent : '(不存在)';
  }
  out.afterVisChan = itemText('adc0');
  var c = document.getElementById('adcGrid');
  out.afterAdcCard = (c && c.children.length) ? c.children[0].querySelector('.a-name').textContent : '(无)';
  var chip = document.querySelector('#visGrid .vc-sig[data-sig="adc0"]');
  out.afterChartChip = chip ? chip.textContent : '(无)';
  out.afterChartSigs = vcharts[0].sigs.slice();
  // canvas 图例：drawVChart 用 SIGS[id].label 调 fillText
  out.canvasLegendHit = (window.__fill || []).some(function (t) { return t.indexOf('同步校验通道ZZ') >= 0; });
  out.fillCount = (window.__fill || []).length;
  out.canvasFills = (window.__fill || []).filter(function (t) { return t.indexOf('同步校验通道ZZ') >= 0; }).slice(0, 6);
  out.layer = {w: vcharts[0].layer.w, h: vcharts[0].layer.h, ok: vcharts[0].layer.ok};
  out.errors = window.__errs || [];
  return JSON.stringify(out);
})()
"""


def new_config():
    import telemetry_config as CFG
    cfg = CFG.ui_config()
    cfg["adc"][0] = dict(cfg["adc"][0])
    cfg["adc"][0]["name"] = MARK
    return cfg


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = Probe(view)
    view.setPage(page)

    s = QWebEngineScript()
    s.setName("errprobe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    cfg = new_config()
    apply_js = ("(function(){ window.__fill = []; try { applyUiConfig(%s); } "
                "catch(e){ window.__errs.push('applyUiConfig: ' + e.message); } "
                "return 'applied'; })()" % json.dumps(cfg, ensure_ascii=False))

    res = {}

    def on_load(ok):
        def cb_a(v):
            res["a"] = v
            # 让主循环画几帧，确认「改前」图例是旧名字
            QTimer.singleShot(400, lambda: page.runJavaScript(apply_js))
            QTimer.singleShot(1400, lambda: page.runJavaScript(PHASE_B_JS, cb_b))

        def cb_b(v):
            res["b"] = v
            app.quit()

        QTimer.singleShot(3500, lambda: page.runJavaScript(PHASE_A_JS, cb_a))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()   # ★ 不 show 的话 WebEngine 不合成，canvas 尺寸永远是 0 -> 假故障
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    expect = str(cfg["adc"][0].get("short")) + " " + MARK
    a = json.loads(res.get("a", "{}"))
    b = json.loads(res.get("b", "{}"))
    print("=== 改前（初始配置）===")
    print("  #visChannels adc0 标签 :", a.get("beforeVisChan"))
    print("  #adcGrid 首卡名        :", a.get("beforeAdcCard"))
    print("  图表 chip              :", a.get("chartChip"))
    print("  拖拽方式               :", a.get("dropMode"))
    print("  zoneVisible(zoneVisual):", a.get("zoneVisible"))
    print("  chart layer            :", a.get("layer"), "frozen=", a.get("frozen"))
    print("=== 改后（推入 name=%s，期望标签=%s）===" % (MARK, expect))
    print("  #visChannels adc0 标签 :", b.get("afterVisChan"))
    print("  #adcGrid 首卡名        :", b.get("afterAdcCard"))
    print("  图表 chip              :", b.get("afterChartChip"))
    print("  canvas 图例含新名      :", b.get("canvasLegendHit"))
    print("  canvas fillText 总数   :", b.get("fillCount"))
    print("  canvas 命中新名的文字  :", b.get("canvasFills"))
    print("  layer(改后)            :", b.get("layer"))
    print("  JS 错误(a/b)           :", a.get("errors"), b.get("errors"))

    checks = [
        ("② #visChannels 通道名已同步", b.get("afterVisChan") == expect),
        ("② 且确实变了（非改前旧值）", a.get("beforeVisChan") != b.get("afterVisChan")),
        ("① #adcGrid 卡片名已同步（对照）", b.get("afterAdcCard") == MARK),
        ("③ 图表信号 chip 已同步", b.get("afterChartChip") == expect),
        ("③ canvas 图例重绘出新名", b.get("canvasLegendHit") is True),
        ("全程无 JS 运行时错误", not a.get("errors") and not b.get("errors")),
    ]
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
