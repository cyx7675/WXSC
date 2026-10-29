# -*- coding: utf-8 -*-
"""launcher_web.py -- 用 PyQt6 WebEngine 把无线数传 Web 前端包成本地窗口程序。

页面源：telemetry_web/index.html（手写 CSS + 本地字体，离线可用，无外部依赖）。
串口通信：主进程用 pyserial 真实打开 COM 口，调用 protocol.parse_stream 解析
真实帧，通过 QWebChannel 把数据推送给页面绘制。
数据落盘：CSV 录制直接在 Python 端写入磁盘（读取线程内），与前端渲染完全
解耦 —— 前端再卡、刷新再慢，也不会丢数据、不会拖慢写盘。
打包后资源位于 sys._MEIPASS，开发态位于脚本同目录。
"""
import os

# ---- 渲染模式：★ 只需改下面这一行，改完重新打包即可 ★ ----
#   "smooth" = 保留 GPU 渲染 + GPU 合成（滚动/拖动跟手；个别显卡驱动下可能闪烁）
#   "stable" = 全面禁用 GPU，纯 CPU 软件光栅（★绝对不会闪烁★；滚动/拖动略慢）
RENDER_MODE = "smooth"

# ══════════════════════════════════════════════════════════════════════════
#  ★ 图形后端：现场实测确定的最优解（诊断_闪烁排查.bat 菜单第 12 项）
#
#  测出的问题：本机是 Optimus 双显卡（NVIDIA RTX 4060 笔记本独显负责渲染，
#  显示输出走核显），帧需跨 GPU 拷贝一次。Windows 上 Qt 6 的场景图默认走
#  D3D11，与 Chromium 的 D3D11 交换链叠加时，该跨显卡拷贝会与合成不同步，
#  表现为偶发闪烁。之前的取舍是"GPU 全开(闪) / GPU 全关(卡)"二选一。
#
#  解法：把【Qt 场景图】的后端从 D3D11 换成 OpenGL（QSG_RHI_BACKEND=opengl）。
#  改的是 Qt 合成 WebEngine 纹理的那条路径，绕开了出问题的组合——
#  实测既不闪、也不牺牲滚动流畅度。
#
#  想继续排查/对比其他后端：设环境变量 TELEMETRY_GL=<预设名> 即可临时覆盖，
#  或双击「诊断_闪烁排查.bat」按菜单选（其中 "default" = 不覆盖的基准）。
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_GL_PRESET = "qt-opengl"

_GL_PRESETS = {
    # 名称            (追加的 Chromium 参数,          追加的 Qt 环境变量)
    "default":     ([], {}),
    "d3d11":       (["--use-gl=angle", "--use-angle=d3d11"], {}),
    "d3d9":        (["--use-gl=angle", "--use-angle=d3d9"], {}),
    "d3d11on12":   (["--use-gl=angle", "--use-angle=d3d11on12"], {}),
    "gl":          (["--use-gl=angle", "--use-angle=gl"], {}),
    "vulkan":      (["--use-gl=angle", "--use-angle=vulkan"], {}),
    # ★ 不提供 --use-gl=desktop：本机（RTX 4060 Laptop）实测直接崩溃
    #   （退出码 0xC0000409），列出来只会让人白试一轮。
    "warp":        (["--use-gl=angle", "--use-angle=d3d11-warp"], {}),
    # 说明：--use-gl=desktop 与 --use-gl=swiftshader 在本机实测都会直接崩溃
    # （0xC0000409）。Windows 上 QtWebEngine 必须走 ANGLE，故不提供这两项。
    "inproc":      (["--in-process-gpu"], {}),
    "novsync":     (["--disable-gpu-vsync"], {}),
    "qt-software": ([], {"QSG_RHI_BACKEND": "software"}),
    "qt-d3d11":    ([], {"QSG_RHI_BACKEND": "d3d11"}),
    "qt-vulkan":   ([], {"QSG_RHI_BACKEND": "vulkan"}),
    "qt-opengl":   ([], {"QSG_RHI_BACKEND": "opengl"}),
}
# 没显式指定就用实测最优的默认；指定了但名字不认，也回退到默认（不静默失效）
_GL_NAME = os.environ.get("TELEMETRY_GL", "").strip().lower()
if not _GL_NAME:
    _GL_NAME = DEFAULT_GL_PRESET
elif _GL_NAME not in _GL_PRESETS:
    print("[gl] 未知的 TELEMETRY_GL=%r，已回退默认 %s。可选：%s"
          % (_GL_NAME, DEFAULT_GL_PRESET, ", ".join(sorted(_GL_PRESETS))))
    _GL_NAME = DEFAULT_GL_PRESET
_GL_EXTRA, _GL_QT_ENV = _GL_PRESETS[_GL_NAME]
for _k, _v in _GL_QT_ENV.items():
    # Qt 场景图后端必须在 Qt 加载前落到环境变量里
    os.environ[_k] = _v
print("[gl] 图形后端：%s%s"
      % (_GL_NAME, ("  " + " ".join(_GL_EXTRA)) if _GL_EXTRA else ""))

# ---- Chromium 渲染参数：必须在导入 QtWebEngine 之前设置 ----
# 闪烁 vs 跟手是互相拉扯的，两种模式的取舍：
#   smooth：GPU 光栅 + GPU 合成，二者都走 GPU「一致」，滚动才跟手；
#           canvas 仍走 CPU（高频重绘是画面撕裂主源）。
#   stable：--disable-gpu 全禁，一切走 CPU 软件光栅，画面绝对稳，代价是滚动变慢。
_COMMON_FLAGS = [
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling",
    "--disable-ipc-flooding-protection",
    "--disable-dev-shm-usage",
]
if RENDER_MODE == "stable":
    _ANTI_FLICKER_FLAGS = (["--disable-gpu", "--disable-gpu-compositing",
                            "--disable-accelerated-2d-canvas"]
                           + _COMMON_FLAGS)
else:
    _ANTI_FLICKER_FLAGS = ["--disable-accelerated-2d-canvas"] + _COMMON_FLAGS
# 后端排查预设追加在最后，这样它在命令行里优先级最高、最能覆盖默认行为
_ANTI_FLICKER_FLAGS = _ANTI_FLICKER_FLAGS + list(_GL_EXTRA)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", " ".join(_ANTI_FLICKER_FLAGS))

import sys
import json
import math
import shutil
import threading
import time
from datetime import datetime

import serial
import serial.tools.list_ports

from PyQt6.QtCore import (QUrl, QSize, QFile, QIODevice, QObject, pyqtSignal,
                          pyqtSlot, QTimer, Qt)
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QFileDialog
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

import protocol

# ★ 遥测配置唯一来源：通道名 / 字段表 / CSV 列 / 曲线颜色全在这个文件里。
#   本项目所有 Python 代码都不再硬编码这些信息，改协议只改它。
import telemetry_config as CFG

# ---- 推送节流常量：数据源长度/频率不定，防止高频数据打爆 UI 线程 ----
FLUSH_INTERVAL_MS = 50      # 主线程批量推送间隔（20 Hz），与渲染帧率解耦
MAX_FLUSH_FRAMES = 1500     # 单次推送最大帧数（洪峰限流，保留最近帧）
MAX_HEX_LINES = 20          # 单次推送最大 HEX 行数（HEX 合并为一次信号）
REC_FLUSH_EVERY = 200       # 录制时每 N 行 flush 一次磁盘
REC_WRITE_BUFFER = 256      # 内存攒行阈值：攒够再一次性 write，显著减少读线程 syscall

# ---- 串口读取节拍（★ 决定"数据会不会堵塞"的关键参数）----
# 曾踩的坑：ser.read(4096) 会一直读到「凑满 4096 字节」或「timeout 到期」才返回。
# 真实带宽只有 4350 B/s，4096 永远凑不满 -> 每次都要等满 0.2s 才吐一批，
# 实测数据到达节拍只有 4.9 Hz：界面一顿一顿、端到端延迟 200ms、前端趋势取样
# 也被拖到 5Hz（600 点变成 123 秒，而坐标轴还标着 30 秒）。
# 修法：只读「当前已有的字节」(in_waiting)，有数据立刻返回；空闲时 read(1)
# 最多阻塞 SERIAL_TIMEOUT，不会忙等。实测节拍回到 ~20 Hz。
SERIAL_TIMEOUT = CFG.SERIAL.get("timeout", 0.05)   # 读超时（s）：只对空闲等待有影响
MAX_PENDING_FRAMES = 3000   # 待推送队列上限：主进程被弹窗/对话框阻塞时不让内存无限涨

# ---- 串口链路参数：数据位 / 校验位 / 停止位 ----
# 全部来自 telemetry_config.py 第 1 区 SERIAL。改那里即可，不用动本文件。
# （界面上只暴露「端口 + 波特率」两个最常用的选项）
_SERIAL_BYTESIZE = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS,
                    8: serial.EIGHTBITS}.get(CFG.SERIAL.get("bytesize", 8),
                                             serial.EIGHTBITS)
_SERIAL_PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                  "O": serial.PARITY_ODD}.get(CFG.SERIAL.get("parity", "N"),
                                              serial.PARITY_NONE)
_SERIAL_STOPBITS = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE,
                    2: serial.STOPBITS_TWO}.get(CFG.SERIAL.get("stopbits", 1),
                                                serial.STOPBITS_ONE)

# ---- 带 fields 的「普通包」：类型号 -> 信号名列表 ----
# 从配置第 4 区自动派生，所以你在配置里加字段或加新包类型，这里自动跟上。
# （ADC 包是 12bit 紧凑打包，不在这个表里，走单独分支）
_TYPED_PACKETS = {tid: [f[0] for f in spec.get("fields", [])]
                  for tid, spec in CFG.PROTOCOL.items()
                  if "fields" in spec}

# 录制成行时要求的包顺序（类型号升序，与 CFG.csv_groups() 保持一致）
_TYPED_ORDER = [tid for tid, _names in CFG.csv_groups()]
HEX_EMIT_INTERVAL = 0.10    # HEX 监视器最快刷新间隔（s）：避免 400 行/s 灌进渲染进程

# ---- 数据回放进度上报 ----
# 回放 tick 是 10ms（保证节拍精度），但进度条不需要 100Hz 刷新：
# 100ms 上报一次足够让滑块跟手，且把信号量压到原来的 1/10，不打扰渲染主线程。
REPLAY_PROGRESS_INTERVAL = 0.10

# ---- 回填的前置值回溯上限（帧数）----
# 回填只需要"窗口内每个时刻各类型的最新值"；窗口下界之前那个值，正常数据里每种包都连续
# 到达（几帧内就找齐）。这个上限只用于兜住"某类型很久没发"的病态情况，防止退化成
# O(跳转位置) —— 保证访帧数只与窗口大小有关，与跳到哪儿无关。
# 175 帧/s 下 20000 帧 ≈ 114 秒，足以覆盖正常发包节奏。
MAX_BACKFILL_LOOKBACK = 20000

