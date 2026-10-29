# -*- coding: utf-8 -*-
"""
record_check.py —— 录制落盘端到端自检（不需要硬件、不需要窗口）
==================================================================
直接实例化后端对象，喂给它一批由 simulator 组出的真实帧，走完整的
「解析 → 配对 → 拼行 → 落盘」链路，最后把 CSV 读回来核对：

  1) 表头列数 == 数据行列数
  2) 行内容确实是喂进去的值（不是全 0、不是陈旧值）
  3) 三类包缺任意一类时不会写出半行

★ 为什么要有这个脚本：录制链路是「改协议最容易悄悄坏掉」的地方 ——
  改了字段名 / 加了包类型，界面看不出来，但录出来的 CSV 会串列或全 0。
  改完 telemetry_config.py 跑一次它，比事后发现数据废掉便宜得多。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication          # noqa: E402

import telemetry_config as CFG                    # noqa: E402
import protocol                                   # noqa: E402
import launcher_web as L                          # noqa: E402

OK_MARK = "[OK]"
FAIL_MARK = "[FAIL]"
_problems = []


def check(name, cond, detail=""):
    print(("  %s  " % (OK_MARK if cond else FAIL_MARK)) + name
          + (("  -> " + detail) if detail and not cond else ""))
    if not cond:
        _problems.append(name)


def main():
    app = QApplication(sys.argv)
    backend = L.Backend()

    tmpdir = tempfile.mkdtemp(prefix="tel_rec_")
    backend.save_dir = tmpdir

    print("── 录制落盘端到端 ──────────────────────────────")

    # ---- 组一批真实帧 ★ 测试值与期望值全部按配置推导，不写死 ----
    #   以前这里喂 {"SPEED": 1057} 并断言 CSV 里就是 "1057"：
    #     ① SPEED 是 int16，scale=0.001 时 1057 显示值需要 raw=1057000，
    #        直接溢出 int16 —— 组出来的帧本身就是错的，落盘当然对不上；
    #     ② 期望写成死的 "12.34"，位数从 2 位改成 1 位后必然误报。
    #   现在：每个字段挑一个【不溢出】的原始码，显示值 = 码 × scale，
    #         期望文本 = 按配置位数（FIELD_DECIMALS）格式化。
    #   于是改 scale、改位数、加字段，这条自检都不会误报 —— 它只验链路通不通。
    _FMT_RANGE = {"<B": (0, 255), "<b": (-128, 127), "<H": (0, 65535),
                  "<h": (-32768, 32767)}
    _SAMPLE_RAW = {"<B": 65, "<b": -30, "<H": 4200, "<h": 1234}

    def _sample_for(fmt):
        lo, hi = _FMT_RANGE.get(fmt, (0, 255))
        return max(lo, min(hi, _SAMPLE_RAW.get(fmt, 1)))

    def _vectors(type_id):
        out = {}
        for f in CFG.PROTOCOL[type_id]["fields"]:
            out[f[0]] = _sample_for(f[2]) * float(f[3])
        return out

    def _expect_text(name, type_id):
        """该字段按「原始码 → 组帧 → 解析 → 按配置位数格式化」后应有的文本。"""
        for f in CFG.PROTOCOL[type_id]["fields"]:
            if f[0] != name:
                continue
            raw = _sample_for(f[2])
            dec = CFG.field_decimals(name, f)
            return "%.*f" % (dec, raw * float(f[3]))
        return None

    adc = [2048] * CFG.ADC_COUNT
    nav = _vectors(0x01)
    mot = _vectors(0x02)
    f_adc = protocol.build_adc_frame(1, 100, adc)
    f_nav = protocol._build_typed_frame(0x01, 1, 100, nav)
    f_mot = protocol._build_typed_frame(0x02, 1, 100, mot)

    # ---- 开录 ----
    backend.startRecording()
    check("录制已启动", backend._recording)
    path = backend._rec_path

    # ---- 只喂 ADC + NAV（缺 MOTOR）：不应该写出任何行 ----
    for fr in protocol.parse_stream(bytearray(f_adc + f_nav)):
        backend._handle(fr)
    backend._flush_rec_buf_locked()
    check("缺一个包时不写半行", backend._rec_rows == 0,
          "实际写了 %d 行" % backend._rec_rows)

    # ---- 再喂 MOTOR：应该立刻成行 ----
    for fr in protocol.parse_stream(bytearray(f_adc + f_nav + f_mot)):
        backend._handle(fr)
    backend._flush_rec_buf_locked()
    check("三类到齐后成行", backend._rec_rows == 1,
          "实际 %d 行" % backend._rec_rows)

    backend.stopRecording()
    app.processEvents()

    # ---- 读回 CSV 核对 ----
    found = [p for p in os.listdir(tmpdir) if p.endswith(".csv")]
    check("CSV 文件已生成", len(found) == 1, str(found))
    if not found:
        return

    full = os.path.join(tmpdir, found[0])
    with open(full, encoding="utf-8-sig") as f:
        lines = [ln for ln in f.read().split("\n") if ln.strip()]

    header = lines[0].split(",")
    row = lines[1].split(",")
    check("表头列数(%d) == 数据列数(%d)" % (len(header), len(row)),
          len(header) == len(row))
    check("表头 == 配置派生的 CSV_HEADER",
          lines[0].strip() == CFG.CSV_HEADER, lines[0][:80])

    # 逐列核对关键值
    def col(name):
        return row[header.index(name)] if name in header else None

    print("\n  关键列取值：")
    _exp_speed = _expect_text("SPEED", 0x02)
    _exp_rpm = _expect_text("MOTOR_RPM", 0x02)
    _exp_roll = _expect_text("ROLL", 0x01)
    checks = [
        ("speed", _exp_speed, col("speed")),
        ("rpm", _exp_rpm, col("rpm") if "rpm" in header else col("motor_rpm")),
        ("roll", _exp_roll, col("roll")),
    ]
    for nm, want, got in checks:
        if nm in header or (nm == "rpm" and "motor_rpm" in header):
            print("    %-10s = %s（期望 %s）" % (nm, got, want))
    check("speed 列 = 喂进去的值（按配置位数格式化 = %s）" % _exp_speed,
          col("speed") == _exp_speed)
    check("roll 列 = 喂进去的值（按配置位数格式化 = %s）" % _exp_roll,
          col("roll") == _exp_roll)
    # ★ ADC 期望值按 ADC_DECODE_MODE 推导，不写死 2048。
    #   写死时：eng 模式下 2048 码会变成 2048/4095×rng（CH1=180.04），
    #   落盘本身完全正确，却是自检在误报 —— 和 selftest.py 踩的是同一个坑。
    # ★ 容差按「CSV 实际写出几位小数」反推（半个末位单位）。以前写死 0.51
    #   是因为 CSV 用 int(v) 截断；现在位数由 adc_csv_fmt() 按 rng/4095 给，
    #   容差必须跟着收，否则 int 截断回归了这条也拦不住。
    _mode = CFG.ADC_DECODE_MODE
    _bad_adc = []
    for _i in range(CFG.ADC_COUNT):
        _want = (2048.0 if _mode != "eng"
                 else 2048.0 * CFG.ADC_FULLSCALE[_i] / 4095.0)
        _txt = row[1 + _i]
        _dec = len(_txt.split(".")[1]) if "." in _txt else 0
        if abs(float(_txt) - _want) > 0.5 * (10 ** -_dec) + 1e-9:
            _bad_adc.append("CH%d 期望 %.4f 实得 %s" % (_i + 1, _want, _txt))
    check("ADC 全列取值正确（%s 模式）" % _mode, not _bad_adc)
    for _b in _bad_adc:
        print("    [FAIL] " + _b)

    print("\n  CSV 前两行：")
    print("    " + lines[0])
    print("    " + lines[1])

    # 清理
    try:
        os.remove(full)
        os.rmdir(tmpdir)
    except Exception:
        pass

    print("\n结果:", "全部通过 " + OK_MARK if not _problems
          else "存在失败 " + FAIL_MARK + "：" + "; ".join(_problems))
    return 0 if not _problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
