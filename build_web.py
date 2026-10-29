# -*- coding: utf-8 -*-
"""build_web.py —— 一键打包：把改好的源码打成单个 exe。

================================================================================
★ 日常用法：双击根目录的「一键打包.bat」即可，不用记命令。
  命令行用法（在 F:\\上位机\\v2 下）：
      python build_web.py              # 完整流程：关进程→清缓存→自检→打包→校验
      python build_web.py --check-only # 只跑自检，不打包（改完配置先跑这个，3 秒）
      python build_web.py --quick      # 跳过自检，只打包（改了界面样式时用）
      python build_web.py --no-explorer# 打完不自动打开文件夹

================================================================================
流水线每一步在干什么、失败了怎么定位：

  步骤 0  环境检查      确认 Python 依赖齐全（PyInstaller / PyQt6 / pyserial）
  步骤 1  关闭旧 exe    不关的话新产物覆盖不了，PyInstaller 会报权限错误
  步骤 2  清理构建缓存  ★ 最关键的一步。不清会出现「exe 是新的、界面是旧的」，
                        这是本项目最容易浪费时间的一个坑
  步骤 3  语法自检      py_compile 三个 py + node --check app.js，
                        低级错误 3 秒内发现，不用等打包 3 分钟
  步骤 4  协议自检      python selftest.py，确认配置表和解析逻辑仍然自洽
  步骤 5  PyInstaller   真正的打包
  步骤 6  产物校验      检查 exe 存在、大小合理、时间戳是刚刚生成的
  步骤 7  打开文件夹    方便直接双击运行新 exe

任何一步失败都会打印带编号的原因和解决办法，并且**不会**把半成品当成成功。
================================================================================
"""
import os
import sys
import ctypes
import json
import shutil
import subprocess
import time

# Windows 控制台默认 GBK，直接 print 中文可能抛 UnicodeEncodeError。
# 这里强制切到 UTF-8 输出；失败也不影响运行（只是个别字符可能显示成 ?）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ----------------------------------------------------------------------------
# 本机回收站 COM 调用损坏，os.remove 会被「安全删除」拦截抛错，
# PyInstaller 清理阶段必失败。这里仅针对本次打包临时文件，用 ctypes 真删绕开。
# ⚠️ 不要删这一段，否则打包会在最后一步崩。
# ----------------------------------------------------------------------------
try:
    import sitecustomize

    _kernel32 = ctypes.windll.kernel32

    def _real_delete(abs_path):
        try:
            if os.path.isfile(abs_path):
                _kernel32.DeleteFileW(ctypes.c_wchar_p(abs_path))
            elif os.path.isdir(abs_path):
                _kernel32.RemoveDirectoryW(ctypes.c_wchar_p(abs_path))
        except Exception:
            pass
        return True

    sitecustomize._try_trash = lambda p: _real_delete(p)
except Exception:
    pass

from PyInstaller.__main__ import run  # noqa: E402  (必须在 sitecustomize 补丁之后)

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "telemetry_web")
ICON = os.path.join(HERE, "logo.ico")
CACHE_DIR = os.path.join(HERE, "build_web")
EXE_NAME = "无线数传上位机_v2"
EXE_PATH = os.path.join(HERE, EXE_NAME + ".exe")
LOG_PATH = os.path.join(HERE, "tools", "_build_log.txt")

QUICK = "--quick" in sys.argv
NO_EXPLORER = "--no-explorer" in sys.argv
CHECK_ONLY = "--check-only" in sys.argv   # 只跑自检不打包，几秒钟出结果

T0 = time.time()
_log_lines = []


