# -*- coding: utf-8 -*-
"""task-8 问题三：在运行时逐个注入候选 CSS 作对照，看哪个能治住 hexbox 抖动，
且不破坏「拖动功能区手柄后 HEX 区跟着填满」（B07）的行为。

★ 语义说明（避免误导）：
  第 0 项是**当前文件实际值**（不注入任何 CSS）；第 1 项是**改前基线**
  （显式 `max-height:none !important`，把修复效果还原掉，用来演示 bug 本身）。
  所以无论修复是否已落地，这张表都可读：
    · 第 1 项应当"喂数据不稳定" —— 那才是修复前的状态；
    · 第 0 项（当前文件实际值）应当稳定；
    · 第 3 项是最终采用的方案（稳定 + 保住 B07）。
  不会出现"现状也稳定、于是看不出所以然"的误导。

每个候选：重新加载页面 -> 注入 <style> -> 按抖动行数喂数据 -> 量 hexbox/zoneSerial 高度范围
-> 再把 #zoneSerial 高度设成 500px，量 hexbox 是否填满并内部滚动。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_hexbox_fix_candidates.py
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

CYCLE = [2, 40, 4, 55, 1, 30, 8, 50, 3, 25]
ROUNDS = len(CYCLE)

CANDIDATES = [
    ("0 当前文件实际值（不注入任何 CSS）", ""),
    ("1 改前基线（显式解除 max-height，还原修复前行为）", ".hexbox{max-height:none !important}"),
    ("2 max-height:120px（只看上界，会破坏 B07）", ".hexbox{max-height:120px !important}"),
    ("3 max-height:120px + 手动拉高时解除上限（★最终采用）",
     ".hexbox{max-height:120px !important}"
     "#zoneSerial[style*=\"height\"] .hexbox{max-height:none !important}"),
    ("4 height:0;min-height:0;flex:1 1 0（默认态会塌成 18px）",
     ".hexbox{height:0 !important;min-height:0 !important;flex:1 1 0 !important}"),
]

# 每个候选的预期（用于把"预期对照"printed 出来，避免读者误判）
EXPECT = {
    "0 当前文件实际值（不注入任何 CSS）": "稳定 + 保住B07",
    "1 改前基线（显式解除 max-height，还原修复前行为）": "应当【抖动】（这就是 bug 本身）",
    "2 max-height:120px（只看上界，会破坏 B07）": "稳定但 B07 回归",
    "3 max-height:120px + 手动拉高时解除上限（★最终采用）": "稳定 + 保住B07",
    "4 height:0;min-height:0;flex:1 1 0（默认态会塌成 18px）": "稳定 + B07，但默认高度塌陷",
}

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
  var hb = document.getElementById('hexBox');
  var zs = document.getElementById('zoneSerial');
  var p = document.querySelector('#hexBox .hexpre');
  return JSON.stringify({
    hexH: Math.round(hb.getBoundingClientRect().height),
    hexClientH: hb.clientHeight,
    hexScrollH: hb.scrollHeight,
    zoneH: Math.round(zs.getBoundingClientRect().height),
    preLines: p ? (p.textContent ? p.textContent.split('\\n').length : 0) : -1,
    zh: zs.style.height || ''
  });
})()
"""


def feed_js(n, seed):
    lines = "\n".join("AA 55 %02X %02X 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F" % (i & 0xFF, (i * 7) & 0xFF)
                      for i in range(n))
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

    results = {}
    state = {"ci": 0, "round": 0, "samples": [], "resize": None}

    def measure(cb):
        page.runJavaScript(MEASURE_JS, lambda v: cb(json.loads(v)))

    def start_candidate():
        if state["ci"] >= len(CANDIDATES):
            app.quit()
            return
        name, css = CANDIDATES[state["ci"]]
        state["samples"] = []
        state["resize"] = None
        state["round"] = 0
        state["cur_name"] = name

        def after_inject(_):
            def run_round():
                if state["round"] >= ROUNDS:
                    # resize 阶段：把 zone 高度定死 500px，看 hexbox 能否填满并内部滚动
                    def after_resize(_):
                        measure(finish_candidate)
                    page.runJavaScript(
                        "(function(){ document.getElementById('zoneSerial').style.height='500px'; return 'ok'; })()",
                        lambda _: QTimer.singleShot(200, lambda: after_resize(None)))
                    return
                n = CYCLE[state["round"]]
                state["round"] += 1
                page.runJavaScript(feed_js(n, state["round"]), lambda _: QTimer.singleShot(330, lambda: measure(after_round)))
            def after_round(m):
                state["samples"].append(m)
                run_round()
            run_round()

        def finish_candidate(m):
            state["resize"] = m
            results[state["cur_name"]] = {"samples": state["samples"], "resize": m}
            state["ci"] += 1
            QTimer.singleShot(150, lambda: page.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html"))))

        page.runJavaScript(
            "(function(){ var st=document.createElement('style'); st.id='cand'; st.textContent=%s;"
            "document.head.appendChild(st); return 'ok'; })()" % json.dumps(css),
            after_inject)

    def on_load(ok):
        QTimer.singleShot(3000, start_candidate)

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(180000, app.quit)
    app.exec()

    print("=== 候选对比（每轮行数抖动 %s）===" % CYCLE)
    for name, _ in CANDIDATES:
        r = results.get(name)
        if not r:
            print("  %-52s 未采集" % name)
            continue
        hs = [s["hexH"] for s in r["samples"]]
        zs = [s["zoneH"] for s in r["samples"]]
        rv = r["resize"]
        print("  %s" % name)
        print("     hexBox 高度范围 %s ... %s（振幅 %s）" % (min(hs), max(hs), max(hs) - min(hs)))
        print("     zoneSerial 高度范围 %s ... %s" % (min(zs), max(zs)))
        print("     手动拉高到 500px 后: hexBox=%s clientH=%s scrollH=%s  -> %s"
              % (rv["hexH"], rv["hexClientH"], rv["hexScrollH"],
                 "填满并内部滚动" if rv["hexH"] > 200 and rv["hexClientH"] < rv["hexScrollH"] else "没有填满/没内部滚动"))
    print()
    print("=== 判定（附每项预期，便于对照）===")
    for name, _ in CANDIDATES:
        r = results.get(name)
        if not r:
            continue
        hs = [s["hexH"] for s in r["samples"]]
        zs = [s["zoneH"] for s in r["samples"]]
        rv = r["resize"]
        stable = (max(hs) - min(hs)) == 0 and (max(zs) - min(zs)) == 0
        keeps_b07 = rv["hexH"] > 200 and rv["hexClientH"] < rv["hexScrollH"]
        print("  %-52s 喂数据稳定=%-5s 保留B07=%-5s 最小hexH=%-5s"
              % (name, stable, keeps_b07, min(hs)))
        print("      预期: %s" % EXPECT.get(name, "-"))
    print()
    print("说明：第 1 项是**显式还原修复前行为**的对照，它抖动才是正常的；")
    print("      第 0 项是当前文件实际值，应当稳定。")


if __name__ == "__main__":
    main()
