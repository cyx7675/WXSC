#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scroll_probe.py — 真实滚动剖析（定位「滚动卡顿」到底贵在哪）

为什么不用 scroll_bench.py
--------------------------
scroll_bench 测的是「我们自己 JS 的 step 耗时」，测不到浏览器滚动的真实成本：
合成层更新、光栅化、样式重算、布局。用户感知的"卡"来自这些，不是 JS。

本脚本用 CDP Performance 域直接读浏览器的计数器，在真实 Chromium 里跑三档：
    still          静止 + 演示数据
    scroll         连续滚动 + 演示数据
    scroll_nodata  连续滚动 + 冻结数据（把「滚动」和「20Hz 更新」两个成本分开）

读的计数器（增量）：
    LayoutCount / LayoutDuration        布局次数与耗时  ← 滚动卡顿的头号指标
    RecalcStyleCount / RecalcStyleDuration  样式重算次数与耗时
    ScriptDuration                       JS 执行
    TaskDuration                         主线程总任务耗时

同时用 rAF 采样真实帧间隔（fps / p95 / max），以及滚动期间
stage.scrollHeight 的抖动幅度（content-visibility 没配好会导致滚动条跳变）。

用法：
    cd F:\\上位机\\v2
    python tools\\scroll_probe.py
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _free_port(preferred=8794):
    for p in (preferred, 8795, 8796):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no port")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _flatten(js: str) -> str:
    """把多行 JS 压成一行：剥掉 // 行注释后拼接。

    ⚠️ 必须剥注释——整段 JS 是通过命令行参数传进去的，换行会被吃掉，
       一句 `// xxx` 行尾注释会把后面所有代码全注释掉（表现为
       SyntaxError: Unexpected end of input）。字符串里没有 // ，安全。
    """
    out = []
    for line in js.split("\n"):
        s = line.strip()
        if not s:
            continue
        # 只剥行注释（本文件 JS 的字符串常量里不含 //）
        c = s.find("//")
        if c >= 0:
            s = s[:c].strip()
        if s:
            out.append(s)
    return " ".join(out)


def _cli(*args, cwd=None, timeout=300):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


