# -*- coding: utf-8 -*-
"""_ux_mock.py —— task-26 设计提案的**渲染驱动**（把 design/*.html 出成 PNG）

复用 tools/_ux_shot.py（Chrome headless + DevTools Protocol）。
本脚本只负责：对每个方向 × 每个视图/状态，调一次 _ux_shot.py。

用法：
  <venv>\\python.exe tools\\_ux_mock.py            # 出全部
  <venv>\\python.exe tools\\_ux_mock.py A          # 只出方向 A

输出：design/shots/<方向>_<视图>[_告警].png
  · 整页稿：1600×1520 视口（一屏看完整个页面，含 6~7 个分区）
  · 动力区特写：#zoneP2
  · 关键区特写：#zoneP1（姿态）/ #zoneTrend（分析）/ #topbar+#footer
  · 告警态：--post-js 调 protoSetMode('alarm')
"""
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(HERE, "tools")
SHOTS = os.path.join(HERE, "design", "shots")
PY = sys.executable

DIRS = {
    "A": ("dirA_cluster.html", "仪表台"),
    "B": ("dirB_sheet.html", "数据表"),
    "C": ("dirC_bands.html", "叙事带"),
}

NORMAL = "(function(){ protoSetMode('normal'); return 'ok'; })()"
ALARM = "(function(){ protoSetMode('alarm'); if(window._redrawExtra) _redrawExtra(); if(window._redrawAcc) _redrawAcc(); return 'ok'; })()"


def shot(prefix, html, shots, post, h=1000):
    cmd = [PY, os.path.join(TOOLS, "_ux_shot.py"),
           "--out", SHOTS, "--prefix", prefix, "--width", "1600", "--height", str(h),
           "--demo", "0.8", "--shots", shots, "--html", os.path.join(HERE, "design", html),
           "--post-js", post]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=HERE)
    out = [l for l in (r.stdout or "").splitlines() if l.startswith("SAVED") or "RESULT" in l]
    print("  [%s] %s" % (prefix, " | ".join(out[-6:])))
    return r.returncode == 0


def main():
    os.makedirs(SHOTS, exist_ok=True)
    want = sys.argv[1].upper() if len(sys.argv) > 1 else "ABC"
    ok = True
    for k in "ABC":
        if k not in want:
            continue
        html, nick = DIRS[k]
        print("=== 方向 %s（%s）===" % (k, nick))
        # 整页（一屏看完）+ 告警态整页
        ok &= shot("%s_整页_正常" % k, html, "page", NORMAL, h=1520)
        ok &= shot("%s_整页_告警" % k, html, "page", ALARM, h=1520)
        # 动力区特写（本轮重点）+ 告警态
        ok &= shot("%s_动力区_正常" % k, html, "#zoneP2", NORMAL, h=900)
        ok &= shot("%s_动力区_告警" % k, html, "#zoneP2", ALARM, h=900)
        # 关键区
        ok &= shot("%s_姿态区" % k, html, "#zoneP1", NORMAL, h=900)
        ok &= shot("%s_分析区" % k, html, "#zoneTrend", NORMAL, h=900)
        ok &= shot("%s_顶栏底栏" % k, html, "topbar,footer", NORMAL, h=900)
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
