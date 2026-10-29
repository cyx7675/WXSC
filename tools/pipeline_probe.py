# -*- coding: utf-8 -*-
"""pipeline_probe.py -- 端到端数据管线探针（只读诊断，不改业务代码）

用 pyserial 的 loop:// 虚拟串口把「协议帧字节流」按真实速率灌进 Backend 的
读取线程，走完整链路：_reader -> parse_stream -> _handle -> _pending -> _flush
-> dataReady 信号。测量：

  - 每秒「有数据的推送」次数（决定前端取样率 / 趋势时间轴是否准）
  - 每秒帧数、每批帧数分布
  - _pending 积压峰值（越大 = 主进程被阻塞时堆积越多）
  - 端到端延迟估计：从帧进入串口到被推送出去的时间

用法（必须 offscreen，venv python）：
    QT_QPA_PLATFORM=offscreen python tools/pipeline_probe.py
"""
import json
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import protocol                                    # noqa: E402
from PyQt6.QtWidgets import QApplication           # noqa: E402
from PyQt6.QtCore import QTimer                    # noqa: E402

DURATION = 8.0
BYTES_PER_SEC = 4350      # 实测协议带宽：2700(ADC) + 500(NAV) + 1150(MOTOR)


def gen_stream(seconds=12.0):
    out = bytearray()
    seq = 0
    n = int(seconds * 100)
    for i in range(n):
        t_ms = int(i * 10)
        vals = [2000 + int(300 * (i % 7) + k * 11) % 4096 for k in range(10)]
        out += protocol.build_adc_frame(seq & 0xFF, t_ms, vals); seq += 1
        if i % 2 == 0:
            out += protocol.build_motor_frame(seq & 0xFF, t_ms, 60, 4000 + (i * 7) % 5000,
                                              80, 90, 480, 110, 12.34); seq += 1
        if i % 4 == 0:
            # ★ 按配置的 NAV 字段顺序传（协议加字段后这里不用改）
            _nav = [f[0] for f in protocol.PROTOCOL[0x01]["fields"]]
            out += protocol.build_nav_frame(
                seq & 0xFF, t_ms, *[protocol.to_display(0x01, n, 1200) for n in _nav]); seq += 1
    return bytes(out)


def main():
    import serial
    import launcher_web as LW

    app = QApplication(sys.argv)
    be = LW.Backend()

    dev = serial.serial_for_url("loop://", timeout=getattr(LW, "SERIAL_TIMEOUT", 0.2))
    be.ser = dev
    be.running = True
    be.thread = threading.Thread(target=be._reader, daemon=True)
    be.thread.start()

    hits = []            # (时刻, 本批帧数)
    peak = [0]

    def on_data(j):
        try:
            n = len(json.loads(j)) if j else 0
        except Exception:
            n = 0
        hits.append((time.perf_counter(), n))

    be.dataReady.connect(on_data)

    stream = gen_stream(DURATION + 4)
    stop = threading.Event()

    def feed():
        pos = 0
        step = max(1, BYTES_PER_SEC // 20)     # 每 50ms 送一次，贴近串口真实到达
        while not stop.is_set():
            dev.write(stream[pos:pos + step])
            pos = (pos + step) % len(stream)
            time.sleep(0.05)

    threading.Thread(target=feed, daemon=True).start()

    def watch():
        peak[0] = max(peak[0], len(be._pending))

    tw = QTimer()
    tw.timeout.connect(watch)
    tw.start(50)

    t0 = time.perf_counter()
    QTimer.singleShot(int(DURATION * 1000), app.quit)
    app.exec()
    stop.set()
    dur = time.perf_counter() - t0
    be.running = False
    try:
        dev.close()
    except Exception:
        pass

    if len(hits) < 2:
        print("未收到任何推送，检查 loop:// 是否正常")
        return 1

    frames = sum(h[1] for h in hits)
    gaps = [(hits[i][0] - hits[i - 1][0]) * 1000 for i in range(1, len(hits))]
    gaps_s = sorted(gaps)
    sizes = sorted(h[1] for h in hits)

    def q(a, p):
        return a[min(len(a) - 1, int(len(a) * p))]

    print("=" * 74)
    print("端到端数据管线（loop:// 虚拟串口，真实速率 %d B/s，持续 %.1f s）" % (BYTES_PER_SEC, dur))
    print("=" * 74)
    print("  有数据的推送次数 : %d 次 -> %.2f Hz（前端趋势取样率就是它）" % (len(hits), len(hits) / dur))
    print("  推送间隔 ms      : p50=%.1f  p95=%.1f  max=%.1f" % (q(gaps_s, .5), q(gaps_s, .95), max(gaps)))
    print("  每批帧数         : p50=%d  max=%d" % (q(sizes, .5), max(sizes)))
    print("  总帧数 / 每秒    : %d -> %.1f 帧/s（理论 175）" % (frames, frames / dur))
    print("  _pending 积压峰值: %d 帧" % peak[0])
    print()
    print("  判据：推送应为 ~20 Hz（=FLUSH_INTERVAL_MS 50ms）；")
    print("        若为 ~5 Hz 说明读取线程把数据攒成了 timeout 长度的块。")
    if len(hits) / dur < 12:
        print("  >>> 异常：数据被攒块推送，前端显示会一顿一顿，趋势时间轴也会失真")
    else:
        print("  >>> 正常：数据按 20Hz 稳定推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
