"""★「改了配置，界面到底动没动」矩阵自检 ★

这是本项目最重要的一道自检。它会：
  1. 用**真实的 telemetry_config.ui_config()** 生成一份配置；
  2. 逐个把用户最可能改的项改掉（通道名/单位/量程/波特率…）；
  3. 把改后的配置喂给真实的 index.html + app.js，调 applyUiConfig()；
  4. 读回 DOM **实际显示的文字**和信号表内部的 min/max，判断有没有跟着变。

任何一行 FAIL = 用户改了对应配置项，界面却纹丝不动 —— 这是最伤的一种 bug，
因为它不会报错、其余自检也全绿，只有用户肉眼看得出来。

用法：
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\python.exe tools\\config_matrix_audit.py
    exit code 0 = 全部生效；非 0 = 存在失效项（会打印清单）
"""
import os
import sys
import copy
import json
import argparse

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                     "--disable-gpu --no-sandbox --disable-software-rasterizer")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import telemetry_config as C  # noqa: E402

from PyQt6.QtCore import QUrl, QTimer          # noqa: E402
from PyQt6.QtWidgets import QApplication       # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage     # noqa: E402

# 本矩阵里给 ACCEL 注入的测试 scale（期望值要从它推导，别到处写死）
_ACC_TEST_SCALE = 0.001
# 给 SPEED 注入的测试 scale。★ 期望文案必须由它推导：
#   以前这里把期望写成死的 "0.01"，于是用户把自己配置里的 SPEED scale 改成
#   0.001 之后，这条用例的语义就对不上了（它验的其实是"注入值"，不是"你的值"）。
_SPD_TEST_SCALE = 0.001


def _fmt_scale_chip(s):
    """复刻 app.js 的 fmtScaleChip()：1 -> 'RAW'，否则 ×<3 位有效数字>。"""
    if not s or s == 1:
        return "RAW"
    return "×" + str(float("%.3g" % float(s)))


def _fmt_like_frontend(v):
    """复刻 app.js 里 fmtFs() 的格式化规则，用来推导期望的刻度文案。"""
    a = abs(v)
    if a >= 1000:
        return str(int(round(v)))
    if a >= 1:
        s = ("%.1f" % v).rstrip("0").rstrip(".")
        return s or "0"
    if a == 0:
        return "0"
    from decimal import Decimal
    return str(float("%.3g" % v))


def _first_accel_field():
    """当前配置里第一个 ACCEL* 字段名（协议把 ACCEL 拆成 ACCEL_LAT/ACCEL_LON 后，
    还有老配置的 ACCEL）—— 加速度仪表取量程/单位就是按这个规则找的。"""
    for f in C.PROTOCOL[0x01]["fields"]:
        if str(f[0]).startswith("ACCEL"):
            return f[0]
    return None


def _expect_accel_scale(got, scale=None):
    """期望：加速度条左刻度 == 配置推导出的满量程，且**显式 GAUGE_RANGES.ACCEL 优先**。

    与 app.js 的 ACC_FS 规则一致：
        GAUGE_RANGES["ACCEL"] 是正数  →  用它（用户 2026-10-15 起显式指定 16.384）
        否则                          →  ACC_RAW_FULLSCALE × 该 ACCEL* 字段的 scale

    ★ 2026-10-16 修：本函数以前**从磁盘配置 C.PROTOCOL 读 scale**，而本用例实际是向
      界面注入 _ACC_TEST_SCALE。用户一旦把配置里的 ACCEL scale 改成别的值（如 0.0098），
      注入值 0.001 与"磁盘值"就不一致 → 界面按注入值画、期望按磁盘值算 → **假红**。
      现在改为：**期望必须用"注入进界面的那个 scale"**，没有显式 GAUGE_RANGES.ACCEL 时
      一律以传入的 scale 推导；scale 为 None 时才回退读当前配置（兼容别的调用点）。
    """
    gr = C.GAUGE_RANGES.get("ACCEL")
    if isinstance(gr, (int, float)) and float(gr) > 0:
        want, how = float(gr), "显式 GAUGE_RANGES.ACCEL"
    else:
        name = _first_accel_field()
        s = scale
        if s is None:
            s = 1.0
            for f in C.PROTOCOL[0x01]["fields"]:
                if name is not None and f[0] == name:
                    s = float(f[3])
                    break
            how = "ACC_RAW_FULLSCALE(%g) × %s.scale(%g)［当前配置］" % (C.ACC_RAW_FULLSCALE, name, s)
        else:
            how = "ACC_RAW_FULLSCALE(%g) × %s.scale(%g)［本用例注入值］" % (C.ACC_RAW_FULLSCALE, name, s)
        want = C.ACC_RAW_FULLSCALE * float(s)
    txt = str(got)
    ok = _fmt_like_frontend(want) in txt or _fmt_like_frontend(want) in txt.replace("−", "-")
    return (ok, "加速度左刻度应≈-%s（%s），实际 %r" % (_fmt_like_frontend(want), how, got))


