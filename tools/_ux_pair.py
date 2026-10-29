# -*- coding: utf-8 -*-
"""_ux_pair.py —— 生成"N 版对照图"（人眼直接比）

用法：
  <venv>\\python.exe tools\\_ux_pair.py 输出.png "标题" 图1.png "标签1" 图2.png "标签2" [图3.png "标签3" ...]

行为：
  · 各图按**宽度对齐**（等比缩放到最宽的那张）；
  · 自上而下叠放，中间一条分隔线；
  · 左侧竖排标签条（每版一个颜色）＋ 每格左上角版本标签，便于汇报里用"第①②③版"引用。
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

BG = (231, 236, 243)
LBL_W = 52
GAP = 16
PAD = 20
COLORS = [(150, 160, 175), (14, 138, 98), (29, 78, 216), (154, 74, 6)]


def cjk_font(size):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        return 2
    out, title = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    if len(rest) % 2:
        print("参数应为：输出 标题 (图 标签)+")
        return 2
    panels = [(rest[i], rest[i + 1]) for i in range(0, len(rest), 2)]
    imgs = []
    W = 0
    for p, _lab in panels:
        im = Image.open(p).convert("RGB")
        W = max(W, im.width)
        imgs.append(im)
    scaled = []
    for im in imgs:
        if im.width != W:
            h = int(round(im.height * W / im.width))
            im = im.resize((W, h), Image.LANCZOS)
        scaled.append(im)

    head = 56 if title else 0
    H = head + PAD + sum(im.height + GAP for im in scaled) + PAD - GAP
    canvas = Image.new("RGB", (LBL_W + W + PAD * 2, H), BG)
    d = ImageDraw.Draw(canvas)
    if title:
        d.text((LBL_W + PAD, 16), title, fill=(20, 28, 41), font=cjk_font(28))
    fnum = cjk_font(24)
    ftag = cjk_font(16)

    y = head + PAD
    for i, (im, (_p, lab)) in enumerate(zip(scaled, panels)):
        col = COLORS[i % len(COLORS)]
        canvas.paste(im, (LBL_W + PAD, y))
        d.rectangle([0, y, LBL_W, y + im.height], fill=col)
        # 左上角版本标签（叠在图上）
        tag = "%d) %s" % (i + 1, lab)
        tw = d.textlength(tag, font=ftag)
        d.rectangle([LBL_W + PAD, y, LBL_W + PAD + tw + 14, y + 24], fill=col)
        d.text((LBL_W + PAD + 7, y + 4), tag, fill=(255, 255, 255), font=ftag)
        # 左侧竖排标签
        t = Image.new("RGBA", (160, 38), (0, 0, 0, 0))
        td = ImageDraw.Draw(t)
        td.text((8, 6), tag, fill=(255, 255, 255), font=fnum)
        t = t.rotate(90, expand=True)
        canvas.paste(t, (3, y + max(0, im.height // 2 - 80)), t)
        y += im.height + GAP
        if i < len(scaled) - 1:
            d.line([LBL_W, y - GAP // 2, canvas.width, y - GAP // 2],
                   fill=(120, 132, 150), width=2)
    canvas.save(out)
    print("SAVED", out, canvas.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
