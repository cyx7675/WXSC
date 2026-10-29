# -*- coding: utf-8 -*-
"""_ux_shot.py —— 真实渲染截图 + 分区几何采集（产品体验改前/改后对照用）

为什么要新写一个：
  · `QWebEngineView.grab()` 拿不到网页内容（页面在独立渲染进程里合成）；
  · `tools/preview_screenshot.py` / `tools/_diag_shot.py` 依赖 `playwright-cli`，
    本机 `where playwright-cli` 找不到该命令（实测）；
  · 本机 QtWebEngine 的 `QTWEBENGINE_REMOTE_DEBUGGING` 端口实测连不上（超时，
    已用最小脚本复现），所以不走 QtWebEngine。
改用本机已安装的 **Chrome headless + DevTools Protocol**：由真实 Chromium 光栅化，
拿到的是真渲染结果，且支持 `clip` 精确截取任意分区。

用法：
  <venv>\\python.exe tools\\_ux_shot.py --out tools/ux_before --prefix before \
      --width 1600 --height 1000 --demo 8 --shots page,zones,chrome [--tall 3200]

  --shots page      视口截图
  --shots zones     逐个分区（zoneP2/P1/P0/Trend/Visual/Serial/Record/Settings）
  --shots chrome    顶栏 + 导航 + 底栏
  --tall N          额外截一张 N 高的窗口（一屏看完整个 stage，不用滚动）
  --extra-js "..."  截图前再跑一段 JS（用于 hover / 暂停 / 特定视图）
  --view all|overview|visual|serial

输出：<out>/<prefix>_*.png 与 <out>/<prefix>_rects.json（几何证据）
"""
import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

ZONES = ["zoneP2", "zoneP1", "zoneP0", "zoneTrend",
         "zoneVisual", "zoneSerial", "zoneRecord", "zoneSettings"]

DEMO_ON = ("(function(){ try{ setDemo(true); }catch(e){}"
           " try{ if(typeof MOCKD!=='undefined'){} }catch(e){}"
           " return 'ok'; })()")

SHOW_ALL = ("(function(){ document.querySelectorAll('.zone').forEach(function(z){"
            " z.classList.remove('hidden'); z.style.removeProperty('height'); });"
            " return 'ok'; })()")

CLICK_NAV = ("(function(){var n=document.querySelectorAll('.rail-item');"
             "if(n[%d]) n[%d].click();return 'ok';})()")

# 让所有画布/曲线都用累积到的演示数据重画一遍（不依赖主循环的节流）
RENDER_ALL = ("(function(){ try{ render(); }catch(e){}"
              " try{ buildChips(); }catch(e){}"
              " try{ trendLayer.sKey=''; trendDirty=true; }catch(e){}"
              " try{ if(typeof refreshAccVec==='function') refreshAccVec(); }catch(e){}"
              " try{ if(typeof paintVCharts==='function') paintVCharts(); }catch(e){}"
              " try{ if(typeof paintVCharts==='function') paintVCharts(); }catch(e){}"
              " try{ if(typeof buildVisChannels==='function') buildVisChannels(); }catch(e){}"
              " return 'ok'; })()")

