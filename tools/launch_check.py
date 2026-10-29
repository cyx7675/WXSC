# -*- coding: utf-8 -*-
"""源码态启动自检：不打包，直接实例化 MainWindow 跑几秒。

用途：确认「改完源码 → 程序本身能起」，把问题定位在源码而不是 PyInstaller。
跟 smoke_exe.py 的区别：那个验打包产物（exe），这个验源码。
两者都绿，才能确定"打包没问题"还是"源码有问题"。

用法：python tools\\launch_check.py        （离屏，不需要显示器）
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _ensure_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(s, "encoding", "") or "").lower()
            if "utf" not in enc:
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_ensure_utf8()

# 离屏模式：不需要真实显示器，CI / 远程桌面下也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import launcher_web
except Exception:
    traceback.print_exc()
    print("RESULT: 导入失败 -> 依赖缺失或源码有语法/引用错误")
    sys.exit(1)

app = QApplication(sys.argv)
result = {"ok": False}


def on_timeout():
    result["ok"] = True
    app.quit()


try:
    w = launcher_web.MainWindow()
    w.show()
    print("MainWindow 实例化成功，窗口标题: %s" % w.windowTitle())
    QTimer.singleShot(4000, on_timeout)
    QTimer.singleShot(20000, app.quit)     # 兜底，防止卡死
    app.exec()
except Exception:
    traceback.print_exc()
    print("RESULT: 运行期异常")
    sys.exit(1)

print("RESULT: %s" % ("启动正常，4 秒内无异常" if result["ok"] else "超时未正常退出"))
sys.exit(0 if result["ok"] else 1)
