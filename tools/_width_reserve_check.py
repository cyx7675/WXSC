# -*- coding: utf-8 -*-
"""task-8 F5/F6 验证：计数位宽锁定的「静态位移」与「窄窗省略号」。

思路（全部在同一次页面加载内做 A/B，不依赖外部基线快照）：
  · 每个视口宽度下测两组：
      ON  = 当前 CSS（有 min-width 预留）
      OFF = 运行时注入 `#fpsTag,#rxFoot,#rxTag{min-width:0}`（等于没做预留的旧样式）
  · 静态位移 = ON 与 OFF 的既有元素 left 之差 —— 这是"预留"换来最小位移的真实代价；
  · 新增省略号 = ON 下被 ellipsis 截断、而 OFF 下没被截断的元素（scrollWidth > clientWidth）；
  · 跳变位移 = 计数从 0 变到**现实上限**（fps 999 / rx 9999）时其它元素是否移动（应为 0）。
另附：把计数打到 6 位极端值（123456）时的跳变情况，仅作参考。

★ 比较一律**按子元素下标对位**，不用 id/class 建字典 —— 底栏有 3 个 `.sep`、2 个 `.foot-opt`，
  按类名建字典会互相覆盖、把位移掩盖掉（这个坑在 _footer_replay_probe 里已经踩过一次）。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_width_reserve_check.py
"""
import os
import sys
import json

# ★ 控制台编码自保护（task-8 F8）：Windows GBK 控制台下，print 里出现 ⏸/▶/★ 这类字符会
# 抛 UnicodeEncodeError 直接崩进程；而且崩在打印阶段会让"后面才写 JSON"的取证代码根本执行不到。
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

