# -*- coding: utf-8 -*-
"""js_perf_probe.py -- 前端渲染性能探针（真实浏览器，只读诊断）

做法：复制一份前端到 tools/_perf_web，注入 _perf.js（跑基准并把结果写进
#perfOut），再用 playwright 打开、等待、一次性把结果文本读回来。
这样避免把大段 JS 塞进 shell 参数里做引号转义（转义极易出错）。

测量项：
  - rAF 帧间隔分布（p50/p95/max）与掉帧率
  - longtask（>50ms）次数与总时长
  - updateDom() / paintLayer(drawTrend) 单次耗时
  - 趋势采样实际频率 -> 600 点到底覆盖多少秒（坐标轴标称 30s）

用法：
    python tools/js_perf_probe.py [可见通道数=4|all]
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PERF_JS = r"""
(function () {
  var out = { errs: [] };
  window.addEventListener('error', function (e) { out.errs.push(String(e.message)); });
  var rafGaps = [], longs = [];
  try {
    new PerformanceObserver(function (l) {
      for (var i = 0; i < l.getEntries().length; i++) longs.push(Math.round(l.getEntries()[i].duration));
    }).observe({ entryTypes: ['longtask'] });
  } catch (e) {}
  var last = performance.now();
  (function f(t) { rafGaps.push(t - last); last = t; requestAnimationFrame(f); })(performance.now());

  function bench(fn, n) { fn(); var t = performance.now(); for (var i = 0; i < n; i++) fn(); return (performance.now() - t) / n; }
  function q(arr, p) { if (!arr.length) return -1; var a = arr.slice().sort(function (x, y) { return x - y; }); return +a[Math.min(a.length - 1, Math.floor(a.length * p))].toFixed(1); }

  function finish() {
    var s = rafGaps.slice(5).sort(function (x, y) { return x - y; });
    var cv = document.getElementById('trendCanvas');
    var nb = document.getElementsByTagName('*').length;
    out.rafP50 = q(s, .5); out.rafP95 = q(s, .95); out.rafMax = +Math.max.apply(null, s).toFixed(1);
    out.rafN = s.length;
    out.dropped = s.filter(function (x) { return x > 33; }).length;
    out.longTasks = longs.length;
    out.longTotalMs = longs.reduce(function (a, b) { return a + b; }, 0);
    out.longMaxMs = longs.length ? Math.max.apply(null, longs) : 0;
    out.domMs = +bench(function () { updateDom(); }, 30).toFixed(3);
    out.paintMs = +bench(function () { paintLayer(trendLayer, drawTrend); }, 30).toFixed(3);
    // HEX 监视器：真实串口下后端每 50ms 推一批（实测约 9~20 行），这里按 9 行/批测
    var hex9 = [];
    for (var h = 0; h < 9; h++) hex9.push('55 AA 00 1F 40 9C 00 00 08 00 20 08 30 08 40 08 50 08 60 08 70 08 80 08 90 08 A0 08');
    out.hexMs = +bench(function () { pushHexLines(hex9.join('\n')); }, 40).toFixed(3);
    out.hexNodes = document.getElementById('hexBox').children.length;
    out.canvasPx = cv.width * cv.height;
    out.canvasCss = cv.clientWidth + 'x' + cv.clientHeight;
    out.dpr = window.devicePixelRatio;
    out.domNodes = nb;
    out.sampleHz = +((state.buf.speed.length - window.__n0) / ((Date.now() - window.__t0) / 1000)).toFixed(2);
    out.bufLen = state.buf.speed.length;
    out.windowSec = +(600 / Math.max(0.01, out.sampleHz)).toFixed(1);
    out.fpsTag = (document.getElementById('fpsTag') || {}).textContent;
    out.rxTag = (document.getElementById('rxTag') || {}).textContent;
    var d = document.createElement('div');
    d.id = 'perfOut';
    d.style.display = 'none';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }

  window.addEventListener('load', function () {
    setTimeout(function () {
      try {
        if (window.__CH === 'all') { state.visible = new Set(Object.keys(SIGS)); buildChips(); }
        setDemo(true);
        window.__n0 = state.buf.speed.length;
        window.__t0 = Date.now();
        setTimeout(finish, 10000);
      } catch (e) { out.errs.push('probe: ' + e); finish(); }
    }, 800);
  });
})();
"""


def _free_port(preferred=8791):
    for p in (preferred, 8792, 8793, 8794, 8795):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(root, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _cli(*args, timeout=240):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    nch = sys.argv[1] if len(sys.argv) > 1 else "4"
    web = os.path.join(HERE, "_perf_web")
    if os.path.isdir(web):
        shutil.rmtree(web)
    shutil.copytree(os.path.join(ROOT, "telemetry_web"), web)

    html_path = os.path.join(web, "index.html")
    html = open(html_path, encoding="utf-8").read()
    html = html.replace("</body>",
                        "<script>window.__CH='%s';</script>\n<script src='_perf.js'></script>\n</body>" % nch)
    open(html_path, "w", encoding="utf-8").write(html)
    open(os.path.join(web, "_perf.js"), "w", encoding="utf-8").write(PERF_JS)

    port = _free_port()
    srv = _serve(web, port)
    url = "http://127.0.0.1:%d/index.html" % port
    try:
        _cli("kill-all")
        rc, log = _cli("open", '"%s"' % url)
        if rc != 0:
            print("打开失败:", log[:600])
            return 1
        _cli("resize", "1600", "1000")
        time.sleep(14)
        rc, log = _cli("run-code", "--raw",
                       '"async (page) => await page.evaluate(() => { var e = document.getElementById(\'perfOut\'); return e ? e.textContent : \'NO_RESULT\'; })"')
        txt = log.strip()
        # playwright-cli 会把返回值当 JS 字符串打印，内层引号被转义成 \" —— 先还原
        if '\\"' in txt:
            txt = txt.replace('\\"', '"').replace('\\\\', '\\')
        i, j = txt.find("{"), txt.rfind("}")
        if i < 0 or j < 0:
            print("探针未产出结果:\n", txt[:2000])
            return 1
        try:
            d = json.loads(txt[i:j + 1])
        except Exception as e:
            print("JSON 解析失败(%s)，原始片段:\n" % e, txt[i:j + 1][:2000])
            return 1
        print("=" * 72)
        print("前端渲染性能（可见通道 %s · 1600x1000 · 演示模式 20Hz）" % nch)
        print("=" * 72)
        print("  rAF 帧间隔    p50=%.1f ms  p95=%.1f ms  max=%.1f ms" % (d["rafP50"], d["rafP95"], d["rafMax"]))
        print("  掉帧(>33ms)   %d / %d 帧 = %.1f%%" % (d["dropped"], d["rafN"], 100.0 * d["dropped"] / max(1, d["rafN"])))
        print("  长任务(>50ms) %d 次，合计 %d ms，最长 %d ms" % (d["longTasks"], d["longTotalMs"], d["longMaxMs"]))
        print("  updateDom     %.3f ms/次  (20Hz → 占单核 %.2f%%)" % (d["domMs"], d["domMs"] * 20 / 10))
        print("  HEX 一批(9行) %.3f ms/次  (20Hz → 占单核 %.2f%%)  当前行数 %s" % (
            d.get("hexMs", -1), d.get("hexMs", 0) * 20 / 10, d.get("hexNodes")))
        print("  paintLayer    %.3f ms/次  (30fps → 占单核 %.2f%%)" % (d["paintMs"], d["paintMs"] * 30 / 10))
        print("  canvas        %d px (CSS %s, dpr %s)" % (d["canvasPx"], d["canvasCss"], d["dpr"]))
        print("  DOM 节点数    %d" % d["domNodes"])
        print("  趋势采样率    %.2f Hz → 600 点实际覆盖 %.1f s（坐标轴标称 30 s）" % (d["sampleHz"], d["windowSec"]))
        print("  fpsTag=%s  rxTag=%s  JS 错误=%s" % (d["fpsTag"], d["rxTag"], d["errs"]))
        return 0
    finally:
        srv.shutdown()
        shutil.rmtree(web, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
