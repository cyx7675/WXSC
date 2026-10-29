# -*- coding: utf-8 -*-
"""gl_probe.py —— 图形后端现场排查：逐个试哪个后端不闪，不用重新打包。

为什么需要这个
--------------
本机是 Optimus 双显卡（NVIDIA RTX 4060 独显负责渲染，显示输出通常走核显），
帧要跨 GPU 拷贝一次；该拷贝若与 Qt 合成 / VBlank 不同步，就表现为偶发闪烁。
所以「GPU 全开 = 闪、GPU 全关 = 卡」并不是唯二的选项 ——
换【图形后端】是另一个维度，可能避开那条跨显卡链路。

用法
----
    双击 诊断_闪烁排查.bat（推荐），或：
    <venv>\\Scripts\\python.exe tools\\gl_probe.py

流程：选一个后端 -> 程序启动 -> 你观察是否还闪 -> 关掉程序 -> 回到菜单换下一个。
确认某个后端不闪之后，把它固化进 launcher_web.py 再打包即可。
"""
import os
import sys
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "launcher_web.py")
REG_PATH = r"Software\Microsoft\DirectX\UserGpuPreferences"

# (预设名, 说明)  —— 预设名要和 launcher_web.py 里 _GL_PRESETS 的键一致
PRESETS = [
    ("default",     "不覆盖（Qt 默认 D3D11）— 基准，用来确认闪烁确实存在"),
    ("d3d9",        "ANGLE/D3D9 — 老的交换链路径"),
    ("d3d11on12",   "ANGLE/D3D11on12 — 走 D3D12 交换链"),
    ("gl",          "ANGLE/OpenGL — Chromium 侧换 GL"),
    ("vulkan",      "ANGLE/Vulkan — 换 Vulkan 交换链"),
    ("warp",        "D3D11 WARP — 微软软件光栅器，但仍走 GPU 管线"),
    ("inproc",      "--in-process-gpu — 去掉跨进程纹理传递"),
    ("novsync",     "--disable-gpu-vsync — 关掉 GPU 垂直同步"),
    ("qt-software", "QSG_RHI_BACKEND=software — Qt 场景图走软件合成"),
    ("qt-d3d11",    "QSG_RHI_BACKEND=d3d11 — Qt 场景图走 D3D11"),
    ("qt-vulkan",   "QSG_RHI_BACKEND=vulkan — Qt 场景图走 Vulkan"),
    ("qt-opengl",   "QSG_RHI_BACKEND=opengl — Qt 场景图走 OpenGL"),
]


def current_default():
    """从 launcher_web.py 读出 DEFAULT_GL_PRESET（用 ast 解析，避免 import 副作用）。"""
    try:
        import ast
        with open(os.path.join(HERE, "launcher_web.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if getattr(t, "id", "") == "DEFAULT_GL_PRESET":
                        return node.value.value
    except Exception:
        pass
    return ""


def launch(preset):
    env = dict(os.environ)
    env["TELEMETRY_GL"] = preset
    env["PYTHONIOENCODING"] = "utf-8"
    print("\n  >>> 正在用后端 [%s] 启动，观察是否还闪烁…\n" % preset)
    try:
        subprocess.call([sys.executable, APP], cwd=HERE, env=env)
    except KeyboardInterrupt:
        pass
    print("\n  <<< 程序已关闭，回到菜单。\n")


def set_gpu_pref(path, pref):
    """写 Windows 每程序 GPU 偏好：1=省电(核显) 2=高性能(独显) 0=让系统决定。"""
    try:
        import winreg
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                 winreg.KEY_SET_VALUE)
        if pref == 0:
            try:
                winreg.DeleteValue(key, path)
                msg = "已清除该程序的 GPU 偏好（恢复系统默认）"
            except FileNotFoundError:
                msg = "本来就没有设置过"
        else:
            winreg.SetValueEx(key, path, 0, winreg.REG_SZ, "GpuPreference=%d;" % pref)
            msg = "已设为 %s" % ("省电/核显" if pref == 1 else "高性能/独显")
        winreg.CloseKey(key)
        print("\n  [OK] %s\n       对象：%s\n       改完要【完全退出程序再重开】才生效。" % (msg, path))
    except Exception as e:
        print("\n  [失败] 写注册表出错：%s" % e)
    input("\n  按回车返回菜单…")


def gpu_pref_menu():
    exe = os.path.join(HERE, "无线数传上位机_v2.exe")
    while True:
        print("""
──────────────────────────────────────────────────────────
  Windows 每程序 GPU 偏好（Optimus 跨显卡问题的正面解法）
──────────────────────────────────────────────────────────
  原理：显示输出走核显时，让程序也用核显渲染，就不存在跨 GPU 拷贝，
        跨显卡撕裂闪烁会直接消失，且【不牺牲任何流畅度】。

  对象：%s

    [1] 设为 省电 / 核显（推荐先试这个）
    [2] 设为 高性能 / 独显
    [0] 清除设置，恢复系统默认
    [b] 返回上一级
──────────────────────────────────────────────────────────""" % exe)
        c = input("  选择：").strip().lower()
        if c == "b":
            return
        if c in ("1", "2"):
            set_gpu_pref(exe, int(c))
        elif c == "0":
            set_gpu_pref(exe, 0)
        else:
            print("  无效输入。")


def main():
    dflt = current_default()
    while True:
        print("""
══════════════════════════════════════════════════════════
  闪烁排查 · 图形后端切换（源码启动，秒级生效，不用打包）
══════════════════════════════════════════════════════════
  挑一个跑起来看还闪不闪；关掉程序就回到这里换下一个。""")
        for i, (name, desc) in enumerate(PRESETS, 1):
            mark = " ★当前默认" if name == dflt else ""
            print("   %2d) %-12s %s%s" % (i, name, desc, mark))
        print("""
    g) Windows 每程序 GPU 偏好设置（核显/独显，正面解法）
    q) 退出
══════════════════════════════════════════════════════════""")
        choice = input("  选择：").strip().lower()
        if choice == "q":
            return
        if choice == "g":
            gpu_pref_menu()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(PRESETS):
            launch(PRESETS[int(choice) - 1][0])
        else:
            print("  无效输入。")


if __name__ == "__main__":
    main()
