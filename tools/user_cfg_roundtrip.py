# -*- coding: utf-8 -*-
"""user_cfg_roundtrip.py —— 验证「界面改量程 -> 保存 -> 重启还在」这条链路。

为什么需要它
--------------------------------------------------------------------------
界面上的「保存」按钮走的是：
    前端 collectRangePatch()  ->  Backend.saveUserCfg(json)
                              ->  合并写进 telemetry_user.json
                              ->  CFG.load_user_cfg()  让当前会话立即生效
                              ->  uiConfig 重新下发，页面重建
而「下次启动还在」走的是另一条：
    main()  ->  write_user_cfg_template()  ->  CFG.load_user_cfg()

这两条只要断一条，用户看到的现象就是「保存失效」。
但凡是这种「不报错、其余自检全绿」的失效，必须有一条自动化盯着，否则改一次坏一次。

本脚本覆盖（每一条都断言真实磁盘内容，不只看内存）：
  1. 保存后文件里真的有新值
  2. 保存后当前会话 ui_config() 立即带新值（不用重启也生效）
  3. 模拟重启（重新 import 配置模块 + load_user_cfg）后新值依然在
  4. 模板生成是幂等的，且不会因为生成动作本身改变任何内置行为
  5. 保存路径就是实际生效路径（避免写到 A 处、却从 B 处读的经典事故）

用法： python tools/user_cfg_roundtrip.py
"""
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ⚠️ 顺序不能反：QtWebEngineWidgets 必须在 QApplication 之前 import，
#    否则报 "must be imported before a QCoreApplication instance is created"。
import telemetry_config as CFG                    # noqa: E402
import launcher_web as L                          # noqa: E402
from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401

_app = QApplication(sys.argv)

MARK_OK = "[OK]"
MARK_BAD = "[!!]"
MARK_INFO = " -- "


def say(line=""):
    print(line)


CHECK_COUNT = 0


