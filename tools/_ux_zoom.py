import sys

from PIL import Image

src, out = sys.argv[1], sys.argv[2]
x0, y0, x1, y1, k = [int(v) for v in sys.argv[3:8]]
im = Image.open(src).convert("RGB").crop((x0, y0, x1, y1))
im = im.resize((im.width * k, im.height * k), Image.NEAREST)
im.save(out)
print("SAVED", out, im.size)
