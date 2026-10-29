# -*- coding: utf-8 -*-
"""task-7 探针：①底栏几何（含"新增控件零位移"的 A/B 证据）②回放暂停 UI 功能。

设计成**可以改前/改后各跑一次**：几何部分把底栏每个子元素、底栏/页面/记录区/串口区
的 getBoundingClientRect 全部 dump 出来，改前跑一次存基线、改后跑一次做 diff。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_footer_replay_probe.py [输出json路径]
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

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
})();
"""

GEO_JS = """
(function () {
  function rect(el){
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
  }
  function tagOf(el){
    var id = el.id ? ('#' + el.id) : '';
    var cls = (el.className && typeof el.className === 'string') ? ('.' + el.className.trim().split(/\\s+/).join('.')) : '';
    var tx = (el.textContent || '').trim().slice(0, 14);
    return (id || cls || el.tagName) + '「' + tx + '」';
  }
  function collect(){
    var foot = document.getElementById('footer');
    var o = {
      footer: rect(foot),
      children: [],
      zoneRecord: rect(document.getElementById('zoneRecord')),
      zoneSerial: rect(document.getElementById('zoneSerial')),
      bodyH: Math.round(document.body.getBoundingClientRect().height),
      docH: document.documentElement.scrollHeight,
      stageH: Math.round((document.getElementById('stage') || document.body).getBoundingClientRect().height)
    };
    if (foot) Array.prototype.forEach.call(foot.children, function (el) {
      var r = rect(el);
      o.children.push({tag: tagOf(el), x: r.x, y: r.y, w: r.w, h: r.h});
    });
    return o;
  }
  var foot = document.getElementById('footer');
  var out = {
    footer: rect(foot),
    footerStyle: null,
    footerChildren: [],
    bodyH: Math.round(document.body.getBoundingClientRect().height),
    docH: document.documentElement.scrollHeight,
    stageH: Math.round((document.getElementById('stage') || document.body).getBoundingClientRect().height),
    zoneRecord: rect(document.getElementById('zoneRecord')),
    zoneSerial: rect(document.getElementById('zoneSerial')),
    replayRange: rect(document.getElementById('replayRange')),
    replayPos: rect(document.getElementById('replayPos')),
    pauseBtn: rect(document.getElementById('replayPauseBtn')),
    rrInFooter: !!(foot && foot.contains(document.getElementById('replayRange'))),
    rrInRecord: !!(document.getElementById('zoneRecord') && document.getElementById('zoneRecord').contains(document.getElementById('replayRange'))),
    pbInFooter: !!(foot && foot.contains(document.getElementById('replayPauseBtn'))),
    rrCount: document.querySelectorAll('#replayRange').length,
    pbCount: document.querySelectorAll('#replayPauseBtn').length
  };
  if (foot){
    var cs = getComputedStyle(foot);
    out.footerStyle = {height: cs.height, flex: cs.flex, overflow: cs.overflow, gap: cs.gap,
                       whiteSpace: cs.whiteSpace, padding: cs.padding};
    Array.prototype.forEach.call(foot.children, function (el, i) {
      var r = rect(el);
      out.footerChildren.push({i: i, tag: tagOf(el), x: r.x, y: r.y, w: r.w, h: r.h});
    });
  }
  /* ★ 决定性证据，且与字体/视口无关：在同一次加载里把新增的 .rp-group
     临时 display:none 测一遍、再恢复测一遍 —— 若既有元素两边坐标完全一致，
     就证明"新增控件零位移"，不需要依赖任何外部基线快照或字体度量。 */
  var g = document.querySelector('.rp-group');
  if (g){
    g.style.display = 'none';
    var hid = collect();
    g.style.display = '';
    var show = collect();
    out.ab = {hidden: hid, shown: show};
  } else {
    out.ab = null;
  }
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

FUNC_JS = """
(function () {
  var out = {};
  var rr = document.getElementById('replayRange');
  var pos = document.getElementById('replayPos');
  var pb = document.getElementById('replayPauseBtn');
  out.exists = {range: !!rr, pos: !!pos, pauseBtn: !!pb};

  /* backend 打桩：记录 setReplayPaused / seekReplay / stopReplay 的调用 */
  window.__calls = [];
  window.backend = {
    setReplayPaused: function (p) { window.__calls.push(['setReplayPaused', p]); },
    seekReplay: function (f) { window.__calls.push(['seekReplay', f]); },
    stopReplay: function () { window.__calls.push(['stopReplay']); }
  };
  state._replayStateWired = true;
  state._replayPausedWired = true;
  function snap(tag){
    return {step: tag,
            pbDisabled: pb ? pb.disabled : null,
            pbText: pb ? pb.textContent : null,
            pbPressed: pb ? pb.getAttribute('aria-pressed') : null,
            pbTitle: pb ? pb.getAttribute('title') : null,
            rrDisabled: rr ? rr.disabled : null,
            rrValue: rr ? rr.value : null,
            pos: pos ? pos.textContent : null,
            paused: state.replayPaused,
            frozen: state.frozen};
  }
  var steps = [];
  steps.push(snap('0-初始（未回放）'));

  /* 回放开始 -> 暂停按钮应可用（且仍是"播放中"外观） */
  onReplayState(true, 'run01.bin');
  steps.push(snap('1-replayStateChanged(True)'));

  /* 后端确认已暂停 */
  onReplayPausedChanged(true);
  steps.push(snap('2-replayPausedChanged(True)'));

  /* 点按钮 -> 请求"继续" */
  pb.click();
  steps.push(snap('3-点按钮(期望请求继续)'));
  out.callsAfterFirstClick = window.__calls.slice();

  /* 连点两下（在途请求未确认）：第一次请求暂停、第二次请求继续，不能两次都发同一个值 */
  window.__calls = [];
  onReplayPausedChanged(false);
  pb.click();
  pb.click();
  out.callsAfterDoubleClick = window.__calls.slice();
  steps.push(snap('4-连点两下'));

  /* 后端回包纠正 */
  onReplayPausedChanged(true);
  steps.push(snap('5-回包纠正为已暂停'));

  /* 进度推进 + 拖动 seek（回归） */
  window.__calls = [];
  onReplayProgress(150, 300, 22.8, 45.6);
  rr.value = '250';
  rr.dispatchEvent(new Event('input', {bubbles: true}));
  rr.dispatchEvent(new Event('change', {bubbles: true}));
  out.callsAfterDrag = window.__calls.slice();
  steps.push(snap('6-回放中拖动'));

  /* 停止 -> 暂停按钮回到不可用 */
  onReplayState(false, '');
  steps.push(snap('7-replayStateChanged(False)'));

  out.steps = steps;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
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

    res = {}

    def on_load(ok):
        def cb2(v):
            res["func"] = v
            app.quit()

        def cb(v):
            res["geo"] = v
            QTimer.singleShot(150, lambda: page.runJavaScript(FUNC_JS, cb2))
        QTimer.singleShot(3200, lambda: page.runJavaScript(GEO_JS, cb))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    geo = json.loads(res.get("geo", "{}"))
    raw_func = res.get("func")
    func = json.loads(raw_func) if isinstance(raw_func, str) else {}

    # ---- 零位移 A/B 证据：同一加载里 .rp-group 隐藏 vs 显示，既有元素坐标必须完全一致 ----
    # （与字体度量、视口尺寸都无关，因此在别的机器/视口上跑也成立）
    # ⚠️ 隐藏只是 display:none，元素仍在 DOM 里（rect 归零），所以按"同顺序逐项对位"比较，
    #    不能按 tag 建字典（底栏里有 3 个 .sep「|」tag 相同，字典会互相覆盖掩盖位移）。
    ab = geo.get("ab") or {}
    hid, show = ab.get("hidden") or {}, ab.get("shown") or {}
    kh = hid.get("children") or []
    ks = show.get("children") or []
    rr_ = lambda c: (c["x"], c["y"], c["w"], c["h"])
    same_len = len(kh) == len(ks)
    pairs = [(h, s) for h, s in zip(kh, ks) if "rp-group" not in h["tag"]]
    moved = [h["tag"] for h, s in pairs if rr_(h) != rr_(s)]
    gp = [(h, s) for h, s in zip(kh, ks) if "rp-group" in h["tag"]]
    group_hidden_rect = rr_(gp[0][0]) if gp else None
    group_shown_rect = rr_(gp[0][1]) if gp else None

    # ★ F8：**先落盘 JSON，再打印**。以前 JSON 写在所有 print 之后，而 GBK 控制台会在
    #   print ⏸ 时 UnicodeEncodeError 崩掉 -> 一条证据都取不到。现在即使打印阶段出问题，
    #   证据文件也已经存在（并且默认路径不再依赖调用方传参）。
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "_footer_replay_result.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"geo": geo, "func": func, "r2_ab": {"moved": moved,
                                                       "same_len": same_len,
                                                       "group_hidden": group_hidden_rect,
                                                       "group_shown": group_shown_rect}},
                      f, ensure_ascii=False, indent=2)
        print("WROTE", out_path)
    except Exception as e:
        print("JSON 写入失败:", e)

    if not func:
        print("（功能部分未执行 —— 通常是 onReplayPausedChanged / #replayPauseBtn 尚未实现，"
              "基线采集时属正常）")
    print("=== 几何 ===")
    print(json.dumps(geo, ensure_ascii=False, indent=2))
    print("=== 功能 ===")
    print("exists:", func.get("exists"))
    print("callsAfterFirstClick :", func.get("callsAfterFirstClick"))
    print("callsAfterDoubleClick:", func.get("callsAfterDoubleClick"))
    print("callsAfterDrag       :", func.get("callsAfterDrag"))
    print("%-26s %-9s %-5s %-8s %-12s %-9s %s" % ("步骤", "pb禁用", "文本", "pressed", "title", "rr禁用", "pos"))
    for st in func.get("steps", []):
        print("%-26s %-9s %-5s %-8s %-12s %-9s %s" % (st["step"], st["pbDisabled"], st["pbText"],
                                                       st["pbPressed"], st["pbTitle"], st["rrDisabled"], st["pos"]))
    print("JS 错误:", func.get("errors"))
    print("--- 零位移 A/B（同一加载内隐藏/显示 .rp-group）---")
    print("  底栏矩形  隐藏=%s  显示=%s" % (hid.get("footer"), show.get("footer")))
    print("  前 8 个既有子元素对位比较，坐标有变化的: %s" % (moved or "无（零位移）"))
    print("  子元素个数 隐藏/显示 = %s/%s" % (len(kh), len(ks)))
    print("  新增组 rect 隐藏=%s 显示=%s" % (group_hidden_rect, group_shown_rect))
    print("  zoneRecord 隐藏=%s 显示=%s" % (hid.get("zoneRecord"), show.get("zoneRecord")))
    print("  zoneSerial 隐藏=%s 显示=%s" % (hid.get("zoneSerial"), show.get("zoneSerial")))
    print("  页面高 隐藏=%s/%s/%s 显示=%s/%s/%s"
          % (hid.get("bodyH"), hid.get("docH"), hid.get("stageH"),
             show.get("bodyH"), show.get("docH"), show.get("stageH")))

    # （JSON 已在打印之前写过一次；这里不再重复覆盖，避免把更全的那份盖掉）

    # ---- 断言 ----
    steps = func.get("steps", [])

    def sx(i):
        return steps[i] if i < len(steps) else {}

    dbl = func.get("callsAfterDoubleClick") or []
    checks = [
        ("进度条在底栏内", geo.get("rrInFooter") is True),
        ("记录区里已没有进度条", geo.get("rrInRecord") is False),
        ("进度条全页只有一份", geo.get("rrCount") == 1),
        ("回放暂停按钮在底栏内且只有一份", geo.get("pbInFooter") is True and geo.get("pbCount") == 1),
        ("底栏高度仍是 30px", (geo.get("footer") or {}).get("h") == 30),
        ("进度条落在底栏纵向范围内", (geo.get("replayRange") or {}).get("y", -1) >= (geo.get("footer") or {}).get("y", 0)
         and (geo.get("replayRange") or {}).get("y", 1e9) < (geo.get("footer") or {}).get("y", 0) + 30),
        ("初始（未回放）暂停按钮不可用且显示 ⏸", sx(0).get("pbDisabled") is True and sx(0).get("pbText") == "⏸"),
        ("回放开始后暂停按钮可用", sx(1).get("pbDisabled") is False),
        ("replayPausedChanged(True) -> 文案变 ▶ / aria-pressed=true",
         sx(2).get("pbText") == "▶" and sx(2).get("pbPressed") == "true" and sx(2).get("pbTitle") == "继续回放"),
        ("已暂停时点按钮 -> 请求 setReplayPaused(False)", func.get("callsAfterFirstClick") == [["setReplayPaused", False]]),
        ("连点两下 -> 依次请求 True/False（不是同一个值发两次）",
         dbl == [["setReplayPaused", True], ["setReplayPaused", False]]),
        ("replayPausedChanged(False) -> 文案回 ⏸", sx(4).get("pbText") == "⏸"),
        ("拖动 -> seekReplay(0.25)（回归）", func.get("callsAfterDrag") == [["seekReplay", 0.25]]),
        ("停止后暂停按钮回到不可用 + ⏸ + 进度条禁用",
         sx(7).get("pbDisabled") is True and sx(7).get("pbText") == "⏸" and sx(7).get("rrDisabled") is True),
        ("停止后 pos 回占位", sx(7).get("pos") == "— / —"),
        # ---- 零位移 A/B（隐藏新增组 vs 显示新增组），与字体/视口无关 ----
        ("零位移 A/B：底栏矩形完全一致", bool(hid) and hid.get("footer") == show.get("footer")),
        ("零位移 A/B：隐藏/显示两种状态下子元素个数相同", same_len is True),
        ("零位移 A/B：8 个既有底栏子元素坐标零位移", len(pairs) == 8 and not moved),
        ("零位移 A/B：隐藏时新增组确实不渲染（rect 归零）", group_hidden_rect == (0, 0, 0, 0)),
        ("零位移 A/B：显示时新增组落在底栏内且有尺寸",
         bool(group_shown_rect) and group_shown_rect[2] > 0
         and group_shown_rect[1] >= (show.get("footer") or {}).get("y", 0)),
        ("零位移 A/B：zoneRecord / zoneSerial 矩形一致",
         hid.get("zoneRecord") == show.get("zoneRecord") and hid.get("zoneSerial") == show.get("zoneSerial")),
        ("零位移 A/B：页面/舞台高度一致",
         (hid.get("bodyH"), hid.get("docH"), hid.get("stageH"))
         == (show.get("bodyH"), show.get("docH"), show.get("stageH"))),
        ("无 JS 运行时错误", not func.get("errors")),
    ]
    print("---")
    if not func:
        print("结果: 仅采集几何（功能断言跳过）")
        return
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
