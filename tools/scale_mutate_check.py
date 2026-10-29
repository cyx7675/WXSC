# -*- coding: utf-8 -*-
"""
scale_mutate_check.py —— ★「改了系数，自检会不会误报」变异自检 ★
==================================================================
起因（2026-10-09 用户亲历）：
    把第 4 区 SPEED 的 scale 从 0.01 改成 0.001，selftest / record_check
    立刻报红。查下来根因不是协议错，而是**位数由 scale 反推**：
        CSV 位数   = ceil(-log10(scale))     ← 改系数，位数跟着跳
        界面位数   = 各处写死的 toFixed(1)   ← 改系数，位数纹丝不动
        自检期望值 = 写死的 "1057" / "12.34" ← 位数一变就对不上
    三套口径互相打架，谁改系数谁报红。

现在位数只认配置（第 3 区 ADC_DECIMALS / 第 5 区 FIELD_DECIMALS），
这个脚本就负责把这件事钉死：直接改真实配置里的 scale，跑完整自检，
只要有一项红 = 还有地方在偷偷按 scale 推位数 / 写死了期望值。

用法：
    cd F:\\上位机\\v2
    <venv>\\python.exe tools\\scale_mutate_check.py
    退出码 0 = 任意 scale 下自检都通过；非 0 = 存在随系数漂移的断言
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "telemetry_config.py")

# 变异目标：(信号名, 偏移, 格式) —— 挑车速，因为它是 int16 + 小 scale 的组合，
# 既容易溢出（raw=1057000 超 int16 就是当年写死期望值的翻车点），位数又敏感。
TARGET = ("SPEED", 17, "<h")
# 变异档位：覆盖"整数原始码 / ×100 定点 / ×1000 定点 / 换算成 km/h 的怪系数"
VALUES = ["1.0", "0.01", "0.001", "0.036"]
TOOLS = ["selftest.py", os.path.join("tools", "record_check.py")]

PAT = re.compile(r'\("%s",\s*%d,\s*"%s",\s*[0-9eE.+-]+\)'
                 % (TARGET[0], TARGET[1], TARGET[2]))

ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"
ENV["QT_QPA_PLATFORM"] = "offscreen"

MARK_OK = "[OK]"
MARK_FAIL = "[FAIL]"


def _ensure_utf8():
    try:
        if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    _ensure_utf8()
    src = open(CFG, encoding="utf-8").read()
    m = PAT.search(src)
    if not m:
        print("%s 配置里找不到 %s 字段，无法变异" % (MARK_FAIL, TARGET[0]))
        return 1

    print("=" * 70)
    print("  改 %s 的 scale，自检会不会跟着误报？（变异自检）" % TARGET[0])
    print("=" * 70)
    print("  原始行: %s" % m.group(0))
    print("  变异档: %s" % ", ".join(VALUES))

    bad_total = []
    try:
        for val in VALUES:
            open(CFG, "w", encoding="utf-8").write(
                src[:m.start()]
                + '("%s",         %d, "%s", %s)' % (TARGET[0], TARGET[1], TARGET[2], val)
                + src[m.end():])
            print("\n  ── scale = %s ──" % val)
            for tool in TOOLS:
                p = subprocess.run([sys.executable, tool], cwd=ROOT,
                                   capture_output=True, env=ENV)
                out = (p.stdout or b"").decode("utf-8", "replace")
                bad = [l.strip() for l in out.splitlines()
                       if "❌" in l or "[FAIL]" in l or "存在失败" in l]
                ok = (p.returncode == 0 and not bad)
                print("     %s %-18s exit=%d%s"
                      % (MARK_OK if ok else MARK_FAIL, os.path.basename(tool),
                         p.returncode, ("" if ok else "  失败 %d 项" % len(bad))))
                for l in bad[:5]:
                    print("         " + l)
                if not ok:
                    bad_total.append("scale=%s / %s" % (val, os.path.basename(tool)))
    finally:
        open(CFG, "w", encoding="utf-8").write(src)
        print("\n  （配置文件已还原）")

    print("\n" + "=" * 70)
    if bad_total:
        print("  %s 以下组合改了系数就报错 —— 说明还有断言依赖 scale/位数："
              % MARK_FAIL)
        for b in bad_total:
            print("    · " + b)
        print("=" * 70)
        return 1
    print("  %s %d 档 scale 下两套自检全部通过：位数与系数已彻底解耦。"
          % (MARK_OK, len(VALUES)))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
