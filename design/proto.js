/* proto.js —— task-26 设计提案用的小型绘图/数据层（**提案原型，不是产品代码**）
 *
 * 设计约束（与产品一致，保证"设计落在可实现范围内"）：
 *   · 纯手写 HTML/CSS/JS，无任何外部字体/CDN/网络资源
 *   · 高频元素不带 transition>50ms / will-change / content-visibility
 *   · 仪表用 <canvas> 绘制（与产品里矢量图/趋势图同一套做法）；
 *     **注意**：产品里 SVG 表盘的指针用 `translate(a b) rotate(deg)` 会被
 *     overflow:hidden 裁掉（详见 tools/_ux_needle_probe.py）——canvas 天然没有这个坑。
 *   · 数据是**真机量级**：车速 31.2 m/s / 转速 5200 RPM / 电压 443.9 V /
 *     电流 150.0 A / 功率 66.6 kW / 加速度 ±1 g，且有"正常 / 告警"两态。
 *
 * 用法：页面里写 `<canvas data-w="ring" data-sig="speed"></canvas>`，
 *      本文件扫描 [data-w] 并按类型填充；`protoSetMode('alarm')` 整体切到告警态。
 */
(function () {
  'use strict';

  /* ---------- 0. 确定性伪随机（截图可复现） ---------- */
  var _seed = 20261016;
  function rnd() { _seed = (_seed * 1103515245 + 12345) & 0x7fffffff; return _seed / 0x7fffffff; }
  function noise(a) { return (rnd() - 0.5) * 2 * a; }

  /* ---------- 1. 信号定义（真机量级 + 阈值语义） ---------- */
  // threshold: {warn, over} 用于状态色；fs = 满量程
  var SIG = {
    speed:    { label: '车速',     unit: 'm/s', dec: 2, fs: 40,    warn: 34,   over: 38 },
    rpm:      { label: '电机转速', unit: 'RPM', dec: 0, fs: 10000, warn: 8000, over: 9500, lim: 3800 },
    throttle: { label: '油门开度', unit: '%',   dec: 0, fs: 100,   warn: 95,   over: 100 },
    torque:   { label: '扭矩',     unit: 'N·m', dec: 1, fs: 150,   warn: 135,  over: 148 },
    temp:     { label: '电机温度', unit: '°C',  dec: 0, fs: 120,   warn: 70,   over: 90 },
    volt:     { label: '母线电压', unit: 'V',   dec: 1, fs: 600,   warn: 0,    over: 0, lo: 380, hi: 520 },
    amp:      { label: '母线电流', unit: 'A',   dec: 1, fs: 200,   warn: 180,  over: 195 },
    power:    { label: '电池功率', unit: 'kW',  dec: 1, fs: 120,   warn: 68,   over: 80 },
    roll:     { label: '横滚 ROLL',  unit: '°', dec: 1, fs: 30,  warn: 22, over: 28 },
    pitch:    { label: '俯仰 PITCH', unit: '°', dec: 1, fs: 20,  warn: 12, over: 16 },
    yaw:      { label: '航向 YAW',   unit: '°', dec: 0, fs: 360, warn: 0,  over: 0 },
    accLat:   { label: '横向加速度', unit: 'g', dec: 2, fs: 1.5,  warn: 1.2, over: 1.45 },
    accLon:   { label: '纵向加速度', unit: 'g', dec: 2, fs: 1.5,  warn: 1.2, over: 1.45 },
    accMag:   { label: '合成加速度', unit: 'g', dec: 2, fs: 1.5,  warn: 1.2, over: 1.45 }
  };

  // ADC 10 路（名称/单位取自真机界面：悬架位移 mm、转向角 °、压力 kg）
  var ADC = [
    { ch: 'CH01', name: '悬架位移1',  unit: 'mm', v: 2063.0, min: 0, max: 4095 },
    { ch: 'CH02', name: '悬架位移2',  unit: 'mm', v: 2091.0, min: 0, max: 4095 },
    { ch: 'CH03', name: '悬架位移3',  unit: 'mm', v: 2080.0, min: 0, max: 4095 },
    { ch: 'CH04', name: '悬架位移4',  unit: 'mm', v: 2042.0, min: 0, max: 4095 },
    { ch: 'CH05', name: '转向位移',   unit: 'mm', v: 2054.0, min: 0, max: 4095 },
    { ch: 'CH06', name: '转向角位移', unit: '°',  v: 21.4,   min: 0, max: 4095 },
    { ch: 'CH07', name: '压力传感器1', unit: 'kg', v: 1895.0, min: 0, max: 4095 },
    { ch: 'CH08', name: '压力传感器2', unit: 'kg', v: 1797.0, min: 0, max: 4095 },
    { ch: 'CH09', name: '压力传感器3', unit: 'kg', v: 1639.0, min: 0, max: 4095 },
    { ch: 'CH10', name: '压力传感器4', unit: 'kg', v: 1486.0, min: 0, max: 4095 }
  ];

  /* ---------- 2. 两条"这一跑"的物理自洽序列（30 s @ 20 Hz = 600 点） ----------
     normal = 一圈带制动与弯道的巡航；alarm = 连续全油门的"热圈"。
     所有量互相自洽：torque = P/(ω)，amp = P/0.9/V，volt = 492 - amp*0.1，
     power = volt*amp/1000，所以曲线之间的关系是真的（不是各自随机的）。 */
  var N = 600, DT = 0.05;

  function profile(kind) {
    var out = [], sp = 0, temp = 46, thrS = 0.4;
    for (var i = 0; i < N; i++) {
      var t = i * DT, u = t % 36;
      var thr, br;
      if (kind === 'alarm') {
        thr = u < 30 ? 0.94 : 0.80; br = 0;                 // 长时间全油门 -> 功率/温度超限
      } else {
        if (u < 9)        { thr = 0.88; br = 0; }
        else if (u < 11)  { thr = 0.55; br = 0; }
        else if (u < 13.5){ thr = 0;    br = 1; }
        else if (u < 20)  { thr = 0.30; br = 0; }
        else if (u < 26)  { thr = 0.86; br = 0; }
        else if (u < 30)  { thr = 0.25; br = 0.2; }
        else if (u < 33)  { thr = 0;    br = 0.9; }
        else              { thr = 0.62; br = 0; }
      }
      // 一阶滞后：真实油门/扭矩不可能瞬间跳变（否则趋势图会出现方波，看着假）
      thrS += (thr - thrS) * 0.13;
      var drag = 0.0032 * sp * sp;
      var a = thrS * (kind === 'alarm' ? 5.6 : 4.6) - drag - br * 8.0;
      sp = Math.max(0, Math.min(kind === 'alarm' ? 38 : 36, sp + a * DT));
      var gr = 168;
      var rpm = Math.max(900, Math.min(9800, 900 + sp * gr + noise(30)));
      var kw = thrS * (kind === 'alarm' ? 92 : 74);          // 目标机械功率 kW
      var tq = Math.min(150, kw / Math.max(1, rpm * 0.00010472) / 1000 * 1000);
      var volt = Math.max(300, 492 + noise(1.2));
      var amp = Math.min(200, kw * 1000 / 0.9 / volt);
      volt = Math.max(300, volt - amp * 0.10);
      var pw = volt * amp / 1000;
      temp += ((44 + amp * 0.33) - temp) * 0.02;
      out.push({ sp: sp, rpm: rpm, thr: thrS * 100, tq: tq, temp: temp,
                 volt: volt, amp: amp, pw: pw });
    }
    return out;
  }

  // 把序列尾端平滑地拉到目标当前值（保证"大数字"与"曲线末端"一致）
  function blendTail(arr, target, k) {
    k = k || 18;
    var n = arr.length, old = arr[n - 1];
    for (var i = 0; i < k; i++) {
      var w = (i + 1) / k, idx = n - k + i;
      arr[idx] = arr[idx] + (target - old) * w;
    }
    arr[n - 1] = target;
    return arr;
  }
  function pick(p, key) { var a = [], i; for (i = 0; i < p.length; i++) a.push(p[i][key]); return a; }

  var RAW = { normal: profile('normal'), alarm: profile('alarm') };

  /* ---------- 3. 当前状态（normal / alarm） ---------- */
  var TARGET = {
    normal: { speed: 31.2, rpm: 5200, throttle: 88, torque: 96.4, temp: 63,
              volt: 443.9, amp: 150.0, power: 66.6,
              roll: 0.0, pitch: -0.9, yaw: 0.0,
              accLat: 0.35, accLon: 0.62, accMag: 0.71 },
    alarm:  { speed: 36.4, rpm: 7450, throttle: 94, torque: 132.7, temp: 96,
              volt: 419.6, amp: 202.0, power: 84.8,
              roll: -11.4, pitch: 1.8, yaw: 47.0,
              accLat: 1.28, accLon: 1.36, accMag: 1.87 }
  };

  var MODE = 'normal';
  var V = {};       // 当前值
  var S = {};       // 当前序列

  function rebuild() {
    var p = RAW[MODE], tg = TARGET[MODE], k;
    S = {
      speed: blendTail(pick(p, 'sp'), tg.speed),
      rpm: blendTail(pick(p, 'rpm'), tg.rpm),
      throttle: blendTail(pick(p, 'thr'), tg.throttle),
      torque: blendTail(pick(p, 'tq'), tg.torque),
      temp: blendTail(pick(p, 'temp'), tg.temp, 60),
      volt: blendTail(pick(p, 'volt'), tg.volt),
      amp: blendTail(pick(p, 'amp'), tg.amp),
      power: blendTail(pick(p, 'pw'), tg.power)
    };
    V = {};
    for (k in tg) V[k] = tg[k];
    // 派生量：合成加速度
    V.accMag = Math.sqrt(V.accLat * V.accLat + V.accLon * V.accLon);
    for (k in SIG) if (typeof V[k] === 'undefined') V[k] = 0;
    // "本跑峰值"：<id>-mx（趋势区/磁贴里显示"这一跑最大拉到多少"）
    for (k in S) {
      if (!S[k] || !S[k].length || k.indexOf('adc') === 0) continue;
      V[k + '-mx'] = Math.max.apply(null, S[k]);
      SIG[k + '-mx'] = { label: '峰值', unit: (SIG[k] && SIG[k].unit) || '',
                         dec: (SIG[k] && SIG[k].dec) || 0, fs: (SIG[k] && SIG[k].fs) || 1 };
    }
    // ADC 10 路的迷你趋势（悬架/压力各自缓慢波动，不是同一条曲线）
    var seed0 = (MODE === 'alarm') ? 771 : 313;
    for (var a = 0; a < ADC.length; a++) {
      var arr = [], base = ADC[a].v, amp = base > 1000 ? base * 0.012 : 1.6;
      _seed = seed0 + a * 977;
      for (var j = 0; j < 60; j++) base += noise(amp) - (base - ADC[a].v) * 0.12, arr.push(base);
      S['adc' + a] = arr;
    }
  }

  /* ---------- 4. 状态判定（供各方向统一用色） ---------- */
  function stateOf(id) {
    var s = SIG[id]; if (!s) return 'ok';
    var v = V[id];
    if (id === 'volt') return (v < s.lo || v > s.hi) ? 'warn' : 'ok';
    if (!s.over && !s.warn) return 'ok';
    if (s.over && v >= s.over) return 'over';
    if (s.warn && v >= s.warn) return 'warn';
    return 'ok';
  }
  function fmt(id, v) {
    var s = SIG[id]; if (!s) return String(v);
    v = (typeof v === 'number') ? v : V[id];
    var t = v.toFixed(s.dec);
    if (s.dec === 0 && Math.abs(v) >= 1000) {
      t = v.toLocaleString('en-US', { maximumFractionDigits: 0 });
    }
    return t;
  }

  /* ---------- 5. canvas 工具 ---------- */
  function layer(cv) {
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var w = cv.clientWidth || 120, h = cv.clientHeight || 60;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }
  function cssVar(name, fb) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fb;
  }
  var C = {
    teal: function () { return cssVar('--c-ok', '#0E8A62'); },
    amber: function () { return cssVar('--c-warn', '#B4783F'); },
    red: function () { return cssVar('--c-over', '#C8393B'); },
    cyan: function () { return cssVar('--c-1', '#0B6EA8'); },
    blue: function () { return cssVar('--c-2', '#4C6FD4'); },
    violet: function () { return cssVar('--c-3', '#7C5CC4'); },
    lime: function () { return cssVar('--c-4', '#5E8F2A'); },
    ink: function () { return cssVar('--ink', '#141C29'); },
    dim: function () { return cssVar('--ink-dim', '#5D6981'); },
    line: function () { return cssVar('--line', '#D8DEE8'); },
    track: function () { return cssVar('--track', '#E3E6EB'); }
  };
  function stateColor(st) { return st === 'over' ? C.red() : st === 'warn' ? C.amber() : C.teal(); }

  /* ---------- 6. 各组件绘制 ---------- */

  // 6.1 环形 / 半环仪表（canvas；刻度、值弧、指针、中心读数）
  function drawRing(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h;
    var sweep = o.sweep, start = -90 - sweep / 2;          // 从正上方左右展开
    var cx = w / 2, cy = o.cy !== undefined ? o.cy : h * 0.56;
    var R = Math.min(w / 2 - (o.pad || 10), (o.cy !== undefined ? h - o.cy - 6 : h * 0.44));
    var sig = SIG[o.sig], st = stateOf(o.sig);
    var frac = Math.max(0, Math.min(1, V[o.sig] / (o.fs || sig.fs)));
    var a0 = start * Math.PI / 180, a1 = (start + sweep) * Math.PI / 180;
    var av = a0 + (a1 - a0) * frac;

    ctx.lineCap = 'round';
    ctx.lineWidth = o.rw || 9;
    ctx.strokeStyle = C.track();
    ctx.beginPath(); ctx.arc(cx, cy, R, a0, a1); ctx.stroke();

    // 值弧（语义色）
    ctx.strokeStyle = stateColor(st);
    ctx.beginPath(); ctx.arc(cx, cy, R, a0, av); ctx.stroke();

    // 限扭/阈值刻度线
    if (sig.lim) {
      var al = (start + sweep * (sig.lim / sig.fs)) * Math.PI / 180;
      ctx.strokeStyle = C.dim(); ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(al) * (R - (o.rw || 9) / 2 - 2), cy + Math.sin(al) * (R - (o.rw || 9) / 2 - 2));
      ctx.lineTo(cx + Math.cos(al) * (R + (o.rw || 9) / 2 + 3), cy + Math.sin(al) * (R + (o.rw || 9) / 2 + 3));
      ctx.stroke();
    }
    if (o.warnAt) {
      var aw = (start + sweep * (o.warnAt / (o.fs || sig.fs))) * Math.PI / 180;
      ctx.strokeStyle = C.red(); ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(aw) * (R - (o.rw || 9) / 2 - 3), cy + Math.sin(aw) * (R - (o.rw || 9) / 2 - 3));
      ctx.lineTo(cx + Math.cos(aw) * (R + (o.rw || 9) / 2 + 4), cy + Math.sin(aw) * (R + (o.rw || 9) / 2 + 4));
      ctx.stroke();
    }
    // 刻度
    var n = o.ticks || 0;
    for (var i = 0; i <= n; i++) {
      var aa = (start + sweep * i / n) * Math.PI / 180, big = (i % 2 === 0);
      var r1 = R - (o.rw || 9) / 2 - 3, r2 = r1 - (big ? 7 : 4);
      ctx.strokeStyle = C.line(); ctx.lineWidth = big ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(aa) * r1, cy + Math.sin(aa) * r1);
      ctx.lineTo(cx + Math.cos(aa) * r2, cy + Math.sin(aa) * r2);
      ctx.stroke();
    }
    // 指针（canvas：没有 SVG 那个 translate+rotate 的坑）
    if (o.needle !== false) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(av + Math.PI / 2);           // 指针以"指向正上方"为基
      // 先描一圈暗色再画亮色：指到浅色弧上时也看得清（深色主题必需）
      ctx.strokeStyle = cssVar('--panel', '#121A22');
      ctx.lineWidth = (o.nw || 2.6) + 2.4; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(0, 6); ctx.lineTo(0, -R + (o.rw || 9) / 2 + 6); ctx.stroke();
      ctx.strokeStyle = st === 'ok' ? C.ink() : stateColor(st);
      ctx.lineWidth = o.nw || 2.6;
      ctx.beginPath(); ctx.moveTo(0, 6); ctx.lineTo(0, -R + (o.rw || 9) / 2 + 6); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = st === 'ok' ? C.ink() : stateColor(st);
      ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.fill();
    }
    // 中心读数：先在指针上"挖"一块透明，再写数字 —— 否则读数被指针穿过（经典仪表坑）
    if (o.center !== false) {
      ctx.save();
      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath(); ctx.arc(cx, cy, o.vsize ? o.vsize * 0.92 : 30, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
      ctx.textAlign = 'center';
      ctx.fillStyle = st === 'ok' ? C.ink() : stateColor(st);
      ctx.font = '700 ' + (o.vsize || 30) + 'px Consolas,monospace';
      ctx.textBaseline = 'alphabetic';
      ctx.fillText(fmt(o.sig), cx, cy + (o.vy || 2));
      ctx.fillStyle = C.dim();
      ctx.font = '600 12px Consolas,monospace';
      ctx.fillText(sig.unit, cx, cy + (o.vy || 2) + 16);
      if (o.label) {
        ctx.fillStyle = C.dim();
        ctx.font = '600 12px "Segoe UI","Microsoft YaHei",sans-serif';
        ctx.fillText(sig.label, cx, cy - R - 4);
      }
    }
  }

  // 6.2 迷你趋势（sparkline，可带峰值标记 / 面积填充 / 双轴）
  function drawSpark(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h, pad = 3;
    var arr = S[o.sig] || [];
    if (o.sig2 && S[o.sig2]) { drawSeries(ctx, w, h, pad, arr, C.cyan(), o.fill !== false); drawSeries(ctx, w, h, pad, S[o.sig2], C.amber(), false); return; }
    drawSeries(ctx, w, h, pad, arr, o.color || stateColor(stateOf(o.sig)), o.fill !== false);
    if (o.peak && arr.length) {
      var mx = -Infinity, mi = 0;
      for (var i = 0; i < arr.length; i++) if (arr[i] > mx) { mx = arr[i]; mi = i; }
      var x = pad + (w - pad * 2) * mi / (arr.length - 1);
      var s = SIG[o.sig], lo = Math.min.apply(null, arr), hi = Math.max.apply(null, arr);
      var y = h - pad - (h - pad * 2) * (mx - lo) / ((hi - lo) || 1);
      ctx.strokeStyle = C.dim(); ctx.lineWidth = 1; ctx.setLineDash([2, 2]);
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, h - pad); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = C.dim(); ctx.font = '600 10px Consolas,monospace';
      ctx.textAlign = x > w - 40 ? 'right' : 'left';
      ctx.fillText('峰值 ' + fmt(o.sig, mx), x + (x > w - 40 ? -3 : 3), y - 2);
    }
  }
  function drawSeries(ctx, w, h, pad, arr, color, fill) {
    if (!arr || arr.length < 2) return;
    var lo = Math.min.apply(null, arr), hi = Math.max.apply(null, arr);
    if (hi - lo < 1e-6) { hi = lo + 1; }
    var n = arr.length, i, x, y;
    ctx.beginPath();
    for (i = 0; i < n; i++) {
      x = pad + (w - pad * 2) * i / (n - 1);
      y = h - pad - (h - pad * 2) * (arr[i] - lo) / (hi - lo);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    if (fill) {
      var g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, color + '55'); g.addColorStop(1, color + '05');
      ctx.save();
      ctx.lineTo(w - pad, h - pad); ctx.lineTo(pad, h - pad); ctx.closePath();
      ctx.fillStyle = g; ctx.fill(); ctx.restore();
      ctx.beginPath();
      for (i = 0; i < n; i++) {
        x = pad + (w - pad * 2) * i / (n - 1);
        y = h - pad - (h - pad * 2) * (arr[i] - lo) / (hi - lo);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.lineJoin = 'round';
    ctx.stroke();
  }

  // 6.3 分段温度带（三段底纹 + 当前值标记 + 阈值刻线）
  function drawBand(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h;
    var sig = SIG[o.sig], zones = o.zones || [70, 90], st = stateOf(o.sig);
    var frac = Math.max(0, Math.min(1, V[o.sig] / sig.fs));
    ctx.fillStyle = cssVar('--track', '#E3E6EB');
    ctx.fillRect(0, 6, w, h - 12);
    var x1 = w * zones[0] / sig.fs, x2 = w * zones[1] / sig.fs;
    ctx.fillStyle = 'rgba(14,159,110,.10)'; ctx.fillRect(0, 6, x1, h - 12);
    ctx.fillStyle = 'rgba(180,120,63,.14)'; ctx.fillRect(x1, 6, x2 - x1, h - 12);
    ctx.fillStyle = 'rgba(200,57,59,.14)'; ctx.fillRect(x2, 6, w - x2, h - 12);
    ctx.strokeStyle = C.red(); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(x2, 3); ctx.lineTo(x2, h - 3); ctx.stroke();
    var x = w * frac;
    ctx.fillStyle = stateColor(st);
    ctx.fillRect(0, 6, x, h - 12);
    ctx.fillStyle = C.ink();
    ctx.fillRect(x - 1.5, 1, 3, h - 2);
  }

  // 6.4 安全区间横条（电压/电流/功率：区间底纹 + 上限红线 + 峰值标记）
  function drawSafety(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h;
    var sig = SIG[o.sig], st = stateOf(o.sig);
    var fs = o.fs || sig.fs;
    var frac = Math.max(0, Math.min(1, V[o.sig] / fs));
    ctx.fillStyle = cssVar('--track', '#E3E6EB');
    ctx.fillRect(0, 6, w, h - 12);
    if (o.limit) {                                  // 阈值以上画红区
      var xl = w * o.limit / fs;
      ctx.fillStyle = 'rgba(200,57,59,.13)'; ctx.fillRect(xl, 6, w - xl, h - 12);
    }
    ctx.fillStyle = stateColor(st);
    ctx.fillRect(0, 6, w * frac, h - 12);
    if (o.peak && S[o.sig]) {
      var arr = S[o.sig], mx = Math.max.apply(null, arr);
      var xp = w * Math.min(1, mx / fs);
      ctx.strokeStyle = C.ink(); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(xp, 2); ctx.lineTo(xp, h - 2); ctx.stroke();
    }
    if (o.limit) {                                  // 阈值红线
      var xl2 = w * o.limit / fs;
      ctx.strokeStyle = C.red(); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(xl2, 4); ctx.lineTo(xl2, h - 4); ctx.stroke();
    }
  }

  // 6.5 分段 LED 条（CSS 生成，值到哪亮到哪；段少=粗读，段多=精读）
  function drawSeg(el, o) {
    var n = o.n || 20, sig = SIG[o.sig], st = stateOf(o.sig);
    var frac = Math.max(0, Math.min(1, V[o.sig] / (o.fs || sig.fs)));
    var lit = Math.round(frac * n);
    var html = '';
    for (var i = 0; i < n; i++) {
      var cls = i < lit ? 'on' : '';
      if (i < lit) {
        if (o.zones && i >= o.zones[1] * n) cls += ' seg-red';
        else if (o.zones && i >= o.zones[0] * n) cls += ' seg-amber';
      }
      if (o.lim && i === Math.round(o.lim * n)) cls += ' seg-lim';
      html += '<i class="' + cls + '"></i>';
    }
    el.innerHTML = html;
    el.className = el.className.replace(/\s*is-(ok|warn|over)/g, '') + ' is-' + st;
  }

  // 6.6 状态灯阵列（换挡灯 / 告警灯）
  function drawLights(el, o) {
    var n = o.n || 12, sig = SIG[o.sig] || { fs: 10000 };
    var frac = Math.max(0, Math.min(1, V[o.sig] / (o.fs || sig.fs)));
    var lit = Math.round(frac * n), html = '';
    for (var i = 0; i < n; i++) {
      var cls = i < lit ? 'on' : '';
      if (i < lit) cls += i >= n - 3 ? ' c3' : (i >= n - 6 ? ' c2' : ' c1');
      html += '<i class="' + cls + '"></i>';
    }
    el.innerHTML = html;
  }

  // 6.7 数值磁贴（数字 + 单位 + 状态色 + 可选迷你趋势）
  function fillTile(el, o) {
    var id = o.sig, s = SIG[id], st = stateOf(id);
    var v = el.querySelector('[data-v]');
    if (v) v.textContent = fmt(id);
    el.className = el.className.replace(/\s*is-(ok|warn|over)/g, '') + ' is-' + st;
    var bar = el.querySelector('[data-bar]');
    if (bar) bar.style.transform = 'scaleX(' + Math.max(0, Math.min(1, V[id] / (o.fs || s.fs))).toFixed(4) + ')';
  }

  // 6.7b 多序列趋势（趋势区用；与产品一致：canvas + 网格 + 右侧读数由 HTML 负责）
  function drawTrend(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h;
    var ids = (o.sigs || 'rpm,speed').split(',');
    var pad = { l: 46, r: 10, t: 12, b: 18 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    var cols = [C.cyan(), C.amber(), C.violet(), C.lime()];
    ctx.strokeStyle = C.line(); ctx.lineWidth = 1;
    for (var g = 0; g <= 5; g++) {
      var gy = pad.t + ph * g / 5;
      ctx.globalAlpha = g === 5 ? 1 : 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // 各序列按自身 min/max 归一化（与产品"归一化量程"档同语义）
    for (var k = 0; k < ids.length; k++) {
      var arr = S[ids[k]]; if (!arr) continue;
      var lo = Math.min.apply(null, arr), hi = Math.max.apply(null, arr);
      if (hi - lo < 1e-6) hi = lo + 1;
      ctx.beginPath();
      for (var i = 0; i < arr.length; i++) {
        var x = pad.l + pw * i / (arr.length - 1);
        var y = pad.t + ph - ph * (arr[i] - lo) / (hi - lo);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = cols[k % 4]; ctx.lineWidth = 1.6; ctx.lineJoin = 'round';
      ctx.stroke();
    }
    ctx.fillStyle = C.dim(); ctx.font = '600 10px Consolas,monospace';
    ctx.textAlign = 'right';
    for (var g2 = 0; g2 <= 5; g2++) ctx.fillText((100 - g2 * 20) + '%', pad.l - 6, pad.t + ph * g2 / 5 + 3);
    ctx.textAlign = 'center';
    for (var s = 0; s <= 6; s++) ctx.fillText(s === 0 ? '0s' : '-' + (5 * s) + 's', pad.l + pw * (1 - s / 6), h - 5);
  }

  // 6.7c 历史热条（30 根细柱 = 近期历史；"数据表"方向用它替代折线）
  function drawHeat(el, o) {
    var arr = S[o.sig] || [];
    var n = o.n || 30, step = Math.max(1, Math.floor(arr.length / n));
    var st = stateOf(o.sig);
    var col = st === 'over' ? C.red() : st === 'warn' ? C.amber() : (o.color || C.teal());
    var lo = Math.min.apply(null, arr), hi = Math.max.apply(null, arr);
    if (hi - lo < 1e-6) hi = lo + 1;
    var s = '';
    for (var i = 0; i < n; i++) {
      var v = arr[Math.min(arr.length - 1, i * step)];
      var f = 0.16 + 0.84 * (v - lo) / (hi - lo);
      s += '<i style="height:' + (f * 100).toFixed(1) + '%;background:' + col +
           ';opacity:' + (0.35 + 0.65 * (i / n)).toFixed(2) + '"></i>';
    }
    el.innerHTML = s;
  }

  // 6.7d 竖条（纵向对比用；值到顶即满量程）
  function drawVBar(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h;
    var sig = SIG[o.sig], st = stateOf(o.sig);
    var f = Math.max(0, Math.min(1, V[o.sig] / (o.fs || sig.fs)));
    var bw = Math.max(22, Math.min(34, w * 0.42));      // 够粗才看得出量级（不是一根牙签）
    var x0 = (w - bw) / 2;
    ctx.fillStyle = cssVar('--track', '#E3E6EB');
    ctx.fillRect(x0, 2, bw, h - 4);
    ctx.fillStyle = st === 'over' ? C.red() : st === 'warn' ? C.amber() : (o.color || C.cyan());
    ctx.fillRect(x0, h - 2 - (h - 4) * f, bw, (h - 4) * f);
    if (o.limit) {
      var y = h - 2 - (h - 4) * (o.limit / (o.fs || sig.fs));
      ctx.strokeStyle = C.red(); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x0 - 5, y); ctx.lineTo(x0 + bw + 5, y); ctx.stroke();
    }
    if (o.peak && S[o.sig]) {
      var mx = Math.max.apply(null, S[o.sig]);
      var yp = h - 2 - (h - 4) * Math.min(1, mx / (o.fs || sig.fs));
      ctx.strokeStyle = C.ink(); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x0 - 5, yp); ctx.lineTo(x0 + bw + 5, yp); ctx.stroke();
    }
  }

  // 6.7e 概览统计（"N 项正常 / M 项超限"）：随模式自动更新
  function fillSum(el, o) {
    var ids = (el.getAttribute('data-sigs') || 'temp,power,amp,volt,rpm,speed,torque,throttle,accMag,roll').split(',');
    var ok = 0, warn = 0, over = 0;
    for (var i = 0; i < ids.length; i++) {
      var st = stateOf(ids[i]);
      if (st === 'over') over++; else if (st === 'warn') warn++; else ok++;
    }
    var html = '';
    if (over) html += '<span class="lamp over"></span><b>' + over + '</b> 项超限 ';
    if (warn) html += '<span class="lamp warn"></span><b>' + warn + '</b> 项偏高 ';
    html += '<span class="lamp ok"></span>' + ok + ' 项正常';
    el.innerHTML = html;
    el.className = el.className.replace(/\s*is-(ok|warn|over)/g, '') +
      ' is-' + (over ? 'over' : warn ? 'warn' : 'ok');
  }

  // 6.8 散点（转速-车速、功率-电流相关）
  function drawScatter(cv, o) {
    var L = layer(cv), ctx = L.ctx, w = L.w, h = L.h, pad = 6;
    var xs = S[o.x], ys = S[o.y];
    if (!xs || !ys) return;
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    ctx.strokeStyle = C.line(); ctx.lineWidth = 1;
    for (var g = 1; g < 4; g++) {
      var gy = pad + (h - pad * 2) * g / 4;
      ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(w - pad, gy); ctx.stroke();
    }
    for (var i = 0; i < xs.length; i += 2) {
      var px = pad + (w - pad * 2) * (xs[i] - x0) / ((x1 - x0) || 1);
      var py = h - pad - (h - pad * 2) * (ys[i] - y0) / ((y1 - y0) || 1);
      var f = i / xs.length;
      ctx.fillStyle = 'rgba(76,111,212,' + (0.18 + 0.62 * f).toFixed(2) + ')';
      ctx.beginPath(); ctx.arc(px, py, 1.8, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = C.dim(); ctx.font = '600 10px Consolas,monospace';
    ctx.textAlign = 'left';   ctx.fillText(o.xLabel || '', pad + 2, h - 2);
    ctx.textAlign = 'right';  ctx.fillText(o.yLabel || '', w - pad - 2, 10);
  }

  /* ---------- 7. 扫描并渲染 ---------- */
  function renderAll() {
    var i, el;
    for (i = 0; i < ADC.length; i++) {           // ADC / 姿态 数值不随模式变，保持真实量级
      V['adc' + i] = ADC[i].v;
    }
    var list = document.querySelectorAll('[data-w]');
    for (i = 0; i < list.length; i++) {
      el = list[i];
      var kind = el.getAttribute('data-w');
      var o = {
        sig: el.getAttribute('data-sig'),
        sig2: el.getAttribute('data-sig2') || null,
        fs: parseFloat(el.getAttribute('data-fs')) || 0,
        sweep: parseFloat(el.getAttribute('data-sweep')) || 270,
        ticks: parseInt(el.getAttribute('data-ticks') || '0', 10),
        n: parseInt(el.getAttribute('data-n') || '0', 10) || undefined,
        label: el.getAttribute('data-label') !== '0',
        center: el.getAttribute('data-center') !== '0',
        needle: el.getAttribute('data-needle') !== '0',
        fill: el.getAttribute('data-fill') !== '0',
        peak: el.getAttribute('data-peak') === '1',
        limit: parseFloat(el.getAttribute('data-limit')) || 0,
        warnAt: parseFloat(el.getAttribute('data-warnat')) || 0,
        zones: (el.getAttribute('data-zones') || '').split(',').filter(Boolean).map(Number),
        lim: parseFloat(el.getAttribute('data-lim')) || 0,
        xLabel: el.getAttribute('data-xlabel') || '',
        yLabel: el.getAttribute('data-ylabel') || '',
        sigs: el.getAttribute('data-sigs') || '',
        x: el.getAttribute('data-x'), y: el.getAttribute('data-y'),
        vsize: parseFloat(el.getAttribute('data-vsize')) || 0
      };
      if (o.fs) o.fs = o.fs;
      switch (kind) {
        case 'ring':   drawRing(el, o); break;
        case 'spark':  drawSpark(el, o); break;
        case 'band':   drawBand(el, o); break;
        case 'safety': drawSafety(el, o); break;
        case 'seg':    drawSeg(el, o); break;
        case 'lights': drawLights(el, o); break;
        case 'tile':   fillTile(el, o); break;
        case 'scatter':drawScatter(el, o); break;
        case 'trend':  drawTrend(el, o); break;
        case 'heat':   drawHeat(el, o); break;
        case 'vbar':   drawVBar(el, o); break;
        case 'sum':    fillSum(el, o); break;
      }
    }
    // 纯文本绑定：<span data-txt="speed"></span>（没写 data-v 就用当前值）
    var tx = document.querySelectorAll('[data-txt]');
    for (i = 0; i < tx.length; i++) {
      el = tx[i];
      var id = el.getAttribute('data-txt');
      var raw = el.getAttribute('data-v');
      el.textContent = fmt(id, raw === null ? V[id] : parseFloat(raw));
    }
    // 状态徽标：<span data-state="temp"></span>
    var sb = document.querySelectorAll('[data-state]');
    for (i = 0; i < sb.length; i++) {
      el = sb[i];
      var sid = el.getAttribute('data-state'), sst = stateOf(sid);
      el.className = el.className.replace(/\s*is-(ok|warn|over)/g, '') + ' is-' + sst;
      el.textContent = sst === 'over' ? '超限' : sst === 'warn' ? '偏高' : '正常';
    }
    document.documentElement.setAttribute('data-mode', MODE);
  }

  function protoSetMode(m) {
    MODE = (m === 'alarm') ? 'alarm' : 'normal';
    rebuild(); _seed = 20261016;
    renderAll();
    return MODE;
  }
  function protoRerender() { _seed = 20261016; rebuild(); renderAll(); }

  window.protoSetMode = protoSetMode;
  window.protoRerender = protoRerender;
  window.PROTO = { V: V, S: S, SIG: SIG, ADC: ADC, stateOf: stateOf, fmt: fmt,
                   get mode() { return MODE; }, rebuilt: rebuild, render: renderAll };

  rebuild();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderAll);
  } else {
    renderAll();
  }
  // 尺寸变化（人工拖窗口）时重画 —— 原型专用，产品里由既有 syncLayer 机制负责
  window.addEventListener('resize', function () { renderAll(); });
})();
