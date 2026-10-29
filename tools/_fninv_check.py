# -*- coding: utf-8 -*-
"""_fninv_check.py —— 「功能清单」验收契约的自动校验（配套 tools/_fninv.md）

只读：不修改任何产品文件（telemetry_web/**、launcher_web.py、protocol.py、telemetry_config.py）。

用法：  python tools/_fninv_check.py
退出码：0 = 全 PASS；非 0 = FAIL 条数。

覆盖 `_fninv.md` 里 **P0/P1 且此前无自动验证** 的条目：
  F01 导航 / F04 录制胶囊 / F05 冻结 / F41 通道 chips / F45 数值面板 /
  F50-53 可视化 增删图表 + 拖通道进图 + 取消信号 / F62 连接 / F64 发送 /
  F72 导出 / F80-82 回放条 / F90-91 设置 / F95 分区拖高 / F98 键盘可达 /
  F99 后端 Signal-Slot 契约 / F100 主循环 / 以及"功能元素在真数据下不是 0 尺寸"。

设计要点（避免误报）：
  · 真渲染（离屏 QtWebEngine + view.show()），先灌一批真实形态数据再量尺寸 ——
    百分比填充分条在**无数据时天然为 0**，只有"有数据仍为 0"才算丢了功能。
  · 拖高测试**不让 rAF 随机**：探针内临时接管 requestAnimationFrame 并同步 flush，
    只影响探针页面，不动产品代码。
"""
import ast
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "telemetry_web", "index.html")
APPJS = os.path.join(ROOT, "telemetry_web", "app.js")
LAUNCHER = os.path.join(ROOT, "launcher_web.py")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                      "--disable-gpu --no-sandbox --disable-software-rasterizer")

from PyQt6.QtCore import QUrl, QTimer                        # noqa: E402
from PyQt6.QtWidgets import QApplication                     # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView          # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript   # noqa: E402

RESULTS = []


def chk(ok, code, msg, detail=""):
    RESULTS.append((bool(ok), code, msg, detail if not ok else ""))
    return bool(ok)


# ─────────────────────────── 静态契约 ───────────────────────────

NEED_SIGNALS = ["dataReady", "rawReady", "statusChanged", "errorMsg", "portsReady", "saveDirReady",
                "recordStateChanged", "recordSaved", "recordFailed", "recordFileReady", "saveResult",
                "cfgSaved", "recordProgress", "uiConfig", "replayStateChanged", "replayProgress",
                "replayLoadingChanged", "replayPausedChanged", "replayBackfill"]
NEED_SLOTS = ["refreshPorts", "chooseSaveDir", "setSaveDir", "getSaveDir", "getUiConfig", "saveUserCfg",
              "resetUserCfg", "startRecording", "stopRecording", "isRecording", "chooseReplayFile",
              "startReplay", "stopReplay", "setReplayPaused", "seekReplay", "seekReplayLive",
              "saveTextFile", "exportRecording", "showInExplorer", "connect", "disconnect", "send"]

NEED_JS_FUNCS = ["initZoneResize", "bindPillKey", "toggleChip", "buildChips", "addChart", "removeChart",
                 "paintAccVec", "refreshAccVec", "drawVChart", "drawCursorLayer", "updateSigPanel",
                 "onDataBatch", "applyUiConfig", "pushSample", "endReplayDrag", "resetPwrPeak",
                 "setDemo", "toggleRecord", "exportCsv", "updateRecUI", "setReplayPausedUI"]
# ★ 前端真正会发起的 RPC（`startReplay`/`saveUserCfg`/`isRecording` 等由后端自身或文件对话框触发，
#   界面不直接调用 —— 它们只属于 md §L 的"未接线"清单，不算契约缺失）
NEED_JS_RPC = ["backend.connect", "backend.disconnect", "backend.send", "backend.startRecording",
               "backend.stopRecording", "backend.chooseReplayFile", "backend.chooseSaveDir",
               "backend.stopReplay", "backend.setReplayPaused", "backend.seekReplayLive",
               "backend.seekReplay", "backend.exportRecording", "backend.refreshPorts",
               "backend.showInExplorer", "backend.getUiConfig", "backend.getSaveDir"]
NEED_JS_SIGNALS = ["backend.dataReady", "backend.rawReady", "backend.uiConfig", "backend.statusChanged",
                   "backend.portsReady", "backend.saveDirReady", "backend.recordStateChanged",
                   "backend.recordSaved", "backend.recordProgress", "backend.replayStateChanged",
                   "backend.replayProgress", "backend.replayLoadingChanged",
                   "backend.replayPausedChanged", "backend.replayBackfill", "backend.errorMsg",
                   "backend.saveResult", "backend.cfgSaved"]

