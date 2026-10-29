# -*- coding: utf-8 -*-
"""_pwr_check.py —— 电池功率（派生量 volt × amp / 1000 kW）与阈值告警的实测验收

覆盖：
  ① 派生信号真的进了 SIGS / state.buf / 趋势通道 chip / 可视化通道列表（不是只在卡片里算一下）
  ② 公式正确：443.9 V × 181 A → 80.35 kW，**必须变红**
     443.9 V × 150 A → 66.58 kW，不红（绿色"正常"）
  ③ 阈值来自配置：默认 80；applyUiConfig({ui:{powerWarnKw:100}}) 后 80.35 kW 不再红
  ④ 抖动抑制：80 kW 附近 200 帧 ±0.6 kW 抖动 → 告警态**零次翻转**（B08 同族要求）
  ⑤ 最短驻留：亮起后掉到 66 kW，600ms 内不灭、之后才灭（亮得快、灭得慢）
  ⑥ 峰值保持 + 点击清零；峰值超阈值也转红
  ⑦ 无数据 -> 占位 "—" 且不告警
  ⑧ 无 JS 运行时错误

用法（offscreen）：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_pwr_check.py
输出：tools/_pwr_check.json + 逐条 OK/FAIL
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox --disable-software-rasterizer"

from PyQt6.QtCore import QUrl, QTimer          # noqa: E402
from PyQt6.QtWidgets import QApplication       # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWebEngineCore import QWebEnginePage     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "tools", "_pwr_check.json")

# ★ 全程用**假时钟**（覆盖 performance.now）：
#   updatePowerDom 的"最短驻留 600ms"必须能确定性地验证，不能靠真实 sleep 去赌。
HARNESS = r"""
(function(){
  var R = {steps:[], fails:[], errs:[]};
  window.addEventListener('error', function(e){ R.errs.push(String(e.message)); });
  try{ setDemo(false); }catch(e){}
  clearTelemetry();

  var T = 1000000;
  var realNow = performance.now.bind(performance);
  performance.now = function(){ return T; };

  function feed(v, a, extra){
    var items = [{type:'motor', seq:1, ts:1, speed:12.3, rpm:3000, throttle:50,
                  torque:20, temp:45, volt:v, amp:a}];
    if (extra) items.push(extra);
    onDataBatch(JSON.stringify(items));
    updateDom();
  }
  function adv(ms){ T += ms; }
  /* 每个独立用例都从"干净的告警态"开始：clearTelemetry 会复位 PWR_ON/PWR_NEAR/峰值，
     T 前进 1s 让上一轮的最短驻留窗口彻底过期（否则 600ms 驻留会把上一条用例的红带进来）。 */
  function reset(){ clearTelemetry(); T += 1000; updateDom(); }
  function cardCls(){ return document.getElementById('pwrCard').className; }
  function snap(tag){
    var n = document.getElementById('pwrNum').textContent;
    var s = document.getElementById('pwrState').textContent;
    var o = {tag:tag, cls:cardCls(), num:n, state:s,
             bar:(document.getElementById('pwrBar').className||''),
             peak:document.getElementById('pwrPeakNum').textContent,
             peakCls:document.getElementById('pwrPeak').className,
             raw:+((state.latest.power||0).toFixed(4)),
             warnKw:PWR_WARN_KW, fs:PWR_FS,
             tick:document.getElementById('pwrTick').style.left,
             limitLbl:document.getElementById('pwrLimitLbl').textContent,
             fsLbl:document.getElementById('pwrFsLbl').textContent};
    R.steps.push(o);
    return o;
  }

  /* ---------- ① 派生信号接线 ---------- */
  R.wired = {
    inSIGS: !!SIGS.power,
    unit: SIGS.power && SIGS.power.unit,
    fmt: SIGS.power && SIGS.power.fmt,
    min: SIGS.power && SIGS.power.min,
    max: SIGS.power && SIGS.power.max,
    inBuf: !!state.buf.power,
    chip: !!document.querySelector('#chipBar .chip[data-id="power"]'),
    visChannel: !!document.querySelector('#visChannels .vc-item[data-sig="power"]'),
    sigCount: Object.keys(SIGS).length
  };

  /* ---------- ② 公式 + 阈值 ---------- */
  var a1 = snap('阈值默认/无数据');
  R.noData = a1;

  reset();
  adv(60); feed(443.9, 181);            /* 80.3459 kW */
  var s2 = snap('443.9V x 181A = 80.35kW');

  reset();
  adv(60); feed(443.9, 150);            /* 66.585 kW（干净起点，不带上一帧的驻留） */
  var s3 = snap('443.9V x 150A = 66.59kW');

  /* ---------- ④ 阈值附近抖动：零翻转 ---------- */
  adv(60); feed(443.9, 181);            /* 先进入告警态 */
  var base = cardCls();
  var flips = 0, prev = base;
  for (var i = 0; i < 200; i++){
    adv(50);
    /* 80.3 kW 与 79.7 kW 交替（都在 ±0.6 kW 内抖动） */
    var amp = (i % 2 === 0) ? (80.3 * 1000 / 443.9) : (79.7 * 1000 / 443.9);
    feed(443.9, amp);
    var c = cardCls();
    if (c !== prev){ flips++; prev = c; }
  }
  R.flicker = {baseCls:base, flips:flips, endCls:cardCls(), frames:200};

  /* ---------- ⑤ 最短驻留（亮得快、灭得慢） ---------- */
  adv(60); feed(443.9, 216.3);          /* 96.0 kW -> 亮 */
  var onT = {t:T, cls:cardCls()};
  adv(50);  feed(443.9, 150);           /* 立刻掉到 66.6 kW */
  var d1 = {dt:50, cls:cardCls()};
  adv(250); feed(443.9, 150);           /* 累计 300ms */
  var d2 = {dt:300, cls:cardCls()};
  adv(400); feed(443.9, 150);           /* 累计 700ms > 600ms */
  var d3 = {dt:700, cls:cardCls()};
  R.hold = {on:onT, at50:d1, at300:d2, at700:d3};

  /* ---------- ⑥ 峰值保持 / 清零 ---------- */
  reset();
  adv(60); feed(443.9, 216.3);          /* 96.0 kW */
  var pk1 = snap('峰值 96.0');
  /* "清零"的语义 = **从此重新开始记峰值**：当前值会立刻成为新峰值（96 被忘掉）。
     实测：清零后喂 66.6 kW -> 峰值显示 66.6 而不是 96.0。 */
  resetPwrPeak();
  adv(60); feed(443.9, 150);
  var pk2 = snap('清零后重新记峰值');

  /* ---------- ⑦ 阈值改配置 ---------- */
  applyUiConfig({ui:{powerWarnKw:100}});
  reset();
  adv(60); feed(443.9, 181);            /* 80.35 kW，阈值 100 -> 不该红 */
  var cfg1 = snap('阈值改 100：80.35kW 不应告警');
  applyUiConfig({ui:{powerWarnKw:80}});
  reset();
  adv(60); feed(443.9, 181);
  var cfg2 = snap('阈值改回 80：80.35kW 应告警');
  /* 非法阈值：0 / 负数 / 非数字 -> 回退兜底 80 */
  applyUiConfig({ui:{powerWarnKw:0}});
  var bad1 = {warnKw:PWR_WARN_KW};
  applyUiConfig({ui:{powerWarnKw:-5}});
  var bad2 = {warnKw:PWR_WARN_KW};
  applyUiConfig({ui:{powerWarnKw:'abc'}});
  var bad3 = {warnKw:PWR_WARN_KW};
  applyUiConfig({ui:{powerWarnKw:80}});
  R.cfg = {at100:cfg1, back80:cfg2, bad0:bad1, badNeg:bad2, badStr:bad3};

  /* ---------- ⑧ 无数据复位 ---------- */
  clearTelemetry(); updateDom();
  R.cleared = snap('清空后');

  /* ---------- ⑨ 动力区原有卡片都在 ---------- */
  function nv(id){ var e = document.getElementById(id); return e ? e.textContent : null; }
  R.zoneP2 = {
    hcards: document.querySelectorAll('#zoneP2 .hcard').length,
    scards: document.querySelectorAll('#zoneP2 .scard').length,
    speed: nv('speedNum'), rpm: nv('rpmNum'), thr: nv('thrNum'), tq: nv('tqNum'),
    temp: nv('tempNum'), volt: nv('voltNum'), amp: nv('ampNum'), pwr: nv('pwrNum'),
    pwrcardInP2: !!document.getElementById('pwrCard').closest('#zoneP2'),
    pwrCardCount: document.querySelectorAll('#pwrCard').length,
    /* 趋势里真能画：把 power 勾上、填两个点，检查曲线像素 */
    n: state.buf.power ? state.buf.power.length : -1
  };

  /* 趋势曲线：勾选 power、喂一串点、走主循环同一条绘制路径，再数趋势画布像素 */
  try{
    reset();
    state.visible.add('power');
    buildChips();
    for (var k = 0; k < 40; k++){
      adv(50);
      feed(430 + (k % 7) * 4, 60 + (k % 13) * 9);      /* 20~80 kW 之间来回走 */
    }
    trendLayer.sKey = ''; trendDirty = true;
    paintLayer(trendLayer, drawTrend);                 /* 与主循环 2166 行完全同一条路径 */
    var tc = document.getElementById('trendCanvas');
    var g = tc.getContext('2d');
    var d = g.getImageData(0, 0, tc.width, tc.height).data, npx = 0;
    for (var q = 3; q < d.length; q += 4) if (d[q] > 0) npx++;
    R.trendPixels = npx;
    R.trendBufLen = state.buf.power.length;
    R.trendHasChipOn = !!document.querySelector('#chipBar .chip.on[data-id="power"]');
  }catch(e){ R.trendErr = String(e); }

  performance.now = realNow;
  R.errors = window.__errs || [];
  R.errs = R.errs.concat(window.__errs || []);
  return JSON.stringify(R);
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def on_load(ok):
        page.runJavaScript(HARNESS, cb)

    def cb(v):
        res["r"] = v
        app.quit()

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.show()
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(60000, app.quit)
    app.exec()

    raw = res.get("r")
    if not raw:
        print("FAIL 页面没有返回结果")
        return 1
    try:
        R = json.loads(raw)
    except Exception as e:
        print("FAIL 结果不是 JSON:", e, str(raw)[:400])
        return 1

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)

    st = {s["tag"]: s for s in R["steps"]}
    w = R["wired"]
    checks = [
        ("① power 进了 SIGS（单位 kW / 1 位小数）", w["inSIGS"] and w["unit"] == "kW" and w["fmt"] == 1),
        ("① power 进了 state.buf（趋势/可视化同一份缓冲）", w["inBuf"]),
        ("① 趋势通道里有 power 的 chip", w["chip"]),
        ("① 可视化「数据通道」里有 power", w["visChannel"]),
        ("① 信号总数 = 24（原 23 + 电池功率）", w["sigCount"] == 24),
        ("⑦ 无数据时占位 '—' 且不告警",
         st["阈值默认/无数据"]["num"] == "—" and "pwr-warn" not in st["阈值默认/无数据"]["cls"]),
        ("② 443.9V × 181A = 80.3459 kW（读数 80.3）",
         abs(st["443.9V x 181A = 80.35kW"]["raw"] - 80.3459) < 0.01
         and st["443.9V x 181A = 80.35kW"]["num"] == "80.3"),
        ("② 80.35 kW > 80 -> 卡片变红（class 含 pwr-warn）",
         "pwr-warn" in st["443.9V x 181A = 80.35kW"]["cls"]),
        ("② 80.35 kW -> 状态显示「超限」", st["443.9V x 181A = 80.35kW"]["state"] == "超限"),
        ("② 80.35 kW -> 条形也转红（fill-pwr-over）",
         "fill-pwr-over" in st["443.9V x 181A = 80.35kW"]["bar"]),
        ("② 443.9V × 150A = 66.585 kW（读数 66.6）",
         abs(st["443.9V x 150A = 66.59kW"]["raw"] - 66.585) < 0.01
         and st["443.9V x 150A = 66.59kW"]["num"] == "66.6"),
        ("② 66.59 kW 不告警（绿色「正常」）",
         "pwr-warn" not in st["443.9V x 150A = 66.59kW"]["cls"]
         and st["443.9V x 150A = 66.59kW"]["state"] == "正常"
         and "fill-pwr-ok" in st["443.9V x 150A = 66.59kW"]["bar"]),
        ("④ 80±0.6 kW 抖动 200 帧：告警态零次翻转", R["flicker"]["flips"] == 0),
        ("⑤ 亮得快：超限当帧即红（96.0 kW）", "pwr-warn" in R["hold"]["on"]["cls"]),
        ("⑤ 灭得慢：降到 66.6 kW 后 50ms 仍红", "pwr-warn" in R["hold"]["at50"]["cls"]),
        ("⑤ 灭得慢：累计 300ms 仍红（<600ms 驻留）", "pwr-warn" in R["hold"]["at300"]["cls"]),
        ("⑤ 600ms 驻留到期后熄灭", "pwr-warn" not in R["hold"]["at700"]["cls"]),
        ("⑥ 峰值保持：96.0 kW", st["峰值 96.0"]["peak"] == "96.0"),
        ("⑥ 峰值超阈值 -> 也转红", "pwr-peak-hot" in st["峰值 96.0"]["peakCls"]),
        ("⑥ 清零后重新记峰值（96.0 被忘掉，显示新的 66.6，不再红）",
         st["清零后重新记峰值"]["peak"] == "66.6"
         and "pwr-peak-hot" not in st["清零后重新记峰值"]["peakCls"]),
        ("⑦ 阈值来自配置：改成 100 后 80.35 kW 不告警",
         R["cfg"]["at100"]["warnKw"] == 100 and "pwr-warn" not in R["cfg"]["at100"]["cls"]
         and R["cfg"]["at100"]["limitLbl"] == "上限 100"),
        ("⑦ 阈值刻度/满量程跟着变（100 -> 66.67% / 150 kW）",
         R["cfg"]["at100"]["tick"] == "66.67%" and R["cfg"]["at100"]["fs"] == 150.0),
        ("⑦ 改回 80 后 80.35 kW 又告警", "pwr-warn" in R["cfg"]["back80"]["cls"]),
        ("⑦ 非法阈值 0/-5/'abc' 一律回退 80",
         R["cfg"]["bad0"]["warnKw"] == 80 and R["cfg"]["badNeg"]["warnKw"] == 80
         and R["cfg"]["badStr"]["warnKw"] == 80),
        ("⑧ 清空数据后占位 '—'、无告警",
         R["cleared"]["num"] == "—" and "pwr-warn" not in R["cleared"]["cls"]),
        ("⑨ 动力区原有卡片全在（4 张 hcard + 4 张 scard）",
         R["zoneP2"]["hcards"] == 4 and R["zoneP2"]["scards"] == 4),
        ("⑨ 功率卡在 #zoneP2 内且全页只有一份",
         R["zoneP2"]["pwrcardInP2"] and R["zoneP2"]["pwrCardCount"] == 1),
        ("⑨ 趋势画布上真的画出了曲线（像素 > 300）", (R.get("trendPixels") or 0) > 300),
        ("⑨ 趋势里 power 的缓冲真的在涨（> 20 点）+ chip 是选中态",
         (R.get("trendBufLen") or 0) > 20 and R.get("trendHasChipOn") is True),
        ("无 JS 运行时错误", not R.get("errors") and not R.get("errs")),
    ]

    print("=== 派生量接线 ===")
    print("  ", json.dumps(w, ensure_ascii=False))
    print("=== 关键读数 ===")
    for s in R["steps"]:
        print("  %-30s num=%-7s state=%-6s cls=%-18s raw=%-9s peak=%s"
              % (s["tag"], s["num"], s["state"], s["cls"], s["raw"], s["peak"]))
    print("=== 抖动/驻留 ===")
    print("  200 帧 ±0.6kW 抖动 -> 告警态翻转 %d 次" % R["flicker"]["flips"])
    print("  驻留: 亮=%s 50ms=%s 300ms=%s 700ms=%s"
          % ("红" if "pwr-warn" in R["hold"]["on"]["cls"] else "绿",
             "红" if "pwr-warn" in R["hold"]["at50"]["cls"] else "绿",
             "红" if "pwr-warn" in R["hold"]["at300"]["cls"] else "绿",
             "红" if "pwr-warn" in R["hold"]["at700"]["cls"] else "绿"))
    print("  阈值切换:", R["cfg"]["at100"]["warnKw"], "/", R["cfg"]["at100"]["limitLbl"],
          "/ tick", R["cfg"]["at100"]["tick"], "/ fs", R["cfg"]["at100"]["fs"])
    print("  动力区:", json.dumps(R["zoneP2"], ensure_ascii=False))
    print("  趋势像素:", R.get("trendPixels"), R.get("trendErr") or "")
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    print("WROTE", OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
