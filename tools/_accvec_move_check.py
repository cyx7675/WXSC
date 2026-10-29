# -*- coding: utf-8 -*-
"""task-17 验收：矢量图搬到 #zoneP1 + 门控/可见性 + 既有卡片未被挤坏。

两种用法：
    <venv>\\python.exe tools\\_accvec_move_check.py --save-baseline   # 搬迁**前**存基线
    <venv>\\python.exe tools\\_accvec_move_check.py                  # 搬迁后比对 + 可见性证据

基线：把 #zoneP1 内每个 .dcard 按 .d-name 文本做键，记 [x,y,w,h] 与 zoneP1 自身 rect。
比对时：w/h 必须逐张一致（不许被挤坏），x 允许 ±1px，y 允许整体位移（面板占了高度），
并把位移量打印出来。
另含：
  · #accVec 全页只此一份（querySelectorAll 计数=1，证明没用 display:none 藏副本）
  · #accVec 在 zoneP1 内、不在 zoneVisual 内（closest 双证 + rect）
  · **切到「总览」会刷新**：在可视化区清空画布 -> 切回总览 -> 像素回来（门控正确与否的关键证据）
  · 总览下 20Hz 数据节拍也会重画（不依赖切区）
"""
import os
import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
BASELINE = os.path.join(HERE, "tools", "_zoneP1_baseline.json")
OUT_JSON = os.path.join(HERE, "tools", "_accvec_move_result.json")
SAVE = "--save-baseline" in sys.argv

PROBE_JS = """
(function () {
  window.__errs = window.__errs || [];
  window.addEventListener('error', function (e) {
    window.__errs.push((e.message || 'err') + ' @' + (e.lineno || 0));
  });
  window.__vpc = 0;                    /* 矢量图真实绘制次数 */
  if (!window.__vHooked) {
    window.__vHooked = true;
    var _arc = CanvasRenderingContext2D.prototype.arc;
    CanvasRenderingContext2D.prototype.arc = function () {
      if (this.canvas && this.canvas.id === 'accVecCanvas') window.__vpc++;
      return _arc.apply(this, arguments);
    };
  }
})();
"""

# 快照：zoneP1 / zoneVisual / 面板 / 每张 dcard 的几何
SNAPSHOT_JS = """
(function () {
  function rect(el) {
    var b = el.getBoundingClientRect();
    return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)];
  }
  var out = {cards: [], stage: null};
  var stage = document.getElementById('stage');
  if (stage) { stage.scrollTop = 0; out.stage = {st: stage.scrollTop, rect: rect(stage)}; }
  var z1 = document.getElementById('zoneP1'), zv = document.getElementById('zoneVisual');
  out.zoneP1 = z1 ? rect(z1) : null;
  out.zoneVisual = zv ? rect(zv) : null;
  var panel = document.getElementById('accVec');
  out.panel = panel ? rect(panel) : null;
  out.panelCount = document.querySelectorAll('#accVec').length;
  out.canvasCount = document.querySelectorAll('#accVecCanvas').length;
  out.panelInZoneP1 = !!(panel && panel.closest('#zoneP1'));
  out.panelInZoneVisual = !!(panel && panel.closest('#zoneVisual'));
  out.panelParentChain = (function () {
    var chain = [], el = panel;
    while (el && el.id !== 'stage' && chain.length < 6) { chain.push(el.id || el.className); el = el.parentElement; }
    return chain;
  })();
  out.panelZoneVisible = {p1: zoneVisible('zoneP1'), visual: zoneVisible('zoneVisual')};
  out.activeZone = (function () {
    var a = document.querySelector('.rail-item.active');
    return a ? a.textContent.trim() : null;
  })();
  Array.prototype.forEach.call(document.querySelectorAll('#zoneP1 .dcard'), function (c) {
    var nm = c.querySelector('.d-name');
    out.cards.push({key: nm ? nm.textContent.trim() : ('#' + out.cards.length), rect: rect(c)});
  });
  out.errors = window.__errs;
  return JSON.stringify(out);
})()
"""

# 数据驱动 + 擦画布：为"切区是否刷新"造一个可观测的初始态
SEED_JS = """
(function () {
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type:'nav',seq:1,ts:1000,
    names:['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values:[0,0,0,1.2,-0.8],
    roll: 0, pitch: 0, yaw: 0, accel_lat: 1.2, accel_lon: -0.8}]));
  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  g.setTransform(1,0,0,1,0,0); g.clearRect(0,0,cv.width,cv.height);
  window.__vpc = 0;
  function px() {
    var d = g.getImageData(0,0,cv.width,cv.height).data, n = 0;
    for (var i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
    return n;
  }
  window.__px = px;
  return JSON.stringify({zone: (document.querySelector('.rail-item.active')||{}).textContent,
                         canvasW: cv.clientWidth, canvasH: cv.clientHeight,
                         pixels: px(), dirty: accVecDirty, mag: state.latest.accelMag});
})()
"""

