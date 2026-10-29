#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decode_adc_log.py — 解析 E34 串口抓包（"收：" 行），解码 CRT-WXSC 三包协议

用法:
    python decode_adc_log.py 123.txt            # 解码全部，按类型统计
    python decode_adc_log.py 123.txt --adc      # 只看 ADC 帧，打印每路原始 ADC 值(0~4095)
    python decode_adc_log.py 123.txt --csv out.csv

协议（小端，header 线上=55 AA，tail 线上=AA 55）:
    ADC   27B type=0 : header2 type1 seq1 ts(u32) adc_packed[15] crc16 tail2
    NAV   20B type=1 : header2 type1 seq1 ts(u32) roll/pitch/yaw/accel(i16×4) crc16 tail2
    MOTOR 23B type=2 : header2 type1 seq1 ts(u32) motor_temp rpm torque throttle
                          voltage current speed(i16) crc16 tail2
CRC-16/MODBUS 覆盖 [header .. 倒数第4字节]（不含 crc16 + tail）
"""
import re, struct, sys, argparse

LEN = {0: 27, 1: 20, 2: 23}
TNAME = {0: 'ADC', 1: 'NAV', 2: 'MOTOR'}

# 复刻固件 crc16_table[256]（CRC-16/MODBUS）
TBL = [0x0000,0xC0C1,0xC181,0x0140,0xC301,0x03C0,0x0280,0xC241,0xC601,0x06C0,0x0780,0xC741,0x0500,0xC5C1,0xC481,0x0440,
0xCC01,0x0CC0,0x0D80,0xCD41,0x0F00,0xCFC1,0xCE81,0x0E40,0x0A00,0xCAC1,0xCB81,0x0B40,0xC901,0x09C0,0x0880,0xC841,
0xD801,0x18C0,0x1980,0xD941,0x1B00,0xDBC1,0xDA81,0x1A40,0x1E00,0xDEC1,0xDF81,0x1F40,0xDD01,0x1DC0,0x1C80,0xDC41,
0x1400,0xD4C1,0xD581,0x1540,0xD701,0x17C0,0x1680,0xD641,0xD201,0x12C0,0x1380,0xD341,0x1100,0xD1C1,0xD081,0x1040,
0xF001,0x30C0,0x3180,0xF141,0x3300,0xF3C1,0xF281,0x3240,0x3600,0xF6C1,0xF781,0x3740,0xF501,0x35C0,0x3480,0xF441,
0x3C00,0xFCC1,0xFD81,0x3D40,0xFF01,0x3FC0,0x3E80,0xFE41,0xFA01,0x3AC0,0x3B80,0xFB41,0x3900,0xF9C1,0xF881,0x3840,
0x2800,0xE8C1,0xE981,0x2940,0xEB01,0x2BC0,0x2A80,0xEA41,0xEE01,0x2EC0,0x2F80,0xEF41,0x2D00,0xEDC1,0xEC81,0x2C40,
0xE401,0x24C0,0x2580,0xE541,0x2700,0xE7C1,0xE681,0x2640,0x2200,0xE2C1,0xE381,0x2340,0xE101,0x21C0,0x2080,0xE041,
0xA001,0x60C0,0x6180,0xA141,0x6300,0xA3C1,0xA281,0x6240,0x6600,0xA6C1,0xA781,0x6740,0xA501,0x65C0,0x6480,0xA441,
0x6C00,0xACC1,0xAD81,0x6D40,0xAF01,0x6FC0,0x6E80,0xAE41,0xAA01,0x6AC0,0x6B80,0xAB41,0x6900,0xA9C1,0xA881,0x6840,
0x7800,0xB8C1,0xB981,0x7940,0xBB01,0x7BC0,0x7A80,0xBA41,0xBE01,0x7EC0,0x7F80,0xBF41,0x7D00,0xBDC1,0xBC81,0x7C40,
0xB401,0x74C0,0x7580,0xB541,0x7700,0xB7C1,0xB681,0x7640,0x7200,0xB2C1,0xB381,0x7340,0xB101,0x71C0,0x7080,0xB041,
0x5000,0x90C1,0x9181,0x5140,0x9301,0x53C0,0x5280,0x9241,0x9601,0x56C0,0x5780,0x9741,0x5500,0x95C1,0x9481,0x5440,
0x9C01,0x5CC0,0x5D80,0x9D41,0x5F00,0x9FC1,0x9E81,0x5E40,0x5A00,0x9AC1,0x9B81,0x5B40,0x9901,0x59C0,0x5880,0x9841,
0x8801,0x48C0,0x4980,0x8941,0x4B00,0x8BC1,0x8A81,0x4A40,0x4E00,0x8EC1,0x8F81,0x4F40,0x8D01,0x4DC0,0x4C80,0x8C41,
0x4400,0x84C1,0x8581,0x4540,0x8701,0x47C0,0x4680,0x8641,0x8201,0x42C0,0x4380,0x8341,0x4100,0x81C1,0x8081,0x4040]
assert len(TBL) == 256

def crc16(d):
    c = 0xFFFF
    for b in d:
        c = (c >> 8) ^ TBL[(c ^ b) & 0xFF]
    return c

def u16le(b):
    return b[0] | (b[1] << 8)

def unpack12(buf):
    """15 字节 → 10 个 uint16（每值 <4096），小端 12bit 打包逆运算"""
    v = []
    for i in range(0, 10, 2):
        j = (i // 2) * 3
        v.append((buf[j] << 4) | (buf[j + 1] >> 4))
        v.append(((buf[j + 1] & 0x0F) << 8) | buf[j + 2])
    return v

def load_raw(path):
    raw = bytearray()
    with open(path, 'rb') as f:
        for line in f:
            line = line.decode('utf-8', 'ignore')
            m = re.search(r'收：\s*([0-9A-Fa-f ]+)', line)
            if not m:
                continue
            for t in m.group(1).split():
                raw.append(int(t, 16))
    return raw

def parse(raw):
    frames = []
    i = 0
    while i < len(raw) - 2:
        if raw[i] == 0x55 and raw[i + 1] == 0xAA:
            t = raw[i + 2] if i + 2 < len(raw) else -1
            if t in LEN:
                L = LEN[t]
                if i + L > len(raw):
                    break
                fr = raw[i:i + L]
                tail = (fr[-2] << 8) | fr[-1]
                calc = crc16(fr[:-4])
                got = u16le(fr[-4:-2])
                if calc == got and tail == 0xAA55:
                    frames.append((i, t, fr))
                    i += L
                    continue
        i += 1
    return frames

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--adc', action='store_true', help='只打印 ADC 帧解码')
    ap.add_argument('--csv', help='导出 ADC 解码为 CSV')
    args = ap.parse_args()

    raw = load_raw(args.log)
    frames = parse(raw)
    from collections import Counter
    print(f"文件: {args.log}  字节流 {len(raw)} B  解析帧 {len(frames)}  "
          f"类型分布: {dict(Counter(f[1] for f in frames))}")

    adc = [f for f in frames if f[1] == 0]
    nav = [f for f in frames if f[1] == 1]
    mot = [f for f in frames if f[1] == 2]

    if args.adc or args.csv:
        # 通道索引 → 引脚/传感器（见 CRT-WXSC.ioc + app_types.h ADC_CH_* 枚举）
        names = ['CH1/PA1','CH2/PA2','CH3/PA3','CH4/PA4','CH5/PA5',
                 'ANG/PA6','PRES0/PB0','PRES1/PB1','PRES2/PC0','PRES3/PC0']
        rows = []
        for i, t, fr in adc:
            seq = fr[3]
            ts = struct.unpack('<I', bytes(fr[4:8]))[0]
            v = unpack12(fr[8:23])   # 12bit 解包得到 10 路原始 ADC 计数值(0~4095)
            rows.append((seq, ts, v))
        if args.csv:
            with open(args.csv, 'w', encoding='utf-8') as f:
                f.write('seq,ts_ms,' + ','.join(names) + '\n')
                for seq, ts, v in rows:
                    f.write(f"{seq},{ts}," + ','.join(str(x) for x in v) + '\n')
            print(f"已导出 {len(rows)} 条 ADC 到 {args.csv}")
        else:
            print(f"\n=== ADC 帧 {len(rows)} 条（原始计数值 0~4095，物理量请在上位机换算）===")
            print("  #   seq    ts(ms)    " + '  '.join(f"{n:>10s}" for n in names))
            for k, (seq, ts, v) in enumerate(rows[:80]):
                print(f" {k:2d}  {seq:3d}  {ts:8d}   " + '  '.join(f"{x:10d}" for x in v))
            # 各通道统计
            import statistics
            print("\n各通道 min/max/mean（原始计数值）:")
            for ch in range(10):
                col = [r[2][ch] for r in rows]
                flag = '★恒定(可能为0=断线/未接)' if min(col) == max(col) else '变化'
                print(f"  v{ch} {names[ch]:11s}: min={min(col):4d} max={max(col):4d} "
                      f"mean={statistics.mean(col):.1f}  {flag}")

if __name__ == '__main__':
    main()
