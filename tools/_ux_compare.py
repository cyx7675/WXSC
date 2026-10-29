# -*- coding: utf-8 -*-
"""_ux_compare.py —— 产品体验改版（task-24）改前/改后对照

读两份 `_ux_shot.py` 产出的 `<prefix>_rects.json`，输出：
  · 8 个分区的 rect 对照（x / y / w / h），确认**分区没被挪丢**
  · 关键排版数值对照（字号 / 圆角 / 描边 / 间距 / 配色）
  · 各元素所属分区（防止"东西跑到别的区去了"）

用法：
  <venv>\\python.exe tools\\_ux_compare.py tools/ux_before/before_rects.json tools/ux_after/after_rects.json
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ZONES = ["#zoneP2", "#zoneP1", "#zoneP0", "#zoneTrend",
         "#zoneVisual", "#zoneSerial", "#zoneRecord", "#zoneSettings"]

FONT_KEYS = ["#topbar", "#footer", ".brand h1", ".z-title", ".z-meta", ".ptag",
             ".hcard .h-label", ".hcard .h-num", ".hcard .h-unit",
             ".bar-scale", ".scard .s-name", ".scard .s-num", ".scard .s-unit", ".scard .s-state",
             ".dcard .d-name", ".dcard .d-num", ".raw-chip",
             ".accvec-cap", ".accvec-row", ".accvec-row b", ".accvec-note",
             "#zoneP0 .a-ch", "#zoneP0 .a-name", "#zoneP0 .a-val", "#zoneP0 .a-unit",
             ".chip", ".sig-cap", ".sig-row", ".sig-row .sig-val", ".sig-row .sig-un",
             ".vis-channels .vc-item", ".vc-title", ".vc-sig", ".vc-empty",
             ".sel", ".inp", ".btn", ".ctl-row label", ".chk", ".hint",
             "#replayPos", "#footer b", "#clock", ".pill", ".rail-item", "#zoneSerial .hexbox",
             "#pwrNum", "#pwrState", ".pwr-limit-lbl", ".pwr-peak"]


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    b = load(sys.argv[1])
    a = load(sys.argv[2])

    print("=== ① 8 个分区 rect 对照（分区没被挪丢 = 8 个都在、顺序一致、宽度未被挤）===")
    print("  %-14s %-26s %-26s %s" % ("分区", "改前 [x,y,w,h]", "改后 [x,y,w,h]", "判定"))
    ok = True
    for z in ZONES:
        rb, ra = b.get(z), a.get(z)
        if not rb or not ra:
            print("  %-14s %-26s %-26s 缺失!" % (z, rb, ra))
            ok = False
            continue
        bx, by, bw, bh = rb["x"], rb["y"], rb["w"], rb["h"]
        ax, ay, aw, ah = ra["x"], ra["y"], ra["w"], ra["h"]
        same_w = abs(bw - aw) <= 1
        flag = ("宽不变 ±1px" if same_w else "★宽度变了 %+d" % (aw - bw))
        if not same_w:
            ok = False
        print("  %-14s %-26s %-26s %s (dy=%+d, dh=%+d)"
              % (z, [bx, by, bw, bh], [ax, ay, aw, ah], flag, ay - by, ah - bh))
    print("  分区宽度全部保持 =", ok)

    print()
    print("=== ② 分区顺序（页面从上到下的 y）改前/改后必须一致 ===")
    order_b = sorted(ZONES, key=lambda z: (b[z]["y"], b[z]["x"]))
    order_a = sorted(ZONES, key=lambda z: (a[z]["y"], a[z]["x"]))
    print("  改前:", " ".join(z[1:] for z in order_b))
    print("  改后:", " ".join(z[1:] for z in order_a))
    print("  顺序一致 =", order_b == order_a)

    print()
    print("=== ③ 字号阶梯对照（改前 -> 改后）===")
    print("  %-34s %-8s %-8s %s" % ("元素", "改前", "改后", "说明"))
    for k in FONT_KEYS:
        fb = (b.get(k) or {}).get("font")
        fa = (a.get(k) or {}).get("font")
        if fb is None and fa is None:
            continue
        if fb == fa:
            note = "不变"
        elif fb is None:
            note = "★新增元素"
        else:
            try:
                dv = float(str(fa).replace("px", "")) - float(str(fb).replace("px", ""))
                note = "%+gpx" % dv
            except Exception:
                note = "变化"
        print("  %-34s %-8s %-8s %s" % (k, fb, fa, note))

    print()
    print("=== ④ 圆角 / 描边 / 底面对照 ===")
    for k in ["#zoneP2", "#zoneP1", "#zoneP0", "#zoneTrend",
              "#zoneP2 .hcard", "#zoneP2 .scard", "#zoneP1 .dcard", "#zoneP0 .acard",
              "#zoneTrend .sig-panel", "#zoneVisual .vchart", "#zoneSerial .hexbox",
              ".chip", ".sel", ".btn", ".pill", ".accvec"]:
        rb, ra = b.get(k) or {}, a.get(k) or {}
        if not rb or not ra:
            continue
        print("  %-26s radius %-10s->%-10s  bg %-22s->%-22s  border %s -> %s"
              % (k, rb.get("radius"), ra.get("radius"), rb.get("bg"), ra.get("bg"),
                 rb.get("border"), ra.get("border")))

    print()
    print("=== ⑤ 底纹（原来 P0/P1/P2 各有一层彩色渐变，现应为纯色）===")
    for z in ["#zoneP0", "#zoneP1", "#zoneP2"]:
        print("  %-10s bg 改前=%s  改后=%s" % (z, (b.get(z) or {}).get("bg"), (a.get(z) or {}).get("bg")))

    print()
    print("=== ⑥ 页内滚动高度 ===")
    print("  改前 stage.scrollHeight=%s  改后=%s" % (b["__meta"]["stageScrollH"], a["__meta"]["stageScrollH"]))
    print("  改前 JS 错误=%s  改后 JS 错误=%s"
          % (b["__meta"].get("errs"), a["__meta"].get("errs")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