JS = r"""
async (page) => {
  const sleep = async ms => { await page.waitForTimeout(ms); };
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const get = async () => {
    const { metrics } = await cdp.send('Performance.getMetrics');
    const o = {};
    for (const m of metrics) o[m.name] = m.value;
    return o;
  };

  const KEYS = ['LayoutCount','RecalcStyleCount','LayoutDuration',
                'RecalcStyleDuration','ScriptDuration','TaskDuration'];

  async function phase(name, doScroll, seconds) {
    const a = await get();
    const p = await page.evaluate(async (o) => {
      const stage = document.getElementById('stage');
      const maxY = Math.max(1, stage.scrollHeight - stage.clientHeight);
      const raf = [];
      let y = 0, dir = 1, shMin = 1e9, shMax = 0;
      const t0 = performance.now();
      await new Promise(res => {
        (function f(){
          /* ★ 往复滚动，不要"到底就瞬间跳回顶部"。
             瞬间跳回会让整页元素一次性重新进入视口（content-visibility:auto
             全部重新渲染），测出来的是一个 40ms 的假尖刺 —— 真实用户滚动
             不会这样，用它当指标会把优化方向带偏。 */
          if (o.scroll) {
            y += 9 * dir;
            if (y >= maxY) { y = maxY; dir = -1; }
            if (y <= 0) { y = 0; dir = 1; }
            stage.scrollTop = y;
          }
          const sh = stage.scrollHeight;
          if (sh < shMin) shMin = sh;
          if (sh > shMax) shMax = sh;
          raf.push(performance.now());
          if (performance.now() - t0 < o.sec * 1000) requestAnimationFrame(f);
          else res();
        })();
      });
      const iv = [];
      for (let i = 1; i < raf.length; i++) iv.push(raf[i] - raf[i-1]);
      iv.sort((x,y) => x - y);
      const q = pp => iv.length
        ? +iv[Math.min(iv.length-1, Math.floor(pp * iv.length))].toFixed(2) : 0;
      return {
        frames: iv.length,
        fps: +(iv.length / o.sec).toFixed(1),
        f_p50: q(0.5), f_p95: q(0.95),
        f_max: +(iv[iv.length-1] || 0).toFixed(1),
        jankPct: +(iv.filter(x => x > 20).length / Math.max(1, iv.length) * 100).toFixed(1),
        scrollHJitter: shMax - shMin
      };
    }, { scroll: doScroll, sec: seconds });

    const b = await get();
    const d = { name: name };
    for (const k of KEYS) d[k] = +(((b[k]||0) - (a[k]||0))).toFixed(3);
    Object.assign(d, p);
    d.perSec = {};
    for (const k of ['LayoutCount','RecalcStyleCount']) d.perSec[k] = +(d[k]/seconds).toFixed(1);
    for (const k of ['LayoutDuration','RecalcStyleDuration','ScriptDuration','TaskDuration'])
      d.perSec[k] = +(d[k]*1000/seconds).toFixed(1);
    return d;
  }

  await page.evaluate(() => { if (typeof setDemo === 'function') setDemo(true); });
  await sleep(4000);

  // 静态审计：合成层 / 永久动画 / 高频 transition 元素
  const audit = await page.evaluate(() => {
    const stage = document.getElementById('stage');
    const all = stage.querySelectorAll('*');
    let willChange = 0, trTransform = 0, trLeft = 0, infinite = 0, shadow = 0;
    const wcSel = new Set();
    for (const el of all) {
      const cs = getComputedStyle(el);
      if (cs.willChange && cs.willChange !== 'auto') { willChange++; wcSel.add(el.className || el.tagName); }
      const tr = cs.transitionProperty || '';
      if (tr.includes('transform') || tr === 'all') trTransform++;
      if (tr.includes('left') || tr.includes('width') || tr.includes('height')) trLeft++;
      const ic = cs.animationIterationCount || '';
      if (ic === 'infinite' || (cs.animationName && cs.animationName !== 'none' &&
          ic.indexOf('infinite') >= 0)) infinite++;
      if (cs.boxShadow && cs.boxShadow !== 'none') shadow++;
    }
    return {
      nodes: all.length,
      willChange: willChange,
      willChangeOn: Array.from(wcSel).slice(0, 6),
      transTransform: trTransform,
      transLayoutProp: trLeft,
      infiniteAnim: infinite,
      boxShadow: shadow,
      stageScrollH: stage.scrollHeight
    };
  });

  /* 真实滚轮档：由浏览器合成线程驱动滚动（page.mouse.wheel），
     主线程只收 scroll 事件 —— 这才是用户实际的滚轮体验。
     上面几档是主线程每帧写 scrollTop，比真实滚轮更悲观。 */
  async function phaseWheel(name, seconds) {
    const box = await page.locator('#stage').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const a = await get();
    const sampling = page.evaluate((sec) => new Promise(res => {
      const raf = [];
      const t0 = performance.now();
      (function f(){
        raf.push(performance.now());
        if (performance.now() - t0 < sec * 1000) requestAnimationFrame(f);
        else {
          const iv = [];
          for (let i = 1; i < raf.length; i++) iv.push(raf[i] - raf[i-1]);
          iv.sort((x,y) => x - y);
          const q = pp => iv.length
            ? +iv[Math.min(iv.length-1, Math.floor(pp * iv.length))].toFixed(2) : 0;
          res({ frames: iv.length, fps: +(iv.length / sec).toFixed(1),
                f_p50: q(0.5), f_p95: q(0.95),
                f_max: +(iv[iv.length-1] || 0).toFixed(1),
                jankPct: +(iv.filter(x => x > 20).length / Math.max(1, iv.length) * 100).toFixed(1),
                scrollHJitter: 0 });
        }
      })();
    }), seconds);
    const wheels = (async () => {
      const n = Math.round(seconds * 1000 / 40);
      for (let i = 0; i < n; i++) {
        await page.mouse.wheel(0, 60);
        await page.waitForTimeout(36);
      }
    })();
    const p = await Promise.all([sampling, wheels]).then(r => r[0]);
    const b = await get();
    const d = { name: name };
    for (const k of KEYS) d[k] = +(((b[k]||0) - (a[k]||0))).toFixed(3);
    Object.assign(d, p);
    d.perSec = {};
    for (const k of ['LayoutCount','RecalcStyleCount']) d.perSec[k] = +(d[k]/seconds).toFixed(1);
    for (const k of ['LayoutDuration','RecalcStyleDuration','ScriptDuration','TaskDuration'])
      d.perSec[k] = +(d[k]*1000/seconds).toFixed(1);
    return d;
  }

  const out = [];
  out.push(await phase('still', false, 3));
  out.push(await phase('scroll', true, 3));
  out.push(await phaseWheel('wheel(真实滚轮)', 3));
  await page.evaluate(() => { state.frozen = true; });
  out.push(await phase('scroll_nodata', true, 3));
  await page.evaluate(() => { state.frozen = false; });
  return JSON.stringify({ audit: audit, phases: out });
}
"""


