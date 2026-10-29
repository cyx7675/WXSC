# -*- coding: utf-8 -*-
"""range_editor_check.py —— ⚠️ 已停用（2026-10-06）

「设置 → 量程与单位」在线编辑器已按用户要求整块移除，本脚本依赖的
#rngGrid / #rngOrder / #cfgOrigin 等元素都不存在了，跑必然失败。
已从 build_web.py 的步骤 4.7 摘掉，文件保留作历史参考 —— 若哪天要把
编辑器加回来，照这里列出的三件事重新验一遍即可。

免打包改配置的唯一入口现在只剩 exe 旁的 telemetry_user.json（重启生效），
后端 telemetry_config.load_user_cfg() 仍然按
    telemetry_user.json > telemetry_config.py > app.js 兜底值
的优先级读取。

---- 以下是原始说明 ----
验证「设置 → 量程与单位」在线编辑器真的能改界面。

为什么单独验它
--------------
这个编辑器是「改上位机不用重新打包」的入口，一旦它改了界面却不生效，
用户会当成又一轮「改了 config 没反应」。而 matrix 自检只覆盖后端下发的链路，
覆盖不到「用户在界面里直接改」这条路径。

验三件事
--------
1. 容器生成：量程表格行数 = 通用信号 + ADC 通道 + 2 行界面参数；
2. 改一格量程 -> 点「应用」-> 对应条形图的**刻度文案**和**内部 SIGS.max**都变；
3. 改一格单位 -> 点「应用」-> 单位后缀跟着变；
4. 无后端时点「保存」不能崩（应给出明确提示，而不是白屏或静默失败）。

用法
----
    cd F:\\上位机\\v2
    set QT_QPA_PLATFORM=offscreen
    <venv>\\Scripts\\python.exe tools\\range_editor_check.py
"""
import os
import sys
import json

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-gpu --no-sandbox --disable-software-rasterizer")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QUrl, QTimer              # noqa: E402
from PyQt6.QtWidgets import QApplication           # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1440, 900)
    view.show()
    res = {}

    def on_load(ok):
        QTimer.singleShot(1200, lambda: view.page().runJavaScript(
            "JSON.stringify({rows: document.querySelectorAll('#rngGrid .rng-row').length,"
            " errs: window.__errs || []})", probe))

    from PyQt6.QtWebEngineCore import QWebEnginePage

    class P(QWebEnginePage):
        def javaScriptConsoleMessage(self, l, m, ln, s):
            pass

    def probe(v):
        res["base"] = v
        view.page().runJavaScript(EDIT_JS, finish)

    def finish(v):
        res["after"] = v
        view.page().runJavaScript(SAVE_JS, done)

    def done(v):
        res["save"] = v
        view.page().runJavaScript(ORIGIN_JS, finish2)

    def finish2(v):
        res["origin"] = v
        app.quit()

    view.loadFinished.connect(on_load)
    view.load(QUrl.fromLocalFile(os.path.join(HERE, "telemetry_web", "index.html")))
    QTimer.singleShot(30000, app.quit)
    app.exec()

    try:
        base = json.loads(res.get("base") or "{}")
    except Exception:
        base = {}
    try:
        after = json.loads(res.get("after") or "{}")
    except Exception:
        after = {}
    save_noop = res.get("save")

    print("=" * 64)
    print("量程 / 单位在线编辑器")
    print("=" * 64)
    problems = []

    rows = base.get("rows", 0)
    print("  编辑器行数        : %s" % rows)
    if rows < 15:
        problems.append("量程表格只生成了 %s 行（应 >=15：通用信号+ADC通道+界面参数）" % rows)

    if base.get("errs"):
        problems.append("页面初始化就有 JS 错误：%s" % base["errs"])

    print("  改电压量程 600->120")
    print("    刻度文案        : %r" % after.get("voltScale"))
    print("    SIGS.volt.max   : %r" % after.get("voltMax"))
    if after.get("voltMax") != 120:
        problems.append("改了量程但 SIGS.volt.max=%r（应为 120）" % after.get("voltMax"))
    if "120" not in str(after.get("voltScale", "")):
        problems.append("改了量程但刻度文案还是 %r" % after.get("voltScale"))

    print("  改电压单位 V->Vdc")
    print("    单位后缀        : %r" % after.get("voltUnit"))
    if "Vdc" not in str(after.get("voltUnit", "")):
        problems.append("改了单位但后缀还是 %r" % after.get("voltUnit"))

    print("  无后端点「保存」  : %r" % save_noop)
    if isinstance(save_noop, str) and "Traceback" in save_noop:
        problems.append("无后端点保存把页面搞崩了：%s" % save_noop[:120])

    errs = after.get("errs") or []
    if errs:
        problems.append("编辑过程中出现 JS 错误：%s" % errs[:3])

    # ---- 生效来源可视化：回答「我改的值到底从哪来的」 ----
    try:
        org = json.loads(res.get("origin") or "{}")
    except Exception:
        org = {}
    print("  生效来源可视化")
    print("    状态条存在      : %r" % org.get("hasBox"))
    print("    无覆盖时样式    : %r" % org.get("clsBuiltin"))
    print("    注入覆盖后样式  : %r" % org.get("clsExt"))
    print("    EXT 标记行数    : %r" % org.get("extRows"))
    print("    恢复内置按钮    : %r" % org.get("hasClearBtn"))
    if not org.get("hasBox"):
        problems.append("找不到 #cfgOrigin 状态条 —— 用户无法知道当前配置来源")
    if "src-builtin" not in str(org.get("clsBuiltin", "")):
        problems.append("无外部覆盖时应显示 src-builtin，实际 %r" % org.get("clsBuiltin"))
    if "src-ext" not in str(org.get("clsExt", "")):
        problems.append("有外部覆盖时应显示 src-ext，实际 %r" % org.get("clsExt"))
    if not org.get("extRows"):
        problems.append("注入外部覆盖后没有任何行被标记 EXT")
    if not org.get("hasClearBtn"):
        problems.append("缺少「恢复为内置配置」按钮 —— 覆盖值无法一键清除")

    print()
    if problems:
        for p in problems:
            print("  [FAIL]", p)
        return 1
    print("  [OK] 量程与单位可以在界面里直接改并立即生效；无后端保存也不会崩")
    return 0


