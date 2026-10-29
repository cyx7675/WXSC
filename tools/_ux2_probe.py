# -*- coding: utf-8 -*-
"""_ux2_probe.py —— ux2 只读审查探针：真渲染 + 精确几何/像素证据（不改任何既有文件）

用法:
  python tools/_ux2_probe.py [--demo 10] [--dpr 2] [--shots 1|0]

输出:
  tools/_ux2_out/probe.json       —— 全量几何
  tools/_ux2_out/*.png            —— 高 dpr 截图（可放大看像素）
"""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from _ux_shot import find_chrome, free_port, ws_target  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "tools", "_ux2_out")

# ---------------------------------------------------------------- 采集脚本
DUMP_JS = r"""
(function(){
  function R(e){
    if(!e) return null;
    var b=e.getBoundingClientRect();
    var q=function(v){return Math.round(v*10)/10;};
    return {x:q(b.x),y:q(b.y),w:q(b.width),h:q(b.height),r:q(b.right),b:q(b.bottom),
            cx:q(b.x+b.width/2),cy:q(b.y+b.height/2),t:(e.textContent||'').trim().slice(0,40)};
  }
  function S(e,p){
    if(!e) return null;
    var cs=getComputedStyle(e);
    return {font:cs.fontSize,weight:cs.fontWeight,color:cs.color,bg:cs.backgroundColor,
            align:cs.textAlign,pad:cs.padding,mar:cs.margin,disp:cs.display,
            jc:cs.justifyContent,ai:cs.alignItems,gap:cs.gap,
            rect:R(e), sw:e.scrollWidth, cw:e.clientWidth, sh:e.scrollHeight, ch:e.clientHeight,
            ov:e.scrollWidth>e.clientWidth+1 ? 'X' : (e.scrollHeight>e.clientHeight+1?'Y':''),
            text:(e.textContent||'').trim().slice(0,60)};
  }
  var out={cards:[], rings:[], bars:[], heats:[], trunc:[], spans:[], zones:[], misc:{}};

  /* ---- 分区 ---- */
  ['zoneP2','zoneP1','zoneP0','zoneTrend','zoneVisual','zoneSerial','zoneRecord','zoneSettings']
    .forEach(function(id){ var e=document.getElementById(id); if(e) out.zones.push({id:id,r:R(e),
      hidden:e.classList.contains('hidden')}); });

  /* ---- 动力区所有卡片 ---- */
  var pcards=document.querySelectorAll('#zoneP2 .hcard,#zoneP2 .scard,#zoneP2 .xcard');
  for(var i=0;i<pcards.length;i++){
    var c=pcards[i], o={cls:c.className.split(' ')[0], r:R(c), items:{}};
    [' .h-label',' .h-num',' .h-unit',' .s-name',' .s-num',' .s-unit',' .s-state',
     ' .bar-track',' .bar-fill',' .bar-tick',' .bar-scale',' .heat',' .x-name',' .x-meta']
      .forEach(function(sel){
        var e=c.querySelector(sel.trim()); if(!e) return;
        var key=sel.trim().replace(/^\./,'');
        o.items[key]=S(e);
      });
    var sc=c.querySelector('.bar-scale');
    if(sc){ o.scale=[]; var ch=sc.children;
      var tr=c.querySelector('.bar-track');
      for(var k=0;k<ch.length;k++){ var rr=R(ch[k]);
        o.scale.push({i:k,text:(ch[k].textContent||'').trim(),x:rr.cx,
          frac_track: tr ? Math.round(((rr.cx-R(tr).x)/R(tr).w)*1000)/10 : null}); } }
    var tis=c.querySelectorAll('.bar-tick');
    o.ticks=[]; for(var t=0;t<tis.length;t++){ var rr=R(tis[t]); var tr2=c.querySelector('.bar-track');
      o.ticks.push({id:tis[t].id, styleLeft:tis[t].style.left, vis:getComputedStyle(tis[t]).display!=='none',
        x:rr.cx, frac_track: (tr2 && R(tr2).w)?Math.round(((rr.cx-R(tr2).x)/R(tr2).w)*1000)/10:null,
        color:getComputedStyle(tis[t]).backgroundColor}); }
    var h=c.querySelector('.heat');
    if(h){ var kids=h.children, hs=[];
      for(var j=0;j<kids.length;j++){ var rb=kids[j].getBoundingClientRect();
        hs.push({h:Math.round(rb.height*10)/10, bg:getComputedStyle(kids[j]).backgroundColor,
                 br:getComputedStyle(kids[j]).borderRadius}); }
      o.heat={n:kids.length, rect:R(h), kids:hs,
              gap:getComputedStyle(h).gap}; }
    out.cards.push(o);
  }

  /* ---- 环形表几何 + 读数溢出判定 ---- */
  function ringInfo(cvId, numId, unitId){
    var cv=document.getElementById(cvId), num=document.getElementById(numId),
        un=document.getElementById(unitId);
    if(!cv||!num) return null;
    var w=cv.clientWidth,h=cv.clientHeight, cy=h*0.54;
    var Rr=Math.max(12,Math.min(w/2-8, cy-6, h-cy-6));
    var rw=Math.max(7,Math.min(14,Rr*0.16));
    var rin=Rr-rw, cx=w/2;
    var cr=cv.getBoundingClientRect();
    function corners(e){ var b=e.getBoundingClientRect();
      var pts=[[b.left,b.top],[b.right,b.top],[b.left,b.bottom],[b.right,b.bottom],
               [(b.left+b.right)/2,b.top],[(b.left+b.right)/2,b.bottom],
               [b.left,(b.top+b.bottom)/2],[b.right,(b.top+b.bottom)/2]];
      var mx=-1e9, mn=1e9;
      pts.forEach(function(p){ var dx=p[0]-cr.left-cx, dy=p[1]-cr.top-cy;
        var d=Math.sqrt(dx*dx+dy*dy); if(d>mx) mx=d; if(d<mn) mn=d; });
      return {maxD:Math.round(mx*10)/10, minD:Math.round(mn*10)/10}; }
    var nR=num.getBoundingClientRect(), uR=un?un.getBoundingClientRect():null;
    var nCS=getComputedStyle(num), uCS=un?getComputedStyle(un):null;
    return {canvas:R(cv), W:w,H:h, cy:cy, R:Math.round(Rr*10)/10, rw:Math.round(rw*10)/10,
      rin:Math.round(rin*10)/10,
      num:{rect:R(num), font:nCS.fontSize, color:nCS.color, corners:corners(num)},
      unit: un?{rect:R(un), font:uCS.fontSize, color:uCS.color, corners:corners(un)}:null,
      /* 判定：数字/单位矩形是否越出环内缘圆（越出=压到弧/刻度） */
      numOverflow: (corners(num).maxD > rin - 1),
      unitOverflow: un ? (corners(un).maxD > rin - 1) : null,
      unitBelowNum: un ? Math.round((uR.top-nR.bottom)*10)/10 : null,
      maxFontSize: num.style.fontSize, unitFontSize: un?un.style.fontSize:null,
      text:(num.textContent||'')+(un?(' '+un.textContent):'')};
  }
  out.rings.push(ringInfo('speedGauge','speedNum','speedUnit'));
  out.rings.push(ringInfo('rpmGauge','rpmNum','rpmUnit'));

  /* ---- 全页文字溢出/截断检测 ---- */
  var all=document.querySelectorAll('.zone *');
  for(var i=0;i<all.length;i++){
    var e=all[i];
    if(e.children.length>0) continue;                 /* 只看叶子 */
    var cs=getComputedStyle(e);
    if(cs.display==='none'||cs.visibility==='hidden') continue;
    var t=(e.textContent||'').trim(); if(!t) continue;
    if(e.scrollWidth>e.clientWidth+1){
      out.trunc.push({sel:(e.className||e.tagName)+'#'+(e.id||''), text:t.slice(0,40),
        cw:e.clientWidth, sw:e.scrollWidth, over:e.scrollWidth-e.clientWidth, r:R(e)});
    }
  }

  /* ---- 传感通道区行式布局 ---- */
  var adcs=document.querySelectorAll('#zoneP0 .acard');
  out.adc=[];
  for(var i=0;i<adcs.length;i++){
    var e=adcs[i], o={r:R(e), parts:{}};
    [' .a-ch',' .a-name',' .a-val',' .a-unit',' .a-spark'].forEach(function(s){
      var q=e.querySelector(s.trim()); if(q) o.parts[s.trim().replace('.','')]=S(q); });
    out.adc.push(o);
  }

  /* ---- 姿态区 ---- */
  var dcs=document.querySelectorAll('#zoneP1 .dcard');
  out.dyn=[];
  for(var i=0;i<dcs.length;i++){
    var e=dcs[i], o={r:R(e), cls:e.className, parts:{}};
    e.querySelectorAll('*').forEach(function(q,i2){
      if(i2>14) return;
      var cs=getComputedStyle(q);
      if(q.children.length===0 && (q.textContent||'').trim())
        o.parts[q.className||q.tagName]=S(q);
    });
    var cv=e.querySelector('canvas'); if(cv) o.canvas=R(cv);
    out.dyn.push(o);
  }

  /* ---- 顶栏/底栏度量 ---- */
  out.misc.topbar=R(document.getElementById('topbar'));
  out.misc.footer=R(document.getElementById('footer'));
  out.misc.stage=R(document.getElementById('stage'));
  out.misc.bodyScrollH=document.documentElement.scrollHeight;
  out.misc.viewport={w:window.innerWidth,h:window.innerHeight};
  out.misc.errs=(window.__errs||[]).slice(0,10);
  /* 底栏子元素基线 */
  var f=document.getElementById('footer'); out.misc.footerKids=[];
  if(f){ for(var i=0;i<f.children.length;i++){ var e=f.children[i];
    out.misc.footerKids.push({tag:e.tagName,cls:e.className,id:e.id,r:R(e),
      text:(e.textContent||'').trim().slice(0,24)}); } }
  return JSON.stringify(out);
})()
"""