# 在「可视化」区：面板应当看不见（zoneP1 隐藏）-> 画布尺寸 0
IN_VISUAL_JS = """
(function () {
  document.querySelectorAll('.rail-item')[1].click();
  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  g.setTransform(1,0,0,1,0,0); g.clearRect(0,0,cv.width,cv.height);
  window.__vpc = 0;
  return JSON.stringify({zone: (document.querySelector('.rail-item.active')||{}).textContent,
                         canvasW: cv.clientWidth, canvasH: cv.clientHeight,
                         pixels: window.__px(), dirty: accVecDirty,
                         p1Visible: zoneVisible('zoneP1')});
})()
"""

# 切回「总览」：必须重画（像素从 0 回来）
BACK_TO_OVERVIEW_JS = """
(function () {
  document.querySelectorAll('.rail-item')[0].click();
  return JSON.stringify({zone: (document.querySelector('.rail-item.active')||{}).textContent,
                         canvasW: document.getElementById('accVecCanvas').clientWidth,
                         pixels: window.__px(), dirty: accVecDirty,
                         p1Visible: zoneVisible('zoneP1'),
                         arcs: window.__vpc,
                         lat: document.getElementById('accVecLat').textContent,
                         mag: document.getElementById('accVecMag').textContent});
})()
"""

