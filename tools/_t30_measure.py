# -*- coding: utf-8 -*-
"""_t30_measure.py —— 量测「所有条形的 y 坐标」（task-30 的量化对照）

为什么不用截图肉眼比：用户要的是**证明**同类条落在同一条线上。
本脚本用 QtWebEngine（与产品同一运行时）加载指定 index.html，
读每张卡里 [cap / bar / 带内条 / 刻度] 的 rect，按带分组打印。

用法：
    <venv>\\python.exe tools\\_t30_measure.py --html telemetry_web/index.html --out tools/_t30/after.json
    <venv>\\python.exe tools\\_t30_measure.py --html tools/_t30_before/index.html --out tools/_t30/before.json
    <venv>\\python.exe tools\\_t30_measure.py --compare tools/_t30/before.json tools/_t30/after.json
"""
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer          # noqa: E402
from PyQt6.QtWidgets import QApplication       # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEASURE_JS = io.open(os.path.join(HERE, "tools", "_t30_bars.js"), encoding="utf-8").read()

# 喂一点数据，让"有值"的条真的被画出来（几何与数据无关，但截图更真实）
FEED = """(function(){
  try{ setDemo(false); }catch(e){}
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
  }catch(e){}
  return 'ok';
})()"""


def measure(html, out):
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def done(v):
        res["v"] = v
        app.quit()

    page.loadFinished.connect(lambda ok: QTimer.singleShot(1200, lambda: page.runJavaScript(FEED, lambda r: QTimer.singleShot(400, lambda: page.runJavaScript(MEASURE_JS, done)))))
    view.resize(1600, 1000)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.abspath(html)))
    QTimer.singleShot(30000, app.quit)
    app.exec()
    raw = res.get("v")
    if not raw:
        print("FAIL 页面无返回:", html)
        return None
    d = json.loads(raw)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    print("WROTE", out)
    print("  #stage.scrollHeight =", d.get("stageScrollH"))
    for b in d.get("bands", []):
        print("  %-8s rect=%s" % (b["band"], b["rect"]))
    for grp, title in (("powertrain", "动力区"), ("dynamics", "姿态区")):
        print("--- %s ---" % title)
        print("  %-16s %-20s %-20s %-20s %-12s" % ("卡片", "卡片rect", "条rect", "带内条/热条", "刻度rect"))
        for c in d.get(grp, []):
            k = c["kids"]
            print("  %-16s %-20s %-20s %-20s %-12s" % (
                c["name"][:14], c["rect"], k.get(".bar-track") or "-",
                k.get(".heat") or k.get(".rev-row") or k.get(".d-widget") or "-",
                k.get(".bar-scale") or k.get(".d-scale") or "-"))
    return d


def compare(fb, fa):
    b = json.load(io.open(fb, encoding="utf-8"))
    a = json.load(io.open(fa, encoding="utf-8"))
    print("=== 条形 y 坐标对照（改前 → 改后）===")
    print("%-16s %-10s %-10s %-10s %s" % ("卡片", "改前 barY", "改后 barY", "改后 barH", "同带是否同一 y"))
    for grp, title in (("powertrain", "动力区"), ("dynamics", "姿态区")):
        bm = {c["name"]: c for c in b.get(grp, [])}
        print("-- %s" % title)
        ys = {}
        for c in a.get(grp, []):
            nm = c["name"]
            kb = (bm.get(nm) or {}).get("kids", {})
            barB = (kb.get(".bar-track") or kb.get(".d-widget") or [None] * 4)
            ka = c["kids"]
            barA = (ka.get(".bar-track") or ka.get(".d-widget") or [None] * 4)
            y0 = barB[1] if barB and barB[0] is not None else None
            y1 = barA[1] if barA and barA[0] is not None else None
            h1 = barA[3] if barA and barA[0] is not None else None
            print("%-16s %-10s %-10s %-10s %s" % (nm[:14], y0, y1, h1,
                  "y=%.0f" % y1 if y1 is not None else "-"))
            if y1 is not None:
                ys.setdefault(round(y1), []).append(nm[:12])
        if ys:
            groups = sorted(ys.items())
            print("   → 相同 y 的卡片分组：")
            for y, names in groups:
                print("      y=%-6s %s" % (y, " / ".join(names)))
            print("   → 不同 y 的条数 = %d（1 表示所有条都落在同一条水平线上）" % len(groups))


def main():
    if "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        return compare(sys.argv[i + 1], sys.argv[i + 2]) or 0
    html = "telemetry_web/index.html"
    out = os.path.join(HERE, "tools", "_t30", "measure.json")
    if "--html" in sys.argv:
        html = sys.argv[sys.argv.index("--html") + 1]
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    return 0 if measure(html, out) else 1


if __name__ == "__main__":
    sys.exit(main())