def run_cmd(args, timeout=60):
    """跑外部命令，输出按 GBK 容错解码。

    Windows 中文系统的 tasklist / taskkill 输出是 GBK，按 UTF-8 解码会抛
    UnicodeDecodeError（还会在后台线程里炸出 traceback）。这里统一容错处理。

    另外给子进程注入 PYTHONIOENCODING=utf-8：Python 被管道捕获时会按系统
    locale(GBK) 编码 stdout，脚本里一旦有 ✅ 之类的字符就抛
    UnicodeEncodeError 直接崩掉，退出码非 0 会被这里误判成"自检不通过"。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError:
        # 命令根本不存在（典型：这台机器没装 Node.js）。必须说清楚，
        # 否则会被上层当成"你的代码有语法错误"，完全是误导。
        return -1, "__NOT_FOUND__:%s" % args[0]
    except Exception as e:
        return -1, "__ERROR__:%s" % e
    out = (r.stdout or b"") + (r.stderr or b"")
    for enc in ("gbk", "utf-8"):
        try:
            return r.returncode, out.decode(enc)
        except Exception:
            continue
    return r.returncode, out.decode("utf-8", errors="replace")


def nuke(path):
    """强删文件/目录（含非空目录）。

    本机回收站 COM 调用损坏，Python 的标准删除会被「安全删除」包装拦截，
    rmtree(ignore_errors=True) 会静默失败、目录还在 —— 那样 PyInstaller 会
    复用旧缓存，产出「新 exe + 旧界面」。这里直接用 Win32 API 绕开。
    """
    try:
        if os.path.isfile(path):
            ctypes.windll.kernel32.DeleteFileW(ctypes.c_wchar_p(path))
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        ctypes.windll.kernel32.DeleteFileW(ctypes.c_wchar_p(os.path.join(root, f)))
                    except Exception:
                        pass
                for d in dirs:
                    try:
                        ctypes.windll.kernel32.RemoveDirectoryW(ctypes.c_wchar_p(os.path.join(root, d)))
                    except Exception:
                        pass
            ctypes.windll.kernel32.RemoveDirectoryW(ctypes.c_wchar_p(path))
    except Exception:
        pass
    return not os.path.exists(path)


def say(msg=""):
    """打印到控制台并记进日志。"""
    print(msg)
    _log_lines.append(str(msg))


def section(n, title):
    say("")
    say("─" * 62)
    say("  步骤 %s  %s" % (n, title))
    say("─" * 62)


def fail(reason, howto):
    """打印失败原因与解决办法，然后带着日志退出。"""
    say("")
    say("❌ %s" % reason)
    for line in howto.strip().split("\n"):
        say("   %s" % line)
    say("")
    say("详细日志：%s" % LOG_PATH)
    write_log()
    sys.exit(1)


def write_log():
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            secs = time.time() - T0
            f.write("打包耗时 %.1f 秒\n%s\n" % (secs, "\n".join(_log_lines)))
    except Exception:
        pass


def human_size(n):
    return "%.1f MB" % (n / 1024.0 / 1024.0)


# =============================================================================
say("=" * 62)
say("  无线数传上位机 · 一键打包")
say("  源码目录：%s" % HERE)
say("=" * 62)

# ---------------------------- 步骤 0：环境检查 -------------------------------
section(0, "环境检查")
missing = []
for mod in ("serial", "PyQt6", "PyInstaller"):
    try:
        __import__(mod)
    except Exception:
        missing.append(mod)
if missing:
    fail("缺少依赖：%s" % "、".join(missing),
         "运行：python -m pip install -r requirements.txt\n"
         "（推荐用项目的虚拟环境：\n"
         "  C:\\Users\\Administrator\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe）")
for need, desc in ((WEB_DIR, "前端目录 telemetry_web"), (ICON, "图标 logo.ico")):
    if not os.path.exists(need):
        fail("%s 不存在：%s" % (desc, need), "确认文件没有被误删或改名。")
say("  ✅ 依赖与文件齐全")

# ---------------------------- 步骤 1：关闭旧 exe -----------------------------
if CHECK_ONLY:
    section(1, "关闭旧 exe —— 已跳过（--check-only）")
else:
    section(1, "关闭正在运行的旧 exe")
    _, out1 = run_cmd(["tasklist", "/FI", "IMAGENAME eq %s.exe" % EXE_NAME])
    running = EXE_NAME in out1
    if running:
        say("  发现旧 exe 正在运行，正在关闭…")
        run_cmd(["taskkill", "/F", "/IM", EXE_NAME + ".exe", "/T"])
        time.sleep(1.2)   # 等系统释放文件句柄，立刻打包会撞 PermissionError
        say("  ✅ 已关闭")
    else:
        say("  ✅ 旧 exe 未在运行")

# ---------------------------- 步骤 2：清理缓存 -------------------------------
if CHECK_ONLY:
    section(2, "清理构建缓存 —— 已跳过（--check-only）")
else:
    section(2, "清理构建缓存（防止「新 exe + 旧界面」）")
    if os.path.isdir(CACHE_DIR):
        say("  正在删除 build_web/（Win32 强删，绕过损坏的回收站机制）…")
        if not nuke(CACHE_DIR):
            fail("构建缓存无法删除：%s" % CACHE_DIR,
                 "通常是被杀毒软件或资源管理器占用。\n"
                 "关掉打开的文件夹窗口，或手动删除该目录后重试。")
        say("  ✅ 已删除")
    else:
        say("  ✅ 无缓存，无需清理")

# ---------------------------- 步骤 3：语法自检 -------------------------------
if QUICK:
    section(3, "语法自检 —— 已跳过（--quick）")
else:
    section(3, "语法自检")
    py_files = ["launcher_web.py", "protocol.py", "telemetry_config.py", "selftest.py"]
    for pf in py_files:
        p = os.path.join(HERE, pf)
        rc, out31 = run_cmd([sys.executable, "-m", "py_compile", p])
        if rc != 0:
            fail("%s 有语法错误" % pf, out31.strip()[:600])
        say("  ✅ Python  %s" % pf)
    js = os.path.join(WEB_DIR, "app.js")
    rc, out32 = run_cmd(["node", "--check", js])
    if str(out32).startswith("__NOT_FOUND__"):
        # 没装 Node 只影响"提前发现低级语法错误"这一步，不影响打包本身。
        # 直接放行，否则会卡死在一条跟用户无关的报错上。
        say("  ⚠️  未检测到 Node.js，跳过 JS 语法检查（不影响打包）")
        say("      想启用：装 Node 后重开命令行即可")
    elif rc != 0:
        fail("app.js 有语法错误", out32.strip()[:600])
    else:
        say("  ✅ JS      telemetry_web/app.js")

# ---------------------------- 步骤 4：协议自检 -------------------------------
if QUICK:
    section(4, "协议自检 —— 已跳过（--quick）")
else:
    section(4, "协议自检（selftest.py）")
    rc, out4 = run_cmd([sys.executable, os.path.join(HERE, "selftest.py")], timeout=180)
    lines = (out4 or "").strip().split("\n")
    for line in lines[-6:]:
        say("  %s" % line)
    if rc != 0:
        # 分清两种失败：断言真的失败(配置错了) vs 脚本自己崩了(环境问题)。
        # 崩溃时把完整尾部打出来，否则用户只看到"自检未通过"却不知道为什么。
        crashed = "Traceback" in (out4 or "")
        if crashed:
            say("")
            say("  ── 自检脚本自身的完整报错 ──")
            for line in lines[-25:]:
                say("  %s" % line)
        fail("协议自检%s" % ("脚本崩溃（不是配置问题）" if crashed else "未通过"),
             ("这是脚本运行环境问题，不是你的配置错了。\n"
              "常见：被 GBK 终端/杀软拦截、依赖缺失、文件被占用。\n"
              "可以手动跑一次看完整报错：python selftest.py"
              if crashed else
              "说明 telemetry_config.py 的配置和解析逻辑对不上了。\n"
              "常见原因：改了 fields 忘了改 total、偏移量没和固件对齐。"))

    # 录制落盘端到端：改协议最容易悄悄坏的就是这条链路（界面看不出来，
    # 但录出来的 CSV 会串列或全 0），所以在打包前也跑一次。
    section(4.5, "录制落盘自检（tools/record_check.py）")
    rc5, out5 = run_cmd([sys.executable, os.path.join(HERE, "tools", "record_check.py")],
                        timeout=180)
    for line in (out5 or "").strip().split("\n")[-4:]:
        say("  %s" % line)
    if rc5 != 0:
        crashed = "Traceback" in (out5 or "")
        if crashed:
            for line in (out5 or "").strip().split("\n")[-20:]:
                say("  %s" % line)
        fail("录制落盘自检%s" % ("脚本崩溃" if crashed else "未通过"),
             "CSV 表头与数据列数对不上、或录出来的值不对。\n"
             "常见原因：改了字段名/包类型但 CSV 派生没跟上、REC 相关常量不一致。\n"
             "手动复现：python tools\\record_check.py")

# ---------------------- 步骤 4.6：配置生效矩阵（最要命的一类） -----------------
if QUICK:
    section(4.6, "配置生效矩阵自检 —— 已跳过（--quick）")
else:
    section(4.6, "配置生效矩阵自检（改了 config 界面到底动不动）")
    rc46, out46 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "config_matrix_audit.py")],
        timeout=300)
    lines46 = [l for l in (out46 or "").split("\n") if l.strip()]
    for line in lines46[-8:]:
        say("  %s" % line)
    if rc46 != 0:
        fail("有配置项改了但界面不跟着变",
             "这是最常见也最容易被漏掉的一类问题：配置改对了、自检也全绿，\n"
             "   但界面上的刻度/单位还是旧文案。上面已经列出了失效项。\n"
             "   手动复现：python tools\\config_matrix_audit.py")

# ---------------------- 步骤 4.7：改系数会不会连自检一起搞红 -----------------
# （本步位置原是「量程 / 单位在线编辑器自检」—— 该编辑器 2026-10-06 按用户
#   要求整块移除，tools/range_editor_check.py 保留作历史参考，不再进流水线。
#   免打包改配置的入口现在只剩 telemetry_user.json。）
# ★ 2026-10-09 用户亲历：改 SPEED 的 scale 后 selftest / record_check 立刻报红，
#   根因是「位数由 scale 反推 + 期望值写死」。这一步直接把真实配置里的 scale
#   变异四档再跑一遍自检，专治这类「一改系数就满江红」。
if QUICK:
    section(4.7, "系数变异自检 —— 已跳过（--quick）")
else:
    section(4.7, "系数变异自检（改 scale 不该让自检误报）")
    rc47, out47 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "scale_mutate_check.py")],
        timeout=300)
    for line in (out47 or "").strip().split("\n")[-5:]:
        say("  %s" % line)
    if rc47 != 0:
        fail("改了换算系数，自检就跟着报错",
             "位数/期望值还有地方在偷偷依赖 scale（或写死了数字）。\n"
             "   位数应只由第 3 区 ADC_DECIMALS / 第 5 区 FIELD_DECIMALS 决定，\n"
             "   scale 只管「一个原始码等于多少」，两者互不相干。\n"
             "   手动复现：python tools\\scale_mutate_check.py")

# ---------------------- 步骤 4.75：改字段偏移会不会连自检一起搞红 -------------
# ★ 2026-10-10 用户亲历：把 MOTOR 包 MOTOR_RPM 挪到偏移 10、TORQUE 挪到 9，
#   selftest 立刻红 5 项，但配置体检是绿的（偏移没重叠也没越界）。
#   根因在 protocol.py：组帧按 fields 列表顺序 append、把偏移当摆设，
#   解析却按 off 定位读 —— 列表顺序和偏移顺序一不一致就整体错位。
#   这一步穷举/采样各种合法布局（换顺序 + 插空洞），逐个验
#   「配置体检 / 组帧→解析往返 / 字节落点 == 声明偏移」。
if QUICK:
    section(4.75, "偏移变异自检 —— 已跳过（--quick）")
else:
    section(4.75, "偏移变异自检（改 fields 的偏移不该让自检误报）")
    rc475, out475 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "offset_mutate_check.py")],
        timeout=300)
    for line in (out475 or "").strip().split("\n")[-5:]:
        say("  %s" % line)
    if rc475 != 0:
        fail("改了字段偏移，自检就跟着报错",
             "组帧又退回成「按 fields 列表顺序拼」了 —— 偏移才是字节布局的\n"
             "   唯一真源，列表顺序只该决定 CSV / 界面的列顺序。\n"
             "   看 protocol.py 的 _build_typed_frame，必须按 off 写字节。\n"
             "   手动复现：python tools\\offset_mutate_check.py")

# ---------------------- 步骤 4.8：趋势三档量程 -------------------------------
if QUICK:
    section(4.8, "趋势量程自检 —— 已跳过（--quick）")
else:
    section(4.8, "趋势图三档量程自检（归一化 / 自适应 / 分组分带）")
    rc48, out48 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "trend_mode_check.py")],
        timeout=240)
    for line in (out48 or "").strip().split("\n")[-8:]:
        say("  %s" % line)
    if rc48 != 0:
        fail("趋势图量程模式异常",
             "三档量程有一档画不出曲线，或分带没分出来。\n"
             "   手动复现：python tools\\trend_mode_check.py")

# ---------------------- 步骤 4.85：CSV 列名与数据会不会错位 -------------------
# ★ 2026-10-10 用户要求：ADC / MOTOR / 姿态区都要能改顺序与量程，
#   CSV 列必须跟着改，且「数据与通道名称」绝不能对不上。
#   这一步走端到端：组真实帧 → 解析 → 按 launcher 的方式取值 → 拼 CSV 行，
#   再逐列断言「CH07_xxx 这一格 == 物理第 7 路的值」；
#   然后把 ADC_ORDER / MOTOR / NAV 的顺序打乱若干种，每种都要对得上。
if QUICK:
    section(4.85, "CSV 列对应自检 —— 已跳过（--quick）")
else:
    section(4.85, "CSV 列对应自检（改顺序后列名与数据必须一一对应）")
    rc485, out485 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "column_order_check.py")],
        timeout=240)
    for line in (out485 or "").strip().split("\n")[-5:]:
        say("  %s" % line)
    if rc485 != 0:
        fail("CSV 列名与数据错位",
             "改了顺序之后，某一列的数和它的列名对不上了。\n"
             "   界面卡片顺序 / CSV 列名顺序 / CSV 取值顺序必须全部走\n"
             "   CFG.adc_out_order()（MOTOR、姿态则走第 4 区 fields 的书写顺序）。\n"
             "   手动复现：python tools\\column_order_check.py")

# ---------------------- 步骤 4.86：零点 / 偏置变异 ----------------------------
if QUICK:
    section(4.86, "零点变异自检 —— 已跳过（--quick）")
else:
    section(4.86, "零点 / 偏置变异自检（零点在中间的传感器要能双向显示）")
    rc486, out486 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "zero_mutate_check.py")],
        timeout=240)
    for line in (out486 or "").strip().split("\n")[-4:]:
        say("  %s" % line)
    if rc486 != 0:
        fail("零点配置下有静默失效",
             "填了 zero 之后，下面几件事必须同时成立：\n"
             "   ① 原始码 == zero 时显示 0  ② 显示量程跨 0（min<0<max）\n"
             "   ③ 界面量程 = 协议量程 = CSV 可表示范围  ④ CSV 列名仍对应物理通道\n"
             "   ⑤ 组帧写进去的码 = 显示值/scale + zero\n"
             "   手动复现：python tools\\zero_mutate_check.py")

# ---------------------- 步骤 4.88：历史真实报文回归 ---------------------------
if QUICK:
    section(4.88, "真实报文回归 —— 已跳过（--quick）")
else:
    section(4.88, "真实报文回归（用你抓到的真帧反验字节序 / 12bit 顺序 / scale）")
    rc488, out488 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "real_frame_regress.py")],
        timeout=120)
    for line in (out488 or "").strip().split("\n")[-4:]:
        say("  %s" % line)
    if rc488 != 0:
        fail("历史真帧已被当前配置解错",
             "MOTOR / ADC 真帧是协议结论的唯一物证（小端、msb、438 码 = 438 V）。\n"
             "   现在配置解出来的结果和当时人工读出的字节不一致，\n"
             "   典型原因：改了 MOTOR_ENDIAN、ADC_12BIT_ORDER、字段偏移或 VOLTAGE 的 scale。\n"
             "   手动复现：python tools\\real_frame_regress.py")

# ---------------------- 步骤 4.9：外部配置往返（保存 -> 落盘 -> 重启） -------
if QUICK:
    section(4.9, "外部配置自检 —— 已跳过（--quick）")
else:
    section(4.9, "外部配置 telemetry_user.json 往返自检（保存/重启/幂等）")
    rc49, out49 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "user_cfg_roundtrip.py")],
        timeout=180)
    for line in (out49 or "").strip().split("\n")[-10:]:
        say("  %s" % line)
    if rc49 != 0:
        fail("外部配置往返失败",
             "「界面改量程 -> 保存 -> 重启还在」这条链路断了。\n"
             "   它失效时不会报错、其余自检全绿，只有用户会发现保存没用。\n"
             "   手动复现：python tools\\user_cfg_roundtrip.py")

# ---------------------- 步骤 4.10：配置权威性（改哪里才生效） ---------------
if QUICK:
    section(4.10, "配置权威性审计 —— 已跳过（--quick）")
else:
    section(4.10, "配置权威性审计（同一个参数不允许有两个真源）")
    rc410, out410 = run_cmd(
        [sys.executable, os.path.join(HERE, "tools", "cfg_authority_audit.py")],
        timeout=180)
    for line in (out410 or "").strip().split("\n")[-10:]:
        say("  %s" % line)
    if rc410 != 0:
        fail("存在改动不生效的配置项",
             "有参数只在 app.js 里写了、第 5 区配不到（影子参数）；\n"
             "   或有参数写在第 5 区但前端从来不读（孤儿键）。\n"
             "   两者都表现为「改了配置文件、打包了、界面纹丝不动」。\n"
             "   手动复现：python tools\\cfg_authority_audit.py")

# ---------------------------- 步骤 5：打包 -----------------------------------
if CHECK_ONLY:
    secs = time.time() - T0
    say("")
    say("=" * 62)
    say("  ✅ 自检全部通过（--check-only，未打包）· 耗时 %.1f 秒" % secs)
    say("=" * 62)
    write_log()
    sys.exit(0)

section(5, "PyInstaller 打包（约 1–5 分钟，请稍候）")
if os.path.exists(EXE_PATH):
    if nuke(EXE_PATH):
        say("  已删除旧产物")
    else:
        fail("旧 exe 无法删除：%s" % EXE_PATH,
             "文件被占用。确认程序没在运行，也没在资源管理器里被选中。")

say("  正在打包…")
try:
    run([
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--icon", ICON,
        "--log-level", "WARN",
        "--distpath", HERE,
        "--workpath", CACHE_DIR,
        "--specpath", CACHE_DIR,
        "--add-data", "%s;telemetry_web" % WEB_DIR,
        "--add-data", "%s;." % ICON,
        "--hidden-import", "PyQt6.QtWebEngineWidgets",
        "--hidden-import", "PyQt6.QtWebEngineCore",
        "--hidden-import", "PyQt6.QtWebChannel",
        os.path.join(HERE, "launcher_web.py"),
    ])
except SystemExit as e:
    # PyInstaller 失败时会 sys.exit(非0)，必须拦下来给出人话，
    # 否则脚本直接死在这儿，日志也写不全。
    if e.code:
        fail("PyInstaller 打包失败（退出码 %s）" % e.code,
             "往上翻 PyInstaller 自己的报错。最常见三种：\n"
             "1) 模块没被自动收进去 → 加 --hidden-import\n"
             "2) 杀毒软件拦截了临时目录 → 加白名单后重试\n"
             "3) 磁盘空间不足（需要约 1 GB 余量）")
except Exception as e:
    fail("PyInstaller 执行异常：%s" % e,
         "完整日志：%s" % LOG_PATH)

# ---------------------------- 步骤 6：产物校验 -------------------------------
section(6, "产物校验")
if not os.path.exists(EXE_PATH):
    fail("没有生成 exe",
         "PyInstaller 可能中途失败。往上翻控制台的完整报错，\n"
         "或看 tools/_build_log.txt。")
size = os.path.getsize(EXE_PATH)
stale = time.time() - os.path.getmtime(EXE_PATH) > 300
if size < 50 * 1024 * 1024:
    fail("产物偏小（%s），可能不完整" % human_size(size),
         "正常产物约 190–220 MB。请重新打包。")
if stale:
    fail("产物时间戳不是刚才的，可能是旧的 exe",
         "删除 exe 后重新打包；确认磁盘有足够空间。")
say("  ✅ %s" % os.path.basename(EXE_PATH))
say("     大小 %s · 生成于 %s" % (
    human_size(size), time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(EXE_PATH)))))

secs = time.time() - T0
say("")
say("=" * 62)
say("  ✅ 打包完成，总耗时 %.1f 秒" % secs)
say("  %s" % EXE_PATH)
say("=" * 62)
write_log()

# ---------------------------- 步骤 7：打开文件夹 -----------------------------
if not NO_EXPLORER:
    try:
        subprocess.Popen(["explorer", "/select,", EXE_PATH])
    except Exception:
        pass