RECTS_JS = r"""
(function(){
  function r(sel){
    var e = document.querySelector(sel);
    if(!e) return null;
    var b = e.getBoundingClientRect();
    var cs = getComputedStyle(e);
    var ri = Math.round;
    return {sel:sel, x:ri(b.x*10)/10, y:ri(b.y*10)/10,
            w:ri(b.width*10)/10, h:ri(b.height*10)/10,
            hidden: e.classList.contains('hidden') || cs.display==='none',
            font: cs.fontSize, lh: cs.lineHeight, color: cs.color, bg: cs.backgroundColor,
            radius: cs.borderRadius, pad: cs.padding, gap: (cs.gap||''),
            border: cs.borderColor, shadow: (cs.boxShadow||'none').slice(0,60)};
  }
  var sels = ['#app','#rail','#topbar','#stage','#main','#footer',
    '#zoneP2','#zoneP1','#zoneP0','#zoneTrend','#zoneVisual','#zoneSerial','#zoneRecord','#zoneSettings',
    '#zoneP2 .hero-grid','#zoneP2 .hcard','#zoneP2 .scard','#zoneP1 .dyn-grid','#zoneP1 .dcard',
    '#zoneP0 .adc-grid','#zoneP0 .acard','#zoneTrend .chip-bar','#zoneTrend .chart-box',
    '#zoneTrend .sig-panel','#zoneTrend .sig-row','#zoneVisual .vis-channels','#zoneVisual .vchart',
    '#zoneSerial .hexbox','#zoneRecord .loglist','#zoneSettings .ctl-row',
    '#accVec','#accVecCanvas','.brand h1','.brand .sub','.z-title','.z-meta','.ptag',
    '.hcard .h-label','.hcard .h-num','.hcard .h-unit','.bar-scale','.bar-track',
    '.scard .s-name','.scard .s-num','.scard .s-state','.scard .s-unit',
    '#zoneP0 .acard','#zoneP0 .a-ch','#zoneP0 .a-name','#zoneP0 .a-val','#zoneP0 .a-unit','#zoneP0 .a-spark',
    '.dcard .d-name','.dcard .d-num','.raw-chip','.chip','.sig-cap','.sig-row .sig-name','.sig-row .sig-val','.sig-row .sig-un',
    '.zone-head','.mode-btn','#replayRange','#replayPos','#footer b','#replayPauseBtn',
    '.accvec-row','.accvec-cap','.accvec-note','.accvec-row b','.hexpre','#footer > span:last-child',
    '.vc-title','.vc-sig','.sel','.inp','.btn','.ctl-row label','.chk','.hint','#clock','.pill','.rail-item'];
  var out = {};
  sels.forEach(function(s){ var v=r(s); if(v) out[s]=v; });
  out.__meta = {vw: window.innerWidth, vh: window.innerHeight,
                scrollH: document.documentElement.scrollHeight,
                stageScrollH: (document.getElementById('stage')||{}).scrollHeight,
                dpr: window.devicePixelRatio,
                errs: (window.__errs||[]).slice(0,20)};
  return JSON.stringify(out);
})()
"""


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.isfile(c):
            return c
    raise SystemExit("找不到 Chrome/Edge")