NEED_HTML_IDS = [
    "livePill", "liveDot", "liveText", "rxPill", "rxTag", "recPill", "recDot", "recText",
    "freezeBtn", "freezeText", "clock", "rail", "stage", "toastWrap",
    "zoneP2", "speedGauge", "speedNum", "speedUnit", "speedBar", "rpmGauge", "rpmNum", "rpmBar",
    "revRow", "pwrCard", "pwrNum", "pwrPeak", "pwrPeakNum", "thrBar", "thrNum", "tqBar", "tqNum",
    "tempBar", "tempNum", "voltBar", "voltNum", "ampBar", "ampNum",
    # ★ 2026-10-16 按用户要求删除 `#zoneP2` 底部整块 bandC（内含 #dualCanvas 转速×车速双轴、
    #   #scatterCanvas 功率×电流散点）→ 这两个 id 从清单**如实移出**（对称地少掉 A_ids_missing /
    #   A_ids_collapsed 里针对它们的两条探测）。
    #   除此之外本清单**没有任何一条断言被放宽/删除**（ADC 卡片数、分区数、关键函数、RPC、
    #   信号监听、Signal/Slot 契约、填充分条等全部原样保留）；剩下的元素仍逐个受塌陷探测覆盖。
    "zoneP1", "rollNum", "rollNeedle", "pitchNum", "pitchUp", "pitchDn", "yawNum", "yawPtr",
    "accNum", "accNeg", "accPos", "accChip", "accScaleLo", "accScaleHi",
    "accVec", "accVecCanvas", "accVecChip", "accVecLat", "accVecLon", "accVecMag", "accVecNote",
    "zoneP0", "adcGrid", "zoneTrend", "normBtn", "chipBar", "trendCanvas", "cursorCanvas",
    "pauseTag", "selStats", "sigPanel",
    "zoneVisual", "addChartBtn", "visChannels", "visGrid",
    "zoneSerial", "portSelect", "baudSelect", "connectBtn", "refreshPortsBtn", "connDot", "connText",
    "hexBox", "cmdInput", "cmdMode", "sendBtn",
    "zoneRecord", "recMeta", "saveDirInput", "browseDirBtn", "openDirBtn", "recBtn", "exportBtn",
    "replayBtn", "logList", "zoneSettings", "demoMode", "autoRecord", "bufInput",
    "footer", "fpsTag", "rxFoot", "replayPauseBtn", "replayRange", "replayPos",
]
# 有数据时必须"长出来"的填充分条（布局重排把它们压成 0 就是丢功能）
FILL_IDS = ["speedBar", "rpmBar", "thrBar", "tqBar", "tempBar", "voltBar", "ampBar",
            "accNeg", "accPos", "pitchUp", "pitchDn"]


def static_checks():
    app = io.open(APPJS, encoding="utf-8").read()
    html = io.open(HTML, encoding="utf-8").read()
    chk("initZoneResize" in app and ".zone-resize" in app, "F95",
        "分区拖高手柄仍由 initZoneResize() 运行时注入（.zone-resize）",
        "app.js 找不到 initZoneResize/.zone-resize")
    miss = [f for f in NEED_JS_FUNCS if f not in app]
    chk(not miss, "F-js-func", "app.js 关键函数/绑定仍在（%d 项）" % len(NEED_JS_FUNCS), "缺失: %s" % miss)
    miss = [x for x in NEED_JS_RPC if x not in app]
    chk(not miss, "F-js-rpc", "app.js 仍在调用后端 RPC（%d 项）" % len(NEED_JS_RPC), "缺失: %s" % miss)
    miss = [x for x in NEED_JS_SIGNALS if x not in app]
    chk(not miss, "F-js-sig", "app.js 仍在监听后端信号（%d 项）" % len(NEED_JS_SIGNALS), "缺失: %s" % miss)
    chk("bindPillKey" in app and "keydown" in app, "F98", "role=button 的键盘可达（Enter/Space）")
    chk("requestAnimationFrame(render)" in app, "F100", "主循环 requestAnimationFrame(render) 仍在")
    chk(html.count('class="rail-item') >= 3, "F01", "导航 rail-item ≥3 个（总览/可视化/串口）",
        "实际 %d" % html.count('class="rail-item'))

    tree = ast.parse(io.open(LAUNCHER, encoding="utf-8").read())
    sigs, slots = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", None) == "pyqtSignal":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        sigs.add(t.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                d = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(d, "attr", None) == "pyqtSlot" or getattr(d, "id", None) == "pyqtSlot":
                    slots.add(node.name)
    miss = [s for s in NEED_SIGNALS if s not in sigs]
    chk(not miss, "F99-signals", "后端 pyqtSignal 契约齐全（%d 个）" % len(NEED_SIGNALS), "缺失: %s" % miss)
    miss = [s for s in NEED_SLOTS if s not in slots]
    chk(not miss, "F99-slots", "后端 @pyqtSlot 契约齐全（%d 个）" % len(NEED_SLOTS), "缺失: %s" % miss)


