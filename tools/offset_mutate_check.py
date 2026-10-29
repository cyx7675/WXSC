# -*- coding: utf-8 -*-
"""
offset_mutate_check.py —— ★「改了字段偏移，自检会不会误报」变异自检 ★
==================================================================
起因（2026-10-10 用户亲历）：
    把第 4 区 MOTOR 包里 MOTOR_RPM 挪到偏移 10、TORQUE 挪到 9，
    selftest 立刻红 5 项。配置体检却是绿的（偏移没重叠、没越界）。

    根因不在配置，而在 protocol.py 的 _build_typed_frame：
        组帧 = 按 fields 列表顺序 append   ← 把偏移当摆设
        解析 = 按字段声明的 off 定位读
    列表顺序和偏移顺序只要不一致（rpm 写在 torque 前面，偏移却在它后面），
    写进去的位置和读出来的位置就整体错位 —— 报错信息还完全指不到根因。

现在组帧按【偏移】写，偏移才是字节布局的唯一真源。这个脚本负责钉死它：
穷举/采样出各种合法布局（换顺序、插空洞），每种都跑
    ① 配置体检    ② 组帧→解析往返   ③ 字节落点 == 声明偏移
只要有一项红 = 又有地方把「列表顺序」当成了布局。

用法：
    cd F:\\上位机\\v2
    <venv>\\python.exe tools\\offset_mutate_check.py
    退出码 0 = 任意合法偏移布局下都自洽；非 0 = 存在依赖列表顺序的代码

★ 全部在内存里变异，不写磁盘、不改你的配置文件（跑完自动还原）。
"""
import itertools
import os
import random
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import telemetry_config as CFG          # noqa: E402
import protocol as P                    # noqa: E402

ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"

MARK_OK = "[OK]"
MARK_FAIL = "[FAIL]"

# 每个 struct 格式挑一个有辨识度的测试原始码（负数类型取负，顺带验有符号还原）
_SAMPLE_RAW = {"<B": 65, "<b": -30, "<H": 4200, "<h": -2048,
               "<I": 123456, "<i": -50000}


def _ensure_utf8():
    try:
        if "utf" not in (sys.stdout.encoding or "").lower():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _size(fmt):
    return struct.calcsize(fmt)


def _layout(items, order, gaps=None):
    """按 order 把字段从偏移 8 依次排布；gaps[i] = 第 i 个字段前留几个保留字节。

    返回 (fields, end)。fields 是 [(name, off, fmt, scale)]。
    """
    gaps = gaps or [0] * len(order)
    res, cur = [], 8
    for k, i in enumerate(order):
        cur += int(gaps[k])
        name, fmt, scale = items[i]
        res.append((name, cur, fmt, scale))
        cur += _size(fmt)
    return res, cur


def _variants(spec, max_cases):
    """生成要测的布局列表 [(说明, fields)]。"""
    fields = spec["fields"]
    items = [(f[0], f[2], f[3]) for f in fields]      # name, fmt, scale
    crc_at = int(spec["total"]) - 4
    out = [("配置原始布局", [tuple(f) for f in fields])]

    seen = {tuple(f[1] for f in fields)}
    rng = random.Random(20261010)
    perms = list(itertools.permutations(range(len(items))))
    rng.shuffle(perms)
    n = 0
    for order in perms:
        if n >= max_cases:
            break
        # 一半插空洞（模拟固件里保留/对齐字节），一半严丝合缝
        for gap_mode in (0, 1):
            if n >= max_cases:
                break
            gaps = [0] * len(order)
            if gap_mode and crc_at - 8 - sum(_size(items[i][1]) for i in order) >= 1:
                gaps[rng.randrange(len(order))] = 1
            fl, end = _layout(items, order, gaps)
            if end > crc_at:
                continue
            key = tuple(f[1] for f in fl)
            if key in seen:
                continue
            seen.add(key)
            out.append(("%s%s" % ("->".join(f[0] for f in fl[:3]) + "…",
                                  "（含保留字节）" if gap_mode else ""), fl))
            n += 1
    return out


