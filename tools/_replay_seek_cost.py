# -*- coding: utf-8 -*-
"""task-8 F1 验收：真实长录播下 seekReplay 的**成本实测**（只读调用后端，不改任何文件）。

为什么要它：前端把"拖动中每个 input 都发 seekReplay"改成"拖动中发 O(1) 的 seekReplayLive、
松手发一次 seekReplay"。要证明这个改动有意义，必须量出后端 seekReplay 的真实代价 ——
它内部要做"跳转 + 回填一窗历史"，而回填是 O(跳转位置)。

本脚本直接实例化 launcher_web.Backend()，用真实 .bin 载入帧，然后：
  1. 量 seekReplay 在 frac 0.25/0.5/0.75/1.0 的耗时；
  2. **按改前的行为**真实跑一遍"30 步拖动"（30 次 seekReplay），给累计耗时；
  3. **按改后的行为**算：1 次 seekReplay（松手）+ 拖动中的 live 调用
     （后端若已提供 seekReplayLive 就实测；没有就标注为"未提供、按 O(1) 估计"）；
  4. 核对回填契约：槽数 vs bufPoints、末槽 ts == 跳转帧 ts、sampleMs。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    <venv>\\python.exe tools\\_replay_seek_cost.py [bin路径]
"""
import os
import sys
import json
import time
import statistics

# ★ 控制台编码自保护（task-8 F8）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PyQt6.QtWidgets import QApplication          # noqa: E402
import launcher_web                               # noqa: E402

def _pick_bin():
    """自动挑 telemetry/ 下最大的 .bin —— 不硬编码文件名。

    ★ 2026-10-16：原实现硬编码 telemetry_20261013_213238.bin，用户换了一批真机录播后
    该文件已不存在 → 探针 `FileNotFoundError` 直接 exit 1。改成自动挑选，
    以后换文件也不会失效；也可以用命令行参数显式指定。
    """
    d = os.path.join(HERE, "telemetry")
    bins = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".bin")] if os.path.isdir(d) else []
    if not bins:
        return os.path.join(HERE, "sample_capture.bin")
    return max(bins, key=os.path.getsize)

BIN = sys.argv[1] if len(sys.argv) > 1 else _pick_bin()
OUT_JSON = os.path.join(HERE, "tools", "_replay_seek_cost.json")
DRAG_STEPS = 30


def ms(t0):
    return (time.perf_counter() - t0) * 1000.0