# ─────────────────────────── 真渲染行为校验 ───────────────────────────

JS1 = r"""
(function(){
  var R = {};
  function rec(code, ok, msg, detail){ R[code] = {ok: !!ok, msg: msg, detail: detail || ''}; }
  function $(id){ return document.getElementById(id); }
  function T(id){ var e = $(id); return e ? (e.textContent || '').trim() : null; }
  function toastText(){ var w = $('toastWrap'); return w ? (w.textContent || '') : ''; }
  function click(el, o){ el.dispatchEvent(new MouseEvent('click', Object.assign({bubbles:true, cancelable:true}, o||{}))); }
  function key(el, k){ el.dispatchEvent(new KeyboardEvent('keydown', {key:k, bubbles:true, cancelable:true})); }
  function box(el){ if(!el) return null; var b = el.getBoundingClientRect();
                    return {w: Math.round(b.width), h: Math.round(b.height)}; }
  var appEl = $('app'); appEl.style.width='1440px'; appEl.style.height='860px';
  window.dispatchEvent(new Event('resize'));

  /* ---------- 先灌一批"真机形态"数据：让百分比填充分条有值 ---------- */
  try{
    onDataBatch(JSON.stringify([
      {type:'adc', seq:1, ts:1000, values:[100,200,300,400,500,600,700,800,900,1000]},
      {type:'nav', seq:2, ts:1010, roll:45, pitch:8, yaw:0, accel_lat:1.5, accel_lon:1.5},
      {type:'motor', seq:3, ts:1020, motor_temp:40, torque:50, motor_rpm:1500,
       throttle:60, voltage:400, current:10, speed:10}
    ]));
    state.frozen = false; domDirty = true; trendDirty = true; lastDom = -1e9; lastDraw = -1e9;
    updateDom();
  }catch(e){ rec('A_feed', false, '灌入真机形态数据', String(e)); }
  rec('A_feed', !!$('accNum') && T('accNum') !== '—', '灌入真机形态数据后读数面板有数',
      'accNum=' + T('accNum') + ' roll=' + T('rollNum'));

  /* ---------- A. id 齐全 / 无重复 / 有数据时不是 0 尺寸 ---------- */
  (function(){
    var need = __IDS__;
    var missing = [], dup = [], dead = [], fills = [];
    need.forEach(function(id){ if(!$(id)) missing.push(id); });
    var seen = {};
    Array.prototype.forEach.call(document.querySelectorAll('[id]'), function(e){
      if (seen[e.id]) dup.push(e.id); seen[e.id] = 1;
    });
    var ALLOW = {toastWrap:1, pauseTag:1, selStats:1};   /* 空提示容器 / 条件显示：默认 0x0 属设计 */
    need.forEach(function(id){
      var e = $(id); if(!e) return;
      var z = e.closest('.zone');
      if (z && z.classList.contains('hidden')) return;
      var s = box(e); if(!s) return;
      if (s.w < 2 && s.h < 2 && !ALLOW[id]) dead.push(id + '(' + s.w + 'x' + s.h + ')');
    });
    __FILLS__.forEach(function(id){
      var e = $(id); if(!e) return;
      var s = box(e); if(!s) return;
      if (s.w < 2 && s.h < 2) fills.push(id + '(' + s.w + 'x' + s.h + ')');
    });
    rec('A_ids_missing', missing.length===0, '清单 ' + need.length + ' 个功能入口 id 全部存在',
        '缺失: ' + missing.join(','));
    rec('A_ids_dup', dup.length===0, '页面内没有重复 id', '重复: ' + dup.join(','));
    rec('A_ids_collapsed', dead.length===0,
        '功能元素没有塌成 0x0（布局丢失探测；条件显示项已排除并单测）', '塌陷: ' + dead.join(' '));
    rec('A_fills_grow', fills.length===0,
        '有数据时填充分条不再是 0 尺寸（' + __FILLS__.length + ' 项）', '仍为 0: ' + fills.join(' '));
    /* 条件显示项：冻结时 #pauseTag 必须出现（F46 的行为契约） */
    var pt = $('pauseTag');
    if (pt){
      var wasFrozen = state.frozen;
      setFrozen(true, true);
      var s1 = box(pt);
      setFrozen(!!wasFrozen, true);
      var s0 = box(pt);
      rec('A_pauseTag_on_freeze', s1 && s1.w > 0 && s1.h > 0 && s0 && (s0.w < 2 || s0.h < 2),
          '冻结时 #pauseTag 显示、恢复后隐藏（暂停角标行为）',
          'frozen=' + (s1 && s1.w + 'x' + s1.h) + ' live=' + (s0 && s0.w + 'x' + s0.h));
    } else {
      rec('A_pauseTag_on_freeze', false, '冻结时 #pauseTag 显示', '缺 #pauseTag');
    }
  })();

  /* ---------- B. 导航切换 ---------- */
  (function(){
    var items = document.querySelectorAll('.rail-item');
    var all = ['zoneVisual','zoneP0','zoneP1','zoneP2','zoneTrend','zoneSerial','zoneRecord','zoneSettings'];
    function vis(){ var o = {}; all.forEach(function(id){ var e=$(id); o[id] = e ? !e.classList.contains('hidden') : null; }); return o; }
    if (items.length < 3){ rec('B_nav_click', false, '导航项 ≥3', '实际 ' + items.length); return; }
    items[1].click();
    var v1 = vis();
    var ok1 = all.every(function(id){ return v1[id] === (id === 'zoneVisual'); });
    var act1 = items[1].classList.contains('active') && items[1].getAttribute('aria-current') === 'true';
    items[2].click();
    var v2 = vis();
    var ok2 = all.every(function(id){ return v2[id] === (id === 'zoneSerial'); });
    items[0].click();
    var v3 = vis();
    var ok3 = all.every(function(id){ return v3[id] === (id !== 'zoneVisual'); });
    var act0 = items[0].classList.contains('active');
    rec('B_nav_click', ok1 && ok2 && ok3 && act1 && act0,
        '三个导航项点击后分区显隐正确 + active/aria 同步',
        JSON.stringify({visual:v1, serial:v2, overview:v3, act1:act1, act0:act0}));
  })();

  /* ---------- C. 录制胶囊 / 冻结（含键盘） ---------- */
  (function(){
    var dot0 = $('recDot').className, btn0 = T('recBtn'), aria0 = $('recPill').getAttribute('aria-pressed');
    click($('recPill'));
    var dot1 = $('recDot').className, btn1 = T('recBtn'), aria1 = $('recPill').getAttribute('aria-pressed');
    var ok = (dot0 !== dot1) && (btn0 !== btn1) && (aria0 !== aria1);
    /* 还原：清空本地攒行，避免 toggleRecord(false) 触发下载 */
    try { state.recLines = [CSV_HEADER_LOCAL]; } catch(e){}
    if (state.recording) click($('recPill'));
    var dot2 = $('recDot').className;
    rec('C_recPill_click', ok, 'recPill 点击切换录制（recDot/recBtn/aria-pressed 三者同步变化）',
        'dot ' + dot0 + '->' + dot1 + ' btn ' + btn0 + '->' + btn1 + ' aria ' + aria0 + '->' + aria1);
    rec('C_recPill_restore', dot2 === dot0, '再次点击能回到未录制态（可逆）', 'dot=' + dot2 + ' 期望 ' + dot0);

    var f0 = state.frozen, x0 = T('freezeText');
    click($('freezeBtn'));
    var f1 = state.frozen, x1 = T('freezeText');
    key($('freezeBtn'), 'Enter');
    var f2 = state.frozen, x2 = T('freezeText');
    var okF = (f0 !== f1) && (x0 !== x1) && (f1 !== f2) && (x1 !== x2);
    /* 键盘 Space 也要能用 */
    key($('freezeBtn'), ' ');
    var f3 = state.frozen;
    if (state.frozen) click($('freezeBtn'));
    rec('C_freeze_click_key', okF, 'freezeBtn 点击/Enter 均切换冻结（state + freezeText 同步）',
        'frozen ' + f0 + '->' + f1 + '->' + f2 + ' text ' + x0 + '->' + x1 + '->' + x2);
    rec('C_freeze_space', f3 !== f2, 'freezeBtn 支持 Space 键', 'frozen ' + f2 + '->' + f3);
  })();

  /* ---------- D. 趋势：chips / 三档量程 / 数值面板 ---------- */
  (function(){
    var chips = document.querySelectorAll('#chipBar .chip');
    var okAttr = chips.length > 0 && Array.prototype.every.call(chips, function(c){
      return c.getAttribute('role') === 'button' && c.getAttribute('tabindex') === '0' && c.dataset.id;
    });
    rec('D_chips_exist', okAttr, '通道 chip 存在且带 role=button/tabindex/data-id', '数量=' + chips.length);
    var first = chips[0], id = first.dataset.id;
    var on0 = first.classList.contains('on');
    var vis0 = state.visible ? state.visible.has(id) : null;
    click(first);
    var now = document.querySelector('#chipBar .chip[data-id="' + id + '"]');
    var on1 = now ? now.classList.contains('on') : null;
    var vis1 = state.visible ? state.visible.has(id) : null;
    var toggled = (vis0 === null) ? (on0 !== on1) : (vis0 !== vis1);
    rec('D_chips_toggle', toggled && on0 !== on1,
        'chip 点击切换显隐（state.visible 与 .on 同步；重建后按 data-id 重新取节点）',
        'on ' + on0 + '->' + on1 + ' visible ' + vis0 + '->' + vis1);
    var back = document.querySelector('#chipBar .chip[data-id="' + id + '"]');
    if (back) click(back);

    var labels = [];
    for (var i = 0; i < 4; i++){ labels.push(T('normBtn')); click($('normBtn')); }
    var uniq = labels.filter(function(v, i){ return labels.indexOf(v) === i; });
    rec('D_normBtn_cycle', uniq.length === 3, 'normBtn 循环三档量程（归一化/自适应/分组分带）',
        'labels=' + JSON.stringify(labels));

    var rows = document.querySelectorAll('#sigPanel .sig-row');
    if (rows.length){
      rows[0].dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
      var hl = state.hl;
      var idr = rows[0].dataset.id;
      var v0 = state.visible ? state.visible.has(idr) : null;
      click(rows[0]);
      var v1 = state.visible ? state.visible.has(idr) : null;
      rows[0].dispatchEvent(new MouseEvent('mouseleave', {bubbles:true}));
      rec('D_sigPanel_interact', !!hl && (v0 === null || v0 !== v1),
          'sigPanel 行 mouseover 设置高亮 state.hl、click 切换该通道（等效 chip）',
          'hl=' + hl + ' visible ' + v0 + '->' + v1);
    } else {
      rec('D_sigPanel_interact', false, 'sigPanel 行 mouseover/click 生效', '没有 .sig-row');
    }
  })();

  /* ---------- E. 分区拖高（临时接管 rAF 以便确定性 flush；只影响探针页面） ---------- */
  (function(){
    var zones = Array.prototype.slice.call(document.querySelectorAll('.zone'));
    var handles = document.querySelectorAll('.zone-resize');
    rec('E_resize_handles', zones.length > 0 && handles.length === zones.length,
        '每个 .zone 都有 .zone-resize 拖高手柄', zones.length + ' zones / ' + handles.length + ' handles');
    var pick = null;
    zones.forEach(function(z){
      if (z.classList.contains('hidden')) return;
      var h = z.getBoundingClientRect().height;
      if (!pick || h > pick.h) pick = {z: z, h: h};
    });
    window.__pick = null;
    if (!pick){ rec('E_resize_drag', false, '有可拖动分区', '无可见分区'); return; }
    var hd = pick.z.querySelector('.zone-resize');
    if (!hd){ rec('E_resize_drag', false, '该分区有手柄', pick.z.id); return; }
    var h0 = Math.round(pick.h);
    window.__pick = {id: pick.z.id, before: h0};
    var rafOrig = window.requestAnimationFrame, q = [];
    window.requestAnimationFrame = function(cb){ q.push(cb); return q.length; };
    var r = hd.getBoundingClientRect();
    hd.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, clientX: Math.round(r.left + 5), clientY: 300}));
    document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, clientX: Math.round(r.left + 5), clientY: 380}));
    document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, clientX: Math.round(r.left + 5), clientY: 380}));
    var queued = q.length;
    var qq = q.slice(0); q.length = 0;
    window.requestAnimationFrame = rafOrig;
    qq.forEach(function(cb){ try { cb(performance.now()); } catch(e){} });
    var inline = pick.z.style.height;
    var h1 = Math.round(pick.z.getBoundingClientRect().height);
    rec('E_resize_drag', h1 >= h0 + 40,
        '拖动 .zone-resize 真的改变分区高度（B07 拖高契约）'
        + '  [' + pick.z.id + ' ' + h0 + 'px -> ' + h1 + 'px, inline=' + inline
        + ', 本轮 rAF 回调=' + queued + ']',
        'zone=' + pick.z.id + ' before=' + h0 + ' after=' + h1 + ' inline=' + inline + ' 本轮 rAF 回调=' + queued);
  })();

  /* ---------- F. 回放条 ---------- */
  (function(){
    var rg = $('replayRange'), pb = $('replayPauseBtn');
    var okAttr = rg && rg.type === 'range' && rg.min === '0' && rg.max === '1000' && rg.step === '1';
    rec('F_range_attrs', !!okAttr, '#replayRange 是 range 且 min/max/step=0/1000/1',
        rg ? [rg.type, rg.min, rg.max, rg.step].join('/') : 'missing');
    rec('F_init_disabled', rg && rg.disabled === true && pb && pb.disabled === true,
        '非回放态下滑块与暂停按钮都禁用',
        'range=' + (rg && rg.disabled) + ' pause=' + (pb && pb.disabled));
    onReplayLoadingChanged(true, '正在解析… 123 帧');
    var p1 = T('replayPos'), d1 = rg.disabled;
    onReplayProgress(0, 0, 0, 0);
    var p2 = T('replayPos');
    onReplayLoadingChanged(false, '');
    rec('F_loading_text', p1 === '正在解析… 123 帧' && p2 === p1 && d1 === true,
        '加载中 #replayPos 显示后端文案、进度回包不覆盖、滑块保持禁用',
        'pos=' + p1 + '->' + p2 + ' disabled=' + d1);
    onReplayState(true, 'qa.bin');
    var en = rg.disabled === false;
    onReplayState(false, '');
    rec('F_replay_enable', en, '回放态下进度条启用（replayStateChanged 接线）', 'disabled=' + rg.disabled);
    rec('F_pos_reset', T('replayPos') === '— / —', '停止回放后 #replayPos 复位为 "— / —"', 'now=' + T('replayPos'));
  })();

  /* ---------- G. 可视化工作台：增卡 / 拖通道进图 / 取消信号 / 删卡 ---------- */
  (function(){
    var n0 = $('visGrid').children.length;
    click($('addChartBtn'));
    var n1 = $('visGrid').children.length;
    rec('G_add_chart', n1 === n0 + 1, 'addChartBtn 点击后 visGrid 卡片 +1', n0 + '->' + n1);
    var card = $('visGrid').children[n1 - 1];
    var parts = card ? {del: !!card.querySelector('.vc-del'), sigs: !!card.querySelector('.vc-sigs'),
                        canvas: !!card.querySelector('.vc-canvas'), empty: !!card.querySelector('.vc-empty')} : {};
    rec('G_card_parts', parts.del && parts.sigs && parts.canvas && parts.empty,
        '新卡片结构完整（.vc-del / .vc-sigs / .vc-canvas / 空态提示）', JSON.stringify(parts));

    var items = document.querySelectorAll('#visChannels .vc-item[draggable="true"]');
    rec('G_channels_draggable', items.length > 0,
        '通道列表项 draggable（可拖进图表）', '数量=' + items.length);
    if (items.length && card){
      var dt = new DataTransfer();
      items[0].dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer: dt}));
      var carried = dt.getData('text/plain');
      rec('G_dragstart_sets_data', !!carried, '拖拽通道时 dragstart 写入通道 id', 'carried=' + carried);
      card.dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer: dt}));
      var hl = card.classList.contains('dragover');
      card.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer: dt}));
      var sig0 = card.querySelectorAll('.vc-sig').length;
      card.dispatchEvent(new DragEvent('dragleave', {bubbles:true, cancelable:true, dataTransfer: dt}));
      rec('G_drop_adds_sig', carried ? sig0 >= 1 : false,
          '把通道拖到图表卡片上会新增 .vc-sig（drag&drop 接线）', 'dragover高亮=' + hl + ' vc-sig=' + sig0);
      if (sig0 >= 1){
        click(card.querySelector('.vc-sig'));
        var sig1 = card.querySelectorAll('.vc-sig').length;
        rec('G_sig_click_removes', sig1 === sig0 - 1,
            '点击 .vc-sig 把该信号移出图表', sig0 + '->' + sig1);
      } else {
        rec('G_sig_click_removes', false, '点击 .vc-sig 把该信号移出图表', '没有 .vc-sig');
      }
    } else {
      rec('G_dragstart_sets_data', false, '拖拽通道 dragstart 写入 id', '无 draggable 通道');
      rec('G_drop_adds_sig', false, '拖通道进图新增 .vc-sig', '无 draggable 通道');
      rec('G_sig_click_removes', false, '点击 .vc-sig 移出信号', '无 .vc-sig');
    }
    if (card && parts.del){
      click(card.querySelector('.vc-del'));
      var n2 = $('visGrid').children.length;
      rec('G_del_chart', n2 === n0, '.vc-del 点击后卡片被移除', n1 + '->' + n2 + '（原始 ' + n0 + '）');
    } else {
      rec('G_del_chart', false, '.vc-del 点击后卡片被移除', '缺 .vc-del');
    }
    var vis = document.querySelectorAll('#visChannels .vc-item');
    rec('G_vis_channels', vis.length > 0, '#visChannels 有通道条目', '数量=' + vis.length);
  })();

  /* ---------- H. 串口 / 发送 / 导出 / 回放按钮 / 峰值 / 设置 ---------- */
  (function(){
    var ps = $('portSelect'), bs = $('baudSelect'), cm = $('cmdMode');
    rec('H_serial_controls', !!ps && !!bs && !!cm && bs.options.length >= 3 && cm.options.length >= 2,
        '端口/波特率/发送格式控件存在且选项合理',
        'baud=' + (bs ? bs.options.length : -1) + ' mode=' + (cm ? cm.options.length : -1));

    var t0 = toastText();
    click($('connectBtn'));
    var t1 = toastText();
    rec('H_connect_wired', t1 !== t0, 'connectBtn 点击有反馈（浏览器模式给出提示而非静默）',
        'toast 增量=' + JSON.stringify(t1.slice(Math.min(t0.length, t1.length))));

    $('cmdInput').value = '';
    key($('cmdInput'), 'Enter');
    var t2 = toastText();
    rec('H_send_empty_safe', t2 === t1, '空输入按 Enter 不发送也不报错', 'toast 变了=' + (t2 !== t1));

    $('cmdInput').value = '55 AA 01';
    click($('sendBtn'));
    var t3 = toastText();
    rec('H_send_wired', t3 !== t2, 'sendBtn 点击有反馈（HEX 文本进发送链路）',
        'toast 增量=' + JSON.stringify(t3.slice(Math.min(t2.length, t3.length)).slice(0, 60)));
    $('cmdInput').value = '';

    var t4 = toastText();
    click($('exportBtn'));
    var t5 = toastText();
    rec('H_export_wired', t5 !== t4, 'exportBtn 点击有反馈（仍接着导出链路）',
        'toast 增量=' + JSON.stringify(t5.slice(Math.min(t4.length, t5.length)).slice(0, 60)));

    var t6 = toastText();
    click($('replayBtn'));
    var t7 = toastText();
    rec('H_replay_wired', t7 !== t6, 'replayBtn 点击有反馈（仍接着回放链路）',
        'toast 增量=' + JSON.stringify(t7.slice(Math.min(t6.length, t7.length)).slice(0, 60)));

    var t8 = toastText();
    click($('browseDirBtn'));
    var t9 = toastText();
    rec('H_browse_wired', t9 !== t8, 'browseDirBtn 点击有反馈', 'delta=' + (t9 !== t8));

    /* 峰值清零：改的是 #pwrPeakNum（不是按钮整体文字） */
    var n = $('pwrPeakNum');
    if (n){
      n.textContent = '99.9';
      click($('pwrPeak'));
      var after = T('pwrPeakNum');
      rec('H_pwr_peak_click', after !== '99.9', 'pwrPeak 点击执行峰值清零（#pwrPeakNum 被重置）',
          '注入 99.9 -> 点击后 ' + after);
    } else { rec('H_pwr_peak_click', false, 'pwrPeakNum 存在', 'missing'); }

    var dm = $('demoMode');
    dm.checked = true; dm.dispatchEvent(new Event('change', {bubbles:true}));
    var on = (state.demo === true);
    dm.checked = false; dm.dispatchEvent(new Event('change', {bubbles:true}));
    var off = (state.demo === false);
    rec('H_demo_toggle', on && off, 'demoMode 勾选/取消都能切换 state.demo', 'on=' + on + ' off=' + off);

    var b0 = (typeof BUF === 'number') ? BUF : null;
    $('bufInput').value = '1234';
    $('bufInput').dispatchEvent(new Event('change', {bubbles:true}));
    var b1 = (typeof BUF === 'number') ? BUF : null;
    $('bufInput').value = '999999999';
    $('bufInput').dispatchEvent(new Event('change', {bubbles:true}));
    var b2 = (typeof BUF === 'number') ? BUF : null;
    rec('H_buf_change', b1 === 1234 && b1 !== b0 && b2 !== 999999999 && b2 >= 100 && b2 <= 20000,
        'bufInput change 生效（1234 被采纳；1e9 被夹紧到上限内）',
        'BUF ' + b0 + ' -> ' + b1 + ' -> (1e9) ' + b2);
  })();

  /* ---------- I. toast 弹出 + 可点击关闭（关闭效果在 phase2 复核） ---------- */
  (function(){
    var w = $('toastWrap');
    var mark = 'fninv-' + Math.random().toString(36).slice(2, 8);
    window.__toastMark = mark;
    toast(mark, 'ok');
    var has = (w.textContent || '').indexOf(mark) >= 0;
    rec('I_toast_show', has, 'toast() 能在 #toastWrap 弹出提示', 'text=' + JSON.stringify((w.textContent||'').slice(0, 60)));
    var el = null;
    Array.prototype.forEach.call(w.children, function(c){ if ((c.textContent||'').indexOf(mark) >= 0) el = c; });
    if (el){
      el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
      rec('I_toast_click', true, 'toast 元素可点击（触发关闭流程）');
    } else {
      rec('I_toast_click', false, 'toast 元素可点击', '没找到含标记的 toast 元素');
    }
  })();

  R._errs = (window.__errs || []).slice(0, 10);
  return JSON.stringify(R);
})()
"""

