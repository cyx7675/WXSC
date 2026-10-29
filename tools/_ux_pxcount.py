"""数一张图里"接近某个颜色"的像素数（用于确认画布/SVG 里某元素到底有没有画出来）。"""
import sys

from PIL import Image

src, r, g, b, tol = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
im = Image.open(src).convert("RGB")
px = im.load()
n = 0
xs, ys = [], []
for y in range(im.height):
    for x in range(im.width):
        c = px[x, y]
        if abs(c[0] - r) <= tol and abs(c[1] - g) <= tol and abs(c[2] - b) <= tol:
            n += 1
            xs.append(x)
            ys.append(y)
print("size=%sx%s  match=%d" % (im.width, im.height, n))
if n:
    print("  x:%d..%d  y:%d..%d" % (min(xs), max(xs), min(ys), max(ys)))
