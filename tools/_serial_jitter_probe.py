# -*- coding: utf-8 -*-
"""task-8 问题三探针：喂 RAW 数据时「串口助手」区到底哪个元素在动。

★ 方法论修正（第一版探针踩过的坑）：不能每喂一行就手动 flushHex()，
  那样 hexBuf 每次只有 1 行，<pre> 永远只有 1 行高，测不出真实行为。
  真实链路是：onRaw() 在 20~100Hz 被调用，flushHex() 由 **200ms 定时器**触发，
  每次 flush 写入"这 200ms 内累积的行"（hexBuf 每次 flush 后被清空，
  所以 <pre> 是**滚动窗口**而不是无限增长的历史）。

因此本探针按真实节拍走：每轮喂 N 行 -> 等 300ms 让定时器自己 flush -> 量几何。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_serial_jitter_probe.py [--lines 25] [--rounds 12]
"""
import os
import sys
import json

# ★ 控制台编码自保护（task-8 F8）：Windows GBK 控制台下，print 里出现 ⏸/▶/★ 这类字符会
# 抛 UnicodeEncodeError 直接崩进程；而且崩在打印阶段会让“后面才写 JSON”的取证代码根本执行不到。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

LINES_PER_ROUND = 25
ROUNDS = 12
# ★ 每轮喂的行数**故意抖动**（1→55），才能暴露"高度随内容量上下摆动"这种真·跳动；
#   恒定行数只能测出一次性的增长。
CYCLE = [2, 40, 4, 55, 1, 30, 8, 50, 3, 25, 12, 45]

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
})();
"""

MEASURE_JS = """
(function () {
  function r(sel){
    var el = document.querySelector(sel);
    if (!el) return null;
    var b = el.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)];
  }
  var hb = document.getElementById('hexBox');
  var stage = document.getElementById('stage');
  var foot = document.getElementById('footer');
  var out = {
    zoneSerial: r('#zoneSerial'),
    hexBox: r('#hexBox'),
    hexPre: r('#hexBox .hexpre'),
    ctlRow: r('#zoneSerial .ctl-row'),
    zoneHead: r('#zoneSerial .zone-head'),
    zoneRecord: r('#zoneRecord'),
    zoneTrend: r('#zoneTrend'),
    zoneP0: r('#zoneP0'),
    footer: r('#footer'),
    bodyH: Math.round(document.body.getBoundingClientRect().height),
    stageScrollH: stage ? stage.scrollHeight : null,
    stageClientH: stage ? stage.clientHeight : null,
    hexScrollH: hb ? hb.scrollHeight : null,
    hexClientH: hb ? hb.clientHeight : null,
    hexScrollW: hb ? hb.scrollWidth : null,
    hexClientW: hb ? hb.clientWidth : null,
    preLines: (function(){ var p = document.querySelector('#hexBox .hexpre');
                           return p ? (p.textContent ? p.textContent.split('\\n').length : 0) : -1; })(),
    footChildren: [],
    topChildren: [],
    topDiag: [],
    rxPill: (function(){ var el = document.getElementById('rxPill'); if (!el) return null;
                         var b = el.getBoundingClientRect();
                         return [Math.round(b.left), Math.round(b.width)]; })()
  };
  if (foot) Array.prototype.forEach.call(foot.children, function (el) {
    var b = el.getBoundingClientRect();
    out.footChildren.push([Math.round(b.left), Math.round(b.width)]);
  });
  var topbar = document.getElementById('topbar');
  if (topbar) Array.prototype.forEach.call(topbar.children, function (el) {
    var b = el.getBoundingClientRect();
    out.topChildren.push([Math.round(b.left), Math.round(b.width)]);
    var cs = getComputedStyle(el);
    out.topDiag.push({k: (el.id || el.className || el.tagName), left: Math.round(b.left), w: Math.round(b.width),
                      ml: cs.marginLeft, fg: cs.flexGrow, fs: cs.flexShrink, fb: cs.flexBasis});
  });
  return JSON.stringify(out);
})()
"""


def feed_js(nlines, seed=0):
    """构造一次 onRaw 调用：一次喂 nlines 行（模拟 200ms 内累积的帧）。"""
    lines = "\n".join(
        "AA 55 %02X %02X 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F %02X" % (i & 0xFF, (i * 7) & 0xFF, seed & 0xFF)
        for i in range(nlines))
    return "onRaw(%s); 'fed'" % json.dumps(lines)


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)

    s = QWebEngineScript()
    s.setName("errprobe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    res = {"series": []}

    setup_js = ("(function(){ setLive('live','COM3'); "
                "setNum(document.getElementById('rxTag'),'0'); "
                "setNum(document.getElementById('rxFoot'),'0'); return 'setup'; })()")

    def measure(tag, then):
        def cb(v):
            d = json.loads(v)
            d["tag"] = tag
            res["series"].append(d)
            then()
        page.runJavaScript(MEASURE_JS, cb)

    def on_load(ok):
        def run_resize():
            """阶段②：用户拖动功能区手柄把 #zoneSerial 设成固定高度后，
               hexbox 应该填满剩余空间并内部滚动（B07 的原始意图），而不是继续被内容撑大。"""
            def after_resize():
                measure("resize-500px", lambda: page.runJavaScript(
                    "(function(){ document.getElementById('zoneSerial').style.height=''; return 'ok'; })()",
                    lambda __: measure("resize-还原", app.quit)))
            page.runJavaScript(
                "(function(){ document.getElementById('zoneSerial').style.height='500px'; return 'ok'; })()",
                lambda _: QTimer.singleShot(200, after_resize))

        def restore_topbar_and_resize():
            page.runJavaScript(
                "(function(){ setNum(document.getElementById('liveText'),'未连接');"
                "setNum(document.getElementById('rxTag'),'0'); return 'ok'; })()",
                lambda _: run_resize())

        def after_C2():
            # D：liveText 文案变宽（"未连接"->"演示模式"）——评估要不要也一起锁。
            # ★ 先把 rxTag 复位回 0，保证 D 只改 liveText 这一个变量。
            page.runJavaScript(
                "(function(){ setNum(document.getElementById('rxTag'),'0');"
                "setNum(document.getElementById('liveText'),'演示模式'); return 'ok'; })()",
                lambda _2: measure("D-顶栏liveText演示模式", restore_topbar_and_resize))

        def after_C():
            # C2：顶栏极端值（6 位）—— 参考项，用来展示"按现实取值预留"的边界
            page.runJavaScript(
                "(function(){ setNum(document.getElementById('rxTag'),'123456'); return 'ok'; })()",
                lambda _2: measure("C2-顶栏极端值123456", after_C2))

        def after_B2():
            # C：顶栏 #rxTag 0 -> 9999（现实上限；F4 的验证点，保持单变量）
            page.runJavaScript(
                "(function(){ setNum(document.getElementById('rxFoot'),'0');"
                "setNum(document.getElementById('rxTag'),'9999'); return 'ok'; })()",
                lambda _2: measure("C-顶栏rxTag9999", after_C))

        def after_B():
            # B2：底栏极端值（6 位）—— 参考项
            page.runJavaScript(
                "(function(){ setNum(document.getElementById('rxFoot'),'123456'); return 'ok'; })()",
                lambda _2: measure("B2-底栏极端值123456", after_B2))

        def run_round(i):
            if i > ROUNDS:
                # B：底栏**现实上限**（fps 999 / rxFoot 9999，与 F6 的 3ch/4ch 预留一致）；
                # 随后 B2/C/C2 是极端值参考与顶栏验证
                page.runJavaScript(
                    "(function(){ setNum(document.getElementById('fpsTag'),'999');"
                    "setNum(document.getElementById('rxFoot'),'9999'); return 'ok'; })()",
                    lambda _: measure("B-底栏现实上限(fps999/rx9999)", after_B))
                return
            n = CYCLE[(i - 1) % len(CYCLE)]
            page.runJavaScript(feed_js(n, i), lambda _: QTimer.singleShot(
                320, lambda: measure("round%d(%d行)" % (i, n), lambda: run_round(i + 1))))

        def after_setup(_):
            measure("0-基线", lambda: run_round(1))
        page.runJavaScript(setup_js, after_setup)

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    allseries = res["series"]
    # 稳定性断言只看"喂数据"阶段；resize 阶段是**故意**改高度，单独报告
    series = [s for s in allseries if s["tag"].startswith("0-") or s["tag"].startswith("round")]
    resize = [s for s in allseries if s["tag"].startswith("resize")]
    watch = ["zoneSerial", "hexBox", "hexPre", "ctlRow", "zoneHead", "zoneRecord",
             "zoneTrend", "zoneP0", "footer", "bodyH", "stageScrollH", "stageClientH"]
    print("=== 喂数据阶段：每轮行数按 %s 抖动 + 等 300ms 让 200ms 定时器 flush ===" % CYCLE)
    print("%-16s %-16s %-16s %-8s %-8s %s" % ("轮次", "hexBox(x,y,w,h)", "pre(x,y,w,h)", "pre行数", "scrollH", "zoneSerial(h)"))
    for st in series:
        print("%-16s %-16s %-16s %-8s %-8s %s" % (
            st["tag"], st.get("hexBox"), st.get("hexPre"), st.get("preLines"),
            st.get("hexScrollH"), (st.get("zoneSerial") or [None]*4)[3]))

    print()
    print("=== 各字段在喂数据阶段出现过几个不同值 ===")
    varying = []
    for f in watch:
        vals = []
        for st in series:
            v = st.get(f)
            v = tuple(v) if isinstance(v, list) else v
            if v not in vals:
                vals.append(v)
        if len(vals) > 1:
            varying.append(f)
        print("  %-14s 不同值 %d 个  %s" % (f, len(vals), vals if len(vals) <= 6 else "(略)"))

    # 底栏子元素是否横向抖动（rxFoot 数字位宽变化）
    footvals = []
    for st in series:
        v = tuple(tuple(x) for x in st.get("footChildren", []))
        if v not in footvals:
            footvals.append(v)
    print("  %-14s 不同值 %d 个" % ("底栏子元素", len(footvals)))

    print()
    print("hexBox 高度变化范围: %s ... %s（clientHeight %s ... %s, scrollHeight %s ... %s）"
          % (min(s["hexBox"][3] for s in series), max(s["hexBox"][3] for s in series),
             min(s["hexClientH"] for s in series), max(s["hexClientH"] for s in series),
             min(s["hexScrollH"] for s in series), max(s["hexScrollH"] for s in series)))
    print("zoneSerial 高度变化范围: %s ... %s"
          % (min(s["zoneSerial"][3] for s in series), max(s["zoneSerial"][3] for s in series)))
    if len(footvals) > 1:
        print("底栏子元素前后对比（[left,width] 序列）:")
        print("   首次:", footvals[0])
        print("   末次:", footvals[-1])
    print()
    print("=== 区域被用户拉高到 500px 后（B07 原始意图：hexbox 填满并内部滚动）===")
    for st in resize:
        print("  %-14s zoneSerial=%s hexBox=%s clientH=%s scrollH=%s"
              % (st["tag"], st.get("zoneSerial"), st.get("hexBox"),
                 st.get("hexClientH"), st.get("hexScrollH")))
    print("JS 错误:", res.get("errors"))

    # ---- F2 专项：底栏计数位宽变化不得推动任何其它底栏项（含回放进度条）----
    # ★ F6 后只保证**现实取值**（fps<=999 / rx<=9999）零位移；6 位极端值只作参考。
    def pick(prefix):
        got = [s for s in allseries if s["tag"].startswith(prefix)]
        return got[-1] if got else {}

    base_foot = series[0].get("footChildren")
    base_top = series[0].get("topChildren")
    b_real = pick("B-底栏现实上限")
    b_ext = pick("B2-底栏极端值")
    c_real = pick("C-顶栏rxTag9999")
    c_ext = pick("C2-顶栏极端值")
    top_live = pick("D-顶栏liveText")
    after_foot = b_real.get("footChildren")
    after_top = c_real.get("topChildren")
    foot_moved = None
    if base_foot and after_foot and len(base_foot) == len(after_foot):
        foot_moved = [i for i in range(len(base_foot)) if base_foot[i] != after_foot[i]]

    def moved_idx(a, b):
        if not a or not b or len(a) != len(b):
            return None
        return [i for i in range(len(a)) if a[i] != b[i]]

    foot_moved_ext = moved_idx(base_foot, b_ext.get("footChildren"))
    print()
    print("=== F2 专项：底栏计数位宽（现实上限 fps 0->999 / rxFoot 0->9999）===")
    print("  基线底栏子元素 [left,width]:", base_foot)
    print("  变后底栏子元素 [left,width]:", after_foot)
    print("  底栏有位移的子元素序号:", foot_moved if foot_moved is not None else "(长度不一致)")
    print("  参考·极端值 rxFoot=123456 时有位移的序号: %s" % foot_moved_ext)
    print("  顶栏有位移的子元素 [left,width] 基线/变后: %s / %s" % (base_top, after_top))

    # ---- F4 专项：顶栏 #rxTag 0 -> 9999 不得推动任何其它顶栏项 ----
    top_rx_children = c_real.get("topChildren")
    top_live_children = top_live.get("topChildren")
    top_moved = moved_idx(base_top, top_rx_children)
    top_moved_ext = moved_idx(base_top, c_ext.get("topChildren"))
    live_moved = moved_idx(base_top, top_live_children)
    print()
    print("=== F4 专项：顶栏 #rxTag 0 -> 9999（现实上限）===")
    print("  基线顶栏子元素 [left,width]:", base_top)
    print("  变后顶栏子元素 [left,width]:", top_rx_children)
    print("  顶栏有位移的子元素序号:", top_moved if top_moved is not None else "(长度不一致)")
    print("  参考·极端值 123456 时有位移的序号: %s" % top_moved_ext)
    print("  rxPill: %s -> %s" % (series[0].get("rxPill"), c_real.get("rxPill")))
    print("  各顶栏子元素的 flex/margin（诊断为什么有的项会动）:")
    for d in (series[0].get("topDiag") or []):
        print("     %-18s left=%-5s w=%-4s ml=%-6s grow=%-3s shrink=%-3s basis=%s"
              % (d["k"], d["left"], d["w"], d["ml"], d["fg"], d["fs"], d["fb"]))
    print()
    print("=== 附：顶栏 liveText 文案变宽（\"未连接\"->\"演示模式\"）===")
    print("  变后顶栏子元素 [left,width]:", top_live_children)
    print("  顶栏有位移的子元素序号:", live_moved if live_moved is not None else "(长度不一致)")

    bad = varying
    print("---")
    ok = True
    for name, good in [
        ("喂数据期间 #zoneSerial 尺寸/位置不变", "zoneSerial" not in bad),
        ("#hexBox 尺寸/位置不变（内容只滚动、不撑高）", "hexBox" not in bad),
        ("串口区 .ctl-row / .zone-head 不变", "ctlRow" not in bad and "zoneHead" not in bad),
        ("相邻 #zoneRecord 不变（共用网格行）", "zoneRecord" not in bad),
        ("同网格行其它 zone 不变", "zoneTrend" not in bad and "zoneP0" not in bad),
        ("底栏矩形不变", "footer" not in bad),
        ("底栏子元素不横向位移（rxFoot 位宽）", len(footvals) <= 1),
        # ---- F2：现实取值下的计数位宽变化不得推动其它底栏项（含用户正在拖的进度条）----
        ("F2 底栏 fps 0->999 / rxFoot 0->9999：其它底栏项零位移", foot_moved == []),
        ("F2 底栏子元素数量不变", bool(base_foot) and len(base_foot) == len(after_foot or [])),
        # ---- F4：顶栏 #rxTag 位宽变化不得推动其它顶栏项 ----
        ("F4 顶栏 #rxTag 0->9999：所有既有顶栏子元素零位移", top_moved == []),
        ("F4 顶栏子元素数量不变",
         bool(base_top) and len(base_top) == len(top_rx_children or [])),
        ("bodyH 不变", "bodyH" not in bad),
    ]:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and good
    print("结果:", "全部通过（喂数据期间串口区零跳动）" if ok else "存在跳动 -> " + "; ".join(bad))
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须退非零


if __name__ == "__main__":
    main()
