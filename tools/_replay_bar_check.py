# -*- coding: utf-8 -*-
"""task-4 专项验证：回放进度条 UI。

模拟后端**实测出来的真实事件顺序**（含两个陷阱），验证：
  ① 初始态：滑块禁用、标签占位
  ② 开始序列 progress(0,0,0,0) -> progress(0,n,0,total) -> replayStateChanged(True)
     → 滑块必须【能】用（陷阱1：不能因为先到的复位帧就永久禁用）
  ③ 回放中 progress 更新滑块与 "已播 / 总时长" 标签（用第 3、4 个参数，不估算）
  ④ 拖动 input -> backend.seekReplay(值/1000)；拖动期间回包不抢滑块也不抢标签
  ⑤ 停止 -> 禁用 + 归零 + 占位；停止后再来的 (0,0,0,0) 不会把滑块重新启用
  ⑥ 无 replayStateChanged 信号时用进度兜底启用（且从不禁用）

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\python.exe tools\_replay_bar_check.py
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
  window.addEventListener('unhandledrejection', function (e) {
    window.__errs.push('promise: ' + e.reason);
  });
})();
"""

# 一整幕跑完，把每一步的观测值都收集起来
RUN_JS = """
(function () {
  var out = {};
  function snap(tag) {
    var rr = document.getElementById('replayRange');
    var pos = document.getElementById('replayPos');
    return {
      step: tag,
      disabled: rr ? rr.disabled : null,
      value: rr ? rr.value : null,
      label: pos ? pos.textContent : null,
      replaying: state.replaying,
      wired: state._replayStateWired,
      dragging: state.replayDragging
    };
  }
  var rr = document.getElementById('replayRange');
  var pos = document.getElementById('replayPos');
  out.exists = !!rr && !!pos;
  out.isRange = rr ? rr.type : null;
  out.min = rr ? rr.min : null;
  out.max = rr ? rr.max : null;

  /* ---- 版式证据 —— ★ task-7 后进度条已移到底栏，断言随之改为「在底栏」----
     · 进度条必须落在 #footer 里（不在滚动区），全页只有一份；
     · 记录区那一行必须回到加控件之前的原始状态（7 个控件同处一行、无新控件混入）。 */
  var foot = document.getElementById('footer');
  var rrR = rr.getBoundingClientRect();
  var cs = getComputedStyle(rr);
  var footR = foot.getBoundingClientRect();
  var pb = document.getElementById('replayPauseBtn');
  var orig = ['saveDirInput', 'browseDirBtn', 'openDirBtn', 'recBtn', 'exportBtn', 'replayBtn'];
  var tops = orig.map(function (id) { return Math.round(document.getElementById(id).getBoundingClientRect().top); });
  out.layout = {
    inFooter: foot.contains(rr),
    inRecordZone: document.getElementById('zoneRecord').contains(rr),
    rangeCount: document.querySelectorAll('#replayRange').length,
    pauseBtnInFooter: !!(pb && foot.contains(pb)),
    pauseBtnCount: document.querySelectorAll('#replayPauseBtn').length,
    parentClass: rr.parentElement ? rr.parentElement.className : '',
    footH: Math.round(footR.height),
    rrInsideFooterV: rrR.top >= footR.top - 1 && rrR.bottom <= footR.bottom + 1,
    recordOrigTops: tops,
    recordRowIntact: tops.every(function (t) { return Math.abs(t - tops[0]) < 12; }),
    recordRowChildIds: Array.prototype.map.call(document.getElementById('recBtn').parentElement.children,
      function (el) { return el.id || el.className; }),
    sliderW: Math.round(rrR.width), sliderH: Math.round(rrR.height),
    visible: cs.display !== 'none' && cs.visibility !== 'hidden' && rrR.width > 40 && rrR.height > 0,
    labelText: pos ? pos.textContent : null,
    zoneRecord: (function(){ var z = document.getElementById('zoneRecord').getBoundingClientRect();
                             return {w: Math.round(z.width), h: Math.round(z.height)}; })()
  };

  /* backend 是模块级 var，离屏（无 QWebChannel）时为 null —— 打桩记录 seekReplay 入参。
     ★ 同时把 _replayStateWired 置 true，模拟「后端确实有 replayStateChanged」的真实情形，
       这样回包兜底不生效，才能真正验证陷阱1只靠 state 信号解套。 */
  state._replayStateWired = true;
  window.__seeks = [];      /* 全量 seekReplay（带回填，慢） */
  window.__lives = [];      /* 拖动中的 seekReplayLive（O(1)，不回填） */
  window.backend = {
    seekReplayLive: function (f) { window.__lives.push(f); },
    seekReplay: function (f) { window.__seeks.push(f); },
    stopReplay: function () { window.__stops = (window.__stops || 0) + 1; }
  };
  var steps = [];
  steps.push(snap('0-初始态'));

  /* --- 后端 startReplay 的真实顺序（陷阱1） --- */
  onReplayProgress(0, 0, 0.0, 0.0);
  steps.push(snap('1-复位帧(0,0,0,0)'));
  onReplayProgress(0, 300, 0.0, 45.6);
  steps.push(snap('2-真进度(0,300,0.0,45.6)'));
  onReplayState(true, 'run01.bin');
  steps.push(snap('3-replayStateChanged(True)'));

  /* --- 回放中推进 --- */
  onReplayProgress(150, 300, 22.8, 45.6);
  steps.push(snap('4-中点(150,300,22.8,45.6)'));

  /* --- 拖动：input 事件 -> seekReplayLive（F1：不再每个 input 都发全量 seek） --- */
  rr.value = '250';
  rr.dispatchEvent(new Event('input', {bubbles: true}));
  steps.push(snap('5-拖动到250'));
  out.seeksAfterDrag = window.__seeks.slice();
  out.livesAfterDrag = window.__lives.slice();
  /* 拖动中回包不得抢滑块 / 标签 */
  onReplayProgress(50, 300, 7.6, 45.6);
  steps.push(snap('6-拖动中回包(50,300,7.6,45.6)'));

  /* --- 松手（change）+ 之后回包恢复跟随 --- */
  rr.dispatchEvent(new Event('change', {bubbles: true}));
  out.seeksAfterRelease = window.__seeks.slice();
  onReplayProgress(180, 300, 27.4, 45.6);
  steps.push(snap('7-松手后回包(180,300,27.4,45.6)'));

  /* --- 停止：先来 state(False)，再来一次复位帧 --- */
  onReplayState(false, '回放完成');
  steps.push(snap('8-replayStateChanged(False)'));
  onReplayProgress(0, 0, 0.0, 0.0);
  steps.push(snap('9-停止后的复位帧'));
  /* 再确认一次：复位帧绝不会把滑块启用 */
  onReplayProgress(0, 0, 0.0, 0.0);
  steps.push(snap('10-再补一帧复位'));

  /* --- 兜底路径：后端没有 replayStateChanged 时，收到真进度应启用（且从不禁用） --- */
  state._replayStateWired = false;
  onReplayProgress(10, 300, 1.5, 45.6);
  steps.push(snap('11-无状态信号时的兜底启用'));
  state._replayStateWired = true;

  out.steps = steps;
  out.stopCalls = window.__stops || 0;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


# ---- 第二轮：F1 往返映射 / F2 单帧与零时长 / F3 暂停中开始回放 ----
RUN2_JS = """
(function () {
  var out = {};
  var rr = document.getElementById('replayRange');
  var pos = document.getElementById('replayPos');
  state._replayStateWired = true;

  /* ---- F1：与后端 seekReplay 的往返。后端是 Python：
       idx = int(round(frac*(n-1)))，而 Python 的 round() 是"四舍六入五取偶"，
       与 JS 的 Math.round（.5 一律进位）不同 —— 这里如实复刻，避免测出假象。 */
  function pyRound(x){
    var f = Math.floor(x), d = x - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return (f % 2 === 0) ? f : f + 1;
  }
  function backendSeekIdx(frac, n){ return Math.max(0, Math.min(n - 1, pyRound(frac * (n - 1)))); }
  function expectedValue(frac, n){
    var idx = backendSeekIdx(frac, n);
    return Math.round((n > 1 ? idx / (n - 1) : 0) * 1000);
  }
  /* 完整的用户动作：拖动到 frac -> 松手 -> 后端回报（走真实的分母映射） */
  function roundTrip(frac, n){
    onReplayState(true, 'rt.bin');
    rr.value = String(Math.round(frac * 1000));
    rr.dispatchEvent(new Event('input', {bubbles: true}));
    rr.dispatchEvent(new Event('change', {bubbles: true}));
    var idx = backendSeekIdx(frac, n);
    onReplayProgress(idx, n, idx * 0.1, (n - 1) * 0.1);
    return {req: frac, n: n, idx: idx, value: Number(rr.value), expected: expectedValue(frac, n)};
  }
  out.rt = [];
  [[0.5, 10], [0.5, 11], [1.0, 5], [1.0, 10], [1.0, 2], [0.0, 300], [0.25, 4], [0.75, 4]].forEach(function (c) {
    out.rt.push(roundTrip(c[0], c[1]));
  });
  /* 幂等：同一帧回包重复到达，滑块不应持续漂移 */
  var rt1 = roundTrip(0.5, 10);
  var idx10 = backendSeekIdx(0.5, 10);
  onReplayProgress(idx10, 10, 1.1, 4.5);
  onReplayProgress(idx10, 10, 1.1, 4.5);
  out.rtIdempotent = {first: rt1.value, afterRepeats: Number(rr.value)};
  /* 极端：最后一帧必须能到最右端 */
  out.lastFrame = [];
  [2, 5, 10, 11, 300].forEach(function (n) {
    onReplayState(true, 'end.bin');
    onReplayProgress(n - 1, n, 10.0, 10.0);
    out.lastFrame.push({n: n, value: Number(rr.value)});
  });

  /* ---- F2：单帧 / 零总时长都是"合法的真实值"，不是复位帧 ---- */
  onReplayState(true, 'f2.bin');
  onReplayProgress(0, 300, 12.3, 45.6);              /* 先放一个哨兵标签 */
  out.f2_sentinel = pos.textContent;
  onReplayProgress(0, 1, 0.0, 0.0);                  /* 只有 1 帧 -> total_secs=0.0 */
  out.f2_singleFrame = {label: pos.textContent, value: rr.value, disabled: rr.disabled};
  onReplayProgress(0, 300, 12.3, 45.6);              /* 哨兵复位 */
  onReplayProgress(3, 10, 0.0, 0.0);                 /* 多帧但所有 ts 相同 -> 总时长 0 */
  out.f2_zeroDur = {label: pos.textContent, value: rr.value, disabled: rr.disabled};
  /* 真正的复位帧（n=0）仍要归零 */
  onReplayState(false, '');
  onReplayProgress(0, 0, 0.0, 0.0);
  out.f2_realReset = {label: pos.textContent, value: rr.value, disabled: rr.disabled};

  /* ---- F3（2026-10-15 新契约）：暂停中开始回放 -> **自动解除冻结** + 说明性提示 ----
     ★ 旧契约（"只 warn 提示、绝不擅自解除暂停"）已被 Lead 裁决废弃：
       用户点「数据回放」就是要看数据，画面一动不动等于功能失效。 */
  var _toast = toast;
  window.__toasts = [];
  toast = function (m, k, ms) { window.__toasts.push({m: String(m), k: k || 'info'}); return _toast(m, k, ms); };

  /* 对照：冻结状态下喂数据，缓冲**不增长**（= 用户看到"画面不动"的根因） */
  state.frozen = true;
  Object.keys(SIGS).forEach(function (k) { state.buf[k].length = 0; });
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'adc', seq: 1, ts: 1, values: [1,2,3,4,5,6,7,8,9,10]}]));
  var frozenBufLen = state.buf['adc0'].length;

  window.__toasts = [];
  onReplayState(true, 'cold.bin');
  out.f3_frozen = {
    toasts: window.__toasts.slice(),
    frozenAfter: state.frozen,
    replayingAfter: state.replaying,
    foundAutoToast: window.__toasts.some(function (t) { return t.m.indexOf('自动恢复实时') >= 0; }),
    foundReplayToast: window.__toasts.some(function (t) { return t.m.indexOf('开始回放') >= 0; }),
    foundOldNag: window.__toasts.some(function (t) { return t.m.indexOf('请先点') >= 0; }),
    foundPauseWarn: window.__toasts.some(function (t) { return t.k === 'warn' && t.m.indexOf('暂停') >= 0; }),
    freezeText: ($('freezeText') || {}).textContent,
    ariaPressed: $('freezeBtn').getAttribute('aria-pressed'),
    pauseTagShown: ($('pauseTag') || {}).style ? $('pauseTag').style.display : null,
    disabledAfter: rr.disabled
  };
  /* 自动解冻后，同样的数据必须真的开始推进（缓冲从 0 变 >0） */
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type: 'adc', seq: 2, ts: 2, values: [1,2,3,4,5,6,7,8,9,10]}]));
  out.f3_resume = {frozenBufLen: frozenBufLen, afterBufLen: state.buf['adc0'].length};

  /* 非冻结态开始回放：不该多弹"自动恢复实时"（避免提示滥用），但仍应有"开始回放" */
  state.frozen = false;
  window.__toasts = [];
  onReplayState(true, 'warm.bin');
  out.f3_unfrozen = {
    toasts: window.__toasts.slice(),
    foundAutoToast: window.__toasts.some(function (t) { return t.m.indexOf('自动恢复实时') >= 0; }),
    foundReplayToast: window.__toasts.some(function (t) { return t.m.indexOf('开始回放') >= 0; })
  };
  onReplayState(false, '');
  state.frozen = false;
  toast = _toast;                                     /* 还原探针 */
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


