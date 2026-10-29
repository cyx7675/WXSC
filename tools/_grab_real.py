# -*- coding: utf-8 -*-
"""task-29 追加验证：用 QScreen.grabWindow(winId) 抓**真 QtWebEngine 窗口**（OS 合成后）。

背景：QWebEngineView.grab() 拿不到网页内容（页面在独立渲染进程合成）；
但 QScreen.grabWindow(hwnd) 抓的是操作系统层面的窗口位图，**有可能**拿到真实观感。
不设 QT_QPA_PLATFORM（真窗口）。用法：<venv>\\python.exe tools\\_grab_real.py
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "tools", "_grab_real_qt.png")


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    state = {"n": 0}

    def grab(tag):
        win = view.window()
        scr = win.screen()
        if scr is None:
            print("FAIL: win.screen() 为 None（无显示设备）")
            app.quit()
            return
        pm = scr.grabWindow(win.winId())
        path = OUT if tag == "demo" else OUT.replace(".png", "_idle.png")
        ok = pm.save(path)
        # 采样：统计非白像素与"是否有彩色(UI)像素"，判断内容是否真的被抓到
        img = pm.toImage()
        nonwhite = 0
        colored = 0
        for y in range(0, img.height(), 7):
            for x in range(0, img.width(), 7):
                c = img.pixel(x, y)
                r = (c >> 16) & 255; g = (c >> 8) & 255; b = c & 255
                if r < 245 or g < 245 or b < 245:
                    nonwhite += 1
                if max(r, g, b) - min(r, g, b) > 24:
                    colored += 1
        print("%-5s winId=%s screen=%s 保存=%s 尺寸=%dx%d 非白采样=%d 彩色采样=%d -> %s"
              % (tag, int(win.winId()), scr.name(), ok, pm.width(), pm.height(),
                 nonwhite, colored, path))
        if tag == "idle":
            QTimer.singleShot(1500, lambda: (
                view.page().runJavaScript(
                    "(function(){var d=document.getElementById('demoMode'); if(d){d.checked=true;} setDemo(true); return 'ok';})()"),
                QTimer.singleShot(2500, lambda: grab("demo"))))
        else:
            app.quit()

    QTimer.singleShot(4000, lambda: grab("idle"))
    QTimer.singleShot(30000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
