"""离屏加载 telemetry_web/index.html，捕获 JS 运行时错误并核对 DOM 渲染结果。

用途：改完前端或 telemetry_config.py 后**必须**先跑这个再打包。
      node --check 只能查语法，查不出"变量未定义 / id 写错"这类运行时错误，
      而这些错误会让整个 app.js 中断、页面变成白板。

用法：
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\python.exe tools\\web_ui_selfcheck.py
    然后查看 tools/_dom_check.json

输出两段结果：
    dom  —— 页面基础渲染：errors 必须为空，adcCards=配置通道数，chips=注册信号数，revSegs=12，
            功能分区齐全，且不会自动进入演示模式（读数应为 "—" 占位）
            ★ chips 21->23：协议把 NAV 的加速度拆成 ACCEL_LAT/ACCEL_LON，前端另派生
              ACCEL_MAG（合成总加速度），三路都注册成独立信号 -> 21+3-1=23。
            ★ chips 23->24（2026-10-16）：用户新增派生量 POWER = 电压 × 电流 / 1000（kW），
              于是"前端派生量"从 1 个变 2 个 -> 10+5+7+2 = 24。
              ★ 这次把 chips 也改成**由配置推导**（原来是写死的 23），
                以后再加字段/派生量时这条断言不会再过期 —— 是收紧而不是放宽。
            ★ U08（2026-10-15）：新增 4 条断言，专门盯 #visChannels（可视化区「数据通道」）。
              adcCards 也从写死 10 改成 CFG.ADC_COUNT（同为严格比较，但不再随配置过期）。
    sync —— 配置同步链路：注入一份「多加一个 11 号通道」的配置，
            验证 applyUiConfig 能把通道名/卡片数/曲线开关全部跟着改对。
            这一段红了说明 telemetry_config.py 的改动没能传到界面。
"""
import os
import sys
import json

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录（tools 的上一级）
sys.path.insert(0, HERE)