def free_port(preferred=9333):
    for p in (preferred, 9334, 9335, 9336, 9401, 9411):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ws_target(port, tries=80):
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as f:
                lst = json.load(f)
            for t in lst:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "tools", "ux_shot"))
    ap.add_argument("--prefix", default="shot")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--demo", type=float, default=8.0)
    ap.add_argument("--shots", default="page,zones")
    ap.add_argument("--tall", type=int, default=0)
    ap.add_argument("--view", default="all", choices=["all", "overview", "visual", "serial"])
    ap.add_argument("--extra-js", default="")
    ap.add_argument("--post-js", default="",
                    help="攒完演示数据、截图之前再跑的 JS（用于构造特定状态，如超限告警）")
    ap.add_argument("--post-js-file", default="",
                    help="同 --post-js，但从文件读（长脚本/含引号时更稳）。文件优先。")
    ap.add_argument("--eval-json", default="",
                    help="截图后额外求值一段 JS，把返回值打到 stdout（诊断用）")
    ap.add_argument("--html", default="",
                    help="要加载的 HTML 路径（默认 telemetry_web/index.html）。"
                         "用于对照『另一份候选/历史版本的源码目录』做截图：html 同目录下的 "
                         "app.js / assets 会按相对路径一起加载。")
    args = ap.parse_args()
    if args.post_js_file:
        with open(args.post_js_file, encoding="utf-8") as f:
            args.post_js = f.read()

    chrome = find_chrome()
    port = free_port()
    html = args.html or os.path.join(HERE, "telemetry_web", "index.html")
    html = os.path.abspath(html)
    if not os.path.isfile(html):
        print("FAIL: 找不到 HTML", html)
        return 1
    url = "file:///" + html.replace("\\", "/")
    profile = tempfile.mkdtemp(prefix="uxchrome_")
    os.makedirs(args.out, exist_ok=True)

    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--disable-background-networking", "--disable-sync",
           "--force-device-scale-factor=1", "--hide-crash-restore-bubble",
           "--remote-debugging-port=%d" % port,
           "--user-data-dir=" + profile,
           "--window-size=%d,%d" % (args.width, max(args.height, 400)),
           "--allow-file-access-from-files", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("[chrome] pid=%d port=%d viewport=%dx%d" % (proc.pid, port, args.width, args.height))

    try:
        wsurl = ws_target(port)
        if not wsurl:
            print("FAIL: 拿不到 CDP target")
            return 1
        return run_cdp(wsurl, args)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def run_cdp(wsurl, args):
    # 延迟导入：只有真正跑截图时才需要 Qt 的 WebSocket 客户端
    from PyQt6.QtCore import QUrl, QTimer, QEventLoop
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebSockets import QWebSocket

    app = QApplication.instance() or QApplication(sys.argv)

    class Cdp:
        def __init__(self, url):
            self.ws = QWebSocket()
            try:
                self.ws.setMaxAllowedIncomingMessageSize(128 * 1024 * 1024)
                self.ws.setMaxAllowedIncomingFrameSize(128 * 1024 * 1024)
            except Exception:
                pass
            self._id = 0
            self._pending = {}
            self.open = False
            self.ws.connected.connect(self._on_open)
            self.ws.textMessageReceived.connect(self._on_msg)
            self.ws.open(QUrl(url))

        def _on_open(self):
            self.open = True

        def _on_msg(self, text):
            try:
                m = json.loads(text)
            except Exception:
                return
            mid = m.get("id")
            if mid is not None and mid in self._pending:
                loop, slot = self._pending.pop(mid)
                slot.append(m)
                loop.quit()

        def call(self, method, params=None, timeout=60000):
            self._id += 1
            mid = self._id
            loop = QEventLoop()
            slot = []
            self._pending[mid] = (loop, slot)
            self.ws.sendTextMessage(json.dumps(
                {"id": mid, "method": method, "params": params or {}}))
            t = QTimer()
            t.setSingleShot(True)
            t.timeout.connect(loop.quit)
            t.start(timeout)
            loop.exec()
            t.stop()
            if not slot:
                self._pending.pop(mid, None)
                return None
            return slot[0]

        def js(self, expr):
            r = self.call("Runtime.evaluate",
                          {"expression": expr, "returnByValue": True, "awaitPromise": True})
            try:
                return r["result"]["result"].get("value")
            except Exception:
                return None

    cdp = Cdp(wsurl)
    for _ in range(300):
        if cdp.open:
            break
        app.processEvents()
        time.sleep(0.03)
    if not cdp.open:
        print("FAIL: CDP WebSocket 未连上")
        return 1

    cdp.call("Page.enable")
    cdp.call("Runtime.enable")
    # 精确指定视口（与 --window-size 一起确保一致）
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": args.width, "height": args.height, "deviceScaleFactor": 1,
              "mobile": False})

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(1.5)   # 等页面自身 init 跑完

    cdp.js("window.__errs = window.__errs || [];")
    setup = [DEMO_ON]
    if args.view == "all":
        setup.append(SHOW_ALL)
    elif args.view in ("visual", "serial"):
        setup.append(CLICK_NAV % (1 if args.view == "visual" else 2,
                                  1 if args.view == "visual" else 2))
    if args.extra_js:
        setup.append(args.extra_js)
    for s in setup:
        cdp.js(s)

    print("[demo] 跑 %.1fs 攒数据…" % args.demo)
    pump(args.demo)
    if args.post_js:
        cdp.js(args.post_js)
        pump(0.4)
    if args.view == "all":
        cdp.js(SHOW_ALL)
    cdp.js(RENDER_ALL)
    pump(0.6)

    # 目标列表：(文件名后缀, 选择器)。除保留关键字外，**任意 CSS 选择器**都支持
    # （例如 --shots "#zoneP2 .hcard"），便于只截某一张卡片做 A/B 对照。
    want = []
    for tok in args.shots.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok == "page":
            want.append(("page", None))
        elif tok == "chrome":
            want += [("topbar", "#topbar"), ("rail", "#rail"), ("footer", "#footer")]
        elif tok == "zones":
            want += [(z, "#" + z) for z in ZONES]
        elif tok in ZONES or tok in ("topbar", "rail", "footer"):
            want.append((tok, "#" + tok))
        else:
            nm = re.sub(r"[^0-9A-Za-z_-]+", "_", tok).strip("_") or "sel"
            want.append((nm[:40], tok))
    want_names = set(n for n, _ in want)

    # ---- rects
    raw = cdp.js(RECTS_JS)
    rects = {}
    try:
        rects = json.loads(raw)
        with open(os.path.join(args.out, args.prefix + "_rects.json"), "w",
                  encoding="utf-8") as f:
            json.dump(rects, f, ensure_ascii=False, indent=1)
        m = rects.get("__meta", {})
        print("SAVED rects  viewport=%sx%s stageScrollH=%s errs=%d"
              % (m.get("vw"), m.get("vh"), m.get("stageScrollH"), len(m.get("errs") or [])))
        for e in (m.get("errs") or [])[:8]:
            print("   JSERR:", str(e)[:180])
    except Exception as e:
        print("rects 采集失败:", e, str(raw)[:200])

    ok = True

    def shot(name, clip=None, full=False):
        params = {"format": "png", "captureBeyondViewport": bool(full)}
        if clip:
            params["clip"] = dict(clip, scale=1)
        res = cdp.call("Page.captureScreenshot", params)
        data = (((res or {}).get("result") or {}).get("data"))
        if not data:
            print("  截图失败", name, str(res)[:200])
            return False
        p = os.path.join(args.out, "%s_%s.png" % (args.prefix, name))
        with open(p, "wb") as f:
            f.write(base64.b64decode(data))
        print("SAVED %-34s %8d bytes" % (os.path.basename(p), os.path.getsize(p)))
        return True

    if "page" in want_names:
        ok &= shot("page")
    if "rail" in want_names:
        ok &= shot("rail", {"x": 0, "y": 0, "width": 64, "height": args.height})

    def clip_of(sel, label):
        """把选择器对应元素滚到视口内，返回它的 clip。
        ★ CDP 的 clip 用的是**文档坐标**，所以要加上滚动偏移 —— 产品页的滚动发生在
          #stage 内部（文档滚动为 0，偏移 0，行为不变）；而独立原型页（design/*）
          是整页滚动，不加偏移会截到错位置（实测截出一张近乎空白的图）。"""
        js_sel = json.dumps(sel)
        cdp.js("(function(){var e=document.querySelector(%s);"
               "if(e) e.scrollIntoView({block:'center'});return 'ok';})()" % js_sel)
        pump(0.5)
        b = cdp.js("JSON.stringify((function(){var e=document.querySelector(%s);"
                   "if(!e) return null;var r=e.getBoundingClientRect();"
                   "return{x:r.x+(window.scrollX||0),y:r.y+(window.scrollY||0),"
                   "w:r.width,h:r.height,sx:window.scrollX||0,sy:window.scrollY||0,"
                   "vw:window.innerWidth,vh:window.innerHeight};})())" % js_sel)
        try:
            bb = json.loads(b)
        except Exception:
            print("  取 rect 失败", label)
            return None
        if not bb:
            print("  跳过", label, "(选择器无匹配)")
            return None
        clip = {"x": max(0, bb["x"]), "y": max(0, bb["y"]),
                "width": min(bb["w"], max(4, bb["vw"] - bb["x"] + bb["sx"])),
                "height": min(bb["h"], max(4, bb["vh"] - bb["y"] + bb["sy"]))}
        if clip["width"] < 4 or clip["height"] < 4:
            print("  跳过", label, "(视口外/尺寸 0)")
            return None
        return clip

    for nm, sel in want:
        if sel is None:
            continue
        cl = clip_of(sel, nm)      # 顶栏/底栏也走同一条路径（文档坐标 + 滚动偏移）
        if cl:
            ok &= shot(nm, cl)

    if args.eval_json:
        v = cdp.js(args.eval_json)
        print("[eval-json] " + str(v))

    if args.tall:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": args.width, "height": args.tall, "deviceScaleFactor": 1,
                  "mobile": False})
        cdp.js("window.scrollTo(0,0)")
        pump(0.8)
        r2 = cdp.js("JSON.stringify((function(){var e=document.getElementById('stage');"
                    "return {sh:e.scrollHeight, ch:e.clientHeight, sw:e.scrollWidth};})())")
        print("[tall] stage:", r2)
        ok &= shot("tall", {"x": 0, "y": 0, "width": args.width, "height": args.tall})

    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
