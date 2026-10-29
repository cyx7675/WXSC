# -*- coding: utf-8 -*-
"""_e2e_loading.py —— U06（长录播解析不卡 GUI）+ replayLoadingChanged 真机接线 验收

实现者的 loading 验收只有合成调用（离屏没有真 QWebChannel），这里补上真机：
  1. 真窗口 + 真 QWebChannel + 真长 .bin（20 万帧级）
  2. 解析期间：后端 loading 事件递增、**前端 #replayPos 真的显示解析文案**、**界面仍可交互**
  3. 解析结束：loading 收尾 → 进回放态 → 进度条可用
  4. 对比：同步版路径的帧数与总时长必须一致

用法：python tools/_e2e_loading.py [录播路径]   （需要真实显示环境）
结果写 tools/_e2e_loading.json
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from PyQt6.QtCore import QTimer                      # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402
import launcher_web as L                             # noqa: E402
import protocol                                      # noqa: E402

def _pick_bin():
    """自动挑 telemetry/ 下最大的 .bin（原实现硬编码 telemetry_20261013_213238.bin，
    用户换录播后该文件已不存在 → 探针直接崩）。也可用命令行参数显式指定。"""
    d = os.path.join(HERE, "telemetry")
    bins = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".bin")] if os.path.isdir(d) else []
    return max(bins, key=os.path.getsize) if bins else os.path.join(HERE, "sample_capture.bin")

BIN = sys.argv[1] if len(sys.argv) > 1 else _pick_bin()
OUT = os.path.join(HERE, "tools", "_e2e_loading.json")

R = {"bin": BIN, "loading": [], "dom": [], "heartbeats": 0, "hb_max_gap_ms": 0.0}


def main():
    # ★ 期望帧数必须在**任何测量开始之前**算好：parse_stream 是同步的，
    #   若放在计时中途会把心跳间隔污染成"解析耗时"（我第一版就踩了这个坑）。
    R["explen"] = len(protocol.parse_stream(bytearray(open(BIN, "rb").read())))
    app = QApplication(sys.argv)
    win = L.MainWindow()
    win.resize(1440, 860)
    win.show()
    b = win.backend

    b.replayLoadingChanged.connect(
        lambda on, txt: R["loading"].append([bool(on), txt, round(time.monotonic() - t0, 3)]))
    b.replayProgress.connect(
        lambda i, t, c, s: R.setdefault("progress", []).append([i, t, round(c, 2), round(s, 2)]))
    b.replayStateChanged.connect(
        lambda on, info: R.setdefault("state", []).append([bool(on), info]))
    b.errorMsg.connect(lambda m: R.setdefault("err", []).append(str(m)))

    def js(code, cb=None):
        win.view.page().runJavaScript(code, cb if cb else (lambda r: None))

    # 心跳：证明解析期间事件循环没被独占（界面可交互）
    hb = {"last": None}

    def beat():
        now = time.monotonic()
        R["heartbeats"] += 1
        if hb["last"] is not None:
            gap = (now - hb["last"]) * 1000.0
            if gap > R["hb_max_gap_ms"]:
                R["hb_max_gap_ms"] = round(gap, 1)
        hb["last"] = now

    hbt = QTimer(); hbt.setInterval(20); hbt.timeout.connect(beat)

    poll = QTimer(); poll.setInterval(150)

    def poll_dom():
        def got(v):
            R["dom"].append([round(time.monotonic() - t0, 2)] + v)
        js("""(function(){
            var r=document.getElementById('replayRange'), p=document.getElementById('replayPos');
            return [r?r.disabled:null, p?p.textContent:'', (window.state&&state.replaying)?1:0];
        })()""", got)
    poll.timeout.connect(poll_dom)

    def start():
        hbt.start(); poll.start()
        b.startReplay(BIN)

    def finish():
        hbt.stop(); poll.stop()
        # 纯 JSON.parse 对照不需要；写盘
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1)
        app.quit()

    global t0
    t0 = time.monotonic()
    QTimer.singleShot(3500, start)
    QTimer.singleShot(32000, finish)      # 长文件解析 7s + 回放推进
    QTimer.singleShot(45000, finish)
    app.exec()


if __name__ == "__main__":
    main()