def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "telemetry_web"))
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(_Quiet, directory=root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    try:
        rc, log = _cli("open", '"%s"' % url, cwd=root)
        if rc != 0:
            print("open fail:", log[-400:])
            return 1
        _cli("resize", "1500", "1000", cwd=root)
        rc, log = _cli("run-code", "--raw", '"%s"' % _flatten(JS), cwd=root)
        raw = log.strip()
        # 从输出里抠出 JSON。playwright-cli 可能把返回值再转义一层（外层带引号），
        # 所以不能用裸 find('{')，交给 raw_decode 从第一个 '{' 开始自动判定边界。
        i = 0 if raw[:1] in ('{', '"', '[') else raw.find("{")
        if i < 0:
            print("no json:", raw[-1200:])
            return 1
        try:
            d, _ = json.JSONDecoder().raw_decode(raw, i)
            if isinstance(d, str):          # 结果被转义了一层 -> 再解一次
                d = json.loads(d)
        except Exception as e:
            print("json err:", e)
            print("raw head:", raw[:400])
            print("raw tail:", raw[-400:])
            return 1
    finally:
        srv.shutdown()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_scroll_probe.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    a = d.get("audit", {})
    ps = d.get("phases", [])
    print("=" * 74)
    print("真实滚动剖析 · Chromium · 1500x1000 · 每档 3 秒")
    print("=" * 74)
    print("\n[静态审计]")
    print("  滚动区节点数        : %s" % a.get("nodes"))
    print("  will-change 元素     : %s   %s" % (a.get("willChange"), a.get("willChangeOn")))
    print("  transition 含 transform: %s" % a.get("transTransform"))
    print("  transition 含布局属性 : %s   ← 每帧强制重排" % a.get("transLayoutProp"))
    print("  无限 animation       : %s" % a.get("infiniteAnim"))
    print("  box-shadow 元素      : %s" % a.get("boxShadow"))

    print("\n[每档实测]  帧率 / 掉帧 / 每秒布局次数 / 每秒样式重算次数 / 主线程 ms per 秒")
    print("  %-14s %6s %6s %6s %9s %9s %9s %9s" %
          ("档位", "fps", "p95ms", "maxms", "jank%", "layout/s", "style/s", "main-ms/s"))
    for p in ps:
        s = p.get("perSec", {})
        print("  %-14s %6s %6s %6s %8s%% %9s %9s %9s" %
              (p.get("name"), p.get("fps"), p.get("f_p50"), p.get("f_max"),
               p.get("jankPct"), s.get("LayoutCount"), s.get("RecalcStyleCount"),
               s.get("TaskDuration")))
    print("\n[滚动条抖动] scrollHeight 峰谷差（>0 说明滚动条长度在变，拖动会飘）")
    for p in ps:
        print("  %-14s %s px" % (p.get("name"), p.get("scrollHJitter")))

    print("\n[细分耗时 ms/s]")
    print("  %-14s %10s %10s %10s" % ("档位", "Layout", "RecalcStyle", "Script"))
    for p in ps:
        s = p.get("perSec", {})
        print("  %-14s %10s %10s %10s" %
              (p.get("name"), s.get("LayoutDuration"),
               s.get("RecalcStyleDuration"), s.get("ScriptDuration")))
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
