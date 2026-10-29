# -*- coding: utf-8 -*-
"""_e2e_realdata.py —— 用**用户的真实录播**跑完整应用级链路，专找真机才暴露的问题

与 _e2e_round2 的区别：这里关注"真机数据形态"下的表现——
  · loading / 进度 / 跳转回填是否正常
  · NAV 六路信号（含新固件 22B 的 accel_lat/lon）是否都进缓冲
  · 加速度矢量图在**真实量级**下画出什么、是否超满量程
  · 有没有 JS 运行时错误 / 控制台异常
用法：python tools/_e2e_realdata.py [录播路径]
结果写 tools/_e2e_realdata.json
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from PyQt6.QtCore import QTimer                      # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402
import launcher_web as L                             # noqa: E402

# 默认挑 telemetry/ 下最大的 .bin（用户的真机录播）
_bins = sorted(glob.glob(os.path.join(HERE, "telemetry", "*.bin")),
               key=lambda p: os.path.getsize(p), reverse=True)
if not _bins and len(sys.argv) <= 1:
    print("没有找到 telemetry/*.bin；用法: python tools/_e2e_realdata.py <录播路径>")
    sys.exit(2)
BIN = sys.argv[1] if len(sys.argv) > 1 else _bins[0]
OUT = os.path.join(HERE, "tools", "_e2e_realdata.json")

R = {"bin": os.path.basename(BIN), "size_mb": round(os.path.getsize(BIN) / 1048576.0, 2),
     "loading": [], "progress": [], "state": [], "err": [], "js_err": [], "snap": []}

NAV_SIX = ["roll", "pitch", "yaw", "accelLat", "accelLon", "accelMag"]


def summarize():
    """★ 2026-10-16（质检 F5）：这个探针原来**零 print、零断言** —— 跑完 EXIT=0
    却没有任何可读结论，只能人去翻 641 行 JSON，等于"看着绿其实没人看"。
    现在打印可读摘要 + 关键断言；任一条 FAIL -> 返回 False（__main__ 用退出码 1 体现）。"""
    snaps = {}
    for s in R.get("snap", []):
        snaps[s.get("tag")] = (s.get("v") or {})
    last = snaps.get("3_seek到60%后") or (list(snaps.values())[-1] if snaps else {})
    load_on = [x for x in R["loading"] if x[0]]
    load_off = [x for x in R["loading"] if not x[0]]
    prog = R["progress"]
    frames = prog[-1][1] if prog else 0
    total_s = prog[-1][2] if prog else 0.0          # 已播时长（秒）
    total_secs = prog[-1][3] if prog else 0.0       # 录播总时长（秒）
    nav_last = last.get("navLast") or {}
    nav_len = last.get("navLen") or {}
    js_errs = list(R.get("js_err") or [])
    for s in R.get("snap", []):
        js_errs += list((s.get("v") or {}).get("jsErrs") or [])

    print("=== _e2e_realdata 摘要（真实录播跑完整链路）===")
    print("  录播: %s（%.2f MB）" % (R["bin"], R["size_mb"]))
    print("  loading 事件: %d 次（True×%d / False×%d）  末条=%s"
          % (len(R["loading"]), len(load_on), len(load_off),
             (R["loading"][-1] if R["loading"] else None)))
    print("  回放状态: %s" % (R["state"] or "（无）"))
    print("  进度包: %d  末包 帧数=%s 已播=%.2fs 总时长=%.2fs"
          % (len(prog), frames, total_s, total_secs))
    print("  跳转 60%% 后: 矢量图 inP1=%s  读数 LAT=%s LON=%s MAG=%s  (%s)"
          % (last.get("inP1"), last.get("accLat"), last.get("accLon"), last.get("accMag"),
             last.get("accNote")))
    print("  NAV 六路缓冲长度: %s" % ({k: nav_len.get(k) for k in NAV_SIX},))
    print("  NAV 六路末值:     %s" % ({k: nav_last.get(k) for k in NAV_SIX},))
    print("  缓冲区非空通道数=%s  JS 运行时错误=%d  后端错误=%d"
          % (last.get("bufLen"), len(js_errs), len(R.get("err") or [])))
    if js_errs:
        print("     JS 错误样例: %s" % js_errs[:3])

    checks = [
        ("loading 既有 (True,..) 也有 (False,'')（成对收尾）",
         len(load_on) >= 1 and len(load_off) >= 1
         and (not R["loading"] or (R["loading"][-1][0] is False))),
        ("loading 文案非空（用户能看到反馈）",
         any(str(t).strip() for _, t in load_on)),
        ("回放状态收到过 True（真的进过回放）",
         any(on for on, _ in R["state"])),
        ("进度包非空且总帧数/总时长 > 0", len(prog) > 0 and frames > 0 and total_secs > 0),
        ("跳转 60% 后矢量图仍在 #zoneP1", last.get("inP1") is True),
        ("矢量图三路读数已刷新（非空且不是占位 '—'）",
         all(last.get(k) not in (None, "", "—") for k in ("accLat", "accLon", "accMag"))),
        ("NAV 六路都进了缓冲（长度 > 0）",
         all((nav_len.get(k) or 0) > 0 for k in NAV_SIX)),
        ("无 JS 运行时错误", not js_errs),
        ("无后端错误消息", not (R.get("err") or [])),
    ]
    print()
    print("---")
    ok = True
    for name, good in checks:
        print(("  OK   " if good else "  FAIL ") + name)
        ok = ok and bool(good)
    print("结果:", "全部通过" if ok else "存在失败")
    return ok



def main():
    app = QApplication(sys.argv)
    win = L.MainWindow()
    win.resize(1440, 860)
    win.show()
    b = win.backend

    b.replayLoadingChanged.connect(lambda on, t: R["loading"].append([bool(on), t]))
    b.replayProgress.connect(lambda i, t, c, s: R["progress"].append([i, t, round(c, 2), round(s, 2)]))
    b.replayStateChanged.connect(lambda on, info: R["state"].append([bool(on), info]))
    b.errorMsg.connect(lambda m: R["err"].append(str(m)))

    def js(code, cb=None):
        win.view.page().runJavaScript(code, cb if cb else (lambda r: None))

    # 抓 JS 运行时错误
    js("""(function(){
        if (window.__realdataHooked) return 'already';
        window.__realdataHooked = 1;
        window.__jsErrs = [];
        window.addEventListener('error', function(e){ window.__jsErrs.push(String(e.message)); });
        return 'ok';
    })()""")

    def snap(tag):
        def got(v):
            R["snap"].append({"tag": tag, "v": v})
        js("""(function(){
            var g = function(id){ var e=document.getElementById(id); return e ? e.textContent.trim() : null; };
            var buf = {}, navLen = {}, last = {};
            try { var s = window.state; if (s && s.buf) {
                for (var k in s.buf){ if (s.buf[k] && s.buf[k].length) buf[k] = s.buf[k].length; }
                ['roll','pitch','yaw','accelLat','accelLon','accelMag'].forEach(function(k){
                    var a = s.buf[k];
                    if (a){ navLen[k] = a.length;
                           if (a.length) last[k] = Math.round(a[a.length-1]*1000)/1000; }
                });
            } } catch(e){}
            return {
                accLat: g('accVecLat'), accLon: g('accVecLon'), accMag: g('accVecMag'),
                accChip: g('accVecChip'), accNote: g('accVecNote'),
                pos: g('replayPos'), inP1: !!(document.getElementById('accVec') && document.getElementById('accVec').closest('#zoneP1')),
                bufLen: Object.keys(buf).length, navLen: navLen, navLast: last,
                jsErrs: (window.__jsErrs || []).slice(0, 5)
            };
        })()""", got)

    def finish():
        try:
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(R, f, ensure_ascii=False, indent=1)
            print("WROTE %s" % OUT)
        except Exception as e:
            # 落盘失败不能把摘要一起吞掉（环境只读时 open(...,'w') 会 PermissionError）
            print("WARN 结果 JSON 落盘失败（不影响判定）: %s" % e)
        app.quit()

    QTimer.singleShot(3500, lambda: snap("1_未回放"))
    QTimer.singleShot(4000, lambda: b.startReplay(BIN))
    QTimer.singleShot(7500, lambda: snap("2_解析中或刚进回放"))
    QTimer.singleShot(12000, lambda: b.seekReplay(0.6))
    QTimer.singleShot(13500, lambda: snap("3_seek到60%后"))
    QTimer.singleShot(15000, finish)
    QTimer.singleShot(40000, finish)
    app.exec()


if __name__ == "__main__":
    main()
    sys.exit(0 if summarize() else 1)