EDIT_JS = r"""
(function () {
  function rowByName(name){
    var rows = Array.prototype.slice.call(document.querySelectorAll('#rngGrid .rng-row'));
    for (var i = 0; i < rows.length; i++){
      var lab = rows[i].querySelector('.rn');
      if (lab && lab.textContent.indexOf(name) >= 0) return rows[i];
    }
    return null;
  }
  var r = rowByName('volt');
  if (r){
    r.querySelector('.mx').value = '120';
    r.querySelector('.un').value = 'Vdc';
  }
  document.getElementById('rngApply').click();
  var card = document.getElementById('voltBar').closest('.scard');
  var sc = card ? card.querySelector('.bar-scale') : null;
  var uel = document.getElementById('voltUnit');
  return JSON.stringify({
    voltScale: sc ? sc.textContent : null,
    voltUnit: uel ? uel.textContent : null,
    voltMax: (typeof SIGS !== 'undefined' && SIGS['volt']) ? SIGS['volt'].max : null,
    errs: window.__errs || []
  });
})()
"""

SAVE_JS = r"""
(function () {
  try {
    document.getElementById('rngSave').click();
    return 'clicked-ok';
  } catch (e) {
    return 'Traceback:' + e.message;
  }
})()
"""

# 生效来源可视化：默认应显示 builtin；注入 overrides 后应变 ext 并给行打 EXT 角标。
ORIGIN_JS = r"""
(function () {
  var box = document.getElementById('cfgOrigin');
  if (!box) return JSON.stringify({hasBox: false, errs: window.__errs || []});
  var out = {hasBox: true};
  out.clsBuiltin = box.className;
  out.hasClearBtn = !!document.getElementById('rngClear');
  /* 模拟「外部 json 正在覆盖 VOLTAGE 与第 1 个 ADC 通道」 */
  CFG_USER.loaded = true;
  CFG_USER.path = 'telemetry_user.json';
  CFG_USER.overrides = {gaugeRanges: ['VOLTAGE'], units: [], channels: [0]};
  syncCfgOrigin();
  applyOverrideMarks();
  out.clsExt = box.className;
  out.extRows = document.querySelectorAll('#rngGrid .rng-row.ext').length;
  out.coText = document.getElementById('coText') ? document.getElementById('coText').textContent : null;
  out.errs = window.__errs || [];
  return JSON.stringify(out);
})()
"""

if __name__ == "__main__":
    sys.exit(main())
