# -*- coding: utf-8 -*-
"""perf_probe.py -- v2 上位机数据链路性能探针（只读诊断，不改业务代码）

用法：
    <venv python> tools/perf_probe.py

做四件事：
  1) 串口读取节拍实验：验证 pyserial read(size) 是否把数据攒成 timeout 长度的块
  2) 解析吞吐：parse_stream + CRC 的单帧耗时 / 实时占用率
  3) 突发洪峰：一次灌入 N 帧时的解析耗时（模拟主进程被阻塞后的补推）
  4) 假同步鲁棒性：注入噪声后能否重同步、缓冲是否失控增长
"""
import os
import sys
import time
import math
import random
import struct
import threading

# ★ 输出被重定向/管道时 Python 会用本地编码(cp936)写字节，撞上 µ 等字符会
#   UnicodeEncodeError 直接崩（EXIT≠0，且崩溃点在 print，诊断结果全拿不到）。
#   显式给 stdout/stderr 一个不会崩的编码，errors="replace" 保证最坏也只是问号。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol


def gen_stream(seconds=8.0):
    """按新协议生成真实速率的字节流：ADC 100Hz + MOTOR 50Hz + NAV 25Hz。"""
    out = bytearray()
    n_adc = int(seconds * 100)
    t_ms = 0
    seq = 0
    for i in range(n_adc):
        t_ms = int(i * 10)
        vals = [2000 + int(300 * math.sin(i / 17 + k)) for k in range(10)]
        out += protocol.build_adc_frame(seq & 0xFF, t_ms, vals)
        seq += 1
        if i % 2 == 0:
            out += protocol.build_motor_frame(seq & 0xFF, t_ms, 60, 4000 + i % 2000,
                                              80, 90, 480, 110, 12.34)
            seq += 1
        if i % 4 == 0:
            # ★ 按配置的 NAV 字段顺序传（协议加字段后这里不用改）
            _nav = [f[0] for f in protocol.PROTOCOL[0x01]["fields"]]
            out += protocol.build_nav_frame(seq & 0xFF, t_ms,
                                            *[protocol.to_display(0x01, n, 1200) for n in _nav])
            seq += 1
    return bytes(out)


