/**
 * _t30_bars.js —— task-30 量化证据（两个用途）
 *  ① 「所有条形的 y 坐标」：证明同类条落在同一条水平基准线上
 *  ② 「刻度标签两两包围盒相交计数」：证明 D3 修好之后标签不再互相撞（必须为 0）
 * 也顺带记录 D12 骨架态的检查素材（.is-empty 相关）。
 *
 * 用 QtWebEngine（tools/_t30_measure.py）或真机窗口（tools/_t30_grab_real.py）加载。
 */
(function () {
  function R(el) {
    /* 改前的 HTML 没有 .bandA/.bandB/.bandC —— 不能直接崩。
       2026-10-16 按用户要求删除 #zoneP2 底部整块 bandC 后，`.bandC` 这一项**必然取不到元素**
       → R() 返回 null，out.bands 里记成 {"band":".bandC","rect":null}。这是"该带确已消失"的
       预期证据，不是脚本坏了；_t30_measure.py / _t30_grab_real.py 只用 .get() 读取，不会崩。 */
    if (!el) return null;
    var b = el.getBoundingClientRect();
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
  }
  /* .xcard（bandC 内的分析卡）也随 bandC 一起删除；这里保留选择器作防御性分组，
     删除后只是匹配到的卡片变少（动力区只剩 .hcard/.scard），不会报错。 */
  function cardOf(el) { return el.closest ? el.closest('.hcard,.scard,.xcard,.dcard') : null; }
  function nameOf(card) {
    if (!card) return '?';
    var n = card.querySelector('.cap .nm') || card.querySelector('.d-name');
    return n ? n.textContent.trim() : (card.id || '?');
  }
  function inter(a, b) {
    return !(a[0] + a[2] <= b[0] || b[0] + b[2] <= a[0] || a[1] + a[3] <= b[1] || b[1] + b[3] <= a[1]);
  }

  var out = { powertrain: [], dynamics: [], bands: [], labelClash: [], labels: [] };

  [['#zoneP2 .hcard,#zoneP2 .scard,#zoneP2 .xcard', 'powertrain'],   /* .xcard 2026-10-16 已随 bandC 删除，匹配变少而已 */
   ['#zoneP1 .dcard', 'dynamics']].forEach(function (pair) {
    Array.prototype.forEach.call(document.querySelectorAll(pair[0]), function (c) {
      var kids = {};
      ['.cap', '.bar-track', '.heat', '.rev-row', '.bar-scale', '.big-num', '.ring',
       '.d-top', '.d-num', '.d-widget', '.d-scale'].forEach(function (sel) {
        var e = c.querySelector(sel);
        if (e) kids[sel] = R(e);
      });
      out[pair[1]].push({ name: nameOf(c), id: c.id || '', rect: R(c), kids: kids });
    });
  });
  ['.bandA', '.bandB', '.bandC'].forEach(function (b) {
    /* .bandC 已于 2026-10-16 按用户要求删除；这里**故意保留探测项**：
       rect=null 就是"bandC 确实不存在了"的量化证据。 */
    var e = document.querySelector(b);
    out.bands.push({ band: b, rect: R(e) });
  });

  /* 刻度标签两两相交检测（同一个卡内、不同元素） */
  var all = document.querySelectorAll(
    '#zoneP2 .bar-scale > span, #zoneP2 .bar-scale > i, #zoneP2 .bar-scale > .scale-hint, ' +
    '#zoneP1 .d-scale > i, #zoneP1 .d-scale > .sc');
  Array.prototype.forEach.call(all, function (el) {
    var card = cardOf(el);
    if (!card) return;
    out.labels.push({ card: nameOf(card), t: el.textContent.trim(), r: R(el) });
  });
  for (var i = 0; i < out.labels.length; i++) {
    for (var j = i + 1; j < out.labels.length; j++) {
      var a = out.labels[i], b = out.labels[j];
      if (a.card !== b.card) continue;
      if (inter(a.r, b.r)) out.labelClash.push([a.card, a.t, b.t, a.r, b.r]);
    }
  }
  var st = document.getElementById('stage');
  out.stageScrollH = st ? st.scrollHeight : null;
  out.emptyState = {
    ringsEmptyClass: document.querySelectorAll('.ring-val .v.is-empty').length,
    cardsWithDataEmpty: document.querySelectorAll('#zoneP2 [data-empty]').length,
    sample: (function () {
      var c = document.querySelector('#zoneP2 .hcard[data-empty]');
      return c ? c.getAttribute('data-empty') : null;
    })()
  };
  return JSON.stringify(out);
})()