# ---- 回填/前端缓冲点数边界（task-22）----
# 必须与前端 app.js 的夹紧**完全一致**（否则前后端对同一个配置理解不同）。
# 上限 20000：大屏高刷下够用（20000 × 20Hz ≈ 16 分钟窗口），而下限 10 保证曲线不至于只剩几个点。
BUF_POINTS_MIN = 10
BUF_POINTS_MAX = 20000

# ---- 回放文件解析：分片 + 时间片（U06）----
# 20 万帧 / 5.4MB 的 .bin 一次性 parse_stream 要 7.07 秒（1 小时录播 ≈21 秒），全跑在 GUI
# 线程上 → 白屏。现在按字节分片喂给 parse_stream，并用 QTimer(0) 泵把事件循环让出去。
#
# ★ 实测粒度（2026-10-15 质检复核 + 本机复测）：32KB 一片 ≈35~40ms
#   （质检 38.5~39.4ms；本机 174 轮中位 35.5ms，偶发 ≈90ms 尖峰）—— 这**就是**每轮事件
#   循环的真实阻塞上限（6.6s → ≈40ms，两个数量级改善；1ms 心跳实测最大间隔 51ms）。
#   REPLAY_PARSE_BUDGET_MS 是**兜底上限**，不是每片的目标：预算在**分片之前**检查，
#   所以单轮最多"预算 + 一片"。一片就已超过 25ms 时，效果等于"每轮只跑一片"；
#   若以后把 REPLAY_PARSE_SLICE 调小（例如 8KB ≈10ms/片），预算才会真正逐片累积生效
#   （10+10+10 ≥ 25 → 每轮 3 片收手）。
REPLAY_PARSE_SLICE = 32 * 1024
REPLAY_PARSE_BUDGET_MS = 25.0
# 解析期间 progress 刷新的节流间隔（s）：≤5Hz，别让文案把前端刷花
REPLAY_LOADING_EMIT_INTERVAL = 0.2

# ---- 回放时钟（会话净跨度）参数 ----
# 设备 fr.ts **不是**单调时钟：实测 7 份录播里 6 份有负增量；旧实现把任何 delta<0 当成
# "32 位绕回 +2^32"，于是那一帧被算成 49.7 天后到期 → 回放永久卡死、进度条报 4293657.5 秒。
# task-10 先用"负增量→中位数"修掉了卡死，但独立质检拿 telemetry/reader.log 的**真实接收时长**
# 做真值，发现新总时长系统性偏长 13.6%~57.6%（每条小负抖动都被补了一个帧间隔）：
#   204248 真值 115.0s → 得 181.3s(+57.6%)；220829 真值 340.4s → 得 467.7s(+37.4%)。
#   另外它把 5 处 >2000ms 的**真实停顿**（共 23.4s）压成了中位数，等于少报时间。
# 所以 task-12 改成**会话净跨度**（见 _build_replay_clock）：
#   会话内 clock = 会话基准 + (ts[i] − 会话首帧 ts)，再 running-max 单调化
#   → 小负抖动让时钟原地不动（不补间隔）；真实停顿原样保留；跨会话不把重启差算进去。
# 无正增量（所有 ts 相同/全为负）时回退这个标称帧间隔（≈175 帧/s）。
REPLAY_NOMINAL_GAP_MS = 5.7
# 会话边界：负跳变幅度 > max(50×中位正增量, 500ms) 判为"计数器重启"（不是抖动）。
# 依据（实测 7 份录播的负增量分布是**双峰**的）：抖动一侧全部 ≥ −6ms
# （204248 的 7775 条、220829 的 16187 条负增量最负只有 −6ms），重启一侧是
# −209159 ~ −1344794ms；两者之间**一个样本都没有**。所以阈值在 [约 10ms, 20 万 ms] 之间
# 取任何值结果都一样 —— 我实测 4 档（50×med / max(50×med,500) / max(100×med,1000) /
# max(200×med,2000)）得到的会话划分与总时长**完全相同**（见汇报里的对照）。
REPLAY_SESSION_JUMP_FACTOR = 50
REPLAY_SESSION_JUMP_FLOOR_MS = 500
# 单帧坏值：正跳 > max(60×中位, 3000ms) **且**下一帧回到原趋势（|ts[i+1] − ts[i−1]| ≤ 同门限）
# → 判为该帧 ts 被污染，按"与前一帧同刻"处理（绝不抬高总时长）；反向同理。
# 为什么不直接给正跳变封顶：会话内真实停顿必须保留（用户可能关机 30 分钟再录）。
# 实测 6 份文件里的 5 处 >2000ms 正跳变，下一帧全部是**继续向前**（next=+3~+18ms），
# 都是真停顿，不是坏值 —— 所以封顶会丢时间，而"出去又回来"的判据不会误伤它们。
REPLAY_SPIKE_FACTOR = 60
REPLAY_SPIKE_FLOOR_MS = 3000
# 坏值允许持续的最大帧数：≥2 帧连着的坏值也清（见 _build_replay_clock 的"出去又回来"判据）。
# 取 5 帧而不是更大：真实停顿不会"回来"，所以放宽这个数不会误伤停顿，但会拖慢清理循环。
REPLAY_SPIKE_MAX_FRAMES = 5

# 通道数 / CSV 表头由配置自动派生（telemetry_config.py 唯一来源）
ADC_COUNT = CFG.ADC_COUNT
# CSV 表头由配置自动拼出，列顺序 = time + ADC 通道 + NAV 字段 + MOTOR 字段
CSV_HEADER = CFG.CSV_HEADER

# 版本标识：产物 / 窗口标题都带上，避免"哪个 exe 是最新的"再次踩坑。
# v2.1 = 流畅度与响应式改版：Toast 取代 alert、条形仪表改 transform 合成层、
#        HEX 监视器去强制布局、趋势图静态层缓存、全套响应式断点 + 无障碍
# v2.3 = 协议可配置化：scale 语义统一（显示值 = 原始码 × scale，组帧与解码互为
#        逆运算）；新增包类型 / 加字段自动进 CSV；串口参数（波特率/校验位）入配置；
#        自检期望值改为按配置推导，改 scale 不再误报；新增可扩展性自检
# v2.2 = 配置与接口改版：新增 telemetry_config.py 作为唯一配置源（消除三份
#        通道名硬编码）、CSV 行/表头由配置自动派生、配置经 uiConfig 信号下发
#        前端自动重建；修复失修的 selftest.py；新增一键打包脚本
# v2.4 = 仪表量程也可配：新增第 5 区 GAUGE_RANGES，把原先写死在 index.html 的
#        加速度 ±16384 刻度、单位后缀 raw、车速副标题 0–40 m/s 全部改为按配置
#        下发后同步；本地导出 CSV 的加速度不再 Math.round（小 scale 全变 0）；
#        verify_acc_scale.py 增加「量程标签」断言，防止同类静默失效复发
# v2.5 = 滚动丝滑 + 曲线量程三档：
#        ① 去掉 scroll-behavior:smooth（滚轮停了还在飘，跟手感差）
#        ② 分区 content-visibility:auto，视口外不参与绘制（滚动内容 3851→2542px）
#        ③ IntersectionObserver 可见区节流：滚出视口的分区不再 20Hz 刷新
#        ④ 分区背景渐变改纯色（软件光栅下渐变每帧重算很贵）；滚动中暂停脉冲动画
#        ⑤ 趋势量程三档循环：归一化 / 自适应（每条按窗口 min/max 独立缩放，250ms
#          缓存防"呼吸"）/ 分组分带（P0/P1/P2 各一条带各用量程）；曲线末端标注当前值
#        新增 tools/scroll_bench.py（滚动基准）与 tools/trend_mode_check.py（三档验证）
APP_VERSION = "v2.7"


def recv_stamp() -> str:
    """CSV 时间列：上位机接收时刻，HH:MM:SS.mmm（毫秒精度）。

    原实现只到秒，高采样率下同一秒会写出上百行相同时间戳，无法做时序分析。
    这里保留 HH:MM:SS 前缀（兼容既有正则 ^\\d{2}:\\d{2}:\\d{2}）并追加毫秒。
    """
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class RecordingWriteError(Exception):
    """落盘写入失败（磁盘满 / 权限 / 句柄被占用）。"""



def resource_path(rel: str) -> str:
    """兼容开发态（脚本目录）与 PyInstaller 单文件（_MEIPASS 临时目录）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def default_save_dir() -> str:
    """默认保存目录：程序（exe）所在目录下的 telemetry/。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "telemetry")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = base
    return d


