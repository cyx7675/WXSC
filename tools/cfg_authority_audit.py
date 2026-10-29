# -*- coding: utf-8 -*-
"""cfg_authority_audit.py —— 「改了到底哪里生效」权威性审计。

它回答一个用户天天问、但代码里从来没说清楚的问题：
    ★ 我想改这个值 —— 到底该改哪个文件？要不要打包？会不会被别处悄悄覆盖？

为什么需要
--------------------------------------------------------------------------
本项目历史上反复出现同一类事故：同一个显示参数在 2~3 个地方都有定义，
改了 A 处，实际生效的是优先级更高的 B 处，于是不报错、所有常规自检全绿，
只有人眼能看出来「界面没变」。典型的三层：

    app.js 顶部兜底值          ← 最低优先级（无后端时的演示模式才用）
         ↑ 被下发覆盖
    telemetry_config.py        ← 内置真源（改它要重新打包）
         ↑ 被外部文件覆盖
    telemetry_user.json        ← 最高优先级（免打包，重启生效）

只要 Python 侧又悄悄加了一个 UI_CFG 之类的键却忘了同步到 app.js（或反过来），
用户改了就是白改。本脚本把这些「层叠关系」逐项钉死：

  1. 键集合比对：config 里有的、前端是否真的消费；前端消费的、config 是否可配
  2. 层叠验证：改 config 后，是否被更高优先级的 json 覆盖
  3. 孤儿键：config 里写了但前端不认（改了没反应）
  4. 影子键：前端在用但 config 配不到（只能改前端 + 重新打包）

用法： python tools/cfg_authority_audit.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import telemetry_config as CFG                    # noqa: E402

APP_JS = os.path.join(HERE, "telemetry_web", "app.js")

OK = "[OK]"
BAD = "[!!]"
WARN = "[??]"
INFO = " -- "

problems = []
n_check = 0


def say(line=""):
    print(line)


def check(name, ok, detail="", fatal=True):
    global n_check
    n_check += 1
    say("  %s %s%s" % (OK if ok else (BAD if fatal else WARN), name,
                       ("：%s" % detail) if detail else ""))
    if not ok and fatal:
        problems.append(name)
    return ok


def js_source():
    with open(APP_JS, "r", encoding="utf-8") as fh:
        return fh.read()


def js_ui_cfg_block():
    """抠出 app.js 顶部那个 UI_CFG 字面量里的键（只认顶层缩进两格的键）。"""
    src = js_source()
    m = re.search(r"^var UI_CFG = \{(.*?)^\n", src, re.S | re.M)
    if not m:
        m = re.search(r"var UI_CFG = \{(.*?)\};", src, re.S)
    if not m:
        return {}
    body = m.group(1)
    return set(re.findall(r"^\s{2}([A-Za-z_]\w*)\s*:", body, re.M))


def js_consumed_ui_keys():
    """前端实际读到的 UI_CFG.xxx。"""
    src = js_source()
    # 排除定义块本身，只看消费点；这里简单起见全量取再减去定义键也能得到近似结论，
    # 但定义键本身也是"前端认得"的证据，所以合并返回。
    return set(re.findall(r"UI_CFG\.([A-Za-z_]\w*)", src))


def ui_cfg_cascade():
    """验证 UI_CFG 是否被 json 正确覆盖（且不影响无关键）。"""
    tmpdir = tempfile.mkdtemp(prefix="cfgauthority_")
    tmp = os.path.join(tmpdir, CFG.USER_CFG_NAME)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"ui": {"bufPoints": 111, "trendFps": 12}}, fh)

    orig_paths = CFG.user_cfg_paths
    try:
        CFG.user_cfg_paths = lambda: [tmp]         # 隔离：绝不碰用户的真实 json
        loaded = CFG.load_user_cfg()
        cfg = CFG.ui_config()
    finally:
        CFG.user_cfg_paths = orig_paths
        CFG.load_user_cfg()
    return loaded == tmp, cfg.get("ui") or {}


def main():
    say("=" * 66)
    say("配置权威性审计（改哪里才真的生效）")
    say("=" * 66)

    # ------------------------------------------------------------------
    say("\n[1] 单值权威：同一个参数不允许有两个真源")
    say("    %s 优先级（高 -> 低）" % INFO)
    say("        ① telemetry_user.json  >  ② telemetry_config.py  >  ③ app.js 兜底")

    cfg_ui = set(CFG.UI_CFG.keys())
    js_def = js_ui_cfg_block()
    js_use = js_consumed_ui_keys()

    say("\n[2] UI_CFG 的双份问题（config.py 第 5 区 vs app.js 顶部）")
    say("    %s config.py 可配     : %s" % (INFO, sorted(cfg_ui)))
    say("    %s app.js   定义了    : %s" % (INFO, sorted(js_def)))
    say("    %s app.js   实际消费  : %s" % (INFO, sorted(js_use)))

    shadow = sorted(js_def - cfg_ui)
    if shadow:
        say("    %s 下列参数【config.py 里根本没有】，改配置文件无从下手，" % INFO)
        say("        只能改 app.js 后重新打包：%s" % ", ".join(shadow))
        problems.append("存在 config.py 配不到的前端参数：%s" % ", ".join(shadow))
        check("所有前端参数都能在 config.py 里配", False, "影子参数 %d 个" % len(shadow))
    else:
        check("所有前端参数都能在 config.py 里配", True)

    orphan = sorted(cfg_ui - js_use)
    if orphan:
        say("    %s config.py 里这些键前端从来不读（改了没反应）：%s"
            % (INFO, ", ".join(orphan)))
        check("无孤儿键（config 写了前端不用）", False, ", ".join(orphan))
    else:
        check("无孤儿键（config 写了前端不用）", True)

    # ------------------------------------------------------------------
    say("\n[3] 层叠验证：json 必须能覆盖 config.py（否则「免打包改配置」是假的）")
    ok_path, ui = ui_cfg_cascade()
    check("外部 json 被优先加载", ok_path)
    check("json.ui.bufPoints 覆盖生效", ui.get("bufPoints") == 111, repr(ui.get("bufPoints")))
    check("json.ui.trendFps 覆盖生效", ui.get("trendFps") == 12, repr(ui.get("trendFps")))

    # ------------------------------------------------------------------
    say("\n[4] 这里才是「改了 config.py 打包后界面不变」的元凶")
    say("    %s 只要 telemetry_user.json 里存在同名的真实键，" % INFO)
    say("        它就永远赢过 telemetry_config.py —— 你改 py 再打包也没用。")
    if os.path.isfile(CFG.user_cfg_paths()[0]):
        with open(CFG.user_cfg_paths()[0], "r", encoding="utf-8") as fh:
            disk = json.load(fh)
        effective = {}
        for sec in ("gaugeRanges", "units", "scales", "serial", "ui"):
            s = disk.get(sec)
            if isinstance(s, dict):
                real = [k for k in s.keys() if not str(k).startswith("_")]
                if real:
                    effective[sec] = real
        if effective:
            say("    %s 当前外部文件里的【真实生效键】：" % INFO)
            for sec, keys in effective.items():
                say("        · %-12s %s" % (sec, ", ".join(keys)))
            say("    %s 它们正在覆盖 telemetry_config.py 的同名项。" % INFO)
        else:
            say("    %s 当前外部文件全是说明键（_%s），暂未覆盖任何内置值。"
                % (INFO, "示例_"))
    else:
        say("    %s 当前没有外部配置文件。" % INFO)

    # ------------------------------------------------------------------
    say("\n[5] 常见改动 -> 该动哪一个文件（结论表）")
    rows = [
        ("界面量程/单位", "设置 → 量程与单位（在线改）", "不用打包", "立即"),
        ("通道名/颜色/波特率", "telemetry_user.json", "不用打包", "重启"),
        ("换算系数 scale / 字段 / 包类型", "telemetry_config.py 第 4 区", "要打包", "重启"),
        ("默认曲线 / 曲线窗口 / 刷新率", "telemetry_config.py 第 5 区 UI_CFG", "要打包", "重启"),
        ("布局 / 新增显示块", "index.html + app.js", "要打包", "重启"),
    ]
    say("    %-34s %-38s %-8s %s" % ("我想改…", "去哪里改", "打包?", "生效时机"))
    say("    " + "-" * 88)
    for want, where, need, when in rows:
        say("    %-34s %-38s %-8s %s" % (want, where, need, when))

    # ------------------------------------------------------------------
    say("\n" + "=" * 66)
    if problems:
        say("  %s 发现 %d 个问题：" % (BAD, len(problems)))
        for p in problems:
            say("   · %s" % p)
    else:
        say("  %s 全部通过（共 %d 项断言）" % (OK, n_check))
    say("=" * 66)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
