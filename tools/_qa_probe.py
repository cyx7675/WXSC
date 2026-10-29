# -*- coding: utf-8 -*-
"""QA 独立回归探针（缺陷3 空数据逻辑 + 边界互斥用例）。
覆盖工程师 _demo_probe.py 未测的部分：
  P1 初始空态（>2s 排除 1.2s 自动演示残留）
  P2 部分包解锁：仅注入 adc 包 -> P0 出数、P1/P2 保持 "—"
  P3 模拟断开 onStatus(false) -> 全部回 "—"
  P4 手动开演示 -> 出数 + 截图
  P5 演示中点「连接」-> 弹互斥警告、演示不中断（边界）
  P6 连接态开演示 -> 弹互斥警告、复选框被回退（边界）
  P7 关演示 -> 回 "—"
每阶段读取 console error 与注入的 __errs，必须为空。
用法： cd F:\\上位机\\v2 && <venv>\\python.exe tools\\_qa_probe.py
"""
import os, shutil, socket, subprocess, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.path.dirname(HERE), "telemetry_web")
FAILS = []


def _free_port(preferred=8911):
    for p in range(preferred, preferred + 8):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p)); return p
            except OSError:
                continue
    raise RuntimeError("no free port")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _cli(*args, timeout=120):
    cmd = "playwright-cli " + " ".join(args)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=ROOT)
    return p.returncode, ((p.stdout or "") + (p.stderr or ""))


def run(code):
    rc, out = _cli("run-code", "--raw", '"%s"' % code.replace('"', '\\"'))
    lines = out.strip().splitlines()
    return lines[-1] if lines else ""


def console_errors():
    rc, out = _cli("console", "error")
    bad = [l for l in out.splitlines() if l.strip() and "no console" not in l.lower()
           and not l.startswith("—") and "qwebchannel" not in l.lower()]
    return bad


SAMPLE = ("async (page) => JSON.stringify(await page.evaluate(() => ({"
          "demo: document.getElementById('demoMode').checked,"
          "live: document.getElementById('liveText').textContent,"
          "conn: document.getElementById('connText').textContent,"
          "speed: document.getElementById('speedNum').textContent,"
          "roll: document.getElementById('rollNum').textContent,"
          "adc0: document.querySelector('#adcGrid [data-val]').textContent,"
          "tempState: document.getElementById('tempState').textContent,"
          "errs: window.__errs || []})))")

INJECT_HOOK = ("async (page) => { await page.evaluate(() => {"
               "window.__errs = [];"
               "window.__alerts = [];"
               "window.alert = (m) => { window.__alerts.push(String(m)); };"  # 桩掉 alert，避免模态卡死
               "window.addEventListener('error', e => window.__errs.push(String(e.message)));"
               "window.addEventListener('unhandledrejection', e => window.__errs.push('promise:' + e.reason));"
               "}); return 'ok'; }")

CLICK = "async (page) => { await page.click('#%s'); return 'ok'; }"

# 仅注入 P0 adc 包（模拟部分包到达）
PUSH_ADC = ("async (page) => { await page.evaluate(() => onDataBatch(JSON.stringify("
            "[{type:'adc', values:[100,200,300,400,500,600,700,800,900,1000]}]"
            "))); return 'ok'; }")

STATUS_TRUE = "async (page) => { await page.evaluate(() => onStatus(true, 'COM3')); return 'ok'; }"
STATUS_FALSE = "async (page) => { await page.evaluate(() => onStatus(false)); return 'ok'; }"

# 点击并读取 window.__alerts（alert 已被桩为数组记录，无模态）
CLICK_CONN_DLG = ("async (page) => { await page.evaluate(() => { window.__alerts = []; });"
                  " await page.click('#connectBtn');"
                  " return JSON.stringify(await page.evaluate(() => window.__alerts)); }")
CLICK_DEMO_DLG = ("async (page) => { await page.evaluate(() => { window.__alerts = []; });"
                  " await page.click('#demoMode');"
                  " return JSON.stringify(await page.evaluate(() => window.__alerts)); }")


def check(name, phase_json, expect):
    import json
    try:
        d = json.loads(phase_json)
        if isinstance(d, str):   # playwright-cli --raw 可能带一层引号
            d = json.loads(d)
    except Exception:
        FAILS.append("%s: 采样 JSON 解析失败 -> %r" % (name, phase_json[:200]))
        return
    for k, v in expect.items():
        got = d.get(k)
        if isinstance(v, tuple):   # (predicate_name, ok)
            ok = v[1]
            if not ok(str(got)):
                FAILS.append("%s: 字段 %s 期望满足 %s, 实际 %r" % (name, k, v[0], got))
        elif got != v:
            FAILS.append("%s: 字段 %s 期望 %r, 实际 %r" % (name, k, v, got))
    if d.get("errs"):
        FAILS.append("%s: window.__errs 非空 -> %r" % (name, d["errs"]))
    print("%s: %s" % (name, phase_json))


