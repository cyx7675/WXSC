# -*- coding: utf-8 -*-
"""_ux_cand.py —— task-24 落地后的「改前 / 最终版」对照截图

背景（保留这段是为了可追溯）：
  产品体验复核期间用**运行时注入候选 CSS** 比过条形三版（10px 细线 / 46px 重块 / 22px 轻版），
  2026-10-16 lead 拍板采用**轻版 22px**，值已落到 `telemetry_web/index.html` 的 token 区
  （`--track` / `--track-line` / `.bar-*` / `.fill-*`）。**候选注入那套已删除**，
  本脚本现在只负责出「改前 vs 最终版」的对照图（不再有任何运行时补丁）。

做法：
  1) `tools/ux_before_src/`（从 `git HEAD` 导出的旧 index.html + app.js）作为"改前"；
  2) 当前 `telemetry_web/index.html` 作为"最终版"；
  3) 两次都用**同一份冻结 payload**，条形长度/数字逐像素可比。

用法：
  <venv>\\python.exe tools\\_ux_cand.py
输出：tools/ux_pair/final_before_*.png / final_after_*.png
"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(HERE, "tools")
PY = sys.executable
BEFORE_SRC = os.path.join(TOOLS, "ux_before_src")

# 同一份 payload 喂给两个版本：条形长度、数字、刻度完全可比（不靠随机演示数据）。
FROZEN = open(os.path.join(TOOLS, "_t27_payload.js"), encoding="utf-8").read()
# ★ task-27：payload 独立成 tools/_t27_payload.js —— 之前内联拼接时 setDemo(false) 之后
#   的 40 点历史被 try 吞掉了（改前/改后对照图里热条是等高短柱、双轴/散点是空白），
#   现在改前/改后共用同一份、且自带错误上报（window.__errs）。


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=HERE, **kw)


def export_before_src():
    """把 git HEAD 里的 index.html/app.js 导出到 tools/ux_before_src/（＋assets 拷贝）。"""
    os.makedirs(BEFORE_SRC, exist_ok=True)
    for f in ("index.html", "app.js"):
        r = sh(["git", "show", "HEAD:telemetry_web/" + f])
        if r.returncode != 0:
            print("FAIL git show", f, r.stderr[:200])
            return False
        with open(os.path.join(BEFORE_SRC, f), "w", encoding="utf-8", newline="") as fh:
            fh.write(r.stdout)
    src_assets = os.path.join(HERE, "telemetry_web", "assets")
    dst_assets = os.path.join(BEFORE_SRC, "assets")
    if os.path.isdir(dst_assets):
        shutil.rmtree(dst_assets)
    if os.path.isdir(src_assets):
        shutil.copytree(src_assets, dst_assets)
    print("已导出改前源码 ->", BEFORE_SRC)
    return True


def shot(prefix, shots, html=""):
    cmd = [PY, os.path.join(TOOLS, "_ux_shot.py"),
           "--out", os.path.join(TOOLS, "ux_pair"),
           "--prefix", prefix,
           "--width", "1600", "--height", "1000",
           "--demo", "2.5", "--shots", shots,
           "--post-js", FROZEN]
    if html:
        cmd += ["--html", html]
    r = sh(cmd)
    tail = [l for l in (r.stdout or "").splitlines() if l.startswith("SAVED") or "RESULT" in l]
    print("  [%s] %s" % (prefix, " | ".join(tail[-4:])))
    return r.returncode == 0


def main():
    if not export_before_src():
        return 1
    ok = True
    print("① 改前（git HEAD 的 index.html + app.js）")
    ok &= shot("final_before", "page,zoneP2", html=os.path.join(BEFORE_SRC, "index.html"))
    print("② 最终版（轻版 22px 条形 + 刻度表盘，当前源码）")
    ok &= shot("final_after", "page,zoneP2")
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