JS2 = r"""
(function(){
  var w = document.getElementById('toastWrap');
  var mark = window.__toastMark || '';
  return JSON.stringify({
    toast_gone: (w.textContent || '').indexOf(mark) < 0,
    errs: (window.__errs || []).slice(0, 10)
  });
})()
"""

PROBE_HOOK = """(function(){window.__errs=window.__errs||[];
 window.addEventListener('error',function(e){window.__errs.push(String(e.message)+' @'+(e.lineno||0));});})();"""


class Page(QWebEnginePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.console = []

    def javaScriptConsoleMessage(self, level, msg, line, source):
        self.console.append("[%s] %s" % (level.name, msg))


def run_page():
    app = QApplication.instance() or QApplication(sys.argv)
    view = QWebEngineView()
    page = Page(view)
    view.setPage(page)
    sc = QWebEngineScript()
    sc.setName("fninv"); sc.setSourceCode(PROBE_HOOK)
    sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    page.scripts().insert(sc)

    js1 = (JS1.replace("__IDS__", json.dumps(NEED_HTML_IDS, ensure_ascii=False))
              .replace("__FILLS__", json.dumps(FILL_IDS, ensure_ascii=False)))
    box = {}
    view.resize(1440, 860)
    view.show()

    def phase2():
        page.runJavaScript(JS2, lambda v: (box.update(p2=v), app.quit()))

    def phase1(v):
        try:
            box["p1"] = json.loads(v) if v else {}
        except Exception as e:                                    # noqa: BLE001
            box["err"] = "%s / %r" % (e, str(v)[:300])
            box["p1"] = {}
        QTimer.singleShot(500, phase2)

    view.loadFinished.connect(lambda ok: QTimer.singleShot(
        2500, lambda: page.runJavaScript(js1, phase1)))
    view.load(QUrl.fromLocalFile(HTML))
    QTimer.singleShot(90000, app.quit)
    app.exec()
    return box, page.console


def main():
    static_checks()
    box, console = run_page()
    p1 = box.get("p1") or {}
    if not p1:
        chk(False, "A-render", "页面真渲染并完成全部交互检查", "phase1 无结果: %s" % box.get("err"))
    else:
        for code in sorted(k for k in p1 if not k.startswith("_")):
            it = p1[code]
            chk(it.get("ok"), code, it.get("msg", ""), it.get("detail", ""))
        p2 = {}
        try:
            p2 = json.loads(box["p2"]) if box.get("p2") else {}
        except Exception:                                          # noqa: BLE001
            pass
        chk(p2.get("toast_gone") is True, "I_toast_dismiss",
            "toast 点击后真的被移除（关闭生效）", "toast_gone=%s" % p2.get("toast_gone"))
        errs = list(p1.get("_errs") or []) + list(p2.get("errs") or [])
        chk(not errs, "K_no_js_errors", "全部交互过程中没有 JS 运行时错误", "errs=%s" % errs[:5])
        noisy = [c for c in console if "ERROR" in c or "Uncaught" in c]
        chk(not noisy, "K_console", "控制台无 ERROR/Uncaught", " / ".join(noisy[:4]))

    npass = sum(1 for ok, _c, _m, _d in RESULTS if ok)
    nfail = len(RESULTS) - npass
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 80)
    print("功能清单契约校验 tools/_fninv_check.py   （清单：tools/_fninv.md）")
    print("基线: HEAD %s   product-sha(app.js/index.html) = %s / %s" % (
        os.popen("git rev-parse --short HEAD").read().strip(),
        os.popen("git hash-object telemetry_web/app.js").read().strip()[:12],
        os.popen("git hash-object telemetry_web/index.html").read().strip()[:12]))
    print("       （product-sha = git blob 哈希；与上一次 PASS 的票号不同即说明产品文件被改过，需重跑）")
    print("=" * 80)
    for ok, code, msg, detail in RESULTS:
        print("  %-4s %-22s %s" % ("PASS" if ok else "FAIL", code, msg))
        if not ok and detail:
            print("       └─ %s" % str(detail)[:400])
    print("-" * 80)
    print("结果: %d PASS / %d FAIL" % (npass, nfail))
    print("=" * 80)
    return nfail


if __name__ == "__main__":
    sys.exit(main())