WIDTHS = [1600, 1440, 1280, 1100]
OUT_JSON = os.path.join(HERE, "tools", "_width_reserve_result.json")
# 现实上限：fps 几十~999；RX 每秒 payload 元素数（20Hz×3≈60，115200 下≈400，921600 下≈3000）
REAL_FPS = "999"
REAL_RX = "9999"
EXTREME = "123456"

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
  function kids(sel){
    var box = document.querySelector(sel);
    if (!box) return [];
    return Array.prototype.map.call(box.children, function (el, i) {
      var b = el.getBoundingClientRect();
      return {i: i, k: (el.id || el.className || el.tagName),
              left: Math.round(b.left), w: Math.round(b.width),
              cut: el.scrollWidth > el.clientWidth + 1};   /* true = 被 ellipsis 截断 */
    });
  }
  return JSON.stringify({width: window.innerWidth, foot: kids('#footer'), top: kids('#topbar'),
                         errs: window.__errs});
})()
"""


def set_counters(fps, rx):
    return ("(function(){ setNum(document.getElementById('fpsTag'),%s);"
            "setNum(document.getElementById('rxFoot'),%s);"
            "setNum(document.getElementById('rxTag'),%s); return 'ok'; })()"
            % (json.dumps(fps), json.dumps(rx), json.dumps(rx)))


INJECT_OFF = ("(function(){ if (document.getElementById('wres_off')) return 'ok';"
              "var s=document.createElement('style'); s.id='wres_off';"
              "s.textContent='#fpsTag,#rxFoot,#rxTag{min-width:0 !important}';"
              "document.head.appendChild(s); return 'ok'; })()")
REMOVE_OFF = "(function(){ var s=document.getElementById('wres_off'); if (s) s.remove(); return 'ok'; })()"


def rects(m, box):
    return [(c["i"], c["k"], c["left"], c["w"]) for c in m.get(box, [])]


def moved(a, b, box):
    """按下标对位比较；返回 [(k, before_left, after_left)]。长度不一致返回 None。"""
    ra, rb = rects(a, box), rects(b, box)
    if not ra or not rb or len(ra) != len(rb):
        return None
    out = []
    for x, y in zip(ra, rb):
        if (x[2], x[3]) != (y[2], y[3]):
            out.append((x[1], x[2], y[2]))
    return out


def cut_set(m):
    return {(box, c["i"], c["k"]) for box in ("foot", "top") for c in m.get(box, []) if c["cut"]}


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

    data = {}

    def record(tag, then):
        def cb(v):
            d = json.loads(v)
            data.setdefault(str(d.get("width")), {})[tag] = d
            then()
        page.runJavaScript(MEASURE_JS, cb)

    def build_seq():
        seq = []
        for w in WIDTHS:
            seq.append(("RESIZE", w))
            seq.append(("JS", set_counters("0", "0")))
            seq.append(("MEASURE", "ON0"))
            seq.append(("JS", set_counters(REAL_FPS, REAL_RX)))
            seq.append(("MEASURE", "ONMAX"))
            seq.append(("JS", INJECT_OFF))
            seq.append(("JS", set_counters("0", "0")))
            seq.append(("MEASURE", "OFF0"))
            seq.append(("JS", set_counters(REAL_FPS, REAL_RX)))
            seq.append(("MEASURE", "OFFMAX"))
            seq.append(("JS", REMOVE_OFF))
            seq.append(("JS", set_counters("0", "0")))
            seq.append(("JS", set_counters(EXTREME, EXTREME)))
            seq.append(("MEASURE", "ONEXTREME"))
            seq.append(("JS", set_counters("0", "0")))
        return seq

    seq = build_seq()

    def run(i):
        if i >= len(seq):
            finish()
            return
        kind, payload = seq[i]
        if kind == "RESIZE":
            view.resize(payload, 900)
            QTimer.singleShot(280, lambda: run(i + 1))
            return
        if kind == "MEASURE":
            record(payload, lambda: QTimer.singleShot(120, lambda: run(i + 1)))
            return
        page.runJavaScript(payload, lambda _: QTimer.singleShot(120, lambda: run(i + 1)))

    def finish():
        out = {"widths": WIDTHS, "real": {"fps": REAL_FPS, "rx": REAL_RX},
               "extreme": EXTREME, "data": data}
        try:
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print("WROTE", OUT_JSON)
        except Exception as e:
            print("JSON 写入失败:", e)
        report()
        app.quit()

    def report():
        print("=== F5/F6：计数位宽锁定 —— 静态位移 / 窄窗省略号 / 跳变位移 ===")
        print("（ON=当前样式（有预留）；OFF=注入 min-width:0，等于没预留）")
        all_ok = True
        for w in WIDTHS:
            dd = data.get(str(w))
            if not dd or not all(k in dd for k in ("ON0", "ONMAX", "OFF0", "OFFMAX", "ONEXTREME")):
                print("  FAIL w=%s 采集不全（%s）" % (w, sorted(dd.keys()) if dd else "无"))
                all_ok = False
                continue

            # ① 静态位移：ON0 vs OFF0
            stat = []
            for box in ("foot", "top"):
                for x, y in zip(rects(dd["ON0"], box), rects(dd["OFF0"], box)):
                    if (x[2], x[3]) != (y[2], y[3]):
                        stat.append((box, x[1], "%s->%s" % (y[2], x[2]), x[2] - y[2]))

            # ② 跳变位移（现实上限 / 极端值）
            jf, jt = moved(dd["ON0"], dd["ONMAX"], "foot"), moved(dd["ON0"], dd["ONMAX"], "top")
            ef, et = moved(dd["ON0"], dd["ONEXTREME"], "foot"), moved(dd["ON0"], dd["ONEXTREME"], "top")

            # ③ 新增省略号
            new_cut = sorted(cut_set(dd["ON0"]) - cut_set(dd["OFF0"]))

            print()
            print("  ── 视口宽 %d（实际 innerWidth=%s）──" % (w, dd["ON0"].get("width")))
            print("     静态位移 %d 处:" % len(stat))
            for box, k, chg, d in stat:
                print("        [%s] %-16s %s  (%+dpx)" % (box, k, chg, d))
            print("     跳变位移 现实上限(fps%s/rx%s): 底栏=%s 顶栏=%s" % (REAL_FPS, REAL_RX, jf, jt))
            print("     新增省略号(ON有OFF无): %s" % (new_cut or "无"))
            print("     参考 极端值%s: 底栏=%s 顶栏=%s" % (EXTREME, ef, et))

            jump_ok = (jf == []) and (jt == [])
            cut_ok = (not new_cut) if w >= 1280 else True
            good = jump_ok and cut_ok
            print("     判定: 跳变零位移=%s / 无新增省略号=%s %s"
                  % (jump_ok, not new_cut, "OK" if good else "FAIL"))
            all_ok = all_ok and good

        errs = [d.get("errs") for w in WIDTHS for d in (data.get(str(w)) or {}).values() if d.get("errs")]
        print()
        print("JS 错误:", errs or "无")
        print("结果:", "全部通过" if all_ok else "存在失败")
        sys.exit(0 if all_ok else 1)   # ★ 报 FAIL 必须退非零（与判定**同一作用域**）

    def on_load(ok):
        QTimer.singleShot(3000, lambda: run(0))

    page.loadFinished.connect(on_load)
    view.resize(WIDTHS[0], 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(180000, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
