# -*- coding: utf-8 -*-
"""_t30_grab_real.py —— task-30 的**真机窗口**验证（lead 明确要求：不能只在 Chrome 里看）

做法：不设 QT_QPA_PLATFORM（真窗口），跑真 QtWebEngine：
  1) 喂一段真机量级数据（40 槽历史），让环形/条/热条都有值；
  2) 在**真机窗口**里执行同一份量测 JS（tools/_t30_bars.js）：
     条形 y 坐标分组 + **刻度标签两两包围盒相交计数**（必须 0）；
  3) `QScreen.grabWindow(winId)` 抓 OS 合成后的整窗位图 → tools/_t30/real_after.png
     （与 Chrome 版并排，供 lead 复核）；
  4) 再切到"无数据"态，抓一张 tools/_t30/real_empty.png 验证 D12 骨架态。

用法：<venv>\\python.exe tools\\_t30_grab_real.py          （不要设 QT_QPA_PLATFORM）
"""
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(HERE, "tools", "_t30")
MEASURE_JS = io.open(os.path.join(HERE, "tools", "_t30_bars.js"), encoding="utf-8").read()

FEED = """(function(){
  try{ var d=document.getElementById('demoMode'); if(d){ d.checked=false; } setDemo(false); }catch(e){}
  try{ clearTelemetry(); }catch(e){}
  try{
    for (var i=0;i<40;i++){
      var s=i/40;
      onDataBatch(JSON.stringify([
        {type:'motor',seq:i,ts:i*50,speed:24+8*Math.sin(s*6.28),rpm:4200+1600*Math.sin(s*6.28+1),
         throttle:70+20*Math.sin(s*6.28),torque:80+30*Math.sin(s*6.28+2),temp:60+8*Math.sin(s*3),
         volt:470-40*Math.sin(s*3),amp:130+40*Math.sin(s*3)},
        {type:'nav',seq:i,ts:i*50,names:['ROLL','PITCH','YAW'],values:[40,5,90],
         roll:8*Math.sin(s*6.28),pitch:5,yaw:90,accel_lat:1.2,accel_lon:-0.8}
      ]));
    }
    updateDom();
    if (typeof refreshMini === 'function') refreshMini();
  }catch(e){ window.__feedErr = e.message; }
  return 'ok';
})()"""

# 骨架态（D12）：清空数据 + 打上 frontend 约定的 class，验证 CSS 是否真的把"粗黑—"收干净
EMPTY = """(function(){
  try{ clearTelemetry(); }catch(e){}
  try{
    document.querySelectorAll('#zoneP2 .hcard,#zoneP2 .scard').forEach(function(c){ c.classList.add('is-empty'); });
    document.querySelectorAll('#speedNum,#rpmNum,#pwrNum,#tempNum,#voltNum,#ampNum,#thrNum,#tqNum').forEach(function(e){ e.classList.add('is-empty'); });
    document.querySelectorAll('#zoneP1 .dcard').forEach(function(c){ c.classList.add('is-empty'); });
    document.querySelectorAll('.ring-val .v').forEach(function(e){ e.classList.add('is-empty'); });
    updateDom();
    if (typeof refreshMini === 'function') refreshMini();
  }catch(e){ window.__emptyErr = e.message; }
  return 'ok';
})()"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    state = {"measure": None, "shots": []}

    def grab(tag):
        win = view.window()
        scr = win.screen()
        if scr is None:
            print("FAIL win.screen() 为 None（无显示设备）")
            app.quit()
            return
        pm = scr.grabWindow(win.winId())
        path = os.path.join(OUTDIR, "real_%s.png" % tag)
        ok = pm.save(path)
        print("GRAB %-6s %dx%d saved=%s -> %s" % (tag, pm.width(), pm.height(), ok, path))
        state["shots"].append(path)

    def step_empty():
        view.page().runJavaScript(EMPTY, lambda r: QTimer.singleShot(700, lambda: (grab("empty"), finish())))

    def step_measure():
        def got(v):
            state["measure"] = v
            try:
                d = json.loads(v)
                print("=== 真机窗口量测（同一份 _t30_bars.js）===")
                print("  #stage.scrollHeight =", d.get("stageScrollH"))
                for b in d.get("bands", []):
                    print("  %-8s %s" % (b["band"], b["rect"]))
                for grp, title in (("powertrain", "动力区"), ("dynamics", "姿态区")):
                    ys = {}
                    for c in d.get(grp, []):
                        k = c["kids"]
                        bar = k.get(".bar-track") or k.get(".d-widget")
                        if bar:
                            ys.setdefault(round(bar[1]), []).append("%s(h=%s)" % (c["name"][:10], bar[3]))
                    print("  [%s] 条形 y 分组：" % title)
                    for y, names in sorted(ys.items()):
                        print("      y=%-6s %s" % (y, " / ".join(names)))
                    print("      → 不同 y 的组数 = %d" % len(ys))
                print("  ★ 刻度标签两两包围盒相交 = %d（必须 0）；标签总数 = %d"
                      % (len(d.get("labelClash") or []), len(d.get("labels") or [])))
                for cl in (d.get("labelClash") or [])[:6]:
                    print("      CLASH", cl)
                print("  D12 骨架态素材：", d.get("emptyState"))
            except Exception as e:
                print("解析量测失败:", e, str(v)[:200])
            QTimer.singleShot(300, step_empty)
        view.page().runJavaScript(MEASURE_JS, got)

    def finish():
        print("DONE")
        app.quit()

    QTimer.singleShot(3500, lambda: view.page().runJavaScript(FEED, lambda r: QTimer.singleShot(1200, lambda: (grab("after"), QTimer.singleShot(500, step_measure)))))
    QTimer.singleShot(45000, finish)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