def _expect_dec(got, want_dec):
    """断言 DOM 里读数的小数位数 == want_dec（位数只认配置，不看 scale / 量级）。"""
    txt = str(got).strip()
    if "." in txt:
        dec = len(txt.split(".")[1])
    else:
        dec = 0
    return (dec == want_dec,
            "读数应为 %d 位小数，实际 %r（%d 位）" % (want_dec, txt, dec))


class Probe(QWebEnginePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = []

    def javaScriptConsoleMessage(self, level, msg, line, source):
        self.console.append("[%s] %s (line %d)" % (level.name, msg, line))


INDEX_URL = QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html"))

INSTALL_JS = """
(function(){
  window.__errs = [];
  window.addEventListener('error', function(e){
    window.__errs.push((e.message||'err') + ' @' + (e.lineno||0));
  });
})();
"""


# ---------------------------------------------------------------------------
# 用例定义
#   每个用例 = 「用户改什么」 -> 「界面哪里应该跟着变」
#   mutate : 在 Python 侧改真实配置（模拟用户改 telemetry_config.py）
#   probe  : 页面侧读回证据的 JS（返回可 JSON 化的值）
#   expect : 断言函数 got -> (bool, 说明)
# ---------------------------------------------------------------------------
def _mut_nav_unit(cfg, sym, val):
    for f in cfg["nav"]:
        if f["name"] == sym:
            f["unit"] = val


def _mut_motor_unit(cfg, sym, val):
    for f in cfg["motor"]:
        if f["name"] == sym:
            f["unit"] = val


def _gr(cfg):
    cfg.setdefault("gaugeRanges", {})
    return cfg["gaugeRanges"]


def _mut_adc_zero(cfg, z):
    """把第 1 个 ADC 通道的零点改成 z，用【真源】重新推导下发给前端的那一项。

    ★ 改完立刻还原真实配置：矩阵里每个用例都基于 C.ui_config() 的新快照，
      不还原会污染后面所有用例的基线（这类"用例之间互相污染"最难查）。
    """
    ch = C.ADC_CHANNELS[0]
    old = ch.get("zero", 0)
    ch["zero"] = z
    try:
        cfg["adc"][0] = C.ui_adc_config()[0]      # 真源：含 min/max/zero 全套
    finally:
        if old is None:
            ch.pop("zero", None)
        else:
            ch["zero"] = old


def _expect_zero_range(g):
    """期望：量程跨 0，且端点 = ±(zero 到两端的工程量)。"""
    d = _as_obj(g)
    if not d:
        return (False, "页面没返回量程，实得 %r" % (g,))
    step = float(C.ADC_CHANNELS[0]["rng"]) / 4095.0
    want_lo, want_hi = -2048.0 * step, (4095.0 - 2048.0) * step
    try:
        lo, hi = float(d.get("min")), float(d.get("max"))
    except (TypeError, ValueError):
        return (False, "量程不是数字：%r" % (d,))
    ok = (abs(lo - want_lo) < 1e-6 and abs(hi - want_hi) < 1e-6 and lo < 0 < hi)
    return (ok, "量程应约 %.3f ~ %.3f 且跨 0，实得 %.3f ~ %.3f（txt=%r）"
            % (want_lo, want_hi, lo, hi, d.get("txt")))


def _set_in(cfg, group, sym, key, val):
    """在 nav / motor 字段表里按信号名改一个键。mutate 必须返回 None。"""
    for f in cfg.get(group, []):
        if f.get("name") == sym:
            f[key] = val


def _as_obj(got):
    """QWebEngine 回调可能给 dict，也可能给 JSON 字符串，统一成 dict。"""
    if isinstance(got, dict):
        return got
    if isinstance(got, str):
        try:
            v = json.loads(got)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


CASES = [
    # ---- 通道名 / 单位 ----
    dict(
        section="通道显示",
        desc="改第 1 个 ADC 通道的名字",
        mutate=lambda c: c["adc"][0].__setitem__("name", "前左悬架"),
        probe="document.getElementById('adcGrid') ? "
              "(document.getElementById('adcGrid').children[0]||{}).textContent : ''",
        expect=lambda g: ("前左悬架" in str(g), "卡片 1 文字里应出现 %r" % "前左悬架"),
    ),
    dict(
        section="通道显示",
        desc="改第 1 个 ADC 通道的单位",
        mutate=lambda c: c["adc"][0].__setitem__("unit", "cm"),
        probe="document.getElementById('adcGrid') ? "
              "(document.getElementById('adcGrid').children[0]||{}).textContent : ''",
        expect=lambda g: ("cm" in str(g), "卡片 1 文字里应出现单位 cm"),
    ),
    dict(
        section="通道显示",
        desc="ADC 小数位 1 -> 3（第 3 区 ADC_DECIMALS）",
        # 界面与 CSV 必须同口径：改 dec 后卡片读数就要跟着变位数。
        # 灌一个 3 位小数都不同的值，读 DOM 实际文字判断，只读内部变量抓不到。
        mutate=lambda c: c["adc"][0].__setitem__("dec", 3),
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.adc0 = 13.6666; state.hasData.adc = true;"
              "  if(typeof updateAdcDom==='function') updateAdcDom(state.latest, true);"
              "  var el=document.getElementById('adcGrid');"
              "  var v=el&&el.children[0]?el.children[0].querySelector('[data-val]'):null;"
              "  return v?v.textContent:'';})()",
        expect=lambda g: ("13.667" in str(g),
                          "卡片 1 应显示 %r（3 位小数），实得 %r" % ("13.667", g)),
    ),
    dict(
        section="通道显示",
        desc="ADC 小数位 1 -> 0（第 3 区 ADC_DECIMALS）",
        mutate=lambda c: c["adc"][0].__setitem__("dec", 0),
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.adc0 = 13.6666; state.hasData.adc = true;"
              "  if(typeof updateAdcDom==='function') updateAdcDom(state.latest, true);"
              "  var el=document.getElementById('adcGrid');"
              "  var v=el&&el.children[0]?el.children[0].querySelector('[data-val]'):null;"
              "  return v?v.textContent:'';})()",
        # 13.6666.toFixed(0) = "14"（四舍五入），别写成 13
        expect=lambda g: ("." not in str(g) and str(g).strip() == "14",
                          "卡片 1 应显示整数 14，实得 %r" % (g,)),
    ),
    # ---- 零点 / 偏置（零点在中间的传感器）----
    dict(
        section="零点",
        desc="通道 1 填零点 zero=2048（零点在中间）→ 界面量程必须跨 0",
        # ★ mutate 走【真源】：真的把 zero 写进配置、用 CFG.ui_adc_config() 重新
        #   推一份下发给前端，然后立刻还原 —— 避免"手工算出期望值"式的自证。
        mutate=lambda c: _mut_adc_zero(c, 2048.0),
        probe="(function(){"
              "  if(typeof SIGS==='undefined') return 'nosigs';"
              "  if(typeof state!=='undefined'){"
              "    state.latest.adc0 = -7.25; state.hasData.adc = true;"
              "    if(typeof updateAdcDom==='function') updateAdcDom(state.latest, true); }"
              "  var el=document.getElementById('adcGrid');"
              "  var v=el&&el.children[0]?el.children[0].querySelector('[data-val]'):null;"
              "  return JSON.stringify({min:SIGS['adc0'].min, max:SIGS['adc0'].max,"
              "                         txt:v?v.textContent:''});})()",
        expect=lambda g: _expect_zero_range(g),
    ),
    dict(
        section="零点",
        desc="零点在中间时负值读数要能显示（含负号，不是被压成 0）",
        mutate=lambda c: _mut_adc_zero(c, 2048.0),
        # 注入值取 -7.24：-7.25.toFixed(1) 在 JS 里是 "-7.3"（舍入到远离零的一侧），
        # 写期望值时别踩这个坑；-7.24 才是稳稳的 -7.2。
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.adc0 = -7.24; state.hasData.adc = true;"
              "  if(typeof updateAdcDom==='function') updateAdcDom(state.latest, true);"
              "  var el=document.getElementById('adcGrid');"
              "  var v=el&&el.children[0]?el.children[0].querySelector('[data-val]'):null;"
              "  return v?v.textContent:'';})()",
        expect=lambda g: (str(g).strip().startswith("-") and "7.2" in str(g),
                          "负值读数应显示为 -7.2（一位小数），实得 %r" % (g,)),
    ),
    dict(
        section="通道显示",
        desc="车速单位 m/s -> km/h",
        mutate=lambda c: _mut_motor_unit(c, "SPEED", "km/h"),
        probe="(function(){var e=document.querySelector('#speedNum')&&"
              "document.querySelector('#speedNum').parentNode;"
              "return e?e.textContent:'';})()",
        expect=lambda g: ("km/h" in str(g), "车速单位后缀应变 km/h"),
    ),
    # ---- 量程（GAUGE_RANGES）----
    dict(
        section="量程",
        desc="GAUGE_RANGES.SPEED 40 -> 20",
        mutate=lambda c: _gr(c).__setitem__("SPEED", 20.0),
        probe="(function(){var e=document.getElementById('speedSub');"
              "return e?e.textContent:'';})()",
        expect=lambda g: ("20" in str(g), "车速副标题应出现量程 20"),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.VOLTAGE 600 -> 120（低压车）",
        mutate=lambda c: _gr(c).__setitem__("VOLTAGE", 120.0),
        probe="(function(){var v=document.getElementById('voltBar');"
              "return JSON.stringify({scale:v?(v.style.transform||''):'',"
              "max:(typeof SIGS!=='undefined'&&SIGS['volt'])?SIGS['volt'].max:null});})()",
        expect=lambda g: (abs(_as_obj(g).get("max", -1) - 120.0) < 1e-6,
                          "SIGS['volt'].max 应等于 120，实际 %r" % (g,)),
    ),
    dict(
        section="量程",
        desc="电压条形图的「量程文案」应跟着 SCR_PERIOD 变",
        mutate=lambda c: _gr(c).__setitem__("VOLTAGE", 120.0),
        probe="(function(){var b=document.getElementById('voltBar');"
              "var card=b?b.closest('.scard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return sc?sc.textContent:'';})()",
        expect=lambda g: ("120" in str(g), "电压刻度文案应出现 120"),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.CURRENT 200 -> 60",
        mutate=lambda c: _gr(c).__setitem__("CURRENT", 60.0),
        probe="(function(){var b=document.getElementById('ampBar');"
              "var card=b?b.closest('.scard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return sc?sc.textContent:'';})()",
        expect=lambda g: ("60" in str(g), "电流刻度文案应出现 60"),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.TORQUE 150 -> 80",
        mutate=lambda c: _gr(c).__setitem__("TORQUE", 80.0),
        probe="(function(){var b=document.getElementById('tqBar');"
              "var card=b?b.closest('.hcard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return sc?sc.textContent:'';})()",
        expect=lambda g: ("80" in str(g), "扭矩刻度文案应出现 80"),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.MOTOR_RPM 10000 -> 6000（条形比例也应跟着变）",
        mutate=lambda c: _gr(c).__setitem__("MOTOR_RPM", 6000.0),
        probe="(function(){var b=document.getElementById('rpmBar');"
              "var card=b?b.closest('.hcard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return JSON.stringify({txt:sc?sc.textContent:'',"
              "max:(typeof SIGS!=='undefined'&&SIGS['rpm'])?SIGS['rpm'].max:null});})()",
        expect=lambda g: (abs(_as_obj(g).get("max", -1) - 6000.0) < 1e-6
                          and "6k" in str(_as_obj(g).get("txt", "")),
                          "转速量程应双向同步（文案+内部 max），实际 %r" % (g,)),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.MOTOR_TEMP 120 -> 90",
        mutate=lambda c: _gr(c).__setitem__("MOTOR_TEMP", 90.0),
        probe="(function(){var b=document.getElementById('tempBar');"
              "var card=b?b.closest('.scard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return sc?sc.textContent:'';})()",
        expect=lambda g: ("90" in str(g), "温度刻度文案应出现 90"),
    ),
    dict(
        section="量程",
        desc="GAUGE_RANGES.THROTTLE 100 -> 50",
        mutate=lambda c: _gr(c).__setitem__("THROTTLE", 50.0),
        probe="(function(){var b=document.getElementById('thrBar');"
              "var card=b?b.closest('.hcard'):null;"
              "var sc=card?card.querySelector('.bar-scale'):null;"
              "return sc?sc.textContent:'';})()",
        expect=lambda g: ("50" in str(g), "油门刻度文案应出现 50"),
    ),
    # ---- 单位（NAV / MOTOR）----
    dict(
        section="单位",
        desc="给 VOLTAGE 配单位 V -> Vdc",
        mutate=lambda c: _mut_motor_unit(c, "VOLTAGE", "Vdc"),
        probe="(function(){var e=document.getElementById('voltNum');"
              "return e&&e.parentNode?e.parentNode.textContent:'';})()",
        expect=lambda g: ("Vdc" in str(g), "电压单元后缀应变 Vdc"),
    ),
    dict(
        section="单位",
        desc="给 MOTOR_TEMP 配单位 °C -> ℃",
        mutate=lambda c: _mut_motor_unit(c, "MOTOR_TEMP", "℃"),
        probe="(function(){var e=document.getElementById('tempNum');"
              "return e&&e.parentNode?e.parentNode.textContent:'';})()",
        expect=lambda g: ("℃" in str(g), "温度单位后缀应变 ℃"),
    ),
    dict(
        section="单位",
        desc="ROLL 单位 ° -> deg",
        mutate=lambda c: _mut_nav_unit(c, "ROLL", "deg"),
        probe="(function(){var e=document.getElementById('rollNum');"
              "return e&&e.parentNode?e.parentNode.textContent:'';})()",
        expect=lambda g: ("deg" in str(g), "Roll 单位后缀应变 deg"),
    ),
    # ---- scale ----
    dict(
        section="系数",
        desc="ACCEL* 字段 scale 改了（显式满量程时刻度不应跟着动，否则应缩放）",
        # ★ 目标字段不再写死 "ACCEL"：协议 2026-10-15 把单路 ACCEL 拆成
        #   ACCEL_LAT / ACCEL_LON，写死老名字会改到"不存在的字段"，变成空跑（假绿）。
        mutate=lambda c: _set_in(c, "nav", _first_accel_field(), "scale", _ACC_TEST_SCALE),
        probe="(function(){var lo=document.getElementById('accScaleLo');"
              "return lo?lo.textContent:'';})()",
        # ⚠️ 期望值必须【按当前配置推导】，不能写死 ±16.4 / ±1000。
        #    以前这里就写死过 16.4（源自 ACC_RAW_FULLSCALE=16384），
        #    用户一把满量程改成 1000000，正确结果变成 ±1000 却报失败 ——
        #    自检撒谎比没有自检更糟。现在的规则见 _expect_accel_scale：
        #    显式 GAUGE_RANGES.ACCEL 优先，否则 ACC_RAW_FULLSCALE × scale。
        expect=lambda g: _expect_accel_scale(g, _ACC_TEST_SCALE),
    ),
    dict(
        section="系数",
        desc="SPEED scale 改了，副标题系数角标应跟着变（期望按注入值推导）",
        mutate=lambda c: _set_in(c, "motor", "SPEED", "scale", _SPD_TEST_SCALE),
        probe="(function(){var e=document.getElementById('speedSub');"
              "return e?e.textContent:'';})()",
        # ★ 期望从 _SPD_TEST_SCALE 推，别写死 "0.01" —— 写死的期望值在别人改了
        #   用例注入值（或用户改了自己配置里的值）之后就变成假绿灯/假红灯。
        expect=lambda g: (_fmt_scale_chip(_SPD_TEST_SCALE) in str(g),
                          "车速副标题应显示系数 %s，实际 %r"
                          % (_fmt_scale_chip(_SPD_TEST_SCALE), g)),
    ),
    # ---- 小数位（★ 与 scale 解耦，2026-10-09）----
    dict(
        section="小数位",
        desc="改 SPEED 的 scale 不应改变读数的小数位",
        # 以前位数 = ceil(-log10(scale))：改一次系数位数就跳，界面/CSV/自检三个
        # 口径一起漂。这里灌一个 4 位小数的值，断言位数恒等于配置里的 dec。
        mutate=lambda c: _set_in(c, "motor", "SPEED", "scale", 0.0001),
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.speed = 12.3456; state.hasData.motor = true;"
              "  if(typeof updateMotorDom==='function') updateMotorDom(state.latest, true);"
              "  var e=document.getElementById('speedNum');"
              "  return e?e.textContent:'';})()",
        expect=lambda g: _expect_dec(g, C.field_decimals("SPEED")),
    ),
    dict(
        section="小数位",
        desc="第 5 区 FIELD_DECIMALS.SPEED 2 -> 3，界面读数应变 3 位",
        mutate=lambda c: _set_in(c, "motor", "SPEED", "dec", 3),
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.speed = 12.3456; state.hasData.motor = true;"
              "  if(typeof updateMotorDom==='function') updateMotorDom(state.latest, true);"
              "  var e=document.getElementById('speedNum');"
              "  return e?e.textContent:'';})()",
        expect=lambda g: _expect_dec(g, 3),
    ),
    dict(
        section="小数位",
        desc="第 5 区 FIELD_DECIMALS.VOLTAGE 1 -> 0，电压读数应变整数",
        mutate=lambda c: _set_in(c, "motor", "VOLTAGE", "dec", 0),
        probe="(function(){"
              "  if(typeof state==='undefined') return 'nostate';"
              "  state.latest.volt = 438.6; state.hasData.motor = true;"
              "  if(typeof updateMotorDom==='function') updateMotorDom(state.latest, true);"
              "  var e=document.getElementById('voltNum');"
              "  return e?e.textContent:'';})()",
        expect=lambda g: _expect_dec(g, 0),
    ),
    # ---- 串口 ----
    dict(
        section="串口",
        desc="SERIAL 波特率候选加 921600",
        mutate=lambda c: c["serial"].__setitem__(
            "baud_choices", list(c["serial"].get("baud_choices", [])) + [921600]),
        probe="(function(){var s=document.getElementById('baudSelect');"
              "return s?Array.prototype.map.call(s.options,function(o){return o.value;}).join(','):'';})()",
        expect=lambda g: ("921600" in str(g), "波特率下拉应出现 921600，实际 %r" % (g,)),
    ),
]


def _run_page(payloads):
    """payloads: List[str(JSON cfg)] -> 逐个 applyUiConfig 后返回 probe 结果列表。"""
    app = QApplication.instance() or QApplication(sys.argv)
    results = [None] * len(payloads)
    got_errs = {}

    view = QWebEngineView()
    view.resize(1440, 900)
    page = Probe(view)
    view.setPage(page)
    idx = {"i": 0}
    loop = {"done": False}

    def step():
        i = idx["i"]
        if i >= len(payloads):
            loop["done"] = True
            return
        cfg_json, expr = payloads[i]
        js = ("(function(){ try{ if(typeof applyUiConfig==='function'){"
              " applyUiConfig(%s); } return %s; }catch(e){ return 'JSERR:'+e.message; } })()"
              % (cfg_json, expr))
        page.runJavaScript(js, 0, lambda v, i=i: finish(v, i))

    def finish(val, i):
        results[i] = val
        idx["i"] = i + 1
        QTimer.singleShot(30, step)

    def on_load(ok):
        got_errs["loaded"] = ok
        page.runJavaScript(INSTALL_JS, 0)
        page.runJavaScript("JS_SIGS_READY", 0)
        QTimer.singleShot(700, step)

    page.loadFinished.connect(on_load)
    view.load(INDEX_URL)
    view.show()

    guard = {"n": 0}

    def pump():
        if loop["done"]:
            app.quit()
            return
        guard["n"] += 1
        if guard["n"] > 1200:
            loop["done"] = True
            app.quit()
            return
        QTimer.singleShot(50, pump)

    QTimer.singleShot(50, pump)
    app.exec()
    return results, got_errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(HERE, "tools", "_matrix_audit.json"))
    args = ap.parse_args()

    base = C.ui_config()
    payloads, metas = [], []
    for cs in CASES:
        cfg = copy.deepcopy(base)
        try:
            r = cs["mutate"](cfg)
            if r is not None:      # 允许 mutate 返回一个新对象
                cfg = r
        except Exception as e:
            cfg = base
            print("  [mutate 失败] %s -> %s" % (cs["desc"], e))
        payloads.append((json.dumps(cfg, ensure_ascii=False), cs["probe"]))
        metas.append(cs)

    results, info = _run_page(payloads)

    print("=" * 74)
    print("「改了配置，界面动不动」矩阵自检   (共 %d 项)" % len(CASES))
    print("=" * 74)
    if not info.get("loaded"):
        print("[FATAL] 页面没加载起来")

    rows, fails = [], []
    cur_section = None
    for i, cs in enumerate(metas):
        got = results[i] if i < len(results) else None
        try:
            ok, why = cs["expect"](got)
        except Exception as e:
            ok, why = False, "断言异常: %s" % e
        if isinstance(got, str) and got.startswith("JSERR:"):
            ok, why = False, got
        rows.append(dict(section=cs["section"], desc=cs["desc"], ok=ok,
                         got=got, why=why))
        if not ok:
            fails.append(rows[-1])
        if cs["section"] != cur_section:
            cur_section = cs["section"]
            print("\n【%s】" % cur_section)
        print("  [%s] %s" % ("OK  " if ok else "FAIL", cs["desc"]))
        if not ok:
            s = str(got)
            if len(s) > 130:
                s = s[:130] + "…"
            print("        实际: %s" % s)

    print()
    print("=" * 74)
    print("通过 %d / %d" % (len(rows) - len(fails), len(rows)))
    if fails:
        print("\n★ 以下配置项改了，界面不动（用户会直接感知）：")
        for f in fails:
            print("  · [%s] %s" % (f["section"], f["desc"]))
    else:
        print("全部配置项都能实时驱动界面。")
    print("=" * 74)

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print("明细已写入 %s" % args.json)

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