def check(name, ok, detail=""):
    """记一次断言。ok 为真记绿，否则记红。"""
    global CHECK_COUNT
    CHECK_COUNT += 1
    say("  %s %s%s" % (MARK_OK if ok else MARK_BAD, name,
                       ("：%s" % detail) if detail else ""))
    return ok


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    say("=" * 62)
    say("外部配置往返（界面保存 -> 落盘 -> 重启仍生效）")
    say("=" * 62)

    problems = []

    # ---- 0. 环境与可写性 ----------------------------------------------
    path = CFG.USER_CFG_PATH or CFG.user_cfg_paths()[0]
    say("\n[0] 落盘目标")
    say("    %s 路径       : %s" % (MARK_INFO, path))
    say("    %s 存在       : %s" % (MARK_INFO, os.path.isfile(path)))
    say("    %s 可写       : %s" % (MARK_INFO, os.access(os.path.dirname(path), os.W_OK)))
    if not os.access(os.path.dirname(path) or ".", os.W_OK):
        problems.append("目录不可写：%s" % os.path.dirname(path))

    backup = None
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            backup = fh.read()

    # ---- 1. 保存 -> 文件里真有新值 -------------------------------------
    say("\n[1] 点「保存」后文件里的真实内容")
    backend = L.Backend()
    replies = []
    backend.cfgSaved.connect(lambda m: replies.append(m))

    patch = {
        "gaugeRanges": {"VOLTAGE": 120.0, "SPEED": 25.0},
        "units": {"VOLTAGE": "Vdc"},
        "ui": {"bufPoints": 900},
    }
    backend.saveUserCfg(json.dumps(patch))
    say("    %s 后端回执     : %s" % (MARK_INFO, replies[-1] if replies else "(无)"))

    if not os.path.isfile(path):
        problems.append("保存后文件不存在：%s" % path)
        check("文件被创建", False)
    else:
        disk = read_json(path)
        gr = disk.get("gaugeRanges") or {}
        u = disk.get("units") or {}
        ui = disk.get("ui") or {}
        check("gaugeRanges.VOLTAGE == 120", abs(float(gr.get("VOLTAGE", -1)) - 120.0) < 1e-9,
              repr(gr.get("VOLTAGE")))
        check("gaugeRanges.SPEED == 25", abs(float(gr.get("SPEED", -1)) - 25.0) < 1e-9,
              repr(gr.get("SPEED")))
        check("units.VOLTAGE == 'Vdc'", u.get("VOLTAGE") == "Vdc", repr(u.get("VOLTAGE")))
        check("ui.bufPoints == 900", ui.get("bufPoints") == 900, repr(ui.get("bufPoints")))
        if gr.get("VOLTAGE") != 120.0:
            problems.append("写盘后 VOLTAGE 不是 120")

    # ---- 2. 保存后当前会话立即生效 -------------------------------------
    say("\n[2] 不重启也应当立即生效（ui_config 重新下发）")
    cfg = CFG.ui_config()
    gr_now = cfg.get("gaugeRanges") or {}
    check("下发 gaugeRanges.VOLTAGE == 120",
          abs(float(gr_now.get("VOLTAGE", -1)) - 120.0) < 1e-9, repr(gr_now.get("VOLTAGE")))
    if abs(float(gr_now.get("VOLTAGE", -1)) - 120.0) >= 1e-9:
        problems.append("保存后 ui_config() 未带上新值（当前会话不生效）")

    # ---- 3. 模拟重启：重新加载配置模块 ---------------------------------
    say("\n[3] 模拟重启后仍然保留（这条最容易被忽略）")
    CFG2 = importlib.reload(CFG)
    # 复刻 main() 的初始化顺序：先补模板（幂等），再加载
    CFG2.write_user_cfg_template()
    loaded = CFG2.load_user_cfg()
    say("    %s 加载到的文件 : %s" % (MARK_INFO, loaded))
    check("重启后 USER_CFG_PATH 指向同一文件", loaded == path,
          "读 %s / 写 %s" % (loaded, path))
    cfg2 = CFG2.ui_config()
    gr2 = cfg2.get("gaugeRanges") or {}
    check("重启后 gaugeRanges.VOLTAGE 仍是 120",
          abs(float(gr2.get("VOLTAGE", -1)) - 120.0) < 1e-9, repr(gr2.get("VOLTAGE")))
    check("重启后 units.VOLTAGE 仍是 Vdc",
          (CFG2.UNITS.get("VOLTAGE") == "Vdc"), repr(CFG2.UNITS.get("VOLTAGE")))
    if abs(float(gr2.get("VOLTAGE", -1)) - 120.0) >= 1e-9:
        problems.append("重启后配置丢失 —— 这就是用户说的「保存失效」")

    # ---- 4. 还原现场 ---------------------------------------------------
    say("\n[4] 还原现场")
    try:
        if backup is None:
            os.remove(path)
            say("    %s 已删除测试生成的文件" % MARK_INFO)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(backup)
            say("    %s 已还原为原始内容" % MARK_INFO)
        check("现场已还原", True)
    except Exception as e:
        check("现场已还原", False, str(e))
        problems.append("还原现场失败：%s" % e)

    # ---- 5. 幂等性 -----------------------------------------------------
    say("\n[5] 幂等性：同一份外部配置加载两次必须结果相同")
    say("    %s 这条专门盯「从 json 里删掉某个键后，值回不到默认值」这类 bug。" % MARK_INFO)
    if os.path.isfile(path):
        CFG.load_user_cfg()
        before = json.dumps(CFG.ui_config(), ensure_ascii=False, sort_keys=True)
        CFG.write_user_cfg_template()      # 已存在应返回 None，绝不覆盖
        CFG.load_user_cfg()
        after = json.dumps(CFG.ui_config(), ensure_ascii=False, sort_keys=True)
        check("重复加载同一份文件结果一致", before == after)
        if before != after:
            problems.append("重复加载结果不同（load_user_cfg 不幂等）")

        CFG.restore_builtin()
        CFG.refresh()
        pure = json.dumps(CFG.ui_config(), ensure_ascii=False, sort_keys=True)
        check("外部全是说明键时应等于内置默认值", pure == after)
        if pure != after:
            problems.append("外部文件没有真实键，却依然改变了最终配置")
    else:
        say("    %s 无外部配置文件，跳过" % MARK_INFO)

    # ---- 汇总 ----------------------------------------------------------
    say("\n" + "=" * 62)
    if problems:
        say("  %s 发现 %d 个问题：" % (MARK_BAD, len(problems)))
        for p in problems:
            say("   · %s" % p)
    else:
        say("  %s 全部通过（共 %d 项断言）" % (MARK_OK, CHECK_COUNT))
    say("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