def test_serial_clumping():
    """验证 pyserial read(4096) 是否攒块（决定数据到达节拍）。"""
    print("=" * 74)
    print("[1] 串口读取节拍实验（loop:// 虚拟端口，模拟 4350 B/s 真实速率）")
    print("=" * 74)
    try:
        import serial
    except ImportError:
        print("  未安装 pyserial，跳过")
        return None
    try:
        s = serial.serial_for_url("loop://", timeout=0.2)
    except Exception as e:
        print("  无法打开 loop:// : %s" % e)
        return None

    data = gen_stream(6.0)
    bytes_per_sec = 4350
    stop = threading.Event()

    def feeder():
        pos = 0
        while not stop.is_set():
            chunk = max(1, bytes_per_sec // 20)      # 每 50ms 送一次
            s.write(data[pos:pos + chunk])
            pos = (pos + chunk) % len(data)
            time.sleep(0.05)

    th = threading.Thread(target=feeder, daemon=True)
    th.start()
    time.sleep(0.3)

    # --- 现状：read(4096) ---
    gaps_a, sizes_a = [], []
    last = time.perf_counter()
    for _ in range(20):
        d = s.read(4096)
        now = time.perf_counter()
        gaps_a.append((now - last) * 1000)
        sizes_a.append(len(d))
        last = now
    stop.set()

    print("  现状 read(4096) timeout=0.2 ：")
    print("    每次返回间隔 ms : p50=%.0f  max=%.0f" % (
        sorted(gaps_a)[len(gaps_a) // 2], max(gaps_a)))
    print("    每次返回字节     : p50=%.0f  max=%d" % (
        sorted(sizes_a)[len(sizes_a) // 2], max(sizes_a)))
    print("    => 数据到达节拍 ≈ %.1f Hz" % (1000.0 / (sum(gaps_a) / len(gaps_a))))

    # --- 改进：read(in_waiting or 1) ---
    stop2 = threading.Event()
    feeder.__defaults__ = ()
    th2 = None

    def feeder2():
        pos = 0
        while not stop2.is_set():
            chunk = max(1, bytes_per_sec // 20)
            s.write(data[pos:pos + chunk])
            pos = (pos + chunk) % len(data)
            time.sleep(0.05)

    th2 = threading.Thread(target=feeder2, daemon=True)
    th2.start()
    time.sleep(0.3)
    gaps_b, sizes_b = [], []
    last = time.perf_counter()
    for _ in range(40):
        n = s.in_waiting or 1
        d = s.read(n)
        now = time.perf_counter()
        gaps_b.append((now - last) * 1000)
        sizes_b.append(len(d))
        last = now
    stop2.set()

    print("  改进 read(in_waiting or 1) ：")
    print("    每次返回间隔 ms : p50=%.1f  max=%.1f" % (
        sorted(gaps_b)[len(gaps_b) // 2], max(gaps_b)))
    print("    每次返回字节     : p50=%.1f  max=%d" % (
        sorted(sizes_b)[len(sizes_b) // 2], max(sizes_b)))
    print("    => 数据到达节拍 ≈ %.1f Hz" % (1000.0 / max(0.01, sum(gaps_b) / len(gaps_b))))
    try:
        s.close()
    except Exception:
        pass
    return {"old_hz": 1000.0 / (sum(gaps_a) / len(gaps_a)),
            "new_hz": 1000.0 / max(0.01, sum(gaps_b) / len(gaps_b))}


def test_parse_throughput():
    print()
    print("=" * 74)
    print("[2] 解析吞吐（parse_stream + CRC-16/MODBUS 纯 Python）")
    print("=" * 74)
    stream = gen_stream(8.0)
    # 先数出一共多少帧
    cnt = {"ADC": 0, "NAV": 0, "MOTOR": 0}
    b0 = bytearray(stream)
    for fr in protocol.parse_stream(b0):
        cnt[fr.name] += 1
    total = sum(cnt.values())
    print("  8s 流：%d 字节 / %d 帧（%s）" % (len(stream), total, cnt))

    # 单帧耗时（整段一次解析）
    t = time.perf_counter()
    b = bytearray(stream)
    protocol.parse_stream(b)
    dt = (time.perf_counter() - t) * 1000
    print("  整段解析耗时 %.1f ms -> 单帧 %.1f µs" % (dt, dt * 1000 / total))
    print("  实时占用率（175 帧/s）：%.3f%% CPU" % (dt * 1000 / total * 175 / 10000))

    # 模拟串口分块喂入（每 50ms 一块）
    b = bytearray()
    step = max(1, len(stream) // 160)
    t = time.perf_counter()
    parsed = 0
    for i in range(0, len(stream), step):
        b.extend(stream[i:i + step])
        parsed += len(protocol.parse_stream(b))
    dt2 = (time.perf_counter() - t) * 1000
    print("  分块喂入（%d 块）解析 %d 帧，总耗时 %.1f ms" % (160, parsed, dt2))
    print("  残留缓冲：%d 字节（应为 0）" % len(b))


def test_burst():
    print()
    print("=" * 74)
    print("[3] 突发洪峰：主进程被阻塞（弹窗/文件对话框）后一次性补推")
    print("=" * 74)
    stream = gen_stream(8.0)
    for mult, label in ((1, "8s 积压"), (3, "24s 积压"), (10, "80s 积压")):
        blob = stream * mult
        b = bytearray(blob)
        t = time.perf_counter()
        n = len(protocol.parse_stream(b))
        dt = (time.perf_counter() - t) * 1000
        print("  %s（%d 帧）：单次解析 %.0f ms —— %s" % (
            label, n, dt, "界面会明显卡一下" if dt > 100 else "可接受"))


def test_resync():
    print()
    print("=" * 74)
    print("[4] 假同步 / 噪声注入鲁棒性")
    print("=" * 74)
    stream = gen_stream(2.0)
    random.seed(7)
    # 每 200 字节插 3 个随机噪声字节（模拟无线丢字节/串码）
    dirty = bytearray()
    for i in range(0, len(stream), 200):
        dirty += stream[i:i + 200]
        dirty += bytes(random.randrange(256) for _ in range(3))
    b = bytearray(dirty)
    got = {"ADC": 0, "NAV": 0, "MOTOR": 0}
    t = time.perf_counter()
    for _ in range(200):
        for fr in protocol.parse_stream(b):
            got[fr.name] += 1
        if len(b) > 4096:
            print("  !! 缓冲失控增长 -> %d 字节" % len(b))
            break
    dt = (time.perf_counter() - t) * 1000
    print("  注入噪声后解析出 %s" % got)
    print("  残留缓冲 %d 字节，耗时 %.1f ms" % (len(b), dt))

    # 丢半帧（截断）场景
    b2 = bytearray(stream[:len(stream) // 2])
    n1 = len(protocol.parse_stream(b2))
    b2.extend(stream[len(stream) // 2:])
    n2 = len(protocol.parse_stream(b2))
    print("  半帧截断后接续：先解析 %d 帧，续上后 %d 帧，残留 %d 字节" % (n1, n2, len(b2)))


if __name__ == "__main__":
    r = test_serial_clumping()
    test_parse_throughput()
    test_burst()
    test_resync()
    print()
    print("=" * 74)
    if r:
        print("结论：串口读取节拍 现状 %.1f Hz -> 改进 %.1f Hz" % (r["old_hz"], r["new_hz"]))