def _roundtrip(type_id, fields):
    """组帧 → 解析：每个字段都要还原成喂进去的显示值。返回问题列表。"""
    disp, raw_expected = {}, {}
    for name, _off, fmt, scale in fields:
        raw = _SAMPLE_RAW.get(fmt, 1)
        raw_expected[name] = raw
        disp[name] = raw * float(scale)
    frame = P._build_typed_frame(type_id, 1, 1000, disp)
    got = P.parse_stream(bytearray(frame))
    if not got:
        return ["组出来的帧解析不出来"]
    v = got[0].values
    bad = ["%s 期望 %g 实得 %r" % (n, d, v.get(n)) for n, d in disp.items()
           if v.get(n) != float(d)]
    return bad


def _landing(type_id, fields):
    """每个字段的特征值必须正好落在 [off, off+size) 上。返回问题列表。"""
    spec = CFG.PROTOCOL[type_id]
    crc_at = int(spec["total"]) - 4
    names = [f[0] for f in fields]
    zero = P._build_typed_frame(type_id, 1, 1000, {n: 0.0 for n in names})
    bad = []
    for name, off, fmt, scale in fields:
        size = _size(fmt)
        mark = 0x5A if size == 1 else 0x5A3C
        vals = {n: 0.0 for n in names}
        vals[name] = mark * float(scale)
        got = P._build_typed_frame(type_id, 1, 1000, vals)
        diff = [i for i in range(8, crc_at) if got[i] != zero[i]]
        if not diff:
            bad.append("%s 特征值没落到任何字节上" % name)
            continue
        lo, hi = min(diff), max(diff) + 1
        if lo != off or hi != off + size:
            bad.append("%s 声明 %d~%d 实际 %d~%d"
                       % (name, off, off + size - 1, lo, hi - 1))
    return bad


def main():
    _ensure_utf8()
    print("=" * 72)
    print("  改字段偏移，自检会不会跟着误报？（偏移变异自检）")
    print("=" * 72)

    targets = [t for t in sorted(CFG.PROTOCOL) if "fields" in CFG.PROTOCOL[t]]
    total_bad = []
    for type_id in targets:
        spec = CFG.PROTOCOL[type_id]
        name = spec.get("name", "?")
        original = [tuple(f) for f in spec["fields"]]
        cases = _variants(spec, max_cases=24 if len(original) > 4 else 12)
        print("\n  ── 0x%02X(%s)：%d 种布局 ──" % (type_id, name, len(cases)))
        try:
            for desc, fields in cases:
                spec["fields"] = [list(f) for f in fields]
                probs = [p for p in CFG.validate() if "0x%02X" % type_id in p]
                rt = _roundtrip(type_id, fields)
                ld = _landing(type_id, fields)
                ok = not probs and not rt and not ld
                print("     %s %-34s%s"
                      % (MARK_OK if ok else MARK_FAIL, desc,
                         "" if ok else
                         "  体检%d/往返%d/落点%d" % (len(probs), len(rt), len(ld))))
                for b in (probs + rt + ld)[:4]:
                    print("         ✗ " + b)
                if not ok:
                    total_bad.append("0x%02X %s" % (type_id, desc))
        finally:
            spec["fields"] = [list(f) for f in original]

    # 还原后再跑一次真实自检，确认配置文件本身没被污染
    p = subprocess.run([sys.executable, "selftest.py"], cwd=ROOT,
                       capture_output=True, env=ENV)
    out = (p.stdout or b"").decode("utf-8", "replace")
    bad = [l.strip() for l in out.splitlines() if "❌" in l or "[FAIL]" in l]
    ok = p.returncode == 0 and not bad
    print("\n  %s 还原后 selftest.py exit=%d%s"
          % (MARK_OK if ok else MARK_FAIL, p.returncode,
             "" if ok else "  失败 %d 项" % len(bad)))
    for l in bad[:5]:
        print("         " + l)
    if not ok:
        total_bad.append("还原后 selftest")

    print("\n" + "=" * 72)
    if total_bad:
        print("  %s 以下布局出错 —— 说明还有代码把 fields 列表顺序当布局："
              % MARK_FAIL)
        for b in total_bad[:20]:
            print("    · " + b)
        print("=" * 72)
        return 1
    print("  %s 所有合法偏移布局下：体检 / 往返 / 字节落点 全部自洽。"
          % MARK_OK)
    print("  （列表顺序只决定 CSV 与界面的列顺序，不再影响字节布局）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