def main():
    app = QApplication(sys.argv)                  # noqa: F841  (Qt 需要有 QApplication)
    b = launcher_web.Backend()

    # ---- 模式：--sweep 只对 telemetry/ 下所有 .bin 做回填契约核对（隔离绕回嫌疑）----
    # （注意：必须放在 cap + 信号连接**之后**，否则引用未定义的 cap）
    cap = {"bf_len": 0, "bf_slots": 0, "bf_last_ts": None, "bf_sample_ms": None}
    b.replayBackfill.connect(lambda s: cap.update(
        bf_len=len(s),
        bf_slots=(json.loads(s).get("slots") or [{}])[-1] and len(json.loads(s).get("slots") or []),
        bf_last_ts=((json.loads(s).get("slots") or [{}])[-1] or {}).get("ts"),
        bf_sample_ms=json.loads(s).get("sampleMs"),
    ))

    # ---- 模式：--sweep 只对 telemetry/ 下所有 .bin 做回填契约核对（隔离绕回嫌疑）----
    if "--sweep" in sys.argv:
        import glob
        rows = []
        for p in sorted(glob.glob(os.path.join(HERE, "telemetry", "*.bin"))):
            try:
                b.startReplay(p)
                nn = len(getattr(b, "_replay_frames", []) or [])
                if not nn:
                    rows.append((os.path.basename(p), 0, None, None, None, None, "无帧"))
                    continue
                cap.clear()
                cap.update({"bf_len": 0, "bf_slots": 0, "bf_last_ts": None, "bf_sample_ms": None})
                b.seekReplay(0.5)
                # ★ 口径修正：按冻结规范，slot["ts"] 是**该锚点帧的绝对设备毫秒**
                #   （与 payload["ts"]/fr.ts 同一时间基），不是"距首帧的 delta"。
                #   第一版我拿它跟 _replay_delta_ms() 比，于是 6 个文件全部"不一致" —— 是我的错。
                exp = b._replay_frames[b._replay_idx].ts
                ok = (cap["bf_last_ts"] == exp)
                rows.append((os.path.basename(p), nn, cap["bf_slots"], cap["bf_sample_ms"],
                             cap["bf_last_ts"], exp, "OK" if ok else "**不一致**"))
            except Exception as e:
                rows.append((os.path.basename(p), -1, None, None, None, None, "异常:%s" % e))
        print("=== 回填契约扫描（seek 0.5；末槽 ts 应等于跳转帧 ts）===")
        print("%-34s %-9s %-7s %-9s %-14s %-14s %s"
              % ("文件", "帧数", "槽数", "sampleMs", "末槽ts", "跳转帧ts", "结果"))
        for r in rows:
            print("%-34s %-9s %-7s %-9s %-14s %-14s %s"
                  % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
        try:
            with open(os.path.join(HERE, "tools", "_replay_contract_sweep.json"), "w", encoding="utf-8") as f:
                json.dump([{"file": r[0], "frames": r[1], "slots": r[2], "sampleMs": r[3],
                            "last_ts": r[4], "jump_ts": r[5], "result": r[6]} for r in rows],
                          f, ensure_ascii=False, indent=2)
            print("WROTE tools/_replay_contract_sweep.json")
        except Exception as e:
            print("JSON 写入失败:", e)
        return

    t0 = time.perf_counter()
    b.startReplay(BIN)
    load_ms = ms(t0)
    n = len(getattr(b, "_replay_frames", []) or [])
    print("=== 载入 ===")
    print("  .bin: %s (%.2f MB)" % (os.path.basename(BIN), os.path.getsize(BIN) / 1048576.0))
    print("  帧数 n = %d，载入+解析耗时 %.0f ms" % (n, load_ms))

    if n == 0:
        print("!! 没有解析出帧，后面无法测量")
        return

    def one(frac):
        t = time.perf_counter()
        b.seekReplay(frac)
        return ms(t)

    # ---- 先做一次**隔离**的契约检查（不受后面连续 seek 的干扰）----
    print()
    print("=== ⓪ 回填契约（隔离：只做一次 seekReplay(0.5)）===")
    cap.clear()
    cap.update({"bf_len": 0, "bf_slots": 0, "bf_last_ts": None, "bf_sample_ms": None})
    b.seekReplay(0.5)
    exp_ts = b._replay_frames[b._replay_idx].ts      # 绝对设备毫秒（冻结口径）
    print("  slots=%s（bufPoints=%s）  sampleMs=%s  payload=%s 字节"
          % (cap["bf_slots"], launcher_web.CFG.UI_CFG.get("bufPoints"), cap["bf_sample_ms"], cap["bf_len"]))
    print("  末槽 ts=%s  跳转帧设备 ts=%s  一致: %s"
          % (cap["bf_last_ts"], exp_ts, cap["bf_last_ts"] == exp_ts))
    contract_ok = (cap["bf_slots"] == launcher_web.CFG.UI_CFG.get("bufPoints")
                   and cap["bf_last_ts"] == exp_ts)

    print()
    print("=== ① 单次 seekReplay（含回填一窗）耗时 ===")
    per_frac = {}
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        vals = [one(frac) for _ in range(3)]
        per_frac[frac] = vals
        print("  frac=%.2f  n=%d 次: %s ms  中位 %.1f ms"
              % (frac, len(vals), ["%.1f" % v for v in vals], statistics.median(vals)))

    print()
    print("=== ② 改前行为：30 步拖动 = 30 次 seekReplay（真实跑一遍）===")
    fracs = [0.05 + i * (0.9 / (DRAG_STEPS - 1)) for i in range(DRAG_STEPS)]
    ts = []
    t_all = time.perf_counter()
    for f in fracs:
        t = time.perf_counter()
        b.seekReplay(f)
        ts.append(ms(t))
    old_total = ms(t_all)
    print("  30 次累计 %.1f ms；单次 中位 %.1f / 最小 %.1f / 最大 %.1f ms"
          % (old_total, statistics.median(ts), min(ts), max(ts)))

    print()
    print("=== ③ 改后行为：松手发 1 次 seekReplay，拖动中发 seekReplayLive ===")
    live = getattr(b, "seekReplayLive", None)
    if callable(live):
        lts = []
        t_all = time.perf_counter()
        for f in fracs[:-1]:
            t = time.perf_counter()
            live(f)
            lts.append(ms(t))
        live_total = ms(t_all)
        rel = one(fracs[-1])
        print("  seekReplayLive ×%d 累计 %.2f ms（中位 %.3f ms/次）" % (len(lts), live_total, statistics.median(lts)))
        print("  松手 1 次 seekReplay %.1f ms" % rel)
        print("  → 改后累计 ≈ %.1f ms（对比改前 %.1f ms，降低 %.0f%%）"
              % (live_total + rel, old_total, 100.0 * (1 - (live_total + rel) / old_total)))
        new_total = live_total + rel
    else:
        rel = one(fracs[-1])
        print("  ⚠️ 后端尚未提供 seekReplayLive（launcher_web 里没有该方法）—— 前端已写成"
              "\"有则用、没有就不发\"，因此改后拖动中不发任何请求。")
        print("  松手 1 次 seekReplay %.1f ms（这就是改后一次拖动的后端总成本）" % rel)
        print("  → 改后累计 ≈ %.1f ms（对比改前 %.1f ms，降低 %.0f%%；live 调用为 O(1) 未计入）"
              % (rel, old_total, 100.0 * (1 - rel / old_total)))
        new_total = rel

    print()
    print("=== ④ 1 小时录播外推（按帧数线性）===")
    print("  当前 n=%d 帧；按同样的 O(位置) 特性，1 小时录播（≈6.3×10^5 帧）单次回填约 %.0f ms"
          % (n, statistics.median(per_frac[1.0]) * (6.3e5 / max(1, n))))

    out = {"bin": BIN, "frames": n, "load_ms": load_ms, "per_frac_ms": per_frac,
           "old_drag_total_ms": old_total, "old_drag_single_median_ms": statistics.median(ts),
           "new_total_ms": new_total, "seekReplayLive_available": callable(live),
           "backfill": {"slots": cap["bf_slots"], "sampleMs": cap["bf_sample_ms"],
                        "last_ts": cap["bf_last_ts"], "contract_ok": contract_ok}}
    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print()
        print("WROTE", OUT_JSON)
    except Exception as e:
        print("JSON 写入失败:", e)

    b.stopReplay()


if __name__ == "__main__":
    main()
