# -*- coding: utf-8 -*-
"""_asar.py —— 最小的 asar 读取器（读 DSH 安装包里的源码/文档）

为什么自己写：本机没有 asar CLI，也没有 @electron/asar；
而 app.asar 的格式很简单（JSON 头 + 顺序数据段），够用了。

asar 布局（实测本机 app.asar）：
    0..3   UInt32LE = 4
    4..7   UInt32LE = h2          （头块大小）
    8..11  UInt32LE = json_size
    16..16+json_len  JSON 头（json_len = JSON 自身长度）
    数据段起点 = 8 + h2           ← 条目的 offset 相对这里

用法：
    python tools/_asar.py list  <关键字>        # 列出路径
    python tools/_asar.py cat   <asar内路径>    # 打印文件内容
    python tools/_asar.py find  <关键字> <行数>  # 在所有 .js/.md/.yaml 里搜关键字
"""
import json
import struct
import sys

ASAR = r"F:\deepseek harness\resources\app.asar"


def _load():
    f = open(ASAR, "rb")
    head = f.read(16)
    _a, h2, jsz = struct.unpack("<III", head[:12])
    f.seek(16)
    raw = f.read(jsz).decode("utf-8", "replace")
    hdr, _ = json.JSONDecoder().raw_decode(raw)
    base = 8 + h2
    return f, hdr, base


def _walk(node, path=""):
    out = []
    for name, meta in node.get("files", {}).items():
        full = path + "/" + name
        if "files" in meta:
            out += _walk(meta, full)
        else:
            out.append((full, meta))
    return out


def cmd_list(kw=""):
    f, hdr, base = _load()
    for path, meta in _walk(hdr):
        if kw.lower() in path.lower():
            print("%10d  %s" % (meta.get("size", 0), path))


def cmd_cat(target):
    f, hdr, base = _load()
    for path, meta in _walk(hdr):
        if path == target or path.lstrip("/") == target.lstrip("/"):
            off = base + int(meta.get("offset", 0))
            f.seek(off)
            data = f.read(int(meta.get("size", 0)))
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            print(data.decode("utf-8", "replace"))
            return
    print("NOT FOUND:", target, file=sys.stderr)
    sys.exit(2)


def cmd_find(kw, limit=40):
    f, hdr, base = _load()
    n = 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for path, meta in _walk(hdr):
        if not path.lower().endswith((".js", ".md", ".yaml", ".yml", ".json")):
            continue
        if meta.get("size", 0) > 4_000_000:
            continue
        f.seek(base + int(meta.get("offset", 0)))
        txt = f.read(int(meta.get("size", 0))).decode("utf-8", "replace")
        if kw.lower() in txt.lower():
            n += 1
            print("HIT %s" % path)
            if n >= int(limit):
                return


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "cat":
        cmd_cat(sys.argv[2])
    elif cmd == "find":
        cmd_find(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 40)
    else:
        print(__doc__)
