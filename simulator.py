# -*- coding: utf-8 -*-
"""
simulator.py -- 自定义协议「发送端」模拟器 (v2)
================================================
按照 v2 protocol.py 里的真实帧布局（tx_adc_packet_t 27B / tx_nav_packet_t 20B /
tx_motor_packet_t 23B），生成一段贴近真实的字节流并写入 sample_capture.bin。用途：
  1) 在没有硬件时，用上位机「回放文件」即可体验完整功能；
  2) 该文件的字节格式 = 上位机真实解析格式，便于你比对固件：
     若真实硬件发来的字节在「原始 HEX」面板里对不上，就改 telemetry_config.py
     （第 2 区信封 / 第 4 区字段表），protocol.py 只是算法，不用动。

生成节奏（对应 2026-09-29 新协议）：
  ADC 帧 100Hz，NAV 帧 25Hz，MOTOR 帧 50Hz —— 每 10ms tick 发 1 个 ADC，
  每 2 个 tick 插 1 个 MOTOR，每 4 个 tick 插 1 个 NAV（时间戳与同组 MOTOR 相同）。
"""
import math
from protocol import (build_adc_frame, build_nav_frame, build_motor_frame,
                      to_display)
import telemetry_config as CFG          # ★ 动态读字段表（不要 from-import 快照）

# ★ 组帧函数收的是「显示值」，不是原始码（见 telemetry_config.py 文件头约定：
#   显示值 = 原始码 × scale）。所以本文件里凡是"固件直接发的原始码"，
#   都要先过一遍 to_display() 换算；物理量（角度、m/s）则可以直接传。
NAV_TYPE = 0x01


def gen(duration_s: float = 8.0, out: str = "sample_capture.bin"):
    adc_hz = 100
    motor_hz = 50
    nav_hz = 25
    tick_ms = 1000 // adc_hz          # 10 ms
    n_adc = int(duration_s * adc_hz)
    out_bytes = bytearray()

    seq_a = seq_n = seq_m = 0
    for i in range(n_adc):
        t_ms = i * tick_ms

        # 10 路 ADC：生成 0~4095 的 12bit 演示码（直接喂 build_adc_frame 打包）
        adc = []
        for idx in range(10):
            freq = 0.2 + idx * 0.07
            frac = 0.5 + 0.45 * math.sin(2 * math.pi * freq * i / adc_hz + idx)
            adc.append(int(max(0, min(4095, frac * 4095))))
        out_bytes += build_adc_frame(seq_a, t_ms, adc)
        seq_a += 1

        # MOTOR 50Hz：每 2 个 tick 一帧
        if i % (adc_hz // motor_hz) == 0:
            speed = max(0.0, 12 + 8 * math.sin(2 * math.pi * 0.25 * i / adc_hz))   # m/s
            rpm = int(max(900, min(9800, 900 + speed * 420)))
            torque = int(max(0, min(150, 60 + 50 * math.sin(2 * math.pi * 0.3 * i / adc_hz))))
            throttle = int(max(0, min(100, 40 + 45 * math.sin(2 * math.pi * 0.3 * i / adc_hz))))
            temp = int(45 + 15 * (i / n_adc))
            volt = int(485 + 5 * math.sin(i / adc_hz))
            amp = int(max(0, min(200, torque * rpm * 0.10472 / max(volt, 1))))
            out_bytes += build_motor_frame(seq_m, t_ms, temp, rpm, torque,
                                           throttle, volt, amp, speed)
            seq_m += 1

        # NAV 25Hz：每 4 个 tick 一帧，时间戳与同组 MOTOR 相同
        if i % (adc_hz // nav_hz) == 0:
            roll = 15 * math.sin(2 * math.pi * 0.2 * i / adc_hz)
            pitch = 10 * math.sin(2 * math.pi * 0.27 * i / adc_hz)
            # int16×100 量程 ±327.67°：yaw 按 ±180° 折返（固件也应在此范围内折返）
            yaw = 170 * math.sin(2 * math.pi * 0.15 * i / adc_hz)
            # 加速度 raw 的旧注释保留在下方分支里；这里不再写死 ACCEL 字段名
            nav_names = [f[0] for f in CFG.PROTOCOL[NAV_TYPE]["fields"]]
            vals = []
            for j, nm in enumerate(nav_names):
                if nm == "ROLL":
                    vals.append(roll)
                elif nm == "PITCH":
                    vals.append(pitch)
                elif nm == "YAW":
                    vals.append(yaw)
                elif nm.startswith("ACCEL"):
                    # 加速度：固件里是 int16 裸码，按配置 scale 换算成显示值；
                    # 多个加速度通道（ACCEL_LAT / ACCEL_LON）给不同相位
                    raw = int(4000 * math.sin(2 * math.pi * (0.35 + 0.1 * j) * i / adc_hz))
                    vals.append(to_display(NAV_TYPE, nm, raw))
                else:
                    vals.append(0.0)     # 未来新增的未知字段：给 0（保持帧合法，不猜语义）
            out_bytes += build_nav_frame(seq_n, t_ms, *vals)
            seq_n += 1

    with open(out, "wb") as f:
        f.write(out_bytes)
    print(f"已生成 {out}: {len(out_bytes)} 字节, ADC={seq_a}帧, MOTOR={seq_m}帧, NAV={seq_n}帧")


if __name__ == "__main__":
    gen()