def main():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 partial(_Quiet, directory=ROOT))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % port
    shot_tmp = os.path.join(ROOT, "_qa_tmp.png")
    shot_out = os.path.join(HERE, "_qa_light_demo.png")
    try:
        _cli("close")  # 清理残留会话
        rc, log = _cli("open", '"%s"' % url)
        if rc != 0:
            print("open failed:\n" + log); return 1
        _cli("resize", "1600", "1400")
        run(INJECT_HOOK)
        time.sleep(3)  # >1.2s，排除旧版自动演示残留

        print("== P1 初始空态（不自动演示）==")
        check("P1", run(SAMPLE), {
            "demo": False, "live": "未连接", "speed": "—", "roll": "—",
            "adc0": "—", "tempState": "—"})

        print("== P2 模拟连接 + 仅注入 adc 包（部分解锁）==")
        run(STATUS_TRUE); run(PUSH_ADC); time.sleep(0.6)
        check("P2", run(SAMPLE), {
            "live": ("startswith LIVE", lambda s: s.startswith("LIVE")),
            "adc0": "100",
            "speed": "—", "roll": "—", "tempState": "—"})

        print("== P3 断开连接 -> 全部回占位 ==")
        run(STATUS_FALSE); time.sleep(0.6)
        check("P3", run(SAMPLE), {
            "live": "未连接", "adc0": "—", "speed": "—", "roll": "—"})

        print("== P4 手动开演示 -> 出数 ==")
        run(CLICK % "demoMode"); time.sleep(5)
        check("P4", run(SAMPLE), {
            "demo": True, "live": "LIVE · 演示数据", "conn": "演示模式",
            "speed": ("not —", lambda s: s != "—"),
            "roll": ("not —", lambda s: s != "—"),
            "adc0": ("not —", lambda s: s != "—")})
        rc, _ = _cli("screenshot", '--filename="%s"' % shot_tmp.replace("\\", "/"))
        if os.path.isfile(shot_tmp):
            shutil.move(shot_tmp, shot_out)
            print("SAVED", shot_out)

        print("== P5 演示中点「连接」-> 互斥告警，演示不中断 ==")
        dlg = run(CLICK_CONN_DLG)
        print("P5 dialog:", dlg)
        if "演示模式" not in dlg:
            FAILS.append("P5: 演示中点连接未弹互斥告警 -> %r" % dlg)
        check("P5", run(SAMPLE), {"demo": True, "live": "LIVE · 演示数据"})

        print("== P6 连接态开演示 -> 互斥告警，复选框回退 ==")
        run(CLICK % "demoMode")  # 先关演示
        time.sleep(1.0)
        run(STATUS_TRUE)         # 模拟真实连接
        time.sleep(0.4)
        dlg6 = run(CLICK_DEMO_DLG)
        print("P6 dialog:", dlg6)
        if "已连接" not in dlg6:
            FAILS.append("P6: 连接态开演示未弹互斥告警 -> %r" % dlg6)
        check("P6", run(SAMPLE), {
            "demo": False,
            "live": ("startswith LIVE", lambda s: s.startswith("LIVE"))})
        run(STATUS_FALSE)        # 复位
        time.sleep(0.4)

        print("== P7 演示关 -> 回占位（重开再关一轮）==")
        run(CLICK % "demoMode"); time.sleep(3)
        run(CLICK % "demoMode"); time.sleep(1.5)
        check("P7", run(SAMPLE), {
            "demo": False, "live": "未连接", "speed": "—", "roll": "—", "adc0": "—"})

        errs = console_errors()
        if errs:
            FAILS.append("console error 非空 -> %r" % errs[:5])
        print("console errors:", errs if errs else "(空)")

        print()
        if FAILS:
            print("FAILED %d 项:" % len(FAILS))
            for f in FAILS:
                print("  - " + f)
            return 1
        print("ALL PASS")
        return 0
    finally:
        server.shutdown()
        _cli("close")
        if os.path.isfile(shot_tmp):
            try: os.remove(shot_tmp)
            except OSError: pass


if __name__ == "__main__":
    sys.exit(main())