class Backend(QObject):
    """暴露给页面的通信后端：真实串口 + 解析 + 状态/错误信号 + 文件落盘。"""
    dataReady = pyqtSignal(str)            # JSON 数组：一批解析后的帧
    rawReady = pyqtSignal(str)             # 一批原始字节 HEX（多行，一次信号）
    statusChanged = pyqtSignal(bool, str)  # (connected, info)
    errorMsg = pyqtSignal(str)             # 连接/发送/保存失败原因
    portsReady = pyqtSignal(str)           # JSON 数组：可用串口设备名
    saveDirReady = pyqtSignal(str)         # 当前保存目录
    recordStateChanged = pyqtSignal(bool, str)  # (recording, 当前文件路径)
    recordSaved = pyqtSignal(str, int)     # (文件路径, 行数)
    recordFailed = pyqtSignal(str)         # 落盘失败原因（磁盘满/权限等）
    recordFileReady = pyqtSignal(str)      # 最近一次（或正在写）的录制文件路径，供导出
    saveResult = pyqtSignal(str)           # 导出保存结果提示
    cfgSaved = pyqtSignal(str)             # 量程/单位编辑器写盘回执
    # (已写入行数, mm:ss) 录制进度，每 500ms 一发。
    # 前端靠它把"正在记录"的提示做成平滑跳动，而不再是 1Hz 的秒数变化。
    # 录制进度：前端靠它把"正在记录"做成平滑跳动
    recordProgress = pyqtSignal(int, str)
    # JSON：把 telemetry_config.py 的内容下发给界面（通道名/单位/配色/CSV 表头）。
    # 界面不再硬编码通道名，改配置文件后一切都跟着变。
    uiConfig = pyqtSignal(str)
    # 数据回放状态：(是否回放中, 文件名/提示)。回放走与实时相同的 dataReady 推送。
    replayStateChanged = pyqtSignal(bool, str)
    # 回放进度：(当前帧序 0-based, 总帧数, 当前距首帧秒数, 总时长秒数)。
    # 回放中约 100ms 一发；停止/完成时发 (0, 0, 0.0, 0.0)，前端据此把进度条归零并禁用。
    # 注意：.bin 里的 fr.ts 是设备 32 位毫秒计数，**没有墙钟时间**，
    # 所以两个 float 只能是「相对首帧的秒数」，不是日期时间，也不能还原成真实时刻。
    # 总时长 =（末帧 ts − 首帧 ts），前端可直接显示"已播 12.3s / 共 45.6s"。
    replayProgress = pyqtSignal(int, int, float, float)
    # 回放文件"正在解析"状态：(是否解析中, 文案)。长录播分片解析要几秒~几十秒，
    # 这段时间里 seek/暂停按契约被静默忽略，界面必须能显示"正在解析…"而不是像没反应。
    # ★ 语义与 replayStateChanged 严格区分：后者仍是"是否正在回放"的唯一判据，
    #   前端**不能**把 loading 当成"已开始回放"去提前启用滑块。
    # ★ 语义口径（task-22 更正，原注释不成立）：**只在确实宣告过 `(True,…)` 之后**，
    #   才由 `_finish_replay_loading()` **幂等**补发一次 `(False, "")`（成功 / 解析异常 /
    #   空文件 / 被 stopReplay 取消 / 被再次 startReplay 顶掉 都会走到它）。
    #   没宣告过 loading 就不会发 False —— 因为前端此时也没有 loading 态要收，
    #   多发一条反而会造成"裸 False"、与内部标志不同步（task-19 的 P2 就是这个问题）。
    replayLoadingChanged = pyqtSignal(bool, str)
    # 回放暂停状态：(True = 已暂停)。进入暂停与恢复各发一次；停止/结束时若曾暂停也补发一次
    # False，避免前端按钮卡在"继续"。
    replayPausedChanged = pyqtSignal(bool)
    # 跳转回填：JSON **对象**（不是裸数组），把跳转点之前一窗历史一次性交给前端，避免清空缓冲
    # 导致曲线空 30 秒：
    #   {"sampleMs": 50, "slots": [{"ts": <设备32位毫秒>, "items": [<payload>, ...]}, ...]}
    #   · 槽数 ≈ CFG.UI_CFG["bufPoints"]（默认 600），末槽 = 跳转点那一帧所在时刻；
    #   · "ts" 与 payload["ts"]、fr.ts **同一时间基（设备毫秒）** —— 全链路单一时间基，
    #     不需要读者去记"这个 ts 是相对的"；相对秒数已经在 replayProgress 的 cur_secs 里；
    #   · 每槽 items = 该时刻**各包类型的最新值**（按类型号升序；某类型还没出现过就不放）。
    #     ★ 这样每条曲线都能拿到 ≈ bufPoints 个点；若每槽只放"抽到的那一帧"，高采样率的 ADC
    #       会被 NAV/MOTOR 挤掉（实测 600 槽里 ADC 只剩 300），前端画出来是半密度/台阶状；
    #   · <payload> 与 dataReady 元素**逐字段一致**（同一个 _payload_of() + _finite() 产出）。
    #   · 生成复杂度 O(窗口)，与跳转位置无关（见 _emit_replay_backfill 注释）。
    # ★ 拖拽中用 seekReplayLive(frac)：只跳位置 + 发 replayProgress，O(1)、不发回填；
    #   松手时再调 seekReplay(frac) 做一次干净的"跳转 + 回填"。
    replayBackfill = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.thread = None
        self.running = False
        self.buf = bytearray()
        self._pending = []
        self._lock = threading.Lock()
        self._last_hex_t = 0.0      # 上次推送 HEX 的时刻（HEX 限频用）
        self._flush_timer = QTimer()
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start(FLUSH_INTERVAL_MS)

        # 录制进度心跳：仅在主线程读 _rec_rows（int 读取在 CPython 下是原子的），
        # 不加锁 —— 避免与写盘线程互相阻塞。
        self._rec_t0 = 0.0
        self._prog_timer = QTimer()
        self._prog_timer.timeout.connect(self._emit_record_progress)
        self._prog_timer.start(500)

        # ---- 录制状态（写盘在读取线程内完成） ----
        self.save_dir = default_save_dir()
        self._rec_lock = threading.Lock()
        self._recording = False
        self._rec_file = None
        self._rec_path = ""
        self._rec_rows = 0
        self._rec_last_path = ""       # 最近一次完成/进行中的录制文件（供导出复用）
        self._rec_buf = []             # 内存攒行缓冲：攒够 REC_WRITE_BUFFER 再落盘
        self._rec_failed = False       # 本次录制是否已发生过写入失败
        # 原始字节流录制（数据回放用）：与 CSV 同步写，存完整帧节拍供按时间戳回放
        self._rec_bin_file = None
        self._rec_bin_path = ""
        self._rec_bin_size = 0
        # 配对落盘用：{包类型号: 该包自上次成行以来的最新值列表}。
        # ★ 关键：只有配置里声明的包全都到齐才写行，绝不把别的包的陈旧值补进本行。
        self._rec_parts = {}
        self._last_adc = [0] * CFG.ADC_COUNT
        # 各「普通包」的最新值，按类型号索引（录制配对与以后新增包都用得上）
        self._last_typed = {tid: [0.0] * len(names)
                            for tid, names in _TYPED_PACKETS.items()}
        self._last_nav = self._last_typed.get(0x01, [0, 0, 0, 0])
        self._last_motor = self._last_typed.get(
            0x02, [0] * len(_TYPED_PACKETS.get(0x02, [])))

        # ---- 数据回放状态（时间轴 = 单调重映射时钟 clock[]，见 _build_replay_clock） ----
        self._replaying = False
        self._replay_frames = []       # 已解析的帧列表（ParsedFrame，含 ts）
        self._replay_clock = []        # ★ 单调回放时钟（ms，与帧一一对应）——回放计时的唯一来源
        self._replay_idx = 0
        self._replay_t0 = 0            # 首帧设备 ts（仅参考/诊断用，**不参与回放计时**）
        self._replay_base = 0.0        # 回放开始的 monotonic 时刻
        self._replay_last_emit = 0.0   # 上次发 replayProgress 的 monotonic 时刻（限频）
        self._replay_paused = False    # 回放是否暂停（暂停时保留帧与下标，可继续）
        self._replay_timer = QTimer()
        self._replay_timer.timeout.connect(self._replay_tick)
        # ★ U06：回放文件的分片解析状态 + 泵定时器（未加载时为 None，定时器不跑）
        self._replay_load = None       # {"path","data","pos","buf","frames"}；非 None = 正在解析
        self._replay_loading = False   # 是否已向前端宣告"正在解析"（保证 False 只补发一次）
        self._replay_loading_t = 0.0   # 上次发 loading 文案的时刻（≤5Hz 节流）
        self._replay_load_timer = QTimer()
        self._replay_load_timer.timeout.connect(self._replay_load_tick)

    def _log(self, msg: str):
        """写诊断日志到 telemetry/reader.log（用于排查数据流卡死/堵塞）。"""
        try:
            p = os.path.join(self.save_dir, "reader.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S.%f")[:-3], msg))
        except Exception:
            pass

    # ---------- 端口枚举 ----------
    def enumerate_ports(self):
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            ports = []
        self.portsReady.emit(json.dumps(ports))

    @pyqtSlot()
    def refreshPorts(self):
        """供前端「刷新端口」按钮调用（QWebChannel 只暴露 slot）。"""
        self.enumerate_ports()

    # ---------- 保存目录 ----------
    @pyqtSlot()
    def chooseSaveDir(self):
        """弹出系统文件夹选择框，让用户指定 CSV 保存目录。"""
        d = QFileDialog.getExistingDirectory(
            None, "选择数据保存目录", self.save_dir or "",
            QFileDialog.Option.ShowDirsOnly)
        if d:
            self.save_dir = d
            self.saveDirReady.emit(d)

    @pyqtSlot(str)
    def setSaveDir(self, d):
        d = str(d or "").strip()
        if d and os.path.isdir(d):
            self.save_dir = d
            self.saveDirReady.emit(d)

    @pyqtSlot()
    def getSaveDir(self):
        self.saveDirReady.emit(self.save_dir)

    @pyqtSlot()
    def getUiConfig(self):
        """页面启动后主动拉取界面配置（通道名 / 单位 / 曲线配色 / CSV 表头）。

        以前通道名在 protocol.py、launcher_web.py、app.js 里各存一份，
        改一个传感器要同步三处，漏一处界面就显示错。现在只有
        telemetry_config.py 是唯一来源，页面从这里取，永远不会不一致。
        """
        try:
            self.uiConfig.emit(json.dumps(CFG.ui_config(), ensure_ascii=False))
        except Exception as e:
            self.errorMsg.emit("读取遥测配置失败：%s" % e)

    @pyqtSlot(str)
    def saveUserCfg(self, patch_json):
        """把界面「量程 / 单位」编辑器的改动落盘到 telemetry_user.json。

        这是「改界面不用重新打包」闭环的最后一步：
          前端编辑 -> 本方法合并写盘 -> 重新 load_user_cfg() -> uiConfig 重发
        页面立即按新配置重建，文件也留下了，下次启动还是这个值。
        """
        try:
            patch = json.loads(patch_json or "{}")
            if not isinstance(patch, dict):
                raise ValueError("补丁必须是一个对象")
            path = CFG.USER_CFG_PATH or CFG.user_cfg_paths()[0]

            cur = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        cur = json.load(fh) or {}
                except Exception:
                    cur = {}

            for key in ("gaugeRanges", "units", "scales", "serial", "ui"):
                if isinstance(patch.get(key), dict):
                    tgt = cur.get(key)
                    if not isinstance(tgt, dict):
                        tgt = cur[key] = {}
                    tgt.update(patch[key])
            # 12bit 解包顺序（通道错位的第一个排查点）。protocol.py 是运行期
            # 动态读 CFG.ADC_12BIT_ORDER 的，所以保存后**立即生效，不用重启** ——
            # 用户可以开着串口、按住某个传感器，现场切 4 种顺序看哪个对。
            if isinstance(patch.get("adc12bitOrder"), str):
                cur["adc12bitOrder"] = patch["adc12bitOrder"].strip().lower()
            # MOTOR 包多字节字段字节序（转速乱跳到几万时的排查点）。
            # protocol.py 里 _field_fmt() 用 getattr 动态读，保存后立即生效。
            if isinstance(patch.get("motorEndian"), str):
                e = patch["motorEndian"].strip()
                if e in ("<", ">"):
                    cur["motorEndian"] = e
            if isinstance(patch.get("channels"), list):
                old = cur.get("channels")
                old = old if isinstance(old, list) else []
                for i, item in enumerate(patch["channels"]):
                    if not isinstance(item, dict):
                        continue
                    while len(old) <= i:
                        old.append({})
                    old[i].update(item)
                cur["channels"] = old

            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cur, fh, ensure_ascii=False, indent=2)

            CFG.load_user_cfg()
            if CFG.USER_CFG_ERROR:
                raise RuntimeError(CFG.USER_CFG_ERROR)
            self.uiConfig.emit(json.dumps(CFG.ui_config(), ensure_ascii=False))
            self.cfgSaved.emit("已保存到 %s 并重新生效" % os.path.basename(path))
        except Exception as e:
            self.cfgSaved.emit("保存失败：%s" % e)

    @pyqtSlot()
    def resetUserCfg(self):
        """清空 telemetry_user.json 里的用户覆盖，恢复为内置配置。

        这是给「怎么改配置都不生效」兜底的逃生舱：
        外部文件优先级高于 telemetry_config.py，一旦里面留了旧值，
        改代码再打包也会被它覆盖。点一下这里，把所有外部键清干净。
        """
        try:
            ok = CFG.reset_user_cfg()
            self.uiConfig.emit(json.dumps(CFG.ui_config(), ensure_ascii=False))
            if ok:
                self.cfgSaved.emit("已清空外部覆盖，恢复为内置配置"
                                  + ("（来源：%s）" % os.path.basename(CFG.USER_CFG_PATH or "")
                                     if CFG.USER_CFG_PATH else ""))
            else:
                self.cfgSaved.emit("没有找到外部配置文件，当前已是内置配置")
        except Exception as e:
            self.cfgSaved.emit("恢复失败：%s" % e)

    # ---------- 录制（Python 端实时落盘） ----------
    @pyqtSlot()
    def startRecording(self):
        if self._recording:
            return
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except Exception as e:
            self.errorMsg.emit("保存目录不可用：%s" % e)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.save_dir, "telemetry_%s.csv" % ts)
        bin_path = os.path.join(self.save_dir, "telemetry_%s.bin" % ts)
        try:
            f = open(path, "w", encoding="utf-8-sig", newline="")
            f.write(CSV_HEADER + "\n")
            # ★ 表头立即落盘：否则表头会留在用户态缓冲里，进程异常退出时
            #   磁盘上只留下 0 字节文件（实测 telemetry_20260915_153918.csv）。
            f.flush()
            bin_f = open(bin_path, "wb")   # 原始字节流（回放数据源）
        except Exception as e:
            self.errorMsg.emit("无法创建文件：%s" % e)
            return
        with self._rec_lock:
            self._rec_file = f
            self._rec_path = path
            self._rec_bin_file = bin_f
            self._rec_bin_path = bin_path
            self._rec_bin_size = 0
            self._rec_rows = 0
            self._rec_buf = []
            self._rec_failed = False
            self._rec_parts = {}
            self._recording = True
        self._rec_t0 = time.monotonic()
        self._rec_last_path = path
        self.recordStateChanged.emit(True, path)
        self.recordFileReady.emit(path)
        self.recordProgress.emit(0, "00:00:00")

    @pyqtSlot()
    def stopRecording(self):
        flush_err = None
        with self._rec_lock:
            self._recording = False
            f = self._rec_file
            bin_f = self._rec_bin_file
            path, rows = self._rec_path, self._rec_rows
            bin_path = self._rec_bin_path
            # ★ U10（数据丢失，2026-10-15 修）：**必须先刷缓冲、再置空 _rec_file**。
            #   旧顺序是 `self._rec_file = None` 之后再调 _flush_rec_buf_locked()，
            #   而它开头就是 `if not self._rec_buf or self._rec_file is None: return`
            #   → 每次停止录制，内存里不足一整个 REC_WRITE_BUFFER(256) 的尾行**静默丢失**：
            #     实测只录 6 行 → 磁盘 0 行；磁盘 256 / 内存 301 时丢 45 行。
            #   按 ~140 行/s 估，每次停止最多丢约 1.8 秒数据，而且 _rec_rows 照加、
            #   recordSaved 报的行数比磁盘多（自检撒谎）。
            #   这里只调换顺序，写入语义/缓冲策略一字未改。
            if f is not None:
                try:
                    self._flush_rec_buf_locked()
                except RecordingWriteError as e:
                    # ★ 不能在持锁时走 _on_rec_write_error（它会再取 _rec_lock，且会回调
                    #   stopRecording → 死锁）；这里先记下，出锁后按同样的口径上报。
                    flush_err = e
            self._rec_file = None
            self._rec_path = ""
            self._rec_bin_file = None
            self._rec_bin_path = ""
            self._rec_parts = {}
        if flush_err is not None:
            self.recordFailed.emit("收尾缓冲写入失败：%s" % flush_err)
            self.errorMsg.emit(
                "录制收尾写入失败，已保留此前写入内容：%s\n"
                "常见原因：磁盘空间不足 / 文件被占用 / 无写入权限。\n"
                "文件：%s" % (flush_err, path))
        if f:
            try:
                f.flush()
                f.close()
            except Exception:
                pass
        if bin_f:
            try:
                bin_f.flush()
                bin_f.close()
            except Exception:
                pass
            # 原始字节流一帧都没录到 -> 一并删除空 .bin
            if self._rec_bin_size <= 0:
                try:
                    os.remove(bin_path)
                except Exception:
                    pass
            # 一帧都没录到 -> 删除空文件，避免 telemetry/ 里堆积垃圾 CSV
            if rows <= 0:
                try:
                    os.remove(path)
                except Exception:
                    pass
                self._rec_last_path = ""
                self.recordStateChanged.emit(False, "")
                self.saveResult.emit("本次录制未收到任何数据，已删除空文件。")
                return
            self.recordStateChanged.emit(False, "")
            self.recordSaved.emit(path, rows)
            self.recordFileReady.emit(path)

    @pyqtSlot()
    def isRecording(self):
        self.recordStateChanged.emit(self._recording, self._rec_path)

    # ---------- 数据回放（按帧内时间戳还原节拍） ----------
    @pyqtSlot()
    def chooseReplayFile(self):
        """弹出文件选择框选 .bin 原始数据回放文件。"""
        d, _ = QFileDialog.getOpenFileName(
            None, "选择回放数据（.bin）", self.save_dir or "",
            "原始数据 (*.bin);;所有文件 (*.*)")
        if d:
            self.startReplay(d)

    def _begin_replay_loading(self):
        """宣告"开始解析"：发一次 (True, 0 帧)，并记节流时刻。"""
        self._replay_loading = True
        self._replay_loading_t = time.monotonic()
        self.replayLoadingChanged.emit(True, "正在解析… 0 帧")

    def _emit_replay_loading_progress(self, n_frames, force=False):
        """解析中的文案刷新（≤5Hz）；force=True 时忽略节流。"""
        if not self._replay_loading:
            return
        now = time.monotonic()
        if not force and (now - self._replay_loading_t) < REPLAY_LOADING_EMIT_INTERVAL:
            return
        self._replay_loading_t = now
        self.replayLoadingChanged.emit(True, "正在解析… %d 帧" % n_frames)

    def _finish_replay_loading(self):
        """解析结束（成功/失败/取消）：**保证**补发一次 (False, "")，且只发一次。"""
        if not self._replay_loading:
            return
        self._replay_loading = False
        self.replayLoadingChanged.emit(False, "")

    def _cancel_replay_load(self):
        """取消"正在解析"（**不动**已经在进行的回放）：丢状态 + 停泵 + 幂等收尾 loading。

        ★ P2（2026-10-15 质检打穿后修）：坏路径/读失败必须在**报错之前**走这里。
          旧写法在 `if not os.path.isfile(path)` 分支里直接 `emit(False,"")` 就 return，
          既没取消在跑的解析、也没同步 `_replay_loading` 标志 —— 结果是：
            · 前端收到 (False) 之后又继续收到一串 (True)（loading 闪烁）；
            · 上一次那个大文件**照样解析完并真的开始回放**，而用户刚看到"文件不存在"。
          现在坏路径先取消（旧文件不会再开始回放）、再让 `_finish_replay_loading()` 收尾。
        """
        self._replay_load = None
        self._replay_load_timer.stop()
        self._finish_replay_loading()

    @pyqtSlot(str)
    def startReplay(self, path):
        """回放原始字节流 .bin：读入 → **分片**解析 → 按帧内 ts 节拍推送前端。

        回放复用 _handle → _pending → _flush → dataReady 这条与实时完全相同的链路，
        所以前端无需区分回放/实时，走同一个 onDataBatch。回放与实时串口互斥。

        ★ U06（2026-10-15 修）：解析不再一口气同步跑完。
        实测 5.4MB / 20 万帧的 .bin 单次 `parse_stream` 要 7.07 秒（1 小时录播 ≈21 秒），
        全部发生在 GUI 线程 → 窗口白屏、进度条不动。
        现在改成**时间片分片解析 + QTimer(0) 泵**：每次事件循环最多连续解析
        REPLAY_PARSE_BUDGET_MS 毫秒，然后让出事件循环（界面照常响应动画/点击/关闭），
        下一轮继续。帧列表与"一次 parse_stream 整文件"**完全一致**（见 _replay_load_tick
        的分片拼接方式：parse_stream 只消费完整帧、残缺尾巴留在缓冲里等下一片）。

        为什么不用工作线程：本方案**零线程**——没有跨线程对象访问、没有"解析中关窗导致
        野线程碰已销毁对象"、没有"回放中途再点回放"的竞态；取消只需 stop 定时器 + 丢状态。
        风险面最小，且数据链路语义一字未改（这一节只影响"frames 从哪来"，安装后的
        _replay_frames/_replay_clock/_replay_base/tick/暂停/seek/回填全部照旧）。
        """
        path = str(path or "").strip()
        if not path or not os.path.isfile(path):
            # ★ P2：先取消"正在解析"（否则上一次那个文件会继续解析完并开始回放），
            #    再报错。`_cancel_replay_load()` 只在确实宣告过 loading 时才补 (False,"")，
            #    所以这里不会出现"裸 False"或重复 False。
            self._cancel_replay_load()
            self.errorMsg.emit("回放文件不存在：%s" % path)
            return
        if self.running:
            self.disconnect()          # 回放前断开串口，避免两路数据混在一起
        self.stopReplay()              # 同时会取消上一次未完成的解析（并补发 loading=False）
        try:
            with open(path, "rb") as fh:
                data = fh.read()       # 5MB 级读取本身只要几毫秒，留在 GUI 线程没有可感影响
        except Exception as e:
            self.errorMsg.emit("回放失败：%s" % e)
            return                     # 上面 stopReplay() 已经收尾过 loading，无需再发
        self._replay_load = {
            "path": path,
            "data": data,
            "pos": 0,
            "buf": bytearray(),
            "frames": [],
        }
        self._begin_replay_loading()
        self._replay_load_timer.start(0)

    def _replay_load_tick(self):
        """分片解析泵：每轮事件循环解析若干片，随后把事件循环让出去。

        ★ 预算检查放在**每片之前**（P3 修正）：这样 REPLAY_PARSE_BUDGET_MS 是"下一片还
          要不要开跑"的判据，单轮阻塞上限 = 预算 + 一片。32KB 一片实测 ≈39ms，所以
          当前实际是"每轮一片"；把 SLICE 调小后预算才会逐片累积生效（见常量处注释）。
        """
        st = self._replay_load
        if st is None:
            self._replay_load_timer.stop()
            return
        t0 = time.monotonic()
        data, total = st["data"], len(st["data"])
        buf, frames = st["buf"], st["frames"]
        while st["pos"] < total:
            if (time.monotonic() - t0) * 1000.0 >= REPLAY_PARSE_BUDGET_MS:
                return                 # 已超预算：不再开新片，让出事件循环
            end = min(st["pos"] + REPLAY_PARSE_SLICE, total)
            buf += data[st["pos"]:end]
            st["pos"] = end
            try:
                frames.extend(protocol.parse_stream(buf))
            except Exception as e:
                # 与同步版一致：解析异常按"回放失败"收尾并终止
                self._replay_load = None
                self._replay_load_timer.stop()
                self.errorMsg.emit("回放失败：%s" % e)
                self._finish_replay_loading()
                return
            self._emit_replay_loading_progress(len(frames))     # ≤5Hz：帧数递增地刷新文案
        # ---- 解析完成：以下与"同步版"逐行等价（只是晚了几百毫秒到几十秒）----
        self._replay_load = None
        self._replay_load_timer.stop()
        frames.extend(protocol.parse_stream(buf))   # 收尾：把最后一片里的完整帧取干净
        if not frames:
            self.errorMsg.emit("回放文件无有效数据帧：%s" % st["path"])
            self._finish_replay_loading()
            return
        self._replay_frames = frames
        self._replay_clock = self._build_replay_clock(frames)   # 一次性 O(n) 预计算
        self._replay_idx = 0
        self._replay_t0 = frames[0].ts or 0     # 仅参考/诊断，计时一律走 clock
        self._replay_base = time.monotonic()
        self._replay_last_emit = self._replay_base
        self._replaying = True
        self._replay_timer.start(10)          # 10ms tick，到点的帧逐帧推送
        # ★ loading 必须在"开始回放"之前收尾：前端据此结束"正在解析"提示，
        #   再靠 replayStateChanged/replayProgress 进入真正的回放态。
        self._finish_replay_loading()
        # 起步先发一次进度：前端立刻拿到总帧数，才能把进度条量程设好
        self._emit_replay_progress()
        self.replayStateChanged.emit(True, os.path.basename(st["path"]))

    @pyqtSlot()
    def stopReplay(self):
        was_paused = self._replay_paused
        # ★ 取消可能还在进行的解析（丢状态 + 停泵 + 幂等收尾 loading）
        self._cancel_replay_load()
        self._replaying = False
        self._replay_paused = False
        self._replay_timer.stop()
        self._replay_frames = []
        self._replay_clock = []
        self._replay_idx = 0
        self._replay_last_emit = 0.0
        self.replayProgress.emit(0, 0, 0.0, 0.0)  # 停止：进度归零（前端禁用进度条）
        if was_paused:
            # 暂停中被停止：必须复位前端"继续"按钮，否则它会永远停在那里
            self.replayPausedChanged.emit(False)
        self.replayStateChanged.emit(False, "")

    @pyqtSlot(bool)
    def setReplayPaused(self, paused):
        """暂停 / 继续回放（用户问题四）。

        暂停：停止推进，但**保留** _replay_frames / _replay_idx，_replaying 仍为 True
              （所以进度条与 seekReplay 继续可用，随时能恢复）。
        继续：重算 _replay_base，使剩余帧仍按原始 ts 间隔推进 —— 暂停期间"欠下"的帧
              不会一次性补发（否则瞬间刷屏、曲线拉花，等于把节拍丢了）。
        未在回放中 / 非布尔入参 / 状态未变化 → 静默忽略，不抛异常。
        """
        if not isinstance(paused, bool):
            return                       # 非布尔入参：忽略（QWebChannel 传布尔，直接调用才可能传错）
        if not self._replaying:
            return                       # 未在回放：静默忽略
        if paused == self._replay_paused:
            return                       # 状态没变：不重复发信号
        self._replay_paused = paused
        if paused:
            self._replay_timer.stop()    # 暂停期间不必每 10ms 空转
        else:
            now = time.monotonic()
            self._replay_base = now - self._replay_delta_ms(self._replay_idx) / 1000.0
            self._replay_last_emit = now
            self._replay_timer.start(10)  # 与 startReplay 同一节拍
        self.replayPausedChanged.emit(paused)

    def _locate_replay(self, frac):
        """把回放位置落到 frac ∈[0,1] 对应的帧上；成功返回 True。

        供 seekReplay / seekReplayLive 共用，保证两者定位与节拍重置**完全一致**：
          idx = round(frac*(n-1))，越界夹紧；重算 _replay_base 使 idx 帧"此刻"到期，
          后续帧仍按原始 ts 间隔推进。未在回放中 / 非数字 / 非有限值 → 返回 False（静默忽略）。
        """
        try:
            f = float(frac)
        except (TypeError, ValueError):
            return False                 # 前端传了非数字：忽略，不打断回放
        if not self._replaying:
            return False                 # 未在回放：静默忽略
        frames = self._replay_frames
        n = len(frames)
        if n <= 0 or not math.isfinite(f):
            return False
        f = 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)   # 越界夹紧，不抛异常
        idx = int(round(f * (n - 1)))
        idx = max(0, min(idx, n - 1))
        self._replay_idx = idx
        # 重算节拍基准：让 idx 帧此刻到期（next tick 立即推送），后续帧照旧按 ts 间隔走
        now = time.monotonic()
        self._replay_base = now - self._replay_delta_ms(idx) / 1000.0
        self._replay_last_emit = now
        return True

    @pyqtSlot(float)
    def seekReplay(self, frac):
        """把回放位置跳到 frac ∈[0,1] 对应的帧（用户**松手**时调用）。

        完整跳转：定位 + 发一次 replayProgress + 发一窗 replayBackfill（回填 = O(窗口)）。
        拖拽过程中请改用 seekReplayLive()（O(1)、不发回填），松手再调本方法做干净的一次回填。
        未在回放中（含未加载文件）时**静默忽略**，不抛异常。
        """
        if self._locate_replay(frac):
            self._emit_replay_progress()     # 立刻回报，滑块不会"弹回"旧位置
            self._emit_replay_backfill()     # 紧跟其后：把跳转点之前一窗历史补发给前端

    @pyqtSlot(float)
    def seekReplayLive(self, frac):
        """拖拽进度条**过程中**的轻量跳转：只定位 + 发一次 replayProgress，**不发回填**（O(1)）。

        为什么单独给一个槽：拖拽每移动一点就触发一次，而回填即使已是 O(窗口) 也要现场
        构造 ~450KB 的 JSON；拖动过程只求"位置跟手、数据在动"，不需要历史窗。
        松手时前端再调一次 seekReplay(frac) 做"定位 + 回填"。
        ★ 只更新 _replay_idx 与节拍基准，**不清缓冲、不发 replayBackfill、不改暂停状态**。
        """
        if self._locate_replay(frac):
            self._emit_replay_progress()

    def _replay_delta_ms(self, idx: int) -> int:
        """回放时钟上第 idx 帧的毫秒位置（单调非减）—— **回放计时的唯一来源**。

        ★ 这里返回的不再是"设备 ts 之差"：设备 ts 非单调（真实录播 6/7 份有回跳），
        直接用它会让回放在回跳帧永久卡死（见 _build_replay_clock）。
        tick 到点判定 / 进度秒数 / total / seek 基准 / 回填槽网格全部只认它。
        """
        clock = self._replay_clock
        if not clock:
            return 0
        idx = max(0, min(int(idx), len(clock) - 1))
        return clock[idx]

    @staticmethod
    def _build_replay_clock(frames):
        """把设备 ts 重映射成**单调回放时钟** clock[]（ms，与帧一一对应，clock[0]=0）。

        ★ task-12 版：按**会话净跨度**，目标是让总时长等于"真实接收时长"（对 telemetry/reader.log
        的真值 |偏差| ≤10%）。task-10 的"每条负增量补一个中位间隔"会系统性偏长 13.6%~57.6%。

        三步：
          ① 中位正增量 median（直方图 O(n)，不做大数组排序）—— 只用来定阈值。
          ② **单帧坏值清理**：`ts[i]` 比前一帧跳出 >spike 门限、且下一帧又回到原趋势
             （|ts[i+1] − ts[i−1]| ≤ 同门限）⇒ 该帧 ts 被污染，按"与前一帧同刻"处理。
             正反两个方向都做。这样一帧坏值不会把总时长抬上去。
          ③ **分会话 + 会话内净跨度 + running-max**：
             会话边界 = 负跳变幅度 > max(50×median, 500ms)（实测负增量双峰，抖动 ≥ −6ms、
             重启 ≤ −209159ms，中间无样本）。
             会话内：`p = ts[i] − 会话首帧 ts`，`pos = max(pos, p)`，`clock[i] = 基准 + pos`。
               · 小幅负抖动 → p 不涨 → 时钟原地不动（**不补间隔**，这是修掉 +31% 的关键）；
               · 真实停顿（大正跳变）→ pos 直接抬上去 → **原样保留**（实测恢复 5 处共 23.4s）；
               · 越界/回跳的坏帧靠②先清掉。
             跨会话：`基准 += 上一会话的 pos`，新会话从 0 起 —— 重启造成的时间差**不计入**。

        等价性：干净文件（全部 +10ms、无负增量）上，本算法与 task-10 逐项一致（clock[i]=10i）。
        """
        n = len(frames)
        if n <= 0:
            return []
        # ① 一趟同时做三件事：把 ts 提成 list（后面要反复用）、建正增量直方图（求中位数）、
        #    记录最大正跳/最负跳（用来判断"要不要跑坏值清理"）。真实数据的 gap 取值集中，
        #    直方图 key 只有几十个，所以求中位数是 O(n)+O(key log key)，不排大数组。
        ts = [0] * n
        ts[0] = frames[0].ts or 0
        hist = {}
        total_pos = 0
        max_pos = 0
        min_neg = 0
        for i in range(1, n):
            t = frames[i].ts or 0
            ts[i] = t
            g = t - ts[i - 1]
            if g > 0:
                hist[g] = hist.get(g, 0) + 1
                total_pos += 1
                if g > max_pos:
                    max_pos = g
            elif g < min_neg:
                min_neg = g
        median = 0
        if total_pos:
            half = total_pos // 2
            acc = 0
            for g in sorted(hist):
                acc += hist[g]
                if acc > half:
                    median = g
                    break
        if median <= 0:
            median = max(1, int(round(REPLAY_NOMINAL_GAP_MS)))
        jump_bound = max(REPLAY_SESSION_JUMP_FACTOR * median, REPLAY_SESSION_JUMP_FLOOR_MS)
        spike_bound = max(REPLAY_SPIKE_FACTOR * median, REPLAY_SPIKE_FLOOR_MS)

        # ② 孤立坏值清理：从某帧跳出 >spike 门限，且**随后 ≤REPLAY_SPIKE_MAX_FRAMES 帧内又回到
        #    跳出前的水平** ⇒ 整段判定为坏值，按"与跳出前同刻"处理。
        #    正/反两个方向各扫一遍（O(n)，只有真的出现巨跳时才进内层循环）。
        #    ★ 真停顿不会"回来"（后面的帧继续停在新水平上），所以这个判据不会误伤停顿。
        #    ★ 绝大多数文件根本不存在 >spike_bound 的跳变 → 直接跳过这一整趟扫描（省一次 O(n)）。
        clean = ts
        if max_pos > spike_bound or -min_neg > spike_bound:
            clean = list(ts)
            for sign in (1, -1):
                i = 1
                while i < n:
                    if (clean[i] - clean[i - 1]) * sign > spike_bound:
                        ref = clean[i - 1]
                        j = i + 1
                        while (j < n and j - i <= REPLAY_SPIKE_MAX_FRAMES
                               and abs(clean[j] - ref) > spike_bound):
                            j += 1
                        if (j < n and j - i <= REPLAY_SPIKE_MAX_FRAMES
                                and abs(clean[j] - ref) <= spike_bound):
                            for k in range(i, j):
                                clean[k] = ref    # 坏值段：视为与跳出前同刻，不贡献时长
                            i = j
                            continue
                    i += 1

        # ③ 分会话 + 会话内净跨度 + running-max 单调化
        clock = [0] * n
        base = 0
        s_first = clean[0]
        pos = 0
        for i in range(1, n):
            g = clean[i] - clean[i - 1]
            if g < 0 and -g > jump_bound:          # 计数器重启：换会话，重启差不计入
                base += pos
                s_first = clean[i]
                pos = 0
                clock[i] = base
                continue
            p = clean[i] - s_first
            if p > pos:                            # running-max：小负抖动→原地不动；真停顿→保留
                pos = p
            clock[i] = base + pos
        return clock

    def _emit_replay_progress(self):
        """发一次回放进度：(当前帧序 0-based, 总帧数, 当前距首帧秒数, 总时长秒数)。"""
        n = len(self._replay_frames)
        if not self._replaying or n <= 0:
            return
        cur = min(self._replay_idx, n - 1)      # 已消费/待播的当前帧（完成时夹到末帧）
        self.replayProgress.emit(cur, n, self._replay_delta_ms(cur) / 1000.0,
                                 self._replay_total_secs())

    def _replay_total_secs(self) -> float:
        """整个回放的总时长秒数（= 末帧 ts − 首帧 ts）。只有 1 帧时为 0.0。"""
        n = len(self._replay_frames)
        if n <= 1:
            return 0.0
        return self._replay_delta_ms(n - 1) / 1000.0

    @staticmethod
    def _ui_int(key, default, lo=1, hi=None):
        """读第 5 区 UI_CFG 的整数项；缺项/配错/越界一律夹到合法区间，绝不让回填炸掉回放。

        ★ task-22：新增可选上下界（默认 `lo=1, hi=None`，与既有调用完全兼容）。
          以前只有"非正数退回默认"这一条，于是 `bufPoints: 1e8` 会被原样接受 ——
          回填一次要造 1e8 个槽，GUI 直接卡死 / OOM。前端 app.js 同样夹 [10, 20000]。
        """
        try:
            v = int(CFG.UI_CFG.get(key, default) or default)
        except (TypeError, ValueError):
            v = int(default)
        if hi is not None:
            v = max(int(lo), min(int(hi), v))
        elif v < int(lo):
            v = int(default) if int(default) >= int(lo) else int(lo)
        return v

    def _emit_replay_backfill(self):
        """跳转后补发一窗历史，让曲线立刻显示"当前时刻"的数据（用户问题二）。

        为什么需要：前端 pushSample() 按 sampleMs 抽稀，若跳转后只清空缓冲，曲线会空一整窗
        （默认 600 点 × 50ms = 30 秒），比"数据不对齐"更糟。所以由后端把跳转点**之前**一窗
        的等间隔采样主动发过去，前端一次填满，不必清空。

        载荷形态（接口冻结）：
          {"sampleMs": 50, "slots": [{"ts": <设备32位毫秒>, "items": [<payload>, ...]}, ...]}
          · 槽数 ≈ bufPoints，末槽 = 跳转点那一帧所在时刻；
          · "ts" = **该锚点帧的设备毫秒**，与 payload["ts"]/fr.ts 同一时间基（全链路单一时间基）；
          · 每槽 items = **该时刻各包类型的最新值**（按类型号升序），谁还没出现过就不放。
            ★ 为什么必须这样：若每槽只放"抽到的那一帧"，高采样率的 ADC 会被 NAV/MOTOR 挤掉 ——
              实测 600 槽里 ADC 只剩 300 个，前端画出来是半密度/台阶状，用户照样判"曲线不对"。
              携带各类型最新值后，每条曲线都能拿到 bufPoints 个点，与正常播放密度一致；
          · 槽边界必须显式给出（前端语义 = 逐槽处理，每槽只 push 一个采样点）。
        ★ 元素仍用 _payload_of() 生成 —— 与 dataReady 同格式、同 _finite() 处理，前端一套解析走天下。
        ★ 越界：seek 到 0（t_end=0）时只有 1 个槽，不越界、不出现负数 ts。

        ★★ 复杂度（2026-10-14 修）：必须 O(窗口)，**不能** O(跳转位置)。
        旧实现从帧 0 正向扫到跳转帧，于是耗时 ∝ 跳转位置（实测 8.1 分钟/22.8 万帧的录播上，
        frac=1.0 要 357ms；按 1 小时录播线性外推 ≈2 秒/次，而拖动会连续触发）。
        现在改成三段，访帧数只与窗口大小有关：
          ① 从跳转帧**向前**退到窗口下界（只走窗口内的帧）；
          ② 从窗口下界再往前有限回溯，取各类型在窗口之前的"基础值"（正常几帧就找齐，
             上限 MAX_BACKFILL_LOOKBACK 只兜病态情况）；
          ③ 从窗口下界**正向**推进到跳转帧，逐槽快照（只走窗口内的帧）。
        ②③ 的累加结果与旧实现"从帧 0 扫到跳转帧"完全等价 —— 已用同一批文件逐字段对拍验证。
        """
        frames = self._replay_frames
        n = len(frames)
        if not n:
            return
        sample_ms = self._ui_int("sampleMs", 50)
        # ★ task-22：bufPoints 必须夹上下界（与前端 app.js 的 [10, 20000] 一致）——
        #   1e8 会让"回填一次"要造 1e8 个槽，GUI 卡死 / OOM。
        buf_points = self._ui_int("bufPoints", 600, lo=BUF_POINTS_MIN, hi=BUF_POINTS_MAX)
        end = max(0, min(self._replay_idx, n - 1))      # 跳转点（末槽所在帧）
        t_end = self._replay_delta_ms(end)
        try:
            # ① 窗口下界：最后一个「相对时间 ≤ t_first」的帧。只退窗口内帧数。
            t_first = t_end - (buf_points - 1) * sample_ms
            if t_first < 0:
                t_first = 0                              # 文件开头不足一窗：下界就是首帧
            lo = end
            while lo > 0 and self._replay_delta_ms(lo) > t_first:
                lo -= 1
            # ② 窗口之前的"基础值"：从 lo 往前找每个类型的最后一次出现。
            #    正常数据几帧内就找齐（每帧都连续到达）；上限只兜"某类型很久没发"。
            latest = {}          # 类型号 -> 该类型截至当前槽的最新 payload
            left = set(_TYPED_PACKETS) | {0x00}
            p, scanned = lo, 0
            while p >= 0 and left and scanned < MAX_BACKFILL_LOOKBACK:
                tid = frames[p].type_id
                if tid in left:
                    pl = self._payload_of(frames[p])
                    left.discard(tid)        # 无论 payload 是否可用都落定，避免反复找一个类型
                    if pl is not None:
                        latest[tid] = pl
                p -= 1
                scanned += 1
            # ③ 逐槽正向推进（只走窗口内帧数）；latest 从②的基础值开始累加
            slots = []
            fi = lo
            for k in range(buf_points - 1, -1, -1):
                target = t_end - k * sample_ms
                if target < 0:
                    continue                            # 越界丢弃（seek 到 0 时槽数变少）
                while fi < end and self._replay_delta_ms(fi + 1) <= target:
                    fi += 1
                    pl = self._payload_of(frames[fi])
                    if pl is not None:
                        latest[frames[fi].type_id] = pl
                slots.append({"ts": frames[fi].ts or 0,   # 该锚点帧的设备 32 位毫秒
                              "items": [self._finite(latest[t]) for t in sorted(latest)]})
            if not slots:
                return
            payload = json.dumps({"sampleMs": sample_ms, "slots": slots},
                                 ensure_ascii=False)
        except Exception as e:
            self._log("回填生成失败（已跳过，不影响回放）: %r" % e)
            return
        self.replayBackfill.emit(payload)

    def _replay_tick(self):
        """按**回放时钟**还原节拍：到点的帧走 _handle（与实时同一条推送链路）。

        到点判定走 `_replay_delta_ms()`（= 单调 clock[]），不再直接用设备 ts —— 设备 ts 回跳
        时旧写法会把该帧算成 49.7 天后到期，导致回放永久卡死。
        """
        if not self._replaying:
            self._replay_timer.stop()
            return
        if self._replay_paused:          # 暂停时计时器已停，这里是防御性兜底
            return
        n = len(self._replay_frames)
        now = time.monotonic()
        while self._replay_idx < n:
            due = self._replay_base + self._replay_delta_ms(self._replay_idx) / 1000.0
            if now >= due:
                self._handle(self._replay_frames[self._replay_idx])
                self._replay_idx += 1
            else:
                break
        if self._replay_idx >= n:
            self._replay_timer.stop()
            self._replaying = False
            self.replayProgress.emit(0, 0, 0.0, 0.0)   # 完成：进度归零（与"停止"同一语义）
            self.replayStateChanged.emit(False, "回放完成（%d 帧）" % n)
            return
        # 约每 100ms 上报一次进度（tick 是 10ms，故约每 10 次发一次）
        if now - self._replay_last_emit >= REPLAY_PROGRESS_INTERVAL:
            self._replay_last_emit = now
            self._emit_replay_progress()

    def _emit_record_progress(self):
        """录制进度心跳（500ms）：把已写行数与耗时推给前端做平滑提示。

        ★ 2026-10-16 用户要求「时间戳加上几时」：耗时从「分:秒」改为「时:分:秒」。
          前端 app.js 的 hhmmss() 与之同格式 —— 两边必须一致，否则每 500ms 心跳
          一到文案就会在「01:12:34」与「72:34」之间跳变。回放进度标签未改。
        """
        if not self._recording:
            return
        el = int(time.monotonic() - self._rec_t0)
        self.recordProgress.emit(int(self._rec_rows),
                                 "%02d:%02d:%02d" % (el // 3600, (el // 60) % 60, el % 60))

    def _flush_rec_buf_locked(self):
        """把攒行缓冲一次性写盘（调用方必须已持有 _rec_lock）。"""
        if not self._rec_buf or self._rec_file is None:
            return
        data = "".join(self._rec_buf)
        self._rec_buf = []
        try:
            self._rec_file.write(data)
            # 每攒满一批就 flush：syscall 从「每行一次」降到「每 REC_WRITE_BUFFER 行一次」
            self._rec_file.flush()
        except Exception as e:
            self._rec_failed = True
            raise RecordingWriteError(str(e))

    def _commit_rec_row_locked(self, stamp):
        """三类数据到齐后成行写入（调用方持锁）。

        为什么必须配对：ADC / NAV / MOTOR 是**三类独立的帧**，各自到达、频率
        不同（100 / 25 / 50 Hz）。旧实现在收到任意一类时就写整行，另一半用
        上一帧的陈旧值填充，导致同一行里几半属于不同时刻 —— 赛后按行做时序/
        相关性分析会得出错误结论。
        现在的语义：一行 = 一次「三类数据都到齐」的快照，三半都不含陈旧值。
        """
        # ★ 行内容完全交给配置层拼：包类型 / 字段 / 小数位全从配置派生，
        #   所以在配置里加包、加字段，这里一行都不用改。
        line = CFG.build_csv_row(
            stamp, self._rec_parts[0x00],
            *[self._rec_parts[t] for t in _TYPED_ORDER])
        self._rec_buf.append(line)
        self._rec_rows += 1
        # 各包都已消费 -> 复位，等待下一组，保证同一行里的数据始终同刻
        self._rec_parts = {}
        if len(self._rec_buf) >= REC_WRITE_BUFFER:
            self._flush_rec_buf_locked()

    def _on_rec_write_error(self, err):
        """落盘失败：立即告知用户并停止录制，绝不再静默吞掉。"""
        with self._rec_lock:
            was = self._recording
            self._recording = False
        self.recordStateChanged.emit(False, "")
        self.errorMsg.emit(
            "录制写入失败，已停止录制并保留已写入内容：%s\n"
            "常见原因：磁盘空间不足 / 文件被占用 / 无写入权限。\n"
            "文件：%s" % (err, self._rec_path or self._rec_last_path))
        if was:
            self.stopRecording()

    # ---------- 导出 / 打开文件位置 ----------
    @pyqtSlot(str, str)
    def saveTextFile(self, content, default_name):
        """导出「屏幕缓冲快照」CSV：仅在完全没有录制文件时的兜底通道。

        注意：content 来自前端环形缓冲（默认上限 5000 点），不是完整录制数据。
        正常导出请走 exportRecording()。
        """
        init = os.path.join(self.save_dir, os.path.basename(str(default_name or "export.csv")))
        path, _ = QFileDialog.getSaveFileName(
            None, "导出 CSV", init, "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(content)
        except Exception as e:
            self.errorMsg.emit("保存失败：%s" % e)
            return
        self.saveResult.emit("已保存（屏幕缓冲快照）：%s" % path)

    @staticmethod
    def _export_default_name(default_name, src):
        """导出 CSV 的默认文件名（task-22 · F5）。

        · 前端给的自定义名原样使用；
        · 通用自动名（空 / `telemetry_*`）换成 `<源录制名>_导出_<导出时刻>.csv` ——
          原来的 `telemetry_<导出时刻>.csv` 看起来像一份新录制却没有配对 .bin，
          时间戳也与那次录制无关，极易被误解成"软件写名有 bug"。
        """
        auto = os.path.basename(str(default_name or "")).strip()
        if auto and not auto.lower().startswith("telemetry_"):
            return auto
        base = os.path.splitext(os.path.basename(str(src or "")))[0] or "telemetry"
        return "%s_导出_%s.csv" % (base, time.strftime("%Y%m%d_%H%M%S"))

    @pyqtSlot(str)
    def exportRecording(self, default_name):
        """导出真正的录制数据：把录制文件完整复制到用户指定位置。

        这是「导出 CSV」的正确出口 —— 与录制同一份数据，行数不受前端缓冲上限影响。
        若正在录制，会先把内存攒行缓冲 flush 再复制，保证导出内容是最新的。
        """
        src = self._rec_path if (self._recording and self._rec_path) else self._rec_last_path
        if not src or not os.path.isfile(src):
            # 没有录制文件 -> 退回屏幕缓冲快照
            self.saveResult.emit("没有可导出的录制文件，请先录制，或使用屏幕快照导出。")
            return
        # ★ task-22(F5)：默认名带上**源录制名**（见 _export_default_name 的说明）
        init = os.path.join(self.save_dir, self._export_default_name(default_name, src))
        path, _ = QFileDialog.getSaveFileName(
            None, "导出录制数据", init, "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(src):
            self.saveResult.emit("目标即录制文件本身：%s" % path)
            return
        try:
            if self._recording:
                with self._rec_lock:
                    if self._rec_file is not None:
                        self._flush_rec_buf_locked()
            shutil.copyfile(src, path)
        except Exception as e:
            self.errorMsg.emit("导出失败：%s" % e)
            return
        self.saveResult.emit("已导出录制数据（%d 行）：%s" % (self._rec_rows, path))

    @pyqtSlot(str)
    def showInExplorer(self, path):
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if not d:
            return
        try:
            os.startfile(d)  # Windows：打开资源管理器
        except Exception as e:
            self.errorMsg.emit("无法打开目录：%s" % e)

    # ---------- 连接 / 断开 ----------
    @pyqtSlot(str, int)
    def connect(self, port: str, baud: int):
        if self.running:
            return
        # ★ 若上一次的读取线程尚未退出，先等它结束。
        #   否则旧线程会在 self.ser 被换成新串口后重新满足循环条件，
        #   造成两个线程并发读同一串口、同时改同一个 buf —— 数据会错乱。
        self._join_reader()
        try:
            # ★ 数据位/校验位/停止位全部取自 telemetry_config.py 第 1 区，
            #   界面上只让你选「端口 + 波特率」这两项最常用的。
            #   要改校验位等就去改配置文件，不用动这里。
            self.ser = serial.Serial(
                str(port), int(baud),
                bytesize=_SERIAL_BYTESIZE,
                parity=_SERIAL_PARITY,
                stopbits=_SERIAL_STOPBITS,
                timeout=CFG.SERIAL.get("timeout", 0.05),
                write_timeout=1.0)
        except Exception as e:
            self.errorMsg.emit("无法打开 %s：%s" % (port, e))
            return
        if not self.ser.is_open:
            self.errorMsg.emit("%s 打开失败" % port)
            self.ser = None
            return
        self.buf = bytearray()
        self.running = True       # 先置位，再启动线程
        self.statusChanged.emit(True, "%s@%s" % (port, baud))
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _join_reader(self, timeout: float = 1.5):
        """等待读取线程退出（关闭串口会让阻塞的 read 立即返回）。"""
        t = self.thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout)
            except Exception:
                pass
        self.thread = None

    @pyqtSlot()
    def disconnect(self):
        self.running = False
        # 顺序很重要：先让读取线程停下来，再碰录制文件。
        # 否则主线程 close 文件的同时读线程可能正在 _handle 里写它。
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self._join_reader()
        self.stopRecording()
        self.ser = None
        self.statusChanged.emit(False, "")

    # ---------- 发送指令 ----------
    @pyqtSlot(str, str)
    def send(self, text: str, mode: str):
        if not self.ser or not self.ser.is_open:
            self.errorMsg.emit("未连接，无法发送")
            return
        try:
            if str(mode).upper() == "HEX":
                data = bytes.fromhex(text.replace(" ", "").replace(",", ""))
            else:
                data = text.encode("utf-8", "ignore")
            self.ser.write(data)
        except Exception as e:
            self.errorMsg.emit("发送失败：%s" % e)

    # ---------- 读取线程 ----------
    def _reader(self):
        # ★ 把串口与缓冲捕获为局部变量：避免 disconnect→connect 之间 self.ser/self.buf
        #   被主线程换成新对象后，本线程仍在读同一对象导致的竞态。
        ser = self.ser
        buf = self.buf
        # 接收统计（诊断用）：每 5 秒写一行到 reader.log，用来判断是"线程死了"还是"没数据"
        stat_bytes = 0
        stat_frames = 0
        stat_t = time.monotonic()
        self._log("读取线程启动，串口=%s" % (getattr(ser, "port", "?"),))
        while self.running and ser and ser.is_open:
            try:
                n = ser.in_waiting
                # 有数据读全部；空闲读 1 字节阻塞等待（最多 SERIAL_TIMEOUT，不忙等）
                data = ser.read(n) if n > 0 else ser.read(1)
            except Exception as e:
                # 串口读取异常：记录原因后退出（端口被关闭属正常路径，这里也一并记录）
                self._log("读取异常，线程退出: %r" % e)
                break
            if not data:
                continue
            buf.extend(data)
            stat_bytes += len(data)
            # 防止缓冲区无限增长（保留最近 1MB）
            if len(buf) > (1 << 20):
                del buf[: len(buf) - (1 << 20)]
            try:
                frames = protocol.parse_stream(buf)
                stat_frames += len(frames)
                for fr in frames:
                    self._handle(fr)
            except Exception as e:
                # ★ 单帧解析/处理异常绝不能让读取线程死亡：记录并清空缓冲继续。
                #   否则任何一个坏帧/异常都会让数据流静默中断（界面仍显示"已连接"）。
                self._log("解析/处理异常（已跳过，继续接收）: %r" % e)
                buf.clear()
            # 每 5 秒记录一次接收速率，便于判断数据流是否正常
            if time.monotonic() - stat_t >= 5.0:
                self._log("接收统计: %.0f B/s, %.0f 帧/s, buf=%d, pending=%d"
                          % (stat_bytes / 5.0, stat_frames / 5.0, len(buf), len(self._pending)))
                stat_bytes = 0
                stat_frames = 0
                stat_t = time.monotonic()
        self._log("读取线程结束（running=%s）" % self.running)

    def _payload_of(self, fr: protocol.ParsedFrame):
        """把一帧解析结果转成与 dataReady **完全一致**的单帧 payload；**不改变任何内部状态**。

        返回 None = 配置里没有这个包类型（与 _handle 的丢弃行为一致）。

        为什么单独抽出来：跳转回填（replayBackfill）要发同样格式的元素。若回填另写一份拼装
        代码，两边迟早漂移（前端就得按两种格式解析）——所以实时推送与回填共用这一个函数。
        ★ 这里绝不能顺手更新 _last_adc / _last_typed：那些是"最新值"状态，只该由真实到达的
          帧（_handle）更新。回填喂的是**历史**帧，污染它们会让录制配对把旧值配进新行。
        """
        if fr.type_id == 0x00:   # ADC（27B / 100Hz）
            # 注意键名必须和配置里的 short 字段一致（CH1..CH10），写成 CH01 会全读到 0
            # ★ 顺序走 CFG.adc_out_order()：与 CSV 列名、界面卡片同源。
            #   第 3 区 ADC_ORDER 一改，三者一起变，不可能出现"列和数据错开"。
            return {
                "type": "adc", "seq": fr.seq, "ts": fr.ts,
                "values": [fr.values.get(CFG.ADC_CHANNELS[i]["short"], 0)
                           for i in CFG.adc_out_order()],
            }
        if fr.type_id in _TYPED_PACKETS:     # NAV / MOTOR / 以及你新增的包
            # ★ 取值顺序严格按配置的 fields，不写死字段名。
            #   配置里加/删/改名一个字段，这里和 CSV 会一起跟着变，
            #   不会出现"改了配置但录出来的还是旧列"。
            names = _TYPED_PACKETS[fr.type_id]
            vals = [float(fr.values.get(nm, 0.0)) for nm in names]
            payload = {"type": fr.name.lower(), "seq": fr.seq, "ts": fr.ts,
                       "names": names, "values": vals}
            # 兼容前端：再按第 5 区的映射发一份小写键名（app.js 仍按老键读取）
            for nm, v in zip(names, vals):
                payload[CFG.FRONTEND_KEYS.get(nm, nm.lower())] = v
            return payload
        return None                          # 配置里没有的包类型，不推送

    def _handle(self, fr: protocol.ParsedFrame):
        payload = self._payload_of(fr)
        if payload is None:
            return
        if fr.type_id == 0x00:
            self._last_adc = payload["values"]      # 录制配对用（顺序 = adc_out_order）
        else:
            vals = payload["values"]
            self._last_typed[fr.type_id] = vals     # 录制配对用（按类型号索引）
            if fr.type_id == 0x01:
                self._last_nav = vals
            elif fr.type_id == 0x02:
                self._last_motor = vals
        # 录制在读取线程直接写盘，不受前端渲染性能影响。
        # ★ 配对成行：把配置里声明的各包数据攒齐，只有「都到齐」时才写一行，
        #   保证同一行里的数据同刻、无陈旧值。
        #   时间列 = 上位机接收时刻（HH:MM:SS.mmm），不再只到秒。
        if self._recording:
            try:
                with self._rec_lock:
                    if self._recording and self._rec_file is not None:
                        # 原始字节流：每个合法帧的原始字节追加写 .bin（数据回放的数据源）
                        if self._rec_bin_file is not None:
                            self._rec_bin_file.write(fr.raw)
                            self._rec_bin_size += len(fr.raw)
                        if fr.type_id == 0x00:
                            self._rec_parts[0x00] = list(self._last_adc)
                        elif fr.type_id in _TYPED_PACKETS:
                            self._rec_parts[fr.type_id] = list(
                                self._last_typed.get(fr.type_id, []))
                        # 配置里声明的包全都到齐才成行；缺一个就先攒着，不写半行
                        if (0x00 in self._rec_parts
                                and all(t in self._rec_parts for t in _TYPED_ORDER)):
                            self._commit_rec_row_locked(recv_stamp())
            except RecordingWriteError as e:
                self._on_rec_write_error(e)
            except Exception as e:
                self._on_rec_write_error(e)
        # ★ HEX 字符串推迟到推送时才生成：每帧 27 字节 × 175 帧/s ≈ 4700 次格式化/s，
        #   而 HEX 窗口每 100ms 最多显示 20 行 —— 绝大多数帧的 HEX 根本用不上。
        #   这里只存原始字节（bytes 浅拷贝，几乎零成本）。
        with self._lock:
            # 主进程被弹窗/文件对话框阻塞时 _pending 会一直涨：这里硬性设上限，
            # 丢弃最旧的帧（宁可丢历史，也要保住实时性与内存不失控）。
            if len(self._pending) >= MAX_PENDING_FRAMES:
                del self._pending[: len(self._pending) - MAX_PENDING_FRAMES + 1]
            # ★ 非有限浮点先归一为 None，避免 json 产出非法 JSON 让前端整批丢帧
            self._pending.append((json.dumps(self._finite(payload)), fr.raw))

    def _flush(self):
        """主线程定时器：批量把缓冲的帧推给页面（降低信号频率）。

        关键点：数据率不定且可能很高（数百~数千帧/秒），若每帧发一个信号会
        打爆 UI 线程消息队列导致界面卡顿/闪烁。这里做两件事：
          1) HEX 合并为「一次信号、多行文本」，把 N 次信号压成 1 次；
          2) 洪峰限流：超限只保留最近若干帧，宁可丢旧帧也要保证实时性。
        """
        if not self._pending:
            return
        with self._lock:
            batch = self._pending
            self._pending = []
        if len(batch) > MAX_FLUSH_FRAMES:
            batch = batch[-MAX_FLUSH_FRAMES:]
        arr = [p[0] for p in batch]

        # HEX 监视器：限频 + 只格式化最近若干帧。
        # 不设限频时 20Hz × 20 行 = 400 行/s 灌进渲染进程，纯属浪费且刷得看不清。
        now = time.monotonic()
        if batch and now - self._last_hex_t >= HEX_EMIT_INTERVAL:
            self._last_hex_t = now
            hex_part = batch[-MAX_HEX_LINES:] if len(batch) > MAX_HEX_LINES else batch
            raw_text = "\n".join(" ".join("%02X" % b for b in p[1]) for p in hex_part)
            if raw_text:
                self.rawReady.emit(raw_text)

        self.dataReady.emit("[" + ",".join(arr) + "]")

    @staticmethod
    def _finite(payload: dict) -> dict:
        """把 payload 里的非有限浮点值（NaN / ±Inf）替换为 None —— **递归**处理。

        背景：Python 的 json.dumps 默认把非有限值写成裸的 `NaN` / `Infinity`，
        这是**非法 JSON**，浏览器 JSON.parse 会直接抛异常；而 app.js 的
        onDataBatch 接不住这个异常 —— 结果是**整批（最多 1500 帧）静默消失**：
        曲线卡住、无报错、无日志。
        映射为 None -> JSON null -> JS null，前端显示占位符，单点降级而不是整批丢失。

        ★ 2026-10-16 修：原实现只查**顶层** float，而 payload 的数值其实装在
        `values` **列表**里（{"type":"nav","names":[...],"values":[...]}），
        列表里的 NaN/Inf 会原样穿过 —— 于是这道保险在最需要它的地方是失效的。
        实测复现：
            payload = {"type":"nav", ..., "values":[float('nan'), float('inf'), 0.0]}
            旧实现 -> '"values": [NaN, Infinity, 0.0]'   ← 非法 JSON，前端整批丢失
            新实现 -> '"values": [null, null, 0.0]'      ← 合法，单点降级
        之所以之前没暴露：v2 协议字段全是 int，只有把配置里的 scale/零点改成
        NaN/Inf（telemetry_user.json 允许）才会触发 —— 一旦触发就是静默丢整批数据。
        """
        def clean(v):
            if isinstance(v, float):
                return v if math.isfinite(v) else None
            if isinstance(v, dict):
                return {k: clean(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [clean(x) for x in v]
            return v
        return clean(payload)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("无线数传 · CSUST Racing Telemetry " + APP_VERSION)
        self.setMinimumSize(QSize(1024, 680))
        self.resize(1440, 860)
        self.setWindowIcon(QIcon(resource_path("logo.ico")))

        self.view = QWebEngineView()
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, False)
        # 关闭 GPU 加速 canvas / 平滑滚动动画：消除高频重绘下的窗口抖动
        try:
            s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        except Exception:
            pass
        self.setCentralWidget(self.view)

        # WebChannel：把 Backend 暴露给页面
        self.backend = Backend()
        self.channel = QWebChannel()
        self.channel.registerObject("backend", self.backend)
        self.view.page().setWebChannel(self.channel)

        index_path = resource_path(os.path.join("telemetry_web", "index.html"))
        self.view.setUrl(QUrl.fromLocalFile(index_path))

        # 页面加载完成后：枚举端口 + 注入 QWebChannel 客户端（兜底）
        self.view.loadFinished.connect(self._on_loaded)

    def _on_loaded(self, ok):
        self.backend.enumerate_ports()
        self.backend.getSaveDir()
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            try:
                js = bytes(f.readAll()).decode("utf-8", "ignore")
                if js.strip():
                    self.view.page().runJavaScript(js)
            finally:
                f.close()

    def closeEvent(self, event):
        """关窗收尾：先断开（内部会 join 读取线程 + flush/close 录制文件）再退出。

        没有这一步时，关闭窗口会直接杀掉进程：录制缓冲里最多 256 行还没
        落盘的数据会随进程一起消失，而且录制文件不会被正常关闭。
        """
        try:
            self.backend.disconnect()
        except Exception:
            pass
        event.accept()


def main():
    # ★ 首次运行自动生成 telemetry_user.json 模板，然后再加载一次。
    #   顺序不能反：模板是这一次刚创建的，import 时那次自动加载还没看见它。
    try:
        created = CFG.write_user_cfg_template()
        if created:
            print("[config] 已生成外部配置文件模板：%s" % created)
            print("[config] 改里面的值保存后重启即可生效，不必重新打包")
        CFG.load_user_cfg()
        if CFG.USER_CFG_PATH:
            print("[config] 已加载外部配置：%s" % CFG.USER_CFG_PATH)
        elif CFG.USER_CFG_ERROR:
            print("[config] %s" % CFG.USER_CFG_ERROR)
    except Exception as e:
        print("[config] 外部配置初始化失败（按内置配置运行）：%s" % e)

    # ★ 启动日志：记录本次实际生效的图形后端。
    #   排查闪烁时经常要回答"这个 exe 到底跑的哪个后端"——打包成窗口程序后
    #   没有控制台，print 看不见，所以落一行盘。文件：<保存目录>\startup.log
    try:
        _p = os.path.join(default_save_dir(), "startup.log")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write("%s  图形后端=%s  QSG_RHI_BACKEND=%s  渲染模式=%s  冻结=%s\n"
                     % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        _GL_NAME,
                        os.environ.get("QSG_RHI_BACKEND", "(unset)"),
                        RENDER_MODE,
                        "是" if getattr(sys, "frozen", False) else "否"))
    except Exception:
        pass

    # ★★ 必须在 QApplication 之前设置 ★★
    #   让 Qt 与 QtWebEngine 共享同一个 OpenGL 上下文。否则 WebEngine 的画面提交
    #   与 Qt 部件重绘不同步 —— 这正是「窗口一闪一闪」的根因（GPU 模式尤其明显）。
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("logo.ico")))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