class Probe(QWebEnginePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = []

    def javaScriptConsoleMessage(self, level, msg, line, source):
        self.console.append("[%s] %s (line %d)" % (level.name, msg, line))


PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  window.addEventListener('unhandledrejection', function (e) {
    window.__errs.push('promise: ' + e.reason);
  });
})();
"""

PHASE1_JS = """
JSON.stringify({
  errors: window.__errs || [],
  adcCards: document.getElementById('adcGrid') ? document.getElementById('adcGrid').children.length : -1,
  chips: document.getElementById('chipBar') ? document.getElementById('chipBar').querySelectorAll('.chip').length : -1,
  sigCount: Object.keys(SIGS).length,
  revSegs: document.getElementById('revRow') ? document.getElementById('revRow').children.length : -1,
  zones: ['zoneP2','zoneP1','zoneP0','zoneTrend','zoneSerial','zoneRecord','zoneSettings'].map(function(id){return !!document.getElementById(id);}),
  /* ★ task-28 新增：分区**不许嵌套**。ux 曾整段替换 DOM 时漏了 `</section>`，
     结果 6 个分区被嵌进 P2（P2 高 2525px、可视化区 0×0）。
     判据：每个分区的 parentElement 必须是 #stage（返回 id/class 便于定位问题）。 */
  zoneParents: ['zoneP2','zoneP1','zoneP0','zoneTrend','zoneSerial','zoneRecord','zoneSettings'].map(function(id){
    var e = document.getElementById(id);
    return e && e.parentElement ? (e.parentElement.id || e.parentElement.className) : null;
  }),
  demoOn: document.getElementById('demoMode') ? document.getElementById('demoMode').checked : null,
  liveText: document.getElementById('liveText') ? document.getElementById('liveText').textContent : '',
  speedText: document.getElementById('speedNum') ? document.getElementById('speedNum').textContent : '',
  rpmText: document.getElementById('rpmNum') ? document.getElementById('rpmNum').textContent : '',
  tempText: document.getElementById('tempNum') ? document.getElementById('tempNum').textContent : '',
  accText: document.getElementById('accNum') ? document.getElementById('accNum').textContent : '',
  clockText: document.getElementById('clock') ? document.getElementById('clock').textContent : '',
  sigRows: document.getElementById('sigPanel') ? document.getElementById('sigPanel').querySelectorAll('.sig-row').length : -1,
  chipsOn: document.getElementById('chipBar') ? document.getElementById('chipBar').querySelectorAll('.chip.on').length : -1,
  sigUnit0: (function(){
    var p = document.getElementById('sigPanel'); if (!p) return '';
    var u = p.querySelector('.sig-row .sig-un'); return u ? u.textContent : '';
  })(),
  hexScroll: (function(){
    var b = document.getElementById('hexBox'); if (!b) return '';
    return getComputedStyle(b).flexDirection;
  })(),
  /* 2026-10-06：量程编辑器整块移除后，改为断言「设置区已不含这些元素」，
     防止有人把 rngGrid / cfgOrigin 加回来时忘了同步别处。 */
  rngGone: !document.getElementById('rngGrid') && !document.getElementById('cfgOrigin'),
  /* ★ U08（2026-10-15 补）：可视化区左侧「数据通道」列表也要快照下来 ——
     以前这里**完全不看 #visChannels**，于是"改了第 3 区通道名但可视化区还是旧名字"
     这个 bug 长期没被自检抓到（需求3 的洞）。 */
  visItems: Array.prototype.map.call(
    document.querySelectorAll('#visChannels .vc-item'),
    function(el){ return {id: el.getAttribute('data-sig'), text: (el.textContent || '').trim()}; })
})
"""

PHASE2_JS = """
JSON.stringify({
  adcCards: document.getElementById('adcGrid') ? document.getElementById('adcGrid').children.length : -1,
  firstCardLabel: (function(){
    var c = document.getElementById('adcGrid');
    if (!c || !c.children.length) return '';
    return c.children[0].querySelector('.a-name').textContent;
  })(),
  lastCardLabel: (function(){
    var c = document.getElementById('adcGrid');
    if (!c || !c.children.length) return '';
    return c.children[c.children.length-1].querySelector('.a-name').textContent;
  })(),
  chips: document.getElementById('chipBar') ? document.getElementById('chipBar').querySelectorAll('.chip').length : -1,
  sigCount: Object.keys(SIGS).length,
  /* ★ U08：改配置后可视化区通道列表也必须跟着变（名字 + 顺序都看） */
  visItems: Array.prototype.map.call(
    document.querySelectorAll('#visChannels .vc-item'),
    function(el){ return {id: el.getAttribute('data-sig'), text: (el.textContent || '').trim()}; }),
  errors: window.__errs || []
})
"""


def build_probe_config():
    """构造一份「比当前配置多一个通道」的配置，验证界面能跟着扩。"""
    import telemetry_config as CFG
    cfg = CFG.ui_config()
    cfg["adc"].append(dict(short="CH%d" % (CFG.ADC_COUNT + 1), name="油温传感器",
                           sensor="温度", unit="°C", rng=150.0, color="#111827",
                           min=0, max=4095))
    cfg["csvHeader"] = CFG.CSV_HEADER + ",oil_temp"
    return cfg


def build_reorder_config():
    """把 ADC 通道列表整体逆序 —— 模拟第 3 区 ADC_ORDER 改成逆序后下发的样子。

    ★ 验证「界面卡片顺序也跟着 ADC_ORDER 变」：后端 ui_adc_config() 走
      adc_out_order()，所以列表反过来，界面的卡片就该跟着反 ——
      与 CSV 列顺序同源，不会出现「界面一套顺序、CSV 另一套」。
    """
    import telemetry_config as CFG
    cfg = CFG.ui_config()
    cfg["adc"] = list(reversed(cfg["adc"]))
    return cfg


def build_display_config():
    """在真实 ui_config 基础上改掉几个 DISPLAY_NAMES，验证显示名宏的全链路。"""
    import telemetry_config as CFG
    cfg = CFG.ui_config()
    names = dict(cfg.get("displayNames") or {})
    names.update(DISPLAY_TEST)
    cfg["displayNames"] = names
    return cfg


PHASE3_JS = """
JSON.stringify({
  firstCardLabel: (function(){
    var c = document.getElementById('adcGrid');
    if (!c || !c.children.length) return '';
    return c.children[0].querySelector('.a-name').textContent;
  })(),
  lastCardLabel: (function(){
    var c = document.getElementById('adcGrid');
    if (!c || !c.children.length) return '';
    return c.children[c.children.length-1].querySelector('.a-name').textContent;
  })(),
  /* ★ U08：ADC_ORDER 逆序后，可视化区通道列表的**顺序**也要跟着反 ——
     它和卡片、CSV 列同源（都走 adc_out_order），不能一套顺序界面另一套。 */
  visItems: Array.prototype.map.call(
    document.querySelectorAll('#visChannels .vc-item'),
    function(el){ return {id: el.getAttribute('data-sig'), text: (el.textContent || '').trim()}; }),
  errors: window.__errs || []
})
"""

# ★★ 2026-10-16 新增：界面显示名宏（配置第 ⑥ 区 DISPLAY_NAMES）的端到端检查。
#    背景：用户改 NAV 协议字段名导致 CSV/曲线/旧录播全断 —— 因为"协议字段名"与
#    "界面显示名"是两种东西。现在显示名单独一张宏表，改一处要全线生效。
#    这一幕把 DISPLAY_NAMES 里的名字换成探针值，检查四处显示面是否**全部**跟着变：
#    ① 总览卡片标题（带 data-lbl 的 HTML）② SIGS[id].label（趋势图例/数值面板/chip
#    都读它）③ 趋势 chip ④ 数值面板；同时确认协议侧（CSV/字段名）不被牵连。
DISPLAY_TEST = {"roll": "宏测·横滚", "speed": "宏测·车速", "temp": "宏测·温度",
                "accelLat": "宏测·横向", "rpm": "宏测·转速"}

PHASE4_JS = """
(function(){
  function T(sel){ var e=document.querySelector(sel); return e?(e.textContent||'').trim():null; }
  var chips=[]; var cs=document.querySelectorAll('#chipBar .chip');
  for (var i=0;i<cs.length;i++) chips.push((cs[i].textContent||'').trim());
  var panel=[]; var se=document.querySelectorAll('#sigPanel .sig-row');
  for (var j=0;j<se.length;j++) panel.push((se[j].textContent||'').trim());
  var pn = document.getElementById('sigPanel');
  return JSON.stringify({
    card_roll: T('[data-lbl="roll"]'),
    card_speed: T('[data-lbl="speed"]'),
    card_temp: T('[data-lbl="temp"]'),
    sig_roll: (typeof SIGS!=='undefined' && SIGS.roll) ? SIGS.roll.label : null,
    sig_speed: (typeof SIGS!=='undefined' && SIGS.speed) ? SIGS.speed.label : null,
    chips: chips,
    panel: panel,
    panelHTML: pn ? (pn.textContent||'').replace(/\s+/g,' ').trim().slice(0,600) : '',
    errs: window.__errs || []
  });
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = Probe(view)
    view.setPage(page)

    s = QWebEngineScript()
    s.setName("errprobe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    result = {}
    probe_cfg = build_probe_config()
    apply_js = ("(function(){ try { applyUiConfig(%s); } "
                "catch(e){ window.__errs.push('applyUiConfig: ' + e.message); } })()"
                % json.dumps(probe_cfg, ensure_ascii=False))

    # 第三幕：把通道顺序整体逆序，验证界面卡片顺序跟着 ADC_ORDER 变
    reorder_cfg = build_reorder_config()
    reorder_js = ("(function(){ try { applyUiConfig(%s); } "
                  "catch(e){ window.__errs.push('applyUiConfig: ' + e.message); } })()"
                  % json.dumps(reorder_cfg, ensure_ascii=False))

    def on_load(ok):
        def phase1(v):
            result["dom"] = v
            QTimer.singleShot(300, lambda: page.runJavaScript(apply_js))
            QTimer.singleShot(1200, lambda: page.runJavaScript(PHASE2_JS, phase2))

        def phase2(v):
            result["sync"] = v
            QTimer.singleShot(300, lambda: page.runJavaScript(reorder_js))
            QTimer.singleShot(1200, lambda: page.runJavaScript(PHASE3_JS, phase3))

        def phase3(v):
            result["reorder"] = v
            # 第四幕：套用一份改过 DISPLAY_NAMES 的配置，验证显示名宏全线生效
            dcfg = build_display_config()
            djs = ("(function(){ try { applyUiConfig(%s); } "
                   "catch(e){ window.__errs.push('applyUiConfig/display: ' + e.message); } })()"
                   % json.dumps(dcfg, ensure_ascii=False))
            QTimer.singleShot(300, lambda: page.runJavaScript(djs))
            QTimer.singleShot(1300, lambda: page.runJavaScript(PHASE4_JS, phase4))

        def phase4(v):
            result["display"] = v
            app.quit()

        # 3.5s：无自动演示，等页面初始化 + 主循环稳定即可（读数应为 "—"）
        QTimer.singleShot(3500, lambda: page.runJavaScript(PHASE1_JS, phase1))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))

    QTimer.singleShot(30000, app.quit)
    app.exec()

    out = {
        "dom": json.loads(result.get("dom", "{}")),
        "sync": json.loads(result.get("sync", "{}")),
        "reorder": json.loads(result.get("reorder", "{}")),
        "display": json.loads(result.get("display", "{}")),
        "console": page.console,
    }
    # 控制台级判定，方便直接看红绿
    d, sy, ro = out["dom"], out["sync"], out["reorder"]
    dp = out["display"]
    n = len(probe_cfg["adc"])
    # ★ 信号总数由配置推导（ADC 通道 + NAV 字段 + MOTOR 字段 + 2 个前端派生量：
    #   accel_mag 合成加速度 / power 电池功率），这样以后再加字段/再拆协议/再加派生量，
    #   这条断言会立刻发现"界面注册的信号数没跟上"。
    import telemetry_config as CFG
    expect_sigs = (CFG.ADC_COUNT + len(CFG.PROTOCOL[0x01]["fields"])
                   + len(CFG.PROTOCOL[0x02]["fields"]) + 2)
    # U08：可视化区通道列表（#visChannels）的 ADC 段顺序，应等于当前生效配置的 ADC 顺序
    def vis_adc_texts(items):
        return [it["text"] for it in items if str(it.get("id", "")).startswith("adc")]

    def vis_adc_names(items):
        # 标签格式 = "CHnn 通道名"（short + ' ' + name），只比通道名部分，与 CH 编号无关
        return [t.split(" ", 1)[1] if " " in t else t for t in vis_adc_texts(items)]

    expect_reorder_names = [c["name"] for c in reorder_cfg["adc"]]
    # 加通道后 #visChannels 的总条目数 = ADC(n) + NAV(5) + MOTOR(7) + 派生(2)
    expect_sync_total = (n + len(CFG.PROTOCOL[0x01]["fields"])
                         + len(CFG.PROTOCOL[0x02]["fields"]) + 2)
    checks = [
        ("页面无 JS 运行时错误", (not d.get("errors")) and (not sy.get("errors"))),
        ("ADC 卡片 10 张", d.get("adcCards") == CFG.ADC_COUNT),
        ("趋势通道数 = 注册信号数（%d 个）" % expect_sigs, d.get("chips") == expect_sigs),
        ("换挡灯 12 段", d.get("revSegs") == 12),
        ("7 个分区齐全", all(d.get("zones", []))),
        # ★ task-28 新增：7 个分区必须**全部直属 #stage**（不许互相嵌套）
        #   历史坑：整段替换 DOM 时漏 `</section>` -> 6 个分区被嵌进 P2（P2 高 2525px、可视化区 0×0）
        ("7 个分区都直属 #stage（无嵌套）",
         (d.get("zoneParents") or []) == ["stage"] * 7),
        ("未自动进入演示模式", d.get("demoOn") is False),
        ("趋势数值面板行数=勾选通道数", d.get("sigRows") == d.get("chipsOn") and d.get("sigRows", 0) > 0),
        ("趋势面板首行带单位", d.get("sigUnit0") != ''),
        ("HEX 容器不是 column-reverse", d.get("hexScroll") != 'column-reverse'),
        ("量程编辑器已移除（rngGrid/cfgOrigin 都不存在）", d.get("rngGone") is True),
        ("配置加通道后卡片变 %d 张" % n, sy.get("adcCards") == n),
        ("新通道名已生效", sy.get("lastCardLabel") == "油温传感器"),
        ("通道同步无残留错误", not sy.get("errors")),
        # ★ 改 ADC_ORDER（逆序）后，界面卡片顺序必须与 CSV 列顺序同源地跟着变
        ("改 ADC_ORDER 逆序后首张卡片 = %s" % (reorder_cfg["adc"][0]["name"] or "?"),
         ro.get("firstCardLabel") == reorder_cfg["adc"][0]["name"]),
        ("改 ADC_ORDER 逆序后末张卡片 = %s" % (reorder_cfg["adc"][-1]["name"] or "?"),
         ro.get("lastCardLabel") == reorder_cfg["adc"][-1]["name"]),
        ("顺序切换无 JS 错误", not ro.get("errors")),
        # ★★ U08（新增，2026-10-15）：可视化区「数据通道」列表必须跟着 uiConfig 变。
        #    以前这个脚本完全不看 #visChannels —— 需求3 那个「改了通道名可视化区还是旧名字」
        #    的 bug 因此长期没被自检抓到。下面两条都不放宽既有断言，只补漏洞。
        ("[U08] 配置加通道后 #visChannels 条目数跟着变（%d 路 = ADC %d + NAV %d + MOTOR %d + 派生 2）"
         % (expect_sync_total, n, len(CFG.PROTOCOL[0x01]["fields"]), len(CFG.PROTOCOL[0x02]["fields"])),
         len(sy.get("visItems") or []) == expect_sync_total),
        ("[U08] 配置改名后 #visChannels 显示新通道名「油温传感器」",
         any("油温传感器" in it.get("text", "") for it in (sy.get("visItems") or []))),
        ("[U08] 改 ADC_ORDER 逆序后 #visChannels 的 ADC 顺序与配置一致",
         vis_adc_names(ro.get("visItems") or []) == expect_reorder_names),
        ("[U08] 信号注册总数 = ADC %d + NAV %d + MOTOR %d + 派生 2 = %d"
         % (CFG.ADC_COUNT, len(CFG.PROTOCOL[0x01]["fields"]), len(CFG.PROTOCOL[0x02]["fields"]),
            expect_sigs),
         d.get("sigCount") == expect_sigs),
        # ★★ 2026-10-16 新增：界面显示名宏（DISPLAY_NAMES）必须四处齐变、且不污染协议。
        #    这几条专门防"改了显示名，只有卡片变了、趋势图例/数值面板还是旧名字"这类漏网。
        ("[显示名宏] 总览卡片标题跟着 DISPLAY_NAMES 变（roll/speed/temp 3 项）",
         dp.get("card_roll") == DISPLAY_TEST["roll"]
         and dp.get("card_speed") == DISPLAY_TEST["speed"]
         and dp.get("card_temp") == DISPLAY_TEST["temp"]),
        ("[显示名宏] 信号注册表 SIGS[id].label 跟着变（趋势图例/数值面板读它）",
         dp.get("sig_roll") == DISPLAY_TEST["roll"]
         and dp.get("sig_speed") == DISPLAY_TEST["speed"]),
        ("[显示名宏] 趋势通道 chip 文字跟着变",
         DISPLAY_TEST["roll"] in (dp.get("chips") or [])
         and DISPLAY_TEST["speed"] in (dp.get("chips") or [])),
        #    ★ 数值面板只列「当前勾选」的通道（state.visible）→ 测试值必须包含
        #      默认勾选里的信号（accelLat / rpm），否则面板里根本不会出现它。
        ("[显示名宏] 数值面板行文字跟着变（勾选中的 accelLat/rpm）",
         any(DISPLAY_TEST["accelLat"] in (t or "") or DISPLAY_TEST["rpm"] in (t or "")
             for t in (dp.get("panel") or []))
         or (DISPLAY_TEST["accelLat"] in (dp.get("panelHTML") or "")
             or DISPLAY_TEST["rpm"] in (dp.get("panelHTML") or ""))),
        ("[显示名宏] 套用显示名过程无 JS 错误", not dp.get("errs")),
        # 协议侧不能被显示名污染：CSV 表头 / 协议字段名仍必须是英文内部键
        ("[显示名宏] 协议字段名仍是英文内部键（CSV/旧录播兼容不受牵连）",
         all(f[0].isascii() for tid in (0x01, 0x02) for f in CFG.PROTOCOL[tid]["fields"])),
    ]
    out["checks"] = [{"name": k, "ok": bool(v)} for k, v in checks]

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dom_check.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("WROTE _dom_check.json")
    except Exception as e:
        # ★ 2026-10-16：落盘失败**不能**把自检结果一起吞掉。
        #   实测环境（工作区被判只读/WinError 5）下 open(...,'w') 直接 PermissionError，
        #   而这段落盘在 print 之前 -> 整个自检明明跑完了却一条结果都看不到，
        #   上层（build_web 步骤 4）还会把它当成自检失败。诊断文件是附属品，结果才是主线。
        print("WARN 结果 JSON 落盘失败（不影响判定）: %s" % e)
    for c in out["checks"]:
        print(("  OK   " if c["ok"] else "  FAIL ") + c["name"])
    bad = [c["name"] for c in out["checks"] if not c["ok"]]
    print("结果:", "全部通过" if not bad else "存在失败 -> " + "; ".join(bad))


if __name__ == "__main__":
    main()