RUN3_JS = """
(function () {
  /* F1：长录播拖动 —— 拖动中只发 O(1) 的 seekReplayLive，松手才发一次全量 seekReplay。
     改前是"每个 input 都发 seekReplay"，真实 5.4MB/22.8 万帧录播实测 33~45ms/次，
     30 步拖动累计 ~1.2 秒（见 tools/_replay_seek_cost.py）。 */
  var out = {};
  var rr = document.getElementById('replayRange');
  state.replaying = true;
  state._replayStateWired = true;
  setReplayEnabled(true);
  state.replayDragging = false;

  function dragWith(hasLive, steps) {
    window.__seeks = [];
    window.__lives = [];
    if (hasLive) window.backend.seekReplayLive = function (f) { window.__lives.push(f); };
    else delete window.backend.seekReplayLive;
    state.replayDragging = false;
    for (var i = 1; i <= steps; i++) {
      rr.value = String(Math.round(i / steps * 1000));
      rr.dispatchEvent(new Event('input', {bubbles: true}));
    }
    var intraFull = window.__seeks.length, intraLive = window.__lives.length;
    rr.dispatchEvent(new Event('change', {bubbles: true}));      /* 松手 */
    return {intraFull: intraFull, intraLive: intraLive,
            afterReleaseFull: window.__seeks.length,
            lastFullFrac: window.__seeks[window.__seeks.length - 1],
            steps: steps};
  }

  out.withLive = dragWith(true, 30);
  out.withoutLive = dragWith(false, 30);
  /* 松手必须发（即使节流把最后一个 input 吞了） */
  out.releaseAlwaysSends = out.withLive.afterReleaseFull === 1 && out.withoutLive.afterReleaseFull === 1;
  /* 再点一次鼠标松开（没拖过）不应发 seek —— 全局 mouseup 的安全网不能误触发 */
  window.__seeks = [];
  state.replayDragging = false;
  window.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
  out.strayMouseupSeeks = window.__seeks.length;
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""


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

    res = {}

    def on_load(ok):
        def cb3(v):
            res["r3"] = v
            app.quit()

        def cb2(v):
            res["r2"] = v
            QTimer.singleShot(150, lambda: page.runJavaScript(RUN3_JS, cb3))

        def cb(v):
            res["r"] = v
            QTimer.singleShot(150, lambda: page.runJavaScript(RUN2_JS, cb2))
        QTimer.singleShot(3200, lambda: page.runJavaScript(RUN_JS, cb))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    r = json.loads(res.get("r", "{}"))
    r2 = json.loads(res.get("r2", "{}"))
    r3 = json.loads(res.get("r3", "{}"))
    print("控件: exists=%s type=%s min=%s max=%s" % (r.get("exists"), r.get("isRange"), r.get("min"), r.get("max")))
    lay = r.get("layout", {})
    print("版式: 在底栏=%s  在记录区=%s  全页份数=%s  parent=%s"
          % (lay.get("inFooter"), lay.get("inRecordZone"), lay.get("rangeCount"), lay.get("parentClass")))
    print("      底栏高=%s 滑块纵向在底栏内=%s  暂停按钮在底栏=%s 份数=%s"
          % (lay.get("footH"), lay.get("rrInsideFooterV"), lay.get("pauseBtnInFooter"), lay.get("pauseBtnCount")))
    print("      底栏内滑块 %sx%s 可见=%s" % (lay.get("sliderW"), lay.get("sliderH"), lay.get("visible")))
    print("      记录区 ctl-row 子元素: %s" % lay.get("recordRowChildIds"))
    print("      记录区原有控件 top=%s 同处一行=%s" % (lay.get("recordOrigTops"), lay.get("recordRowIntact")))
    print("      zoneRecord 尺寸=%s" % lay.get("zoneRecord"))
    print("seekReplay(全量) 收到的值:", r.get("seeksAfterDrag"))
    print("seekReplayLive(拖动中) 收到的值:", r.get("livesAfterDrag"))
    print("-" * 78)
    print("%-34s %-9s %-6s %-14s %s" % ("步骤", "disabled", "value", "label", "dragging"))
    for st in r.get("steps", []):
        print("%-34s %-9s %-6s %-14s %s" % (st["step"], st["disabled"], st["value"], st["label"], st["dragging"]))
    print("-" * 78)
    print("JS 错误:", r.get("errors"))
    print("=" * 78)
    print("第二轮：F1 往返映射 / F2 单帧与零时长 / F3 暂停中开始回放")
    print("F1 往返（请求 -> 后端 idx -> 回报滑块值，期望=idx/(n-1)*1000）:")
    for it in r2.get("rt", []):
        print("   请求 %.2f  n=%-4s idx=%-4s 滑块=%-5s 期望=%-5s %s"
              % (it["req"], it["n"], it["idx"], it["value"], it["expected"],
                 "OK" if it["value"] == it["expected"] else "**不一致**"))
    print("   幂等重复回包: first=%s afterRepeats=%s" % (r2.get("rtIdempotent", {}).get("first"),
                                                          r2.get("rtIdempotent", {}).get("afterRepeats")))
    print("   最后一帧是否到最右端: %s" % r2.get("lastFrame"))
    print("F2 单帧/零时长（哨兵标签=%s）:" % r2.get("f2_sentinel"))
    print("   单帧 (0,1,0.0,0.0)      -> %s" % r2.get("f2_singleFrame"))
    print("   零时长 (3,10,0.0,0.0)   -> %s" % r2.get("f2_zeroDur"))
    print("   真复位 (0,0,0.0,0.0)    -> %s" % r2.get("f2_realReset"))
    print("F3 暂停中开始回放:")
    print("   frozen=true  -> %s" % r2.get("f3_frozen"))
    print("   frozen=false -> %s" % r2.get("f3_unfrozen"))
    print("JS 错误(第二轮):", r2.get("errors"))
    print()
    print("=== F1：长录播拖动 —— 拖动中只发 O(1) 的 live seek，松手才发一次全量 seek ===")
    print("  有 seekReplayLive: %s" % r3.get("withLive"))
    print("  无 seekReplayLive（旧后端兜底）: %s" % r3.get("withoutLive"))
    print("  松手必发: %s   没拖过的 mouseup 误触发次数: %s"
          % (r3.get("releaseAlwaysSends"), r3.get("strayMouseupSeeks")))
    print("JS 错误(第三轮):", r3.get("errors"))

    def step(i):
        return r["steps"][i] if i < len(r.get("steps", [])) else {}

    checks = [
        ("滑块与标签存在，type=range 0..1000", r.get("exists") and r.get("isRange") == "range"
         and r.get("min") == "0" and r.get("max") == "1000"),
        ("初始态：禁用 + 占位标签", step(0).get("disabled") is True and step(0).get("label") == "— / —"),
        ("先到的复位帧 (0,0,0,0) 不改 disabled（陷阱1）", step(1).get("disabled") is True),
        ("只有进度帧时【不】启用滑块（唯一判据是 state 信号）",
         step(2).get("label") == "0.0s / 45.6s" and step(2).get("value") == "0" and step(2).get("disabled") is True),
        ("replayStateChanged(True) 后滑块可用（陷阱1解套）", step(3).get("disabled") is False),
        # ⚠️ 下面两条的期望值在修 F1 时被修正过：
        #    idx=150/n=300 在旧代码里算出 500、idx=180 算出 600（那就是 idx/n 的 bug 值）。
        #    正确的分母是 n-1：150/299*1000=502、180/299*1000=602。断言是按"修复后应有的值"写的。
        ("中点进度 (150,300)：value=502(150/299) 且标签 22.8s / 45.6s",
         step(4).get("value") == "502" and step(4).get("label") == "22.8s / 45.6s"),
        # ⚠️ 语义在 F1 修复后改变：拖动 input 现在发的是 O(1) 的 seekReplayLive，
        #    全量 seekReplay（带回填）只在**松手**时发一次。断言按新语义写。
        ("拖动(input) 发 seekReplayLive(0.25) 且此时尚无全量 seek",
         r.get("livesAfterDrag") == [0.25] and r.get("seeksAfterDrag") == []),
        ("松手(change) 后才发一次全量 seekReplay(0.25)", r.get("seeksAfterRelease") == [0.25]),
        ("拖动中回包不抢滑块", step(6).get("value") == "250"),
        ("拖动中回包不抢标签（仍是按滑块算的 11.4s）", step(6).get("label") == "11.4s / 45.6s"),
        ("松手后回包恢复跟随 (180,300) -> 602(180/299)", step(7).get("value") == "602" and step(7).get("label") == "27.4s / 45.6s"),
        ("停止：禁用 + 归零 + 占位", step(8).get("disabled") is True and step(8).get("value") == "0" and step(8).get("label") == "— / —"),
        ("停止后的复位帧不会重新启用滑块（陷阱2）", step(9).get("disabled") is True and step(10).get("disabled") is True),
        ("无状态信号时靠进度兜底启用", step(11).get("disabled") is False),
        ("控件在底栏内、记录区里已无进度条", lay.get("inFooter") is True and lay.get("inRecordZone") is False),
        ("进度条全页只有一份", lay.get("rangeCount") == 1),
        ("回放暂停按钮也在底栏内且只有一份", lay.get("pauseBtnInFooter") is True and lay.get("pauseBtnCount") == 1),
        ("底栏高度仍是 30px 且滑块纵向在其内", lay.get("footH") == 30 and lay.get("rrInsideFooterV") is True),
        ("记录区那一行回到原始状态（7 个控件同处一行）", lay.get("recordRowIntact") is True),
        ("记录区那一行不含回放控件",
         "replayRange" not in (lay.get("recordRowChildIds") or [])
         and "replayPauseBtn" not in (lay.get("recordRowChildIds") or [])),
        ("控件可见且有实际尺寸", lay.get("visible") is True and (lay.get("sliderW") or 0) >= 100),
        ("全程无 JS 运行时错误", not r.get("errors")),
    ]

    # ---------- 第二轮断言：F1 / F2 / F3 ----------
    rt = {("%.2f|%s" % (i["req"], i["n"])): i for i in r2.get("rt", [])}

    def rtv(frac, n):
        return rt.get(("%.2f|%s" % (frac, n)), {}).get("value")

    rtidem = r2.get("rtIdempotent", {})
    lf = {str(i["n"]): i["value"] for i in r2.get("lastFrame", [])}
    f2s = r2.get("f2_singleFrame", {})
    f2z = r2.get("f2_zeroDur", {})
    f2r = r2.get("f2_realReset", {})
    f3f = r2.get("f3_frozen", {})
    f3u = r2.get("f3_unfrozen", {})
    f3f_resume = r2.get("f3_resume", {})

    checks += [
        # ---- F1：分母必须是 n-1 ----
        ("F1 所有往返请求的滑块值都与 idx/(n-1) 期望一致",
         r2.get("rt") and all(i["value"] == i["expected"] for i in r2["rt"])),
        ("F1 n=10 拖动 0.5 松手后【不再回弹到 400】（实际 444 = 帧量化中点）", rtv(0.5, 10) == 444),
        ("F1 n=11 拖动 0.5 松手后精确停在 500（帧栅格可整除）", rtv(0.5, 11) == 500),
        ("F1 回放到最后一帧滑块到最右端 1000（n=2/5/10/11/300）",
         all(lf.get(str(n)) == 1000 for n in (2, 5, 10, 11, 300))),
        ("F1 旧 bug 的典型值 800/900/455 已不复现",
         rtv(1.0, 5) == 1000 and rtv(1.0, 10) == 1000 and rtv(0.5, 11) != 455),
        ("F1 同一帧回包重复到达不漂移", rtidem.get("first") == rtidem.get("afterRepeats")),
        # ---- F2：只有 n<=0 才算复位 ----
        ("F2 单帧 .bin (n=1,tsec=0) 不再被当复位帧：标签=0.0s / 0.0s", f2s.get("label") == "0.0s / 0.0s"),
        ("F2 单帧时滑块仍可用（未被误禁用）", f2s.get("disabled") is False),
        ("F2 多帧但总时长=0（ts 全相同）也正常刷新：标签=0.0s / 0.0s", f2z.get("label") == "0.0s / 0.0s"),
        ("F2 真复位帧 (0,0,0,0) 仍然归零 + 占位 + 禁用",
         f2r.get("label") == "— / —" and f2r.get("value") == "0" and f2r.get("disabled") is True),
        # ---- F3（2026-10-15 新契约）：暂停中开始回放 -> 自动解除冻结 + 说明性提示 ----
        ("F3 暂停中开始回放：state.frozen 自动变为 false", f3f.get("frozenAfter") is False),
        ("F3 暂停中开始回放：出现说明性提示（含\"自动恢复实时\"）", f3f.get("foundAutoToast") is True),
        ("F3 同时仍有\"开始回放\"提示", f3f.get("foundReplayToast") is True),
        ("F3 不再出现旧提示\"请先点继续\"（旧契约已废弃）", f3f.get("foundOldNag") is False),
        ("F3 不再弹 warn 级\"暂停\"提示", f3f.get("foundPauseWarn") is False),
        ("F3 冻结按钮 UI 与 state.frozen 同步（文字/aria/角标）",
         f3f.get("freezeText") == "暂停" and f3f.get("ariaPressed") == "false"
         and f3f.get("pauseTagShown") == "none"),
        ("F3 解冻后数据真的开始推进（冻结时缓冲 0 -> 解冻后 >0）",
         (f3f_resume.get("frozenBufLen") == 0) and (f3f_resume.get("afterBufLen") or 0) > 0),
        ("F3 回放态判定不受影响（replaying=true）", f3f.get("replayingAfter") is True),
        ("F3 暂停中开始回放不影响滑块可用性", f3f.get("disabledAfter") is False),
        ("F3 未暂停时【不】弹\"自动恢复实时\"（提示不滥用）", f3u.get("foundAutoToast") is False),
        ("F3 未暂停时仍有\"开始回放\"提示", f3u.get("foundReplayToast") is True),
        ("第二轮无 JS 运行时错误", not r2.get("errors")),
        # ---- F1：拖动中的成本（每个 input 都发全量 seek 的缺陷已修）----
        ("F1 有 live 接口时：拖动 30 次 input 期间【0 次】全量 seekReplay",
         (r3.get("withLive") or {}).get("intraFull") == 0),
        ("F1 有 live 接口时：拖动中发的是 seekReplayLive（节流后 >=1 次且 <30 次）",
         1 <= (r3.get("withLive") or {}).get("intraLive", 0) < 30),
        ("F1 松手补发一次全量 seekReplay（且用的是最终位置 1.0）",
         (r3.get("withLive") or {}).get("afterReleaseFull") == 1
         and (r3.get("withLive") or {}).get("lastFullFrac") == 1.0),
        ("F1 旧后端（无 live 接口）拖动中一次请求都不发，只在松手发 1 次",
         (r3.get("withoutLive") or {}).get("intraFull") == 0
         and (r3.get("withoutLive") or {}).get("intraLive") == 0
         and (r3.get("withoutLive") or {}).get("afterReleaseFull") == 1),
        ("F1 没拖过的全局 mouseup 不会误发 seek", r3.get("strayMouseupSeeks") == 0),
        ("第三轮无 JS 运行时错误", not r3.get("errors")),
    ]
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