HEAT_TEST_JS = r"""
(function(){
  /* 受控注入：验证 34 段热条的语义（b.length < 34 / 满缓冲 / 阶跃） */
  function setBuf(id, arr){ state.buf[id] = arr.slice(); state.latest[id] = arr[arr.length-1]; }
  var res={};
  var N=state.buf.speed.length;
  var full = state.buf.speed.slice();
  /* 场景1：阶跃 —— 前半 0，后半满量程（40），看热条左右两端是否对得上 */
  var st=[]; for(var i=0;i<600;i++) st.push(i<300?0:40);
  setBuf('speed', st); state.latest.speed=40;
  document.querySelectorAll('.heat').forEach(function(e){ e._sig=''; });
  updateHeats();
  function readHeat(sel){ var h=document.querySelector(sel); var a=[];
    for(var i=0;i<h.children.length;i++){ var b=h.children[i].getBoundingClientRect();
      a.push(Math.round(b.height*10)/10); }
    return a; }
  res.step_full = readHeat('#speedHeat');
  /* 场景2：只有 10 个样本（启动 0.5s）*/
  setBuf('speed', [40,40,40,40,40,40,40,40,40,40]); state.latest.speed=40;
  document.querySelectorAll('.heat').forEach(function(e){ e._sig=''; });
  updateHeats();
  res.only10 = readHeat('#speedHeat');
  /* 场景3：只有一个样本且为 0 */
  setBuf('speed', [0]); state.latest.speed=0;
  document.querySelectorAll('.heat').forEach(function(e){ e._sig=''; });
  updateHeats();
  res.one0 = readHeat('#speedHeat');
  /* 场景4：真机 current 恒 0 —— 散点退化 */
  var amp=[],pw=[]; for(var i=0;i<600;i++){ amp.push(0); pw.push(30+30*Math.sin(i/40)+ (i%17)); }
  state.buf.amp=amp; state.buf.power=pw;
  try{ paintMini(); }catch(e){ res.paintErr=String(e); }
  res.ampRange=[Math.min.apply(null,amp),Math.max.apply(null,amp)];
  res.pwrRange=[Math.min.apply(null,pw),Math.max.apply(null,pw)];
  /* 复位 */
  state.buf.speed = full;
  document.querySelectorAll('.heat').forEach(function(e){ e._sig=''; });
  try{ updateHeats(); }catch(e){}
  return JSON.stringify(res);
})()
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", type=float, default=10.0)
    ap.add_argument("--dpr", type=float, default=2.0)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--shots", type=int, default=1)
    ap.add_argument("--post-js", default="")
    ap.add_argument("--post-js-file", default="")
    ap.add_argument("--no-demo", action="store_true")
    ap.add_argument("--eval-js-file", default="")
    ap.add_argument("--final-js-file", default="", help="截图前最后注入（HEAT_TEST 之后）")
    ap.add_argument("--clips", default="", help="JSON: {名称:选择器}")
    ap.add_argument("--clips-file", default="", help="同 --clips，从文件读（避开 PowerShell 吞引号）")
    args = ap.parse_args()
    args.final_js = ""
    if args.final_js_file:
        with open(args.final_js_file, encoding="utf-8") as f:
            args.final_js = f.read()
    if args.clips_file:
        with open(args.clips_file, encoding="utf-8") as f:
            args.clips = f.read()
    if args.post_js_file:
        with open(args.post_js_file, encoding="utf-8") as f:
            args.post_js = f.read()

    from PyQt6.QtCore import QUrl, QTimer, QEventLoop
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebSockets import QWebSocket

    os.makedirs(OUT, exist_ok=True)
    chrome = find_chrome()
    port = free_port()
    html = os.path.join(HERE, "telemetry_web", "index.html")
    url = "file:///" + html.replace("\\", "/")
    profile = tempfile.mkdtemp(prefix="ux2chrome_")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--disable-background-networking", "--disable-sync",
           "--force-device-scale-factor=1", "--hide-crash-restore-bubble",
           "--remote-debugging-port=%d" % port, "--user-data-dir=" + profile,
           "--window-size=%d,%d" % (args.width, args.height),
           "--allow-file-access-from-files", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("[chrome] pid=%d port=%d" % (proc.pid, port))
    app = QApplication.instance() or QApplication(sys.argv)

    class Cdp:
        def __init__(self, u):
            self.ws = QWebSocket()
            try:
                self.ws.setMaxAllowedIncomingMessageSize(128 * 1024 * 1024)
                self.ws.setMaxAllowedIncomingFrameSize(128 * 1024 * 1024)
            except Exception:
                pass
            self._id = 0
            self._p = {}
            self.open = False
            self.ws.connected.connect(self._on)
            self.ws.textMessageReceived.connect(self._msg)
            self.ws.open(QUrl(u))

        def _on(self):
            self.open = True

        def _msg(self, t):
            try:
                m = json.loads(t)
            except Exception:
                return
            mid = m.get("id")
            if mid in self._p:
                loop, slot = self._p.pop(mid)
                slot.append(m)
                loop.quit()

        def call(self, method, params=None, timeout=90000):
            self._id += 1
            mid = self._id
            loop = QEventLoop()
            slot = []
            self._p[mid] = (loop, slot)
            self.ws.sendTextMessage(json.dumps({"id": mid, "method": method,
                                                "params": params or {}}))
            t = QTimer()
            t.setSingleShot(True)
            t.timeout.connect(loop.quit)
            t.start(timeout)
            loop.exec()
            if not slot:
                self._p.pop(mid, None)
                return None
            return slot[0]

        def js(self, expr):
            r = self.call("Runtime.evaluate",
                          {"expression": expr, "returnByValue": True, "awaitPromise": True})
            try:
                return r["result"]["result"].get("value")
            except Exception:
                return None

    try:
        wsurl = ws_target(port)
        if not wsurl:
            print("FAIL: no CDP target")
            return 1
        cdp = Cdp(wsurl)
        for _ in range(400):
            if cdp.open:
                break
            app.processEvents()
            time.sleep(0.03)
        if not cdp.open:
            print("FAIL: ws not connected")
            return 1
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": args.width, "height": args.height,
                  "deviceScaleFactor": args.dpr, "mobile": False})

        def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                app.processEvents()
                time.sleep(0.02)

        pump(1.5)
        cdp.js("window.__errs=window.__errs||[];")
        if not args.no_demo:
            cdp.js("(function(){try{setDemo(true);}catch(e){return 'demoFail:'+e;}return 'ok';})()")
        print("[demo] 跑 %.1fs… (no_demo=%s)" % (args.demo, args.no_demo))
        pump(args.demo)
        if args.post_js:
            print("[post]", cdp.js(args.post_js))
            pump(0.6)
        cdp.js("(function(){try{render();}catch(e){}return 'ok';})()")
        pump(0.4)

        raw = cdp.js(DUMP_JS)
        with open(os.path.join(OUT, "probe.json"), "w", encoding="utf-8") as f:
            f.write(raw or "{}")
        print("SAVED probe.json %d bytes" % len(raw or ""))

        if not args.no_demo:
            ht = cdp.js(HEAT_TEST_JS)
            with open(os.path.join(OUT, "heat_test.json"), "w", encoding="utf-8") as f:
                f.write(ht or "{}")
            print("HEAT_TEST:", (ht or "")[:1200])
            cdp.js("(function(){try{setDemo(true);}catch(e){}return 'ok';})()")
            pump(1.0)
        else:
            cdp.js("(function(){try{render();}catch(e){}return 'ok';})()")
            pump(0.3)
        if args.final_js:
            print("[final]", cdp.js(args.final_js))
            pump(0.25)

        def shot(name, clip=None):
            p = {"format": "png", "captureBeyondViewport": True}
            if clip:
                p["clip"] = dict(clip, scale=1)
            r = cdp.call("Page.captureScreenshot", p)
            d = (((r or {}).get("result") or {}).get("data"))
            if not d:
                print("  shot fail", name)
                return
            fp = os.path.join(OUT, name + ".png")
            with open(fp, "wb") as f:
                f.write(base64.b64decode(d))
            print("  SAVED %-28s %d bytes" % (name + ".png", os.path.getsize(fp)))

        if args.shots:
            shot("full_hi")
            clips = json.loads(args.clips) if args.clips else {
                "p2": "#zoneP2", "p1": "#zoneP1", "p0": "#zoneP0",
                "speedcard": "#zoneP2 .hcard",
                "tempcard": "#zoneP2 .scard.span4",
                "voltcard": "#zoneP2 .scard.span2",
                "trend": "#zoneTrend", "footer": "#footer", "topbar": "#topbar",
            }
            for nm, sel in clips.items():
                cdp.js("(function(){var e=document.querySelector(%s);"
                       "if(e&&e.scrollIntoView) e.scrollIntoView({block:'center'});return 'ok';})()"
                       % json.dumps(sel))
                pump(0.45)
                b = cdp.js("JSON.stringify((function(){var e=document.querySelector(%s);"
                           "if(!e) return null;var r=e.getBoundingClientRect();"
                           "return {x:r.x,y:r.y,width:r.width,height:r.height,"
                           "vw:window.innerWidth,vh:window.innerHeight};})())" % json.dumps(sel))
                try:
                    bb = json.loads(b)
                except Exception:
                    bb = None
                if not bb:
                    print("  skip", nm)
                    continue
                bb["x"] = max(0, bb["x"])
                bb["y"] = max(0, bb["y"])
                bb["width"] = min(bb["width"], bb["vw"] - bb["x"])
                bb["height"] = min(bb["height"], bb["vh"] - bb["y"])
                if bb["width"] < 4 or bb["height"] < 4:
                    print("  skip", nm, "offscreen")
                    continue
                shot(nm, bb)
        if args.eval_js_file:
            with open(args.eval_js_file, encoding="utf-8") as f:
                ev = cdp.js(f.read())
            print("[eval]", (ev or "")[:6000])
            with open(os.path.join(OUT, "eval.json"), "w", encoding="utf-8") as f:
                f.write(ev or "{}")
        print("RESULT: OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