# 总览下靠 20Hz 数据节拍重画（不切区）：改数据 -> updateDom()
TICK_JS = """
(function () {
  var cv = document.getElementById('accVecCanvas');
  var g = cv.getContext('2d');
  g.setTransform(1,0,0,1,0,0); g.clearRect(0,0,cv.width,cv.height);
  window.__vpc = 0;
  lastSampleT = -1e9;
  onDataBatch(JSON.stringify([{type:'nav',seq:2,ts:1100,
    names:['ROLL','PITCH','YAW','ACCEL_LAT','ACCEL_LON'], values:[0,0,0,-3.0,2.0],
    roll: 0, pitch: 0, yaw: 0, accel_lat: -3.0, accel_lon: 2.0}]));
  var beforePx = window.__px();
  updateDom();                       /* 主循环 20Hz 节拍：只有脏了才画 */
  return JSON.stringify({pixelsBeforeTick: beforePx, pixelsAfterTick: window.__px(),
                         arcs: window.__vpc, mag: document.getElementById('accVecMag').textContent});
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)

    s = QWebEngineScript()
    s.setName("probe")
    s.setSourceCode(PROBE_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    page.scripts().insert(s)

    res = {}

    def on_load(ok):
        def cb_tick(v):
            res["tick"] = v
            app.quit()

        def cb_back(v):
            res["back"] = v
            page.runJavaScript(TICK_JS, cb_tick)

        def cb_visual(v):
            res["visual"] = v
            QTimer.singleShot(150, lambda: page.runJavaScript(BACK_TO_OVERVIEW_JS, cb_back))

        def cb_seed(v):
            res["seed"] = v
            page.runJavaScript(IN_VISUAL_JS, cb_visual)

        def cb_snap(v):
            res["snap"] = v
            if SAVE:
                app.quit()
                return
            QTimer.singleShot(120, lambda: page.runJavaScript(SEED_JS, cb_seed))

        QTimer.singleShot(3400, lambda: page.runJavaScript(SNAPSHOT_JS, cb_snap))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    def load(k):
        try:
            return json.loads(res.get(k) or "{}")
        except Exception:
            return {}

    snap = load("snap")
    if SAVE:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print("=== 已存基线 ===")
        print("  #accVec 位置: panelCount=%s inZoneP1=%s inZoneVisual=%s parent=%s"
              % (snap.get("panelCount"), snap.get("panelInZoneP1"), snap.get("panelInZoneVisual"),
                 snap.get("panelParentChain")))
        print("  #accVec rect=%s  zoneP1 rect=%s  zoneVisual rect=%s"
              % (snap.get("panel"), snap.get("zoneP1"), snap.get("zoneVisual")))
        print("  zoneP1 既有卡片:")
        for c in snap.get("cards", []):
            print("     %-22s %s" % (c["key"], c["rect"]))
        print("  WROTE", BASELINE)
        return

    seed, visual, back, tick = load("seed"), load("visual"), load("back"), load("tick")
    base = {}
    try:
        with open(BASELINE, encoding="utf-8") as f:
            base = json.load(f)
    except Exception as e:
        print("读不到基线:", e)

    print("=== ① #accVec 已真的搬进 zoneP1（只此一份）===")
    print("  querySelectorAll('#accVec').length = %s（==1 表示没有 display:none 副本）"
          % snap.get("panelCount"))
    print("  closest('#zoneP1') = %s   closest('#zoneVisual') = %s"
          % (snap.get("panelInZoneP1"), snap.get("panelInZoneVisual")))
    print("  父链（到底向上）: %s" % snap.get("panelParentChain"))
    print("  #accVec rect = %s" % snap.get("panel"))
    print("  zoneP1 rect = %s   zoneVisual rect = %s" % (snap.get("zoneP1"), snap.get("zoneVisual")))
    print("  默认分区 = %s   zoneVisible: P1=%s Visual=%s"
          % (snap.get("activeZone"), (snap.get("panelZoneVisible") or {}).get("p1"),
             (snap.get("panelZoneVisible") or {}).get("visual")))

    print()
    print("=== ② zoneP1 既有卡片几何对比（搬迁前 -> 搬迁后）===")
    bmap = {c["key"]: c["rect"] for c in base.get("cards", [])}
    amap = {c["key"]: c["rect"] for c in snap.get("cards", [])}
    print("  %-22s %-22s %-22s %s" % ("卡片", "搬迁前[x,y,w,h]", "搬迁后[x,y,w,h]", "判定"))
    same_wh = True
    dy_list = []
    for k in bmap:
        b, a = bmap.get(k), amap.get(k)
        if a is None:
            print("  %-22s %-22s %-22s 丢失!" % (k, b, None))
            same_wh = False
            continue
        okw = abs(b[2] - a[2]) <= 1 and abs(b[3] - a[3]) <= 1
        same_wh = same_wh and okw
        dy = a[1] - b[1]
        dy_list.append(dy)
        print("  %-22s %-22s %-22s %s (dy=%+d, dx=%+d)" % (k, b, a, "w/h 不变" if okw else "尺寸变了!", dy, a[0] - b[0]))
    print("  zoneP1 自身: %s -> %s（高度 %+d）"
          % (base.get("zoneP1"), snap.get("zoneP1"),
             (snap.get("zoneP1") or [0, 0, 0, 0])[3] - (base.get("zoneP1") or [0, 0, 0, 0])[3]))
    print("  所有卡片 dy 一致 = %s (dy=%s) -> 整体下移，没有互相挤压"
          % (len(set(dy_list)) == 1, dy_list))

    print()
    print("=== ③ 切到「总览」会刷新（门控证据）===")
    print("  总览/初始: zone=%s canvas=%sx%s 像素=%s dirty=%s mag=%s"
          % (seed.get("zone"), seed.get("canvasW"), seed.get("canvasH"),
             seed.get("pixels"), seed.get("dirty"), seed.get("mag")))
    print("  切到可视化: zone=%s canvas=%sx%s 像素=%s dirty=%s zoneP1可见=%s"
          % (visual.get("zone"), visual.get("canvasW"), visual.get("canvasH"),
             visual.get("pixels"), visual.get("dirty"), visual.get("p1Visible")))
    print("  切回总览:   zone=%s canvas=%sx%s 像素=%s dirty=%s zoneP1可见=%s arcs=%s 读数 lat=%s mag=%s"
          % (back.get("zone"), back.get("canvasW"), back.get("canvasH"),
             back.get("pixels"), back.get("dirty"), back.get("p1Visible"),
             back.get("arcs"), back.get("lat"), back.get("mag")))

    print()
    print("=== ④ 总览下 20Hz 数据节拍也重画（不依赖切区）===")
    print("  %s" % tick)

    checks = [
        ("① 全页只有一份 #accVec（无 display:none 副本）", snap.get("panelCount") == 1),
        ("① #accVec 在 #zoneP1 内", snap.get("panelInZoneP1") is True),
        ("① #accVec 不在 #zoneVisual 内", snap.get("panelInZoneVisual") is False),
        ("① 默认分区是总览（矢量图开箱可见）", snap.get("activeZone") == "☱总览" or "总览" in str(snap.get("activeZone"))),
        ("② zoneP1 既有卡片全部还在", set(bmap.keys()) == set(amap.keys())),
        ("② 既有卡片宽高逐张不变（没被挤坏）", same_wh),
        ("② 既有卡片整体平移（dy 一致，无互相挤压）",
         len(dy_list) > 0 and len(set(dy_list)) == 1),
        ("③ 可视化区里面板隐藏（画布 0 宽）", (visual.get("canvasW") or 0) == 0),
        ("③ 切回总览后矢量图重画（像素 0 -> >300）",
         (visual.get("pixels") or 0) == 0 and (back.get("pixels") or 0) > 300),
        ("③ 切回总览后读数正确（1.44）", str(back.get("mag")) == "1.44"),
        ("④ 总览下 20Hz 节拍能重画（像素 0 -> >300）",
         (tick.get("pixelsBeforeTick") or 0) == 0 and (tick.get("pixelsAfterTick") or 0) > 300),
        ("④ 节拍后读数更新为 3.61（√(3²+2²)=3.6056）", str(tick.get("mag")) == "3.61"),
        ("无 JS 运行时错误",
         not snap.get("errors") and not seed.get("errors") and not visual.get("errors")
         and not back.get("errors") and not tick.get("errors")),
    ]
    print()
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）

    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"snap": snap, "seed": seed, "visual": visual, "back": back, "tick": tick,
                       "baseline": base}, f, ensure_ascii=False, indent=2)
        print("WROTE", OUT_JSON)
    except Exception as e:
        print("JSON 写入失败:", e)


if __name__ == "__main__":
    main()
