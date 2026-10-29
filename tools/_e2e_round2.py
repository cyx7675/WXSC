# -*- coding: utf-8 -*-
"""_e2e_round2.py —— 第二轮需求的应用级端到端验收（Lead 最终验收用）

与实现者的离屏探针不同：跑**完整 MainWindow**（真窗口 + 真 QWebChannel），
用**真实录播文件**驱动，验证用户五项需求的整条链路，并补上实现者量不到的一项：
**QWebChannel 真实传输 + JSON.parse 耗时**（离屏无真实 channel 量不到）。

验证点：
  1. 进度条在**底栏**、回放中被启用、标签显真实总时长
  2. seek → 后端 replayBackfill → 前端缓冲**恰等于槽数**且末值 = 跳转点值（无拼接）
  3. 回放**暂停/继续**（setReplayPaused 往返 + 暂停期间进度不再推进）
  4. 回填的**端到端耗时**：只能从 `transport_series`（seek 后每 5ms 采样缓冲长度）读，
     取"首个已达槽数"的采样点 → 字段 `fill_ms`（**5ms 粒度，故为上界**）。
     ★ 不要用"快照时刻 − emit 时刻"当耗时，那只是探针自己的定时器间隔
       （曾误报 403ms，独立质检据此提出 F9，已移除该误导字段）。
用法：python tools/_e2e_round2.py    （需要真实显示环境，不要 offscreen）
可用环境变量：E2E_BIN=<录播路径>  E2E_SEEK=<0~1>
结果写 tools/_e2e_round2.json
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

BIN = os.environ.get("E2E_BIN") or os.path.join(HERE, "sample_capture.bin")
SEEK = float(os.environ.get("E2E_SEEK") or "0.5")
OUT = os.path.join(HERE, "tools", "_e2e_round2.json")

R = {"bin": BIN, "snap": [], "notes": [], "backfill_bytes": 0,
     "backfill_slots": 0, "transport_ms": None, "parse_ms": None,
     "buf_after_backfill": None, "pause_probe": []}


def main():
    app = QApplication(sys.argv)
    win = L.MainWindow()
    win.resize(1440, 860)
    win.show()
    b = win.backend

    t_emit = {}

    def on_backfill(s):
        t_emit["t0"] = time.monotonic()
        t_emit["payload"] = s
        R["backfill_bytes"] = len(s)
        try:
            R["backfill_slots"] = len(json.loads(s).get("slots", []))
        except Exception as e:
            R["notes"].append("回填解析失败: %r" % e)

    b.replayBackfill.connect(on_backfill)
    b.errorMsg.connect(lambda m: R["notes"].append("ERR " + str(m)))

    def js(code, cb=None):
        win.view.page().runJavaScript(code, cb if cb else (lambda r: None))

    def snap(tag):
        def got(v):
            R["snap"].append({"tag": tag, "v": v})
        js("""(function(){
            var r=document.getElementById('replayRange');
            var p=document.getElementById('replayPos');
            var pb=document.getElementById('replayPauseBtn');
            if(!r) return null;
            var inFooter = !!document.getElementById('footer').contains(r);
            return {value:r.value, disabled:r.disabled, pos:p?p.textContent:'',
                    inFooter:inFooter, pauseDisabled: pb?pb.disabled:null,
                    pauseText: pb?pb.textContent:''};
        })()""", got)

    def snapshot_buf(tag):
        def got(v):
            R["buf_after_backfill"] = {"tag": tag, "v": v}
            if isinstance(v, dict):
                n = v.get("adc0Len")
                if n and n > 10:
                    R["transport_ms"] = round((time.monotonic() - t_emit["t0"]) * 1000.0, 1)
        js("""(function(){
            var s=window.state; if(!s||!s.buf) return null;
            var o={};
            for (var k in s.buf){ if (s.buf[k] && s.buf[k].length) o[k] = s.buf[k].length; }
            var out={adc0Len:(s.buf['adc0']||[]).length,
                     adc0Last:(s.buf['adc0']||[]).slice(-1)[0],
                     rollLen:(s.buf['roll']||[]).length};
            for (var k in o) out[k+'_len']=o[k];
            return out;
        })()""", got)

    def measure_parse():
        import base64
        s = t_emit.get("payload")
        if not s:
            R["parse_ms"] = {"error": "未捕获到回填载荷"}
            return
        b64 = base64.b64encode(s.encode("utf-8")).decode("ascii")
        js("""(function(){
            var bin=atob('%s');
            var bytes=new Uint8Array(bin.length);
            for(var i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
            var str=new TextDecoder('utf-8').decode(bytes);
            var t=performance.now(); JSON.parse(str); var d=performance.now()-t;
            return {parseMs:Math.round(d*1000)/1000, chars:str.length};
        })()""" % b64, lambda r: R.__setitem__("parse_ms", r))

    def do_pause():
        R["notes"].append("CALL setReplayPaused(True)")
        b.setReplayPaused(True)

    def do_resume():
        R["notes"].append("CALL setReplayPaused(False)")
        b.setReplayPaused(False)

    def pause_probe():
        """暂停期间进度是否真的不动：隔 700ms 读两次滑块值"""
        def got(v):
            R["pause_probe"].append(v)
        js("(function(){var r=document.getElementById('replayRange');"
           "return r?r.value:null;})()", got)

    def finish():
        # ★ 不要用"快照时刻 - emit 时刻"当传输耗时：那只是探针自己的定时器间隔（曾误报 403ms）。
        # 真正的耗时只能从 5ms 采样序列里读：首个"缓冲已到槽数"的采样点。
        ser = [x for x in (R.get("transport_series") or []) if x[1] >= 0]
        n_slots = R.get("backfill_slots") or 0
        hit = [x for x in ser if n_slots and x[1] >= n_slots]
        R["fill_ms"] = hit[0][0] if hit else None        # 采样粒度 5ms，故这是上界
        R.pop("transport_ms", None)                      # 移除误导字段
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(R, f, ensure_ascii=False, indent=1)
        app.quit()

    # ── 传输耗时：seek 后每 5ms 采样一次缓冲长度，画出"被清空→被填满"的时间序列 ──
    pt = QTimer()
    pt.setInterval(5)
    series = []

    def poll_buf():
        if "t0" not in t_emit:
            return
        el = (time.monotonic() - t_emit["t0"]) * 1000.0

        def got(v):
            if isinstance(v, int):
                series.append([round(el, 1), v])

        js("(function(){var s=window.state;return (s&&s.buf&&s.buf['adc0'])?s.buf['adc0'].length:-1;})()", got)

    pt.timeout.connect(poll_buf)

    def start_measure():
        t_emit["t0"] = time.monotonic()      # 以"发出 seek"为起点
        series.append([0.0, -2])             # -2 = 起跑标记
        pt.start()
        b.seekReplay(SEEK)

    def stop_measure():
        pt.stop()
        R["transport_series"] = series

    QTimer.singleShot(3500, lambda: snap("1_未回放"))
    QTimer.singleShot(4200, lambda: b.startReplay(BIN))
    QTimer.singleShot(4700, lambda: snap("2_回放中"))
    QTimer.singleShot(7000, start_measure)                   # 触发 seek + 回填，并开始 5ms 采样
    QTimer.singleShot(7400, lambda: snapshot_buf("3_seek后缓冲"))
    QTimer.singleShot(7900, stop_measure)
    QTimer.singleShot(7800, measure_parse)
    QTimer.singleShot(8200, lambda: b.setReplayPaused(True))  # 暂停
    QTimer.singleShot(8700, lambda: snap("4_已暂停"))
    QTimer.singleShot(9000, pause_probe)
    QTimer.singleShot(9700, pause_probe)                      # 700ms 后
    QTimer.singleShot(10400, lambda: b.setReplayPaused(False))  # 继续
    QTimer.singleShot(10900, lambda: snap("5_已继续"))
    QTimer.singleShot(11500, lambda: b.stopReplay())
    QTimer.singleShot(12000, lambda: snap("6_已停止"))
    QTimer.singleShot(12600, finish)
    QTimer.singleShot(60000, finish)
    app.exec()


if __name__ == "__main__":
    main()
