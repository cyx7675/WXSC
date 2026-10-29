# -*- coding: utf-8 -*-
"""task-17 附加验收：① replayLoadingChanged 加载反馈 ② CSV 本地兜底表头 vs 后端表头。

用法：<venv>\\python.exe tools\\_task17_check.py
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
from PyQt6.QtWebEngineCore import QWebEnginePage

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

LOADING_JS = """
(function () {
  var out = {}, pos = document.getElementById('replayPos'), rr = document.getElementById('replayRange');
  function snap(tag) {
    return {tag: tag, pos: pos.textContent, disabled: rr.disabled,
            replaying: state.replaying, loading: state.replayLoading};
  }
  out.steps = [];
  /* 初态：未回放 */
  out.steps.push(snap('0-初态'));
  /* 加载开始（后端在开始解析时 emit (True, 文案)） */
  onReplayLoadingChanged(true, '正在解析… 12345 帧');
  out.steps.push(snap('1-loading(true)'));
  /* 加载中即使来了进度复位帧，也不该把 disabled 放开 */
  onReplayProgress(0, 0, 0, 0);
  out.steps.push(snap('2-loading + 复位帧'));
  /* 加载结束但还没开始回放 */
  onReplayLoadingChanged(false, '');
  out.steps.push(snap('3-loading(false) 未回放'));
  /* 回放中 -> 加载 -> 恢复（滑块必须先禁用、后恢复可用） */
  onReplayState(true, 'big.bin');
  out.steps.push(snap('4-回放中'));
  onReplayLoadingChanged(true, '正在解析… 99999 帧');
  out.steps.push(snap('5-回放中 loading(true)'));
  onReplayState(false, '');                       /* 用户中途停止：解析文案要留着 */
  out.steps.push(snap('6-停止但仍在 loading'));
  onReplayLoadingChanged(false, '');
  out.steps.push(snap('7-loading(false)'));
  out.errors = window.__errs || [];
  out.csvLocal = buildCsvHeaderLocal();
  /* 记录行也要同序（本地录制兜底） */
  out.recOrder = (function () {
    var L = state.latest;
    state.recLines = [];
    var saved = state.recording;
    state.recording = true;
    try { localRecSample(); } catch (e) { return 'ERR:' + e.message; }
    state.recording = saved;
    var line = state.recLines[0] || '';
    return line.split(',').length;
  })();
  /* ★★ 真实导出抓包：打桩 blobDownload 拿到 exportCsv() 真正生成的文本。
     每个信号灌一个**唯一哨兵值**，再逐列断言"值落在表头说的那一列" ——
     这正是质检抓到的地方（grep exportCsv 为空），也是 torque/motor_rpm 曾互换却没人发现的原因。 */
  out.exportCheck = (function () {
    var SENT = {roll: -111, pitch: -222, yaw: -333, accelLat: -444, accelLon: -555,
                temp: -666, torque: 222, rpm: 1111, throttle: -888, volt: -999,
                amp: -1010, speed: -1212};
    for (var k = 0; k < 10; k++){
      state.buf['adc' + k] = [1000 + k];
      state.latest['adc' + k] = 1000 + k;
    }
    Object.keys(SENT).forEach(function (id) {
      state.buf[id] = [SENT[id]];
      state.latest[id] = SENT[id];
    });
    var captured = null, savedDl = window.blobDownload;
    blobDownload = function (text) { captured = text; };
    var savedDemo = state.demo, savedBackend = backend;
    state.demo = true; backend = null;          /* 走"无后端 -> 导出屏幕缓冲快照"分支 */
    var err = '';
    try { exportCsv(); } catch (e) { err = e.message; }
    state.demo = savedDemo; backend = savedBackend; blobDownload = savedDl;
    /* ★ 注意用 String.fromCharCode(10) 而不是 '\n'：这段 JS 是 Python 三引号字符串，
       '\n' 会被 Python 先转义成真换行，拼进 JS 就成了未闭合的字符串字面量
       （报 Uncaught SyntaxError，且因为 Python 侧不报错、node --check 对着源文件也看不出来）。 */
    var lines = (captured || '').split(String.fromCharCode(10));
    var head = (lines[0] || '').split(',');
    var row = (lines[1] || '').split(',');
    var want = {roll: -111, pitch: -222, yaw: -333, accel_lat: -444, accel_lon: -555,
                motor_temp: -666, torque: 222, motor_rpm: 1111, throttle: -888,
                voltage: -999, current: -1010, speed: -1212};
    var idOfHead = {'accel_lat': 'accelLat', 'accel_lon': 'accelLon', 'motor_temp': 'temp',
                    'motor_rpm': 'rpm', 'voltage': 'volt', 'current': 'amp'};
    function checkRow(rw) {
      var bad = [];
      Object.keys(want).forEach(function (h) {
        var idx = head.indexOf(h);
        if (idx < 0) { bad.push([h, '缺列']); return; }
        var id = idOfHead[h] || h;
        var exp = fmtNum(id, want[h]);
        if (rw[idx] !== exp) bad.push([h, 'idx=' + idx, '实际=' + rw[idx], '期望=' + exp]);
      });
      return bad;
    }
    /* 反向对照：把 torque / motor_rpm 两列在数据行里互换，检查器**必须**报错
       —— 证明这条断言不是空跑（质检抓到的正是这个互换）。 */
    var sw = row.slice(), ti = head.indexOf('torque'), ri = head.indexOf('motor_rpm');
    var tmp = sw[ti]; sw[ti] = sw[ri]; sw[ri] = tmp;
    return {captured: !!captured, err: err, headCols: head.length, rowCols: row.length,
            mism: checkRow(row), mismIfSwapped: checkRow(sw),
            torqueIdx: ti, rpmIdx: ri,
            torqueVal: row[ti], rpmVal: row[ri]};
  })();
  return JSON.stringify(out);
})()
"""


def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    page = QWebEnginePage(view)
    view.setPage(page)
    res = {}

    def on_load(ok):
        def cb(v):
            res["r"] = v
            app.quit()
        QTimer.singleShot(3200, lambda: page.runJavaScript(LOADING_JS, cb))

    page.loadFinished.connect(on_load)
    view.resize(1600, 900)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(40000, app.quit)
    app.exec()

    r = json.loads(res.get("r") or "{}")
    steps = r.get("steps") or []
    print("=== ① replayLoadingChanged 加载反馈（合成调用，后端已上线同形接口）===")
    for s in steps:
        print("  %-24s pos=%-22s 滑块禁用=%-5s replaying=%-5s loading=%s"
              % (s["tag"], s["pos"], s["disabled"], s["replaying"], s["loading"]))

    import telemetry_config as CFG
    back = CFG.ui_config()["csvHeader"]
    loc = r.get("csvLocal") or ""
    lb, ll = back.split(","), loc.split(",")
    print()
    print("=== ② CSV 本地兜底表头 vs 后端表头 ===")
    print("  后端 (%d 列): %s" % (len(lb), back))
    print("  本地 (%d 列): %s" % (len(ll), loc))
    # 包字段段（ADC 之后）必须逐字段相同
    seg_b = [x for x in lb if not x.startswith("CH")]
    seg_l = [x for x in ll if not x.startswith("CH")]
    print("  非 ADC 段逐字段相同 = %s" % (seg_b == seg_l))
    if seg_b != seg_l:
        print("     后端: %s" % seg_b)
        print("     本地: %s" % seg_l)
    diff = [(i, a, b) for i, (a, b) in enumerate(zip(lb, ll)) if a != b]
    print("  逐字段不同的位置: %d 处 -> %s" % (len(diff), diff[:4]))
    print("  （ADC 名字差异来自配置/telemetry_user.json 的通道改名，浏览器兜底无法得知；"
          "连上后端后 app.js 会用后端表头覆盖，见 applyUiConfig）")
    print("  记录行字段数 = %s（应 = 表头列数 %d）" % (r.get("recOrder"), len(ll)))
    ec = r.get("exportCheck") or {}
    print()
    print("=== ③ exportCsv() 真实导出抓包（打桩 blobDownload，每信号唯一哨兵值）===")
    print("  抓到导出文本 = %s   JS 错误 = %r" % (ec.get("captured"), ec.get("err")))
    print("  表头 %d 列 / 数据行 %d 列" % (ec.get("headCols") or -1, ec.get("rowCols") or -1))
    print("  torque 列 idx=%s 值=%s   motor_rpm 列 idx=%s 值=%s"
          % (ec.get("torqueIdx"), ec.get("torqueVal"), ec.get("rpmIdx"), ec.get("rpmVal")))
    print("  逐列错位 = %s" % (ec.get("mism") if ec.get("mism") else "无"))
    print("  反向对照（把两列互换）-> 检查器报错 = %s"
          % ("是（非空跑）" if ec.get("mismIfSwapped") else "否（空跑！）"))

    def step(tag):
        for s in steps:
            if s["tag"] == tag:
                return s
        return {}

    checks = [
        ("① 初态未回放：滑块禁用、占位文案", step("0-初态")["disabled"] is True),
        ("① loading(true) 显示文案", "正在解析" in step("1-loading(true)")["pos"]),
        ("① loading 中滑块保持禁用", step("1-loading(true)")["disabled"] is True),
        ("① loading 中的复位帧不放开滑块", step("2-loading + 复位帧")["disabled"] is True),
        ("① loading 中的复位帧不覆盖解析文案",
         "正在解析" in step("2-loading + 复位帧")["pos"]),
        ("① loading(false) 且未回放 -> 回到占位且仍禁用",
         step("3-loading(false) 未回放")["pos"] == "— / —"
         and step("3-loading(false) 未回放")["disabled"] is True),
        ("① 回放中 loading(true) -> 文案切换且滑块禁用",
         "正在解析" in step("5-回放中 loading(true)")["pos"]
         and step("5-回放中 loading(true)")["disabled"] is True),
        ("① 停止回放不覆盖解析文案（loading 仍在跑）",
         "正在解析" in step("6-停止但仍在 loading")["pos"]),
        ("① loading(false) -> 恢复占位且不残留 loading 标志",
         step("7-loading(false)")["pos"] == "— / —"
         and step("7-loading(false)")["loading"] is False),
        ("① loading 期间 state.replaying 不被篡改（不是回放态）",
         step("1-loading(true)")["replaying"] is False
         and step("5-回放中 loading(true)")["replaying"] is True),
        ("② 表头非 ADC 段逐字段与后端一致", seg_b == seg_l),
        ("② 表头列数一致", len(lb) == len(ll)),
        ("② 本地记录行字段数 == 表头列数", r.get("recOrder") == len(ll)),
        # ★★ 质检抓到的漏点：导出行的值必须落在表头对应的列（torque/rpm 曾互换）
        ("③ exportCsv 真的产出了文本", (r.get("exportCheck") or {}).get("captured") is True),
        ("③ 导出无 JS 异常", (r.get("exportCheck") or {}).get("err") == ""),
        ("③ 导出表头列数 == 数据行列数",
         (r.get("exportCheck") or {}).get("headCols") == (r.get("exportCheck") or {}).get("rowCols")),
        ("③ torque 落在表头的 torque 列（值 222）",
         (r.get("exportCheck") or {}).get("torqueVal") == "222.0"),
        ("③ motor_rpm 落在表头的 motor_rpm 列（值 1111）",
         (r.get("exportCheck") or {}).get("rpmVal") == "1111"),
        ("③ 全部 12 个包字段逐列无错位",
         not (r.get("exportCheck") or {}).get("mism")),
        ("③ 反向对照：把 torque/rpm 两列互换后检查器必须报错（非空跑）",
         bool((r.get("exportCheck") or {}).get("mismIfSwapped"))),
        ("无 JS 运行时错误", not r.get("errors")),
    ]
    print()
    print("---")
    ok = True
    for n, g in checks:
        print(("  OK   " if g else "  FAIL ") + n)
        ok = ok and bool(g)
    print("结果:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)   # ★ 报 FAIL 必须让 CI 看见（否则 = 假通过）


if __name__ == "__main__":
    main()
