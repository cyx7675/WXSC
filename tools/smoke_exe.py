# -*- coding: utf-8 -*-
"""打包后冒烟：启动新 exe，确认它不只是"进程活着"，而是真的弹出了窗口。

检查三项：
  1) 进程能否存活 12 秒 —— 抓「PyInstaller 漏打包模块 → 启动即崩」
     （新增 telemetry_config.py 之后尤其要验：launcher_web.py import 了它，
      没被收进 exe 就会在启动时 ImportError 直接退出）
  2) 是否创建了可见窗口 —— 进程活着但窗口没出来，说明卡在初始化，
     典型是 QtWebEngine 资源（icudtl.dat / *.pak / QtWebEngineProcess.exe）
     没被打进去，表现出来就是"双击没反应"
  3) 窗口标题是否带版本号 —— 顺带确认跑的是新产物，不是旧 exe

用法：python tools\\smoke_exe.py
"""
import os
import sys
import time
import ctypes
import subprocess

# --- 输出编码自保（管道捕获时 Python 会按 GBK 编码，中文/✅ 会崩） ----------
def _ensure_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(s, "encoding", "") or "").lower()
            if "utf" not in enc:
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_ensure_utf8()

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(HERE, "无线数传上位机_v2.exe")
NAME = "无线数传上位机_v2.exe"
TITLE_KEY = "CSUST Racing Telemetry"      # 窗口标题关键字，见 launcher_web.py L631


def run_cmd(args, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("gbk", errors="replace")
    except Exception:
        return ""


def visible_titles():
    """枚举所有可见窗口的标题（纯 ctypes，不依赖 pywin32）。"""
    titles = []
    user32 = ctypes.windll.user32
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        try:
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    titles.append(buf.value)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(PROC(cb), 0)
    except Exception:
        pass
    return titles


def main():
    if not os.path.exists(EXE):
        print("EXE NOT FOUND:", EXE)
        sys.exit(1)
    print("size: %.1f MB" % (os.path.getsize(EXE) / 1048576.0))

    run_cmd(["taskkill", "/F", "/IM", NAME, "/T"])   # 清掉残留进程
    time.sleep(1)

    p = subprocess.Popen([EXE], cwd=HERE)
    print("launched pid", p.pid)

    dead_at = None
    found_title = None
    for i in range(1, 13):          # 最多等 12 秒
        time.sleep(1)
        rc = p.poll()
        if rc is not None:
            dead_at = (i, rc)
            break
        # onefile 是父进程+子进程，窗口不一定挂在启动的这个 pid 上，
        # 所以直接按标题关键字全屏找，比按 pid 找可靠
        for t in visible_titles():
            if TITLE_KEY in t:
                found_title = t
                break
        if found_title:
            print("  t=%ds 窗口已出现: %s" % (i, found_title))
            break
        if i % 4 == 0:
            print("  t=%ds still running (rc=None)" % i)

    ok = True
    if dead_at:
        print("RESULT: 进程在 %ds 退出，退出码 %s -> 启动崩溃（多半是模块没打进去）" % dead_at)
        ok = False
    elif not found_title:
        print("RESULT: 进程活着但 12 秒内没出现窗口 -> 卡在初始化")
        print("       常见原因：QtWebEngine 资源没打进 exe，或杀软拦截了临时目录")
        ok = False
    else:
        print("RESULT: 启动正常，窗口标题 = %s" % found_title)

    if p.poll() is None:
        p.kill()
    run_cmd(["taskkill", "/F", "/IM", NAME, "/T"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
