# -*- coding: utf-8 -*-
"""一次性端到端验证：改 telemetry_config.py 后，exe 里的界面能不能跟着变。

做法：临时给所有通道名加一个前缀，用真实 Backend + QWebChannel 加载页面，
再看界面上 ADC 卡片的名字是不是带上了这个前缀。
若带上了，说明「配置 → 后端 uiConfig 信号 → 前端 applyUiConfig → DOM」整条链路通畅。
"""
import os, sys, json

# ★ 输出被重定向/管道时 Python 会用本地编码(cp936)写字节，撞上 ❌/→ 等字符会
#   UnicodeEncodeError 直接崩（EXIT≠0，且崩溃点在 print，验证结果全拿不到）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import telemetry_config as CFG

PREFIX = "【新】"
_orig_ui_config = CFG.ui_config


def patched_ui_config():
    c = _orig_ui_config()
    for a in c["adc"]:
        a["name"] = PREFIX + a["name"]
    return c


CFG.ui_config = patched_ui_config          # monkeypatch：模拟用户改了通道名

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import launcher_web as L

JS = """
JSON.stringify({
  cards: document.getElementById('adcGrid') ? document.getElementById('adcGrid').children.length : -1,
  firstName: (function(){
    var g = document.getElementById('adcGrid');
    return g && g.children.length ? g.children[0].querySelector('.a-name').textContent : '';
  })(),
  lastName: (function(){
    var g = document.getElementById('adcGrid');
    return g && g.children.length ? g.children[g.children.length-1].querySelector('.a-name').textContent : '';
  })(),
  chips: document.getElementById('chipBar') ? document.getElementById('chipBar').querySelectorAll('.chip').length : -1,
  errs: window.__errs || []
})
"""


def main():
    app = QApplication(sys.argv)
    win = L.MainWindow()          # 不 show：离屏即可，页面照常加载
    result = {}

    def grab(v):
        result["dom"] = v
        app.quit()

    win.view.loadFinished.connect(lambda ok: QTimer.singleShot(3000, lambda: win.view.page().runJavaScript(JS, grab)))
    QTimer.singleShot(20000, app.quit)
    app.exec()

    dom = result.get("dom")
    if not dom:
        print("RESULT: NO DOM (页面没加载完)")
        sys.exit(1)
    d = json.loads(dom)
    print("卡片数      :", d["cards"])
    print("首通道名    :", d["firstName"])
    print("末通道名    :", d["lastName"])
    print("趋势通道数  :", d["chips"])
    print("JS 错误     :", d["errs"])

    ok = (d["cards"] == 10 and d["firstName"].startswith(PREFIX)
          and d["lastName"].startswith(PREFIX) and not d["errs"])
    print("RESULT:", "配置已同步到界面 ✅" if ok else "配置未同步 ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
