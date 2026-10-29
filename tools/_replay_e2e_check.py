# -*- coding: utf-8 -*-
"""_replay_e2e_check.py —— 回放进度条的应用级端到端验证（Lead 最终验收用）

与实现者的离屏探针不同：这里跑**完整 MainWindow**（真窗口 + 真 QWebChannel），
用**真实录播文件**驱动 `startReplay`，验证「后端信号 → 前端 DOM」整条链路。

验证点：
  1. startReplay 后滑块被启用、标签显示真实总时长
  2. 回放推进时滑块与标签实时跟随
  3. seekReplay 能让滑块跳到指定位置
  4. 回放结束/停止后滑块归零并禁用
用法：python tools/_replay_e2e_check.py   （需要真实显示环境，不要 offscreen）
结果写 tools/_replay_e2e.json
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from PyQt6.QtCore import QTimer                      # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402
import launcher_web as L                             # noqa: E402

BIN = os.path.join(HERE, "sample_capture.bin")
OUT = os.path.join(HERE, "tools", "_replay_e2e.json")

R = {"bin": BIN, "progress_events": [], "dom": [], "notes": []}


def main():
    app = QApplication(sys.argv)
    win = L.MainWindow()
    win.resize(1440, 860)
    win.show()
    b = win.backend

    b.replayProgress.connect(
        lambda i, t, c, s: R["progress_events"].append([i, t, round(c, 3), round(s, 3)]))
    b.replayStateChanged.connect(
        lambda on, info: R["notes"].append("state %s %s" % (on, info)))
    b.errorMsg.connect(lambda m: R["notes"].append("ERR " + str(m)))

    def js(code, cb=None):
        win.view.page().runJavaScript(code, cb if cb else (lambda r: None))

    def snap(tag):
        def got(v):
            R["dom"].append({"tag": tag, "v": v})
        js("""(function(){
            var r=document.getElementById('replayRange');
            var p=document.getElementById('replayPos');
            if(!r) return null;
            return {value:r.value, disabled:r.disabled,
                    pos: p ? p.textContent : '(none)'};
        })()""", got)

    def do_start():
        R["notes"].append("CALL startReplay")
        b.startReplay(BIN)

    def do_seek():
        R["notes"].append("CALL seekReplay(0.8)")
        b.seekReplay(0.8)

    def finish():
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1)
        app.quit()

    # 页面加载 → 开始回放 → 观测 → seek → 观测 → 等结束 → 观测
    QTimer.singleShot(3500, lambda: snap("1_未回放"))
    QTimer.singleShot(4200, do_start)
    QTimer.singleShot(4700, lambda: snap("2_回放刚开始"))
    QTimer.singleShot(7000, lambda: snap("3_回放推进中"))
    QTimer.singleShot(7300, do_seek)
    QTimer.singleShot(7800, lambda: snap("4_seek到0.8之后"))
    QTimer.singleShot(13000, lambda: snap("5_回放结束后"))
    QTimer.singleShot(13600, finish)
    QTimer.singleShot(60000, finish)      # 兜底
    app.exec()


if __name__ == "__main__":
    main()
