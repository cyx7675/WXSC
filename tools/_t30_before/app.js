/* =========================================================================
 * 无线数传上位机 v2.1 · 交互逻辑 (app.js)
 * -------------------------------------------------------------------------
 * 数据来源：真实串口（PyQt 主进程 pyserial + QWebChannel 推送），
 *           勾选「演示模式」或浏览器直接打开时用本地模拟数据。
 * CSV 落盘：由 Python 后端实时写磁盘（与渲染解耦），前端只发指令；
 *           无后端（浏览器）时降级为内存攒行、停止后下载。
 *
 * 协议：帧信封与字段表全部由 telemetry_config.py 定义（本文件不存第二份）。
 *   换算统一为「显示值 = 原始码 × scale」，scale 写在配置第 4 区，改配置即生效。
 *   当前三包：P0 ADC / P1 NAV / P2 MOTOR，具体字节布局见配置文件里的注释图。
 *
 * 流畅度设计（v2.1 重写，逐条都有实测依据，见 tools/_perf_bench_*.json）
 *   1) 条形/梯子用 transform:scaleX/scaleY 驱动，不用 width/height
 *      ——width 每次改动都要重排，20Hz × 10 条 = 200 次/秒布局；
 *        transform 只走合成层，主线程零布局。过渡 90ms < 2 个采样间隔，
 *        不会出现"动画还没走完就被下一拍打断"的粘滞感。
 *   2) 用 Toast 取代 alert：alert 会同步阻塞渲染进程消息循环，
 *      且在 QtWebEngine 默认无对话框实现，提示常常直接消失 —— 这正是
 *      "保存提示不流畅"的根因。Toast 只做 transform/opacity，不阻塞。
 *   3) HEX 监视器改成节点池 + CSS column-reverse：
 *      原来每次 flush 都要读 scrollHeight（强制同步布局，实测 1.6ms/次）；
 *      column-reverse 让"最新一行"天然贴底，JS 不再需要操作滚动位置。
 *   4) 趋势图：网格/刻度画进静态缓存层，每帧只 blit 一次 + 画曲线，
 *      去掉离屏双缓冲的整画布 drawImage，lineJoin 改 bevel（round 最贵）。
 *   5) canvas 尺寸由 ResizeObserver 缓存，绝不在 rAF 里读 clientWidth。
 *   6) DPR 上限 2：高倍屏下画布像素随 DPR² 增长，是移动端/笔记本上最大的
 *      一笔隐形开销，超过 2 之后肉眼无收益。
 * ========================================================================= */

'use strict';

/* =========================================================================
 *                    ★★★ 界面配置区 · 改这里就够了 ★★★
 * -------------------------------------------------------------------------
 * 本文件顶部的这一块决定「界面上显示什么」。有两类配置：
 *
 *   A. 【自动同步】通道名 / 单位 / 曲线配色 / CSV 表头
 *      这些由后端从 telemetry_config.py 推过来自动覆盖（见 applyUiConfig），
 *      ★ 改这些请直接改 telemetry_config.py 第 2 区，本文件的同名变量
 *        只是「浏览器直开、没有后端」时的兜底默认值。
 *
 *   B. 【本文件管】显示参数：缓冲深度、取样频率、默认勾选哪几条曲线、
 *      各物理量的显示量程与小数位 —— 就在下面这一块里，改完刷新页面即可。
 *
 * ========================================================================= */

/* ---------- 1. 信号注册表（单一事实来源） ---------- */
/* ⚠️ 兜底默认值。连上程序后会被 telemetry_config.py 的配置覆盖。
 *    要改通道名或颜色 → 改 telemetry_config.py 第 2 区，不用动这里。 */
var ADC_NAMES = ['悬架线位移1','悬架线位移2','悬架线位移3','悬架线位移4',
                 '转向线位移','转向角位移','压力传感器1','压力传感器2','压力传感器3','压力传感器4'];
/* 浅色底专用深一档调色板：白底上保持可辨且互相区分 */
var ADC_COLORS = ['#2563EB','#DC2626','#0E9F6E','#7C3AED','#B45309',
                  '#0891B2','#65A30D','#DB2777','#EA580C','#0F766E'];
var ADC_COUNT = ADC_NAMES.length;
/* 各 ADC 通道的单位（由第 3 区 ADC_CHANNELS 的 unit 下发，卡片右下角显示）。
 * 兜底值与 telemetry_config.py 第 3 区保持一致（浏览器直开/演示模式用）。 */
var ADC_UNITS = ['mm','mm','mm','mm','mm','°','kg','kg','kg','kg'];
/* ADC 读数保留几位小数 —— 由第 3 区 ADC_DECIMALS 经 ui_adc_config 的 dec 下发，
   这里只是浏览器直开（没有后端）时的兜底，必须与 telemetry_config.py 第 3 区
   ADC_DECIMALS 保持一致，否则界面和 CSV 落盘两个口径。 */
var ADC_DEC_FALLBACK = 1;

/* NAV / MOTOR 各信号「界面读数」保留几位小数的【兜底值】。
 * ★★ 位数只由这一张表（或后端下发的 FIELD_DECIMALS）决定，跟 scale 完全无关：
 *     scale 是「一个原始码等于多少」，位数是「你想看多细」，两码事。
 *     以前界面到处写死 toFixed(1)、CSV 那边又按 ceil(-log10(scale)) 反推，
 *     结果改一次系数：界面位数不动、CSV 位数跟着跳、自检期望值也跟着漂 ——
 *     一改 scale 就报红。现在两边都只看这张表。
 *   连上后端后由第 5 区 FIELD_DECIMALS（经 ui_config 的 dec）覆盖；
 *   这里的值只是浏览器直开/演示模式的兜底，必须与配置那边保持一致。 */
var DEC_FALLBACK = {
  speed: 2, rpm: 0, throttle: 0, torque: 1, temp: 0, volt: 1, amp: 1,
  /* 协议把单路 ACCEL 拆成横向/纵向后，三路都用 2 位小数
     （配置里 FIELD_DECIMALS 没有新名字 -> 后端 field_decimals() 走 DEFAULT_DECIMALS，
      这里与它保持一致；连上后端后仍由配置的 dec 覆盖）。 */
  roll: 1, pitch: 1, yaw: 1, accelLat: 2, accelLon: 2, accelMag: 2,
  /* 派生量「电池功率」= 电压 × 电流 / 1000（kW）。
     ★ 它不是协议字段，后端 cfg.motor 里没有它 -> 位数**没有配置来源**，
       就固定用这里的 1 位（80.35 kW 这种精度对"有没有超过 80"足够）。
       若要改：同时改这里与 SIGS.power.fmt。 */
  power: 1
};
/* 12bit 解包顺序（后端下发的当前生效值）。★ 真值来自字节级证据 = 'msb'：
   数据区 15 字节去掉空格连成 hex digit，每 3 个 digit 一组顺序就是 CH1..CH10。
   详见 telemetry_config.py 第 3 区。*/
var ADC_ORDER_NOW = 'msb';

/* MOTOR 包多字节字段的字节序（后端下发的当前生效值）：'<' 小端 / '>' 大端。
   ★ 转速乱跳到几万、电压电流同样离谱 → 八成是这里反了：
     固件手工拼字节常用大端（ADC 打包区已字节级证明是按组大端 msb），
     而 ts 用 u32 直接赋值所以是小端 —— 同一个固件混用两种字节序很常见。
     小端解大端数据时 显示值 = (R&0xFF)<<8 | (R>>8)，真值每变 1 显示跳 256，
     于是「数值几万 + 跳动极快」。界面「设置」里可切换，保存立即生效。 */
var MOTOR_ENDIAN_NOW = '<';

/* ---------- 显示参数：★兜底默认值★ ------------------------------------------
   ⚠️ 这里的注释曾经写着「改这里即刻生效」——那是错的，害人不浅：
      Python 端 telemetry_config.py 第 5 区 UI_CFG 会整份下发把你覆盖掉，
      所以改这里【只有无后端的演示模式】才看得见，连上后端就白改。
   ★ 真正要改去 telemetry_config.py 第 5 区 UI_CFG（或 exe 旁的
     telemetry_user.json 的 "ui" 段，免打包、重启生效）。
   ⚠️ 键名必须与配置那边完全一致；新增显示参数只在那边加，这边会被自动覆盖。 */
var UI_CFG = {
  bufPoints: 600,      /* 曲线缓冲点数；600 点 × 20Hz = 30 秒窗口 */
  sampleMs: 50,        /* 曲线取样周期（ms） */
  trendFps: 30,        /* 曲线重绘上限帧率（机器慢就调低） */
  dprCap: 2,           /* canvas 像素比上限：更高肉眼无收益，开销是平方级 */
  domHz: 20,           /* 数字 / 仪表刷新率上限 */
  /* ★ 滚动期间降频（2026-10-06）。实测：滚动时 20Hz 的 DOM 更新每秒制造 ~19 次
     布局，把滚动帧的 max 从 25ms 顶到 39.6ms（jank 0.3% -> 1.7%）。
     滚动时用户看的是结构不是精确读数，降到 6Hz / 10fps 肉眼无感，
     停手 200ms 后立刻恢复满帧。 */
  domHzScroll: 6,
  trendFpsScroll: 10,
  scrollQuietMs: 200,  /* 最后一次滚动事件后多久算"停手" */
  sparkMs: 250,        /* 卡片迷你趋势图刷新间隔 */
  defaultVisible: ['speed','rpm','throttle','torque'],  /* 演示模式默认勾选 */
  /* ★ 电池功率报警阈值（kW）。**唯一真源是配置**（telemetry_config.py 第 5 区
     UI_CFG["powerWarnKw"]，telemetry_user.json 的 ui 段可覆盖，随 uiConfig 下发）；
     这里的 80 只在"没有后端 / 配置缺这个键"时兜底，前端不再写第二份真源。 */
  powerWarnKw: 80,
  hexMax: 60,          /* HEX 监视器最多保留的行数 */
  revSegs: 12          /* 换挡灯段数 */
};

/* ★★ 小数位的**唯一夹紧点**（2026-10-16 质检 task-21 F1）
 *   小数位是**外部配置**（第 5 区 FIELD_DECIMALS / 第 3 区 ADC_DECIMALS，以及
 *   telemetry_user.json 的 decimals / adcDecimals），而 toFixed 的合法范围只有 0..100：
 *   配置写 200 就会抛 `RangeError: toFixed() digits argument must be between 0 and 100`。
 *   危害不是"这一格没更新"——异常在**同一帧的 DOM 更新函数里**抛出，
 *   会让趋势数值面板 / 悬停读数 / 矢量图读数**整帧**都不再更新，且控制台持续刷错。
 *   后端已在配置侧夹到 [0,20]；前端**再夹一次**（uiConfig 是外部输入，来源不止一处）。
 *   ★ 上限 20 与后端保持一致：两端位数口径必须相同，否则界面与 CSV 又会对不上。 */
var DEC_MAX = 20;
function clampDec(d, fallback){
  var n = Number(d);
  if (!isFinite(n)){
    var f = Number(fallback);
    if (!isFinite(f)) f = 2;
    return Math.max(0, Math.min(DEC_MAX, Math.floor(f)));
  }
  n = Math.floor(n);
  return n < 0 ? 0 : (n > DEC_MAX ? DEC_MAX : n);
}
/* ★★ 缓冲点数上下限 —— **必须与后端完全一致**（后端同样夹 [10, 20000]）。
 *   BUF 决定每条曲线保留多少点（pushSample 里 `b.length > BUF` 才 splice），
 *   而后端 replayBackfill 的槽数也按 bufPoints 走：
 *   两端范围不一致就会出现"后端造 20000 槽、前端缓冲只留 600"的错配（回放只有最后一小段）。
 *   配置写 1e8 时若不夹：缓冲永不 splice -> 21 路 × 20Hz 无限增长（数小时数百 MB）。
 *   非数字/NaN/0/负数一律回退默认 600（0/负不是"要个小缓冲"，而是无效配置；
 *   真正的小缓冲下限由 BUF_MIN=10 保证，且与后端下限一致）。 */
var BUF_MIN = 10, BUF_MAX = 20000, BUF_DEFAULT = 600;
function clampBuf(n){
  var v = Number(n);
  if (!isFinite(v) || v <= 0) return BUF_DEFAULT;
  v = Math.floor(v);
  if (v < BUF_MIN) return BUF_MIN;
  if (v > BUF_MAX) return BUF_MAX;
  return v;
}

var BUF = clampBuf(UI_CFG.bufPoints);   /* 30 s @ 20 Hz 推送（配置值经 clampBuf 夹紧） */

var SIGS = {};   /* id -> {label, unit, color, group, fmt, min, max} */
function defSig(id, label, unit, color, group, fmt, mn, mx, sc){
  /* sc = 「原始码 -> 显示值」的换算系数（第 4 区 fields 的第 4 个数）。
     数值本身由**后端**乘好了再推给前端，这里只是把它带在信号上，
     供「设置 → 量程与单位」里的系数列显示/编辑 —— 前端不再乘第二次。 */
  SIGS[id] = {id:id, label:label, unit:unit, color:color, group:group, fmt:fmt,
              min:mn, max:mx, scale:(sc === undefined ? 1 : Number(sc) || 1)};
}
/* P0：通道数量取决于配置（默认 10） */
for (var ai = 0; ai < ADC_COUNT; ai++)
  /* 第 6 个参数 = 显示小数位：浏览器直开时用 ADC_DEC_FALLBACK，
     连上后端后由 ui_adc_config 的 dec 覆盖（第 3 区 ADC_DECIMALS）。 */
  defSig('adc'+ai, 'CH'+String(ai+1).padStart(2,'0')+' '+ADC_NAMES[ai], ADC_UNITS[ai] || '', ADC_COLORS[ai], 'P0', ADC_DEC_FALLBACK, 0, 4095);
/* P2 动力 —— 第 6 个参数 fmt = 小数位（来自 DEC_FALLBACK，后端会覆盖） */
defSig('speed','车速','m/s','#0891B2','P2',DEC_FALLBACK.speed,0,40);
defSig('rpm','电机转速','RPM','#0E9F6E','P2',DEC_FALLBACK.rpm,0,10000);
defSig('throttle','油门开度','%','#65A30D','P2',DEC_FALLBACK.throttle,0,100);
defSig('torque','扭矩','N·m','#B45309','P2',DEC_FALLBACK.torque,0,150);
defSig('temp','电机温度','°C','#EA580C','P2',DEC_FALLBACK.temp,0,120);
defSig('volt','电压','V','#2563EB','P2',DEC_FALLBACK.volt,0,600);
defSig('amp','电流','A','#7C3AED','P2',DEC_FALLBACK.amp,0,200);
/* ★★ 2026-10-16 用户新需求：**电池输出功率** = 电压 × 电流 / 1000（kW），
 *    超过配置阈值（UI_CFG.powerWarnKw，默认 80 kW）要红色告警。
 *    · 数据来源就是上面两路 volt / amp，二者来自**同一帧 MOTOR**
 *      （偏移 13-14 电压 / 15-16 电流）→ 天然时间对齐，不做插值、不跨帧拼；
 *    · 按 accelMag 同一套做法做成**派生信号**（进 SIGS / state.buf），
 *      于是自动获得：趋势图可勾选、右侧数值面板可读、可视化图表可拖入；
 *    · 量程 min/max 由 syncPwrScale() 按阈值重设（上限 = 1.5 × 阈值），
 *      条形上那根红色刻度就是报警线 —— **不要写死 80/120**。
 *    · ★ 派生量不进 CSV（后端不录它，加了列就和后端录制文件对不上，与 accel_mag 同口径）。 */
defSig('power','电池功率','kW','#BE123C','P2',DEC_FALLBACK.power,0,120);
/* P1 姿态 */
defSig('roll','Roll','°','#7C3AED','P1',DEC_FALLBACK.roll,-180,180);
defSig('pitch','Pitch','°','#0891B2','P1',DEC_FALLBACK.pitch,-90,90);
defSig('yaw','Yaw','°','#0E9F6E','P1',DEC_FALLBACK.yaw,0,360);
/* ★ 2026-10-14：用户把 NAV 包的加速度拆成横向 + 纵向（20B→22B），并新增一路**派生**合成量。
 *   原来的单路 accelRaw 已从协议里删除，这里用三路取代它：
 *     accelLat / accelLon = 固件两路原始码 × scale（后端算好推来）
 *     accelMag            = 前端按 sqrt(lat²+lon²) 每批重算的**合成总加速度**
 *   单位：配置第 5 区 UNITS 里只有旧的 "ACCEL"（值就是空串），新拆出的 ACCEL_LAT/LON
 *   没有条目 -> 后端下发 unit=""。这里**沿用同一约定**（不写死物理单位），
 *   面板上用 fmtScaleChip(ACC_SCALE) 把系数显示出来；若日后配置里补了 unit，
 *   applyUiConfig 会自动跟随（见那里的"只在配置为空时兜底"）。 */
defSig('accelLat','加速度·横向','','#B45309','P1',DEC_FALLBACK.accelLat,-16384,16384);
defSig('accelLon','加速度·纵向','','#2563EB','P1',DEC_FALLBACK.accelLon,-16384,16384);
defSig('accelMag','加速度·合成','','#DC2626','P1',DEC_FALLBACK.accelMag,0,16384);

/* ---- 加速度显示：量程与小数位由配置里的 scale 决定，不写死 ----
 * 以前这里写死 Math.round(...) 和 ±16384，一旦把 ACCEL 的 scale 改成
 * 0.00001 这种小系数，界面就会永远显示 0（因为 0.02 四舍五入成 0）。
 * 现在：满量程(显示单位) = ACC_RAW_FS × scale，小数位按 scale 推。        */
/* ★ 默认值必须**与配置一致**（2026-10-16 task-25 实测）：
 *   ACC_SCALE / ACC_RAW_FS 是"没连后端时（浏览器直开 / 演示模式 / headless 截图）"的兜底。
 *   协议把加速度拆成 ACCEL_LAT/ACCEL_LON 后，配置里那个字段的 scale = 0.001（±16.384 g 满量程）。
 *   以前这里兜底 1.0 × 16384 = **满量程 16384、"RAW" 单位** —— 于是没有后端的一切路径
 *   （包括产品体验专家那批 headless 截图）都会显示"±16384 / RAW / 读数 3533 g"，
 *   而真机回放是 ±16.4。实测确认：这不是真机的错，是**静态兜底值没跟上配置**。
 *   连上后端后仍由 uiConfig 覆盖（applyUiConfig 里按 ACCEL* 前缀匹配，ACCEL_LAT 会命中）。
 *   ⚠️ 与 DEC_FALLBACK 同一条原则：兜底值必须与 telemetry_config.py 保持一致。 */
var ACC_SCALE = 0.001;    /* = 配置 ACCEL_LAT/ACCEL_LON 的 scale（0.001）*/
var ACC_RAW_FS = 16384;   /* 由配置下发覆盖：多少原始码算满量程 */
var ACC_FS = 16384;       /* 显示单位下的满量程，下面自动算 */
/* 显示几位小数：★ 只听配置（FIELD_DECIMALS.ACCEL 经 dec 下发到 SIGS.fmt），
   不再由 scale 反推 —— 位数和系数是两件事，反推会让改一次系数位数就跳。 */
var ACC_DEC = DEC_FALLBACK.accelMag;   /* 三路加速度共用同一个位数来源（合成那路） */
var ACC_UNIT = 'g';        /* 由配置下发覆盖：显示单位（空串则显示 raw） */
var ACC_FS_SET = 0;       /* >0 时用这个当满量程（GAUGE_RANGES.ACCEL 直接指定） */
/* ★ 加速度矢量图的脏标记（声明在这里，因为 updateAccScale() 在模块初始化时就会用到它）。
   语义：只标脏不画；真正绘制在离散调用点或 updateDom() 的 20Hz 节拍上 —— 见 7.6 节。 */
var accVecDirty = true;

/* 把一个满量程值写成人看得懂的刻度：16384 / 16.4 / 0.164 / 1e-05 */
function fmtFs(v){
  var a = Math.abs(v);
  if (a >= 1000) return String(Math.round(v));
  if (a >= 1)    return String(Number(v.toFixed(1)));
  if (a === 0)   return '0';
  return String(Number(v.toPrecision(3)));
}
/* 把 scale 写成角标：1 -> RAW，2.4e-4 -> ×0.00024 */
function fmtScaleChip(s){
  if (!s) return 'RAW';
  if (s === 1) return 'RAW';
  return '×' + String(Number(Number(s).toPrecision(3)));
}

/* 把推导结果刷到 DOM：单位后缀、左右刻度、右上角系数角标。
   ★ 这三处以前是写死在 index.html 里的（"raw" / ±16384），
     所以改了 scale 之后数值对了、量程看起来却没变 —— 就是这里没同步。 */
function syncAccLabels(){
  var lo = $('accScaleLo'), hi = $('accScaleHi'), u = $('accUnit'), chip = $('accChip');
  if (lo)  lo.textContent = '−' + fmtFs(ACC_FS);
  if (hi)  hi.textContent = '+' + fmtFs(ACC_FS);
  /* scale===1 时界面上就是原始码本身，后缀写 raw；否则显示配置里的单位(g/m·s⁻²…)。
     ★ 配置 UNITS 里"加速度"目前是空串（只有旧的 ACCEL 条目、值为空）——按"未配置"处理，
       退回 'g'：依据是原始码 ×0.001、满量程 ±16.384，与 ±16g 加速度计自洽，
       而且 app.js 里 defSig/ACC_UNIT 的**原始默认值本来就是 'g'**（旧单路信号也这么显示）。
       配置一旦给非空单位，这里与三路信号都会自动跟随。 */
  var accU = ACC_UNIT || 'g';
  if (u)   u.textContent  = (ACC_SCALE === 1) ? ' raw' : (accU ? ' ' + accU : '');
  if (chip) chip.textContent = fmtScaleChip(ACC_SCALE);
  /* 三路加速度的信号单位也按同一条规则（矢量图/趋势/面板/SIGS 全部同源） */
  ['accelLat', 'accelLon', 'accelMag'].forEach(function(id){
    if (SIGS[id]) SIGS[id].unit = (ACC_SCALE === 1) ? '' : accU;
  });
}

function updateAccScale(){
  /* 配置的 GAUGE_RANGES.ACCEL 指定了就直接用，没指定就按 raw满量程 × scale 自动算 */
  ACC_FS = ACC_FS_SET > 0 ? ACC_FS_SET : Math.max(1e-12, ACC_RAW_FS * ACC_SCALE);
  /* ★ 位数只看注册信号上的 fmt（= 配置第 5 区 FIELD_DECIMALS.ACCEL），
       不看 scale：改 ACCEL 系数只改量程，不改显示位数。 */
  if (SIGS['accelMag'] && typeof SIGS['accelMag'].fmt === 'number')
    ACC_DEC = clampDec(SIGS['accelMag'].fmt, DEC_FALLBACK.accelMag);
  /* 三路加速度的量程与既有「合成加速度」G 条**同一口径**（ACC_FS）：
     lat/lon 对称 ±ACC_FS，合成幅值只取正半轴 0..ACC_FS。这样矢量图的圈、
     趋势图的归一化量程、P1 的 G 条三者不会互相打脸。 */
  if (SIGS['accelLat']){ SIGS['accelLat'].min = -ACC_FS; SIGS['accelLat'].max = ACC_FS; }
  if (SIGS['accelLon']){ SIGS['accelLon'].min = -ACC_FS; SIGS['accelLon'].max = ACC_FS; }
  if (SIGS['accelMag']){ SIGS['accelMag'].min = 0;       SIGS['accelMag'].max = ACC_FS; }
  syncAccLabels();          /* 量程标签跟着变 */
  accVecDirty = true;       /* 量程变了 -> 矢量图的圈/刻度要重画 */
}
updateAccScale();

/* ============================================================================
 * 7.5b 电池功率（派生量 = 电压 × 电流 / 1000 kW）+ 阈值告警
 * ----------------------------------------------------------------------------
 * 用户原话：「计算一下电池输出的功率，用电池的电压与放电电流的乘积计算，
 *            超过 80kW 有红色警告」。
 *
 * ★ 阈值不是写死的：唯一真源是配置（telemetry_config.py 第 5 区
 *   UI_CFG["powerWarnKw"] / telemetry_user.json 的 "ui" 段），随 uiConfig 下发，
 *   由 applyUiConfig() -> syncPwrScale() 落到 PWR_WARN_KW。
 *   下面这个 80 只是"没有后端 / 配置缺键"时的兜底，不构成第二份真源。
 *
 * ★ 抖动抑制（阈值附近必须不闪 —— 本项目的硬要求，与 B08 同族）：
 *   做法是**"亮得快、灭得慢"的不对称阈值**，而不是简单的 >80 判红：
 *     · 亮：kw > 阈值            —— 立即亮，**不驻留**。安全告警宁可误报不可漏报，
 *                                   任何"连续 N 帧才亮"的驻留都会延迟告警，不能要；
 *     · 灭：kw < 阈值 − 2.0 kW **且** 距上次亮起已 ≥ 600 ms。
 *   为什么这样够：数据 20Hz（50ms 一帧），80 kW 附近 ±0.6 kW 的噪声要让状态在
 *   一帧内跨过 2.0 kW 的回差才能灭，再叠加 600ms 最短驻留 —— 实测 200 帧
 *   80±0.6 抖动下状态**零次翻转**（见汇报里的实测数据）。
 *   "接近上限"（阈值 ×0.85）用同一套回差，避免第二条边界又变成一个抖动源。
 * ★ 切换只切 class，**不加任何 transition**（20Hz 下 >50ms 的过渡必然拖影）。
 * ★ 真机现状：总线电流还没接通，`current` 恒为 0 -> 功率实测恒为 0。
 *   这是**数据现状**，不做任何伪造/兜底补值。
 * ========================================================================== */
var PWR_WARN_KW = 80;      /* 配置下发覆盖；兜底 80（见上） */
var PWR_FS = 120;          /* 条形满量程 = 1.5 × 阈值（阈值刻度落在 66.7%） */
var PWR_HYS = 2.0;         /* 回差 kW */
var PWR_HOLD = 600;        /* 超限后最短驻留 ms */
var PWR_NEAR_F = 0.85;     /* "接近上限" = 0.85 × 阈值 */
var PWR_ON = false, PWR_ON_T = 0;      /* 超限态 + 进入时刻 */
var PWR_NEAR = false;                  /* 接近上限态 */
var PWR_PEAK = 0;                      /* 峰值保持（点卡片上的"峰值"可清零） */

/* 阈值/量程变了 -> 同步条形刻度、报警线位置与 SIGS 量程（唯一同步点） */
function syncPwrScale(){
  var w = Number(PWR_WARN_KW);
  if (!isFinite(w) || w <= 0) w = 80;      /* 配置值不可信：非正数回退兜底 */
  PWR_WARN_KW = w;
  PWR_FS = Math.max(1e-6, w * 1.5);
  /* 趋势/归一化量程按同一口径（和加速度那套一样，不写死） */
  if (SIGS.power){ SIGS.power.min = 0; SIGS.power.max = PWR_FS; }
  var tick = $('pwrTick');
  if (tick) tick.style.left = (w / PWR_FS * 100).toFixed(2) + '%';
  var lbl = $('pwrLimitLbl');
  if (lbl){
    lbl.textContent = '上限 ' + (w % 1 === 0 ? String(w) : w.toFixed(1));
    lbl.style.left = (w / PWR_FS * 100).toFixed(2) + '%';
  }
  setNum($('pwrFsLbl'), (PWR_FS % 1 === 0 ? String(PWR_FS) : PWR_FS.toFixed(1)) + ' kW');
}

/* 峰值清零（卡片右下角那个"峰值"是可点的） */
function resetPwrPeak(){
  PWR_PEAK = 0;
  var n = $('pwrPeakNum');
  if (n) n.textContent = '—';
  var el = $('pwrPeak');
  if (el){ el.classList.remove('pwr-peak-hot'); el._hot = false; }
}

/* 功率卡片：数字 / 状态 / 条形 / 峰值 + 告警态。
 * 由 updateMotorDom() 在 20Hz 的 DOM 节拍里调用（与其它读数同一个门禁，
 * 不额外增加每帧开销，也不做 canvas 绘制）。 */
function updatePowerDom(L, hm){
  var card = $('pwrCard');
  if (!card) return;
  var kw = hm ? (Number(L.power) || 0) : 0;
  var now = performance.now();

  /* --- 两态回差判定（先超限、再接近；无数据直接全灭） --- */
  if (!hm){
    PWR_ON = false; PWR_NEAR = false;
  } else {
    if (kw > PWR_WARN_KW){ PWR_ON = true; PWR_ON_T = now; }
    else if (PWR_ON && kw < PWR_WARN_KW - PWR_HYS && (now - PWR_ON_T) >= PWR_HOLD) PWR_ON = false;
    var nearV = PWR_WARN_KW * PWR_NEAR_F;
    if (kw > nearV) PWR_NEAR = true;
    else if (PWR_NEAR && kw < nearV - PWR_HYS) PWR_NEAR = false;
    if (PWR_ON) PWR_NEAR = true;      /* 超限必然算"接近"，避免两态互相打架 */
    if (kw > PWR_PEAK) PWR_PEAK = kw; /* 峰值保持 */
  }

  var band = PWR_ON ? 'over' : (PWR_NEAR ? 'near' : 'ok');

  setNum($('pwrNum'), hm ? fmtNum('power', kw) : '—');
  setScale($('pwrBar'), hm ? (kw / PWR_FS) : 0);
  var bar = $('pwrBar');
  if (bar){
    var bc = 'bar-fill fill-pwr-' + band;
    if (bar._c !== bc){ bar.className = bc; bar._c = bc; }
  }
  /* 卡片级告警：只切 class（20Hz 下不能有任何过渡） */
  var cc = 'scard' + (band === 'over' ? ' pwr-warn' : band === 'near' ? ' pwr-near' : '');
  if (card._c !== cc){ card.className = cc; card._c = cc; }

  var st = $('pwrState');
  var stTxt = !hm ? '—' : band === 'over' ? '超限' : band === 'near' ? '接近上限' : '正常';
  if (st && st._t !== stTxt){ st.textContent = stTxt; st._t = stTxt; }
  var stCol = !hm ? 'var(--faint)' : band === 'over' ? 'var(--red)' : band === 'near' ? 'var(--amber)' : 'var(--teal)';
  if (st && st._col !== stCol){ st.style.color = stCol; st._col = stCol; }

  /* 峰值保持：显示 + 峰值也超过阈值时同样转红（工程师最关心"这一跑最大拉到多少"） */
  var pk = $('pwrPeakNum');
  if (pk) setNum(pk, PWR_PEAK > 0 ? fmtNum('power', PWR_PEAK) : '—');
  var pkEl = $('pwrPeak');
  if (pkEl){
    var hot = PWR_PEAK > PWR_WARN_KW;
    if (pkEl._hot !== hot){ pkEl.classList.toggle('pwr-peak-hot', hot); pkEl._hot = hot; }
    var pt = '峰值 ' + (PWR_PEAK > 0 ? fmtNum('power', PWR_PEAK) : '—') + ' kW · 点击清零';
    if (pkEl._ti !== pt){ pkEl.title = pt; pkEl._ti = pt; }
  }
}
syncPwrScale();

/* ★★ CSV 列定义 —— **唯一来源**（2026-10-15 质检后重构）。
 *   为什么要抽出来：同一份列序以前在**三处**各写一遍 ——
 *     ① buildCsvHeaderLocal() 的表头  ② localRecSample() 的本地记录行  ③ exportCsv() 的导出快照。
 *   结果就是"改了表头和记录行、漏了导出"，导出的 CSV 里 torque/motor_rpm 两列值互换
 *   （质检用打桩 blobDownload 抓到：表头 torque=17/motor_rpm=18，但第 17 列是 rpm 的值）。
 *   现在三处都从这里取：表头用 [1]，数据行用 [0] 走 fmtNum。
 *   顺序 = 后端 telemetry_config.csv_columns()，**torque 在 motor_rpm 之前**。
 *   派生量 accel_mag 不进 CSV（后端不录它，加了列就对不上后端录制文件）。 */
var CSV_COLS = [
  ['roll',     'roll'],
  ['pitch',    'pitch'],
  ['yaw',      'yaw'],
  ['accelLat', 'accel_lat'],
  ['accelLon', 'accel_lon'],
  ['temp',     'motor_temp'],
  ['torque',   'torque'],
  ['rpm',      'motor_rpm'],
  ['throttle', 'throttle'],
  ['volt',     'voltage'],
  ['amp',      'current'],
  ['speed',    'speed']
];
/* 拼一整行数据列（不含时间列）。get(id) -> 数值；位数一律走 fmtNum（配置口径），
   ★ 不再各写各的 Math.round / toFixed(2)：那是"导出的文件和后端录的不是一个口径"的老坑
   （localRecSample 的注释早就写明要统一，exportCsv 一直没跟上）。 */
function csvDataCols(get){
  var cols = [], i;
  for (i = 0; i < ADC_COUNT; i++) cols.push(fmtNum('adc' + i, get('adc' + i) || 0));
  for (i = 0; i < CSV_COLS.length; i++) cols.push(fmtNum(CSV_COLS[i][0], get(CSV_COLS[i][0]) || 0));
  return cols;
}

/* 本地导出（演示模式、没有后端时用）的 CSV 表头。
 * 连上程序后会被 telemetry_config.py 下发的 csvHeader 覆盖，
 * 保证界面导出的文件和程序录制的文件列完全一致。 */
function buildCsvHeaderLocal(){
  return 'time,' + ADC_NAMES
    .map(function(n, i){ return 'CH' + String(i+1).padStart(2,'0') + '_' + n; }).join(',') +
    ',' + CSV_COLS.map(function(c){ return c[1]; }).join(',');
}
var CSV_HEADER_LOCAL = buildCsvHeaderLocal();

/* ---------- 2. 全局状态 ---------- */
var state = {
  frozen:false, recording:false, recStart:0, recRows:0,
  connected:false, demo:false, replaying:false,
  /* 回放进度条：dragging=用户正在拖（期间回包不抢滑块）；
     replayTotal=后端给过的真实总时长（秒，拖动态标签用它换算）；
     _replayStateWired=后端有没有 replayStateChanged（决定要不要兜底）；
     replayPaused=回放推进是否已暂停（★ 与顶栏 freezeBtn 的 state.frozen 是两件事）；
     replayPausedWant=在途的暂停请求（连点按钮时用它算"下一个目标值"，避免重复发同一个值）；
     _replayPausedWired=后端有没有 replayPausedChanged（无则该信号用本地乐观切换兜底） */
  replayDragging:false, replayTotal:0, _replayStateWired:false,
  /* 加载（解析）中：只影响 #replayPos 文案与滑块可用性，**不是**回放态 */
  replayLoading:false, replayLoadingText:'',
  replayPaused:false, replayPausedWant:null, _replayPausedWired:false,
  buf:{}, latest:{},
  /* 按包类型的"收到过数据"标志：未置位时对应分区显示占位符 "—" */
  hasData:{adc:false, nav:false, motor:false},
  visible:new Set(UI_CFG.defaultVisible),
  /* 趋势量程三档：norm=各信号按注册量程映射 0–100% / auto=每条按窗口自身 min-max
     独立缩放 / band=P0 P1 P2 各占一条带（解决不同数据混轴看不清） */
  scaleMode:'norm',
  hl:null,            /* 面板行 hover 的信号 id：只高亮这条曲线，其余淡出 */
  cur:null,           /* 十字光标 {frac:0..1, active:bool}；null = 鼠标不在图内 */
  sel:null,           /* 框选区间 {i0,i1}（buf 索引）；null = 无框选（数据分析用） */
  logs:[],            /* 记录列表 {name,path,rows,t0} */
  recLines:[],        /* 无后端本地录制攒行 */
  fps:0, _frames:0, _rx:0, rx:0
};
/* 信号组 -> 包类型 映射（P0=adc / P1=nav / P2=motor） */
var GROUP_PKT = {P0:'adc', P1:'nav', P2:'motor'};
Object.keys(SIGS).forEach(function(k){
  state.buf[k] = [];
  state.latest[k] = SIGS[k].min || 0;
});
/* ★ 三路加速度的"无数据"基准必须是 **0**，不能沿用上面那句通用初值。
   通用初值是 SIGS[k].min：横/纵是对称量程 -> **负满量程**（−ACC_FS），
   而合成值 = sqrt(lat²+lon²)。于是"只到达一路"或"复位后只来旧键"时，
   另一路的残留负满量程会被算进合成值 —— 实测会出现 mag≈1000 甚至 sqrt(2)×满量程 这种假读数。
   （复位后 ±ACC_FS = ±16.384 时，假值就是 sqrt(16.384²+16.384²)=23.2；量程若配错还会更夸张。）
   所以：初始化与 clearTelemetry 都调这个，把三路一起归 0。 */
function resetAccelLatest(){
  state.latest.accelLat = 0;
  state.latest.accelLon = 0;
  state.latest.accelMag = 0;
  accVecDirty = true;
}
resetAccelLatest();

/* 清空全部遥测：缓冲区、最新值、hasData 标志一并复位，
 * 之后 updateDom 自动回退到 "—" 占位，趋势图显示空态 */
function clearTelemetry(){
  Object.keys(SIGS).forEach(function(k){
    state.buf[k].length = 0;
    state.latest[k] = SIGS[k].min || 0;
  });
  resetAccelLatest();            /* ★ 三路加速度归 0（不能用负满量程，见上面注释） */
  if (typeof gaugeSpeed !== 'undefined'){ gaugeDirty = true; miniDirty = true; heatDirty = true; }
  resetPwrPeak();                /* ★ 功率峰值与告警态也一并复位（新会话从零开始记峰值） */
  PWR_ON = false; PWR_ON_T = 0; PWR_NEAR = false;
  state.hasData.adc = state.hasData.nav = state.hasData.motor = false;
  state.sel = null;              /* 清空框选 */
  hideSelStats();
  accVecDirty = true;            /* 数据清空 -> 矢量图回到"等待 NAV 数据" */
  trendDirty = true;
  domDirty = true;
  lastSampleT = -1e9;   /* 复位取样节拍，避免残留的时间差吃掉第一帧 */
}

/* 通信后端（QWebChannel 注入）；saveDir 由后端回推 */
var backend = null;
var saveDir = '';

/* 系统"减少动效"偏好：命中时关掉平滑滚动与过渡（CSS 也已处理） */
var REDUCE_MOTION = false;
try {
  REDUCE_MOTION = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
} catch (e) {}

/* ---------- 2.5 Toast：非阻塞提示（全面取代 alert） ----------
 * alert() 的问题：① 同步阻塞渲染进程消息循环，界面直接冻住；
 * ② QtWebEngine 默认不实现 javaScriptAlert，弹窗常常根本不出现 ——
 * 用户只看到"点了保存、什么反馈都没有"，这就是"保存提示不流畅"。 */
var TOAST_MAX = 4;
function toast(msg, kind, ms){
  var wrap = $('toastWrap');
  if (!wrap) return;
  kind = kind || 'info';
  while (wrap.children.length >= TOAST_MAX) wrap.removeChild(wrap.firstChild);

  var el = document.createElement('div');
  el.className = 'toast ' + (kind === 'ok' ? 'ok' : kind === 'err' ? 'err' : kind === 'warn' ? 'warn' : '');
  var icon = kind === 'ok' ? '✓' : kind === 'err' ? '✕' : kind === 'warn' ? '!' : 'i';
  var ic = document.createElement('span');
  ic.className = 'ti'; ic.setAttribute('aria-hidden', 'true'); ic.textContent = icon;
  var tx = document.createElement('span');
  tx.className = 'tx'; tx.textContent = String(msg);
  el.appendChild(ic); el.appendChild(tx);
  wrap.appendChild(el);

  // 两帧后再加 .show，保证 transition 生效（否则插入即终态，没有动画）
  requestAnimationFrame(function(){ requestAnimationFrame(function(){ el.classList.add('show'); }); });

  var life = ms || (kind === 'err' ? 5200 : kind === 'ok' ? 3200 : 3800);
  var t1 = setTimeout(dismiss, life);
  el.addEventListener('click', function(){ clearTimeout(t1); dismiss(); });

  function dismiss(){
    if (el._gone) return;
    el._gone = true;
    el.classList.remove('show');
    el.classList.add('out');
    setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 220);
  }
}

/* ---------- 3. 模拟数据引擎（一圈 36 s 的加减速场景，仅演示模式使用） ---------- */
var sim = {t:0, speed:0, thr:0, a:0, roll:0, pitch:0, yaw:0, temp:42, rpm:900};
var GR = [600, 380, 260, 190];  /* 各挡位速比系数：车速(m/s) -> 电机RPM（演示用） */
var P_MAX = 60000;              /* 电池输出功率硬约束 60 kW */
var T_MAX = 150;                /* MAP 最大转矩 150 N·m */

function lapTargets(u){                     /* u∈[0,36) 单圈相位 */
  if (u < 9)    return {thr:1.0,  br:0,  roll:0};
  if (u < 11)   return {thr:0.55, br:0,  roll:0};
  if (u < 13.5) return {thr:0,    br:1,  roll:0};
  if (u < 20)   return {thr:0.30, br:0,  roll:-11};   /* 左弯 */
  if (u < 26)   return {thr:1.0,  br:0,  roll:0};
  if (u < 30)   return {thr:0.25, br:0.2, roll:13};   /* 右弯 */
  if (u < 33)   return {thr:0,    br:0.9, roll:0};
  return {thr:0.6, br:0, roll:0};                    /* 出弯 */
}

function simStep(dt){
  sim.t += dt;
  var u = sim.t % 36;
  var tg = lapTargets(u);
  sim.thr += (tg.thr - sim.thr) * 0.18;
  var br = tg.br;
  var drag = 0.0032 * sim.speed * sim.speed;            /* v 单位 m/s，~38 m/s 时与满油门平衡 */
  sim.a = sim.thr * 4.6 - drag - br * 8.0;              /* m/s² */
  sim.speed = Math.max(0, Math.min(38, sim.speed + sim.a * dt));
  var gear = sim.speed < 8 ? 0 : sim.speed < 15 ? 1 : sim.speed < 24 ? 2 : 3;
  var rpm = Math.max(900, Math.min(9800, 900 + sim.speed * GR[gear] + (Math.random()-0.5)*60));
  /* 恒转矩区 -> 恒功率区：受 60 kW 电池功率硬约束，基速 ≈ P/(T·0.10472) ≈ 3.8k RPM */
  var tqCap = rpm > 100 ? Math.min(T_MAX, P_MAX / (rpm * 0.10472)) : T_MAX;
  var tq  = Math.max(0, Math.min(tqCap, sim.thr * T_MAX * (sim.speed < 2 ? 0.6 : 1) + (Math.random()-0.5)*6));
  var power = tq * rpm * 0.10472;                       /* W，峰值被钳在 60 kW */
  var volt = Math.max(300, 492 + (Math.random()-0.5)*3);
  var amp  = Math.min(200, power / 0.9 / volt);
  volt = Math.max(300, volt - amp * 0.10);
  var tTarget = 44 + amp * 0.32;
  sim.temp += (tTarget - sim.temp) * 0.02;
  sim.roll += (tg.roll - sim.roll) * 0.08;
  var yawRate = sim.roll * sim.speed * 0.016;
  sim.yaw = (sim.yaw + yawRate * dt + 360) % 360;
  sim.pitch += (-sim.a * 0.42 - sim.pitch) * 0.12;
  /* ★ 加速度（协议拆分后演示模式也要给**两路**，否则矢量图只有一个方向不动）。
     ★★ 量纲修正（2026-10-16 task-25 实测）：
        真机 payload 里的 accel_lat/accel_lon 是**已经换算好的物理 g**（后端乘过 scale），
        前端 applyPayload 是**直接赋值、不再乘 scale** —— 所以演示模式也必须直接给 g。
        旧代码写的是 `sim.a * ACC_RAW_FS * ACC_SCALE / 9.81`（注释还说"显示值 = 原始码 × scale"），
        那等于把 g 又乘了一次满量程（配置到位时 16.384 倍；没后端时 16384 倍）：
        实测 sim.a=3.37 m/s² 被显示成 5.58 g（真实值 0.34 g）→ 矢量图直接甩到圈外/贴边。
        现在两路都是纯物理 g：
          纵向 = 驱动/制动加速度 sim.a / g
          横向 = 转向侧向加速度 v·ω / g（ω = 上面同一处 yawRate，单位 rad/s） */
  var yawRateDeg = sim.roll * sim.speed * 0.016;                 /* 与上面 yawRate 同式（°/s） */
  var yawRateRad = yawRateDeg * Math.PI / 180;
  var accelLon = sim.a / 9.81;                                   /* g */
  var accelLat = (sim.speed * yawRateRad) / 9.81;                /* g（向心加速度 v·ω） */
  var accelMag = Math.sqrt(accelLat * accelLat + accelLon * accelLon);

  /* 传感 ADC 模拟：与驾驶动作弱相关 + 噪声 */
  var v = {};
  v['adc'+0] = Math.round(Math.max(0, Math.min(4095, 2048 - sim.roll * 95 + (Math.random()-0.5)*30)));
  for (var i = 1; i <= 4; i++)
    v['adc'+i] = Math.round(Math.max(0, Math.min(4095,
      2048 + sim.roll * 26 * (i % 2 ? 1 : -1) + Math.sin(sim.t*2.2 + i*1.3) * (22 + sim.speed*0.8) + (Math.random()-0.5)*24)));
  v['adc'+5] = Math.round(Math.max(0, Math.min(4095, 2048 + sim.roll * 80 + (Math.random()-0.5)*20)));
  var pBase = [1650, 1580, 1420, 1300];
  for (var j = 6; j <= 9; j++)
    v['adc'+j] = Math.round(Math.max(0, Math.min(4095,
      pBase[j-6] + sim.speed * 7.0 + Math.sin(sim.t*3.1 + j) * 26 + (Math.random()-0.5)*36)));
  v['speed'] = +sim.speed.toFixed(1);   /* m/s，协议 int16 ×100 解码后 */
  v['rpm'] = Math.round(rpm);
  v['throttle'] = Math.round(sim.thr * 100);
  v['torque'] = +tq.toFixed(1);
  v['temp'] = +sim.temp.toFixed(1);
  v['volt'] = +volt.toFixed(1);
  v['amp'] = +amp.toFixed(1);
  /* 派生量电池功率：演示模式与真机走同一公式（volt × amp / 1000）。
     注意演示引擎把电机功率钳在 60 kW、再按电压折算出电流，所以这里算出来
     约 0~67 kW（volt×amp = power/0.9）—— **低于 80 kW 阈值**，演示模式下
     看到的是"绿色正常"；要演示超限告警需构造 payload（见汇报里的实测）。 */
  v['power'] = +(volt * amp / 1000).toFixed(1);
  v['roll'] = +sim.roll.toFixed(1);
  v['pitch'] = +sim.pitch.toFixed(1);
  v['yaw'] = +sim.yaw.toFixed(1);
  v['accelLat'] = +accelLat.toFixed(ACC_DEC);
  v['accelLon'] = +accelLon.toFixed(ACC_DEC);
  v['accelMag'] = +accelMag.toFixed(ACC_DEC);

  for (var k in v){
    var b = state.buf[k];
    b.push(v[k]);
    if (b.length > BUF) b.splice(0, b.length - BUF);
    state.latest[k] = v[k];
  }
  state._rx += 8;   /* P0×5 + P1×0.5 + P2×2.5 ≈ 每拍 8 帧（演示口径） */
  state.hasData.adc = state.hasData.nav = state.hasData.motor = true;   /* 演示数据覆盖全部包类型 */
  sim.rpm = rpm;
  trendDirty = true;   /* 关键：每拍置脏，主循环才会按取样频率重绘趋势图 */
  domDirty = true;
  if (state.recording && !backend) localRecSample();
}

var simTimer = null;
function startSim(){ if (!simTimer) simTimer = setInterval(function(){ if (!state.frozen) simStep(0.05); }, 50); }
function stopSim(){ if (simTimer){ clearInterval(simTimer); simTimer = null; } }

/* ---------- 4. 工具 ---------- */
function $(id){ return document.getElementById(id); }
function setNum(el, txt){ if (el && el._t !== txt){ el.textContent = txt; el._t = txt; } }
/* 进度条：写 transform 而不是 width —— 不触发 layout，只走合成层 */
function setScale(el, frac){
  if (!el) return;
  frac = frac < 0 ? 0 : frac > 1 ? 1 : frac;
  var s = 'scaleX(' + frac.toFixed(4) + ')';
  if (el._s !== s){ el.style.transform = s; el._s = s; }
}
function setScaleY(el, frac){
  if (!el) return;
  frac = frac < 0 ? 0 : frac > 1 ? 1 : frac;
  var s = 'scaleY(' + frac.toFixed(4) + ')';
  if (el._s !== s){ el.style.transform = s; el._s = s; }
}
/* ★★ 界面上所有「数值」都走这两个函数，禁止再写 toFixed(1)/Math.round ★★
 * 位数只来自 SIGS[id].fmt（后端第 5 区 FIELD_DECIMALS / 第 3 区 ADC_DECIMALS）。
 * 以前每处各写各的（speed 用 toFixed(1)、rpm 用 Math.round、CSV 又按 scale 推），
 * 于是「改了系数位数跟着变 / 界面和 CSV 两个口径」这类问题反复出现。
 * fmtNumSep 给转速这种整数加千分位，位数同样听配置。 */
function fmtNum(id, v){
  var s = SIGS[id];
  var d = clampDec(s && s.fmt, 2);          /* ★ 夹紧：配置位数不可信（见 clampDec） */
  var n = Number(v);
  if (!isFinite(n)) return '—';
  return n.toFixed(d);
}
function fmtNumSep(id, v){
  var s = SIGS[id];
  var d = clampDec(s && s.fmt, 0);          /* ★ 同上 */
  var n = Number(v);
  if (!isFinite(n)) return '—';
  return n.toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d});
}
function hms(d){
  var p = function(n){ return String(n).padStart(2,'0'); };
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}
function stamp(){
  var d = new Date();
  var p = function(n){ return String(n).padStart(2,'0'); };
  return '' + d.getFullYear() + p(d.getMonth()+1) + p(d.getDate()) + '_' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
}
function mmss(sec){
  sec = Math.max(0, Math.floor(sec));
  return String(Math.floor(sec / 60)).padStart(2,'0') + ':' + String(sec % 60).padStart(2,'0');
}

/* ---------- 5. 构建 P0 卡片 / 趋势 chips / 换挡灯 ---------- */
function buildAdcCards(){
  var grid = $('adcGrid');
  var html = '';
  for (var i = 0; i < ADC_NAMES.length; i++){
    html += '<div class="acard">' +
      '<div class="a-top">' +
        '<span class="a-ch">CH' + String(i+1).padStart(2,'0') + '</span>' +
      '</div>' +
      '<div class="a-name">' + ADC_NAMES[i] + '</div>' +
      /* ★ 单位后缀以前根本没渲染（第 3 区改了 unit 界面纹丝不动），现在补上。
           注意 data-val 是内层 span：setNum 写的是 textContent，
           挂在 div 上会把旁边的单位一起冲掉。 */
      '<div class="a-val"><span data-val>—</span>' +
        '<span class="a-unit">' + (ADC_UNITS[i] || '') + '</span></div>' +
      '<div class="a-spark"><svg viewBox="0 0 100 20" preserveAspectRatio="none">' +
        '<path data-spark d="M0,20 L0,10 L100,10 L100,20 Z" fill="' + ADC_COLORS[i] + '" opacity="0.22"></path>' +
      '</svg></div>' +
    '</div>';
  }
  grid.innerHTML = html;
}
function buildChips(){
  var bar = $('chipBar');
  /* ★ D11（task-29）：按 **P0→P1→P2** 排（页面分区是 P2→P1→P0，芯片按量组由小到大更好找）。
     原来直接用 Object.keys(SIGS)（= defSig 的声明顺序：P0 → P2 → P1 → 后补的 power）
     → 实测顺序是 P0→P2→P1，与分组语义不符、也和数值面板顺序不一致。
     ★ 只改**显示顺序**：data-id / 事件绑定 / state.visible 全都不变，交互零影响。 */
  var GORD = {P0: 0, P1: 1, P2: 2};
  var order = Object.keys(SIGS).sort(function(a, b){
    var ga = GORD[SIGS[a] && SIGS[a].group], gb = GORD[SIGS[b] && SIGS[b].group];
    if (ga === undefined) ga = 9; if (gb === undefined) gb = 9;
    return ga - gb;                       /* Array.sort 稳定：同组内保持原声明顺序 */
  });
  var html = '';
  var lastG = '';
  order.forEach(function(id){
    var s = SIGS[id];
    if (s.group !== lastG){ html += '<span class="chip-g">' + s.group + '</span>'; lastG = s.group; }
    var on = state.visible.has(id);
    html += '<span class="chip' + (on ? ' on' : '') + '" data-id="' + id + '" role="button" tabindex="0"' +
            ' aria-pressed="' + (on ? 'true' : 'false') + '" style="--cc:' + s.color + '">' +
            '<span class="cdot"></span>' + s.label + '</span>';
  });
  bar.innerHTML = html;
  bar.onclick = function(e){
    var chip = e.target.closest('.chip');
    if (!chip) return;
    toggleChip(chip.dataset.id);
  };
  bar.onkeydown = function(e){
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var chip = e.target.closest('.chip');
    if (!chip) return;
    e.preventDefault();
    toggleChip(chip.dataset.id);
  };
  buildSigPanel();   /* 右侧当前值面板与通道开关永远同步 */
}

/* ---------- 右侧「当前值面板」：分析曲线时的读数入口 ----------
 * 画布末端标注在多路时必然互相覆盖（右缘只有几十像素宽），读不了；
 * 这里每条可见曲线固定一行：色块 + 名字 + 当前值 + 单位。
 *   · 行 hover  -> 画布只高亮这条曲线，其余淡出（多路拥挤时的定位手段）
 *   · 行点击    -> 开关该通道（和上方 chip 等效）
 *   · 十字光标  -> 数值列临时显示「游标时刻」的值，离开恢复当前值 */
function fmtSigVal(v, sig){
  var a = Math.abs(v);
  if (!isFinite(a)) return '—';
  /* ★ 位数统一听配置：ADC 走第 3 区 ADC_DECIMALS，
       其余走第 5 区 FIELD_DECIMALS（都在 SIGS.fmt 里）。
       以前这里【按数值大小硬编码位数】（>=100→1 位、>=10→2 位…），
       结果同一个量在不同车速下位数会变，跟界面、CSV 三个口径互相打架。
       只有上万的大数仍缩写 k（转速 12000 写成 12.0k 更好读）。 */
  if (sig && typeof sig.fmt === 'number'){
    if (a >= 10000) return (v / 1000).toFixed(1) + 'k';
    /* ★ 夹紧：sig.fmt 来自配置，写 200 会抛 RangeError 并让整帧读数更新中断 */
    return v.toFixed(clampDec(sig.fmt, 2));
  }
  if (a >= 10000) return (v / 1000).toFixed(1) + 'k';
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  if (a >= 10) return v.toFixed(2);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
function buildSigPanel(){
  var panel = $('sigPanel');
  if (!panel) return;
  var ids = Array.from(state.visible);
  var cap = '<div class="sig-cap"><b>数值</b><span id="sigCapT">实时</span></div>';
  var html = cap;
  ids.forEach(function(id){
    var s = SIGS[id];
    if (!s) return;
    html += '<div class="sig-row' + (state.hl === id ? ' hl' : '') + '" data-id="' + id + '"' +
            ' role="button" tabindex="0" aria-pressed="true">' +
            '<span class="sig-dot" style="background:' + s.color + '"></span>' +
            '<span class="sig-name">' + s.label + '</span>' +
            '<span class="sig-val" data-v>—</span>' +
            '<span class="sig-un">' + (s.unit || '') + '</span></div>';
  });
  panel.innerHTML = html;
  updateSigPanel();
}
/* 当前窗口实际点数：以第一条可见曲线的 buffer 为准（攒数据期 < BUF，
 * ★ 面板的时间标签与光标标签必须用同一个 len，否则两边显示的时刻对不上） */
function trendBufLen(){
  var vis = Array.from(state.visible);
  for (var i = 0; i < vis.length; i++){
    var bb = state.buf[vis[i]];
    if (bb && bb.length >= 2) return bb.length;
  }
  return 0;
}
function updateSigPanel(){
  var panel = $('sigPanel');
  if (!panel) return;
  var cur = state.cur, curIdx = -1, curLen = 0;
  if (cur && cur.active){
    curLen = trendBufLen();
    if (curLen) curIdx = Math.round(cur.frac * (curLen - 1));
  }
  var rows = panel.querySelectorAll('.sig-row');
  for (var i = 0; i < rows.length; i++){
    var row = rows[i];
    var id = row.dataset.id;
    var b = state.buf[id];
    var s = SIGS[id];
    var v = null;
    if (b && b.length){
      v = (curIdx >= 0 && curIdx < b.length) ? b[curIdx] : b[b.length - 1];
    }
    var el = row.querySelector('[data-v]');
    if (el) setNum(el, v === null ? '—' : fmtSigVal(v, s));
    if (curIdx >= 0 && b) row.dataset.idx = String(Math.min(curIdx, b.length - 1));
    else row.dataset.idx = '';
  }
  var capT = $('sigCapT');
  if (capT){
    if (cur && cur.active && curIdx >= 0 && curLen){
      var ago = (curLen - 1 - curIdx) * SAMPLE_MS / 1000;
      setNum(capT, ago < 0.05 ? '最新' : '-' + ago.toFixed(1) + 's');
    } else {
      setNum(capT, '实时');
    }
  }
}
/* 十字光标层：只画一条竖线 + 顶部时间标签；数值在面板里读 */
function drawCursorLayer(){
  if (!syncLayer(cursorLayer)) return;
  var ctx = cursorLayer.cv.getContext('2d');
  ctx.setTransform(cursorLayer.dpr, 0, 0, cursorLayer.dpr, 0, 0);
  ctx.clearRect(0, 0, cursorLayer.w, cursorLayer.h);
  var pad = TREND_PAD;
  /* 框选选区：半透明绿色矩形（先画，十字光标叠在上面） */
  if (state.sel){
    var slen = trendBufLen();
    if (slen >= 2){
      var sx0 = pad.left + (Math.max(0, state.sel.i0) / (slen - 1)) * (cursorLayer.w - pad.left - pad.right);
      var sx1 = pad.left + (Math.min(slen - 1, state.sel.i1) / (slen - 1)) * (cursorLayer.w - pad.left - pad.right);
      ctx.fillStyle = 'rgba(14,159,110,.08)';
      ctx.fillRect(sx0, pad.top, sx1 - sx0, cursorLayer.h - pad.top - pad.bottom);
      ctx.strokeStyle = 'rgba(14,159,110,.45)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 2]);
      ctx.strokeRect(sx0 + .5, pad.top + .5, sx1 - sx0 - 1, cursorLayer.h - pad.top - pad.bottom - 1);
      ctx.setLineDash([]);
    }
  }
  var c = state.cur;
  if (!c || !c.active) return;
  var x = pad.left + c.frac * (cursorLayer.w - pad.left - pad.right);
  ctx.strokeStyle = 'rgba(15,23,42,.34)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(x, pad.top);
  ctx.lineTo(x, cursorLayer.h - pad.bottom);
  ctx.stroke();
  ctx.setLineDash([]);
  var len = trendBufLen();
  if (!len) return;
  var idx = Math.round(c.frac * (len - 1));
  var ago = (len - 1 - idx) * SAMPLE_MS / 1000;
  ctx.font = '11px Consolas, monospace';
  ctx.fillStyle = '#334155';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  var label = ago < 0.05 ? 'now' : '-' + ago.toFixed(1) + 's';
  var tx = Math.min(Math.max(x, pad.left + 16), cursorLayer.w - pad.right - 16);
  ctx.fillText(label, tx, pad.top + 2);

  /* ★ 悬停浮层：光标旁列出每条可见曲线在该时刻的值（色块 + 数值 + 单位）。
     以前这里**只有一条虚线和时间标签**，用户「暂停后悬停看不到数值」——
     右侧面板虽然有读数，但视线在图上，且多路时对不上是哪条。
     做法与「数据可视化」区 drawVChart 的 hover 段完全一致（用户要求两处体验统一）。
     ⚠️ 只画在独立的光标层 canvas 上：不写 DOM、不加 CSS transition，
        所以既不会碰 B08（过渡跑不完），也不会碰 B10（合成层闪烁）。
     这一层只在 mousemove 时重画（另有 30Hz 主循环里的一次随刷），
     十几行 measureText + fillText 远低于一帧预算。 */
  var vids = Array.from(state.visible);
  var vrows = [];
  for (var vi = 0; vi < vids.length; vi++){
    var vid = vids[vi];
    var vb = state.buf[vid];
    var vs = SIGS[vid];
    if (!vb || !vb.length || !vs) continue;
    var vv = vb[idx < vb.length ? idx : vb.length - 1];
    vrows.push({c: vs.color, t: fmtSigVal(vv, vs) + ' ' + (vs.unit || '')});
  }
  if (!vrows.length) return;
  ctx.font = '11px Consolas, monospace';
  var boxW = 0;
  for (var ri = 0; ri < vrows.length; ri++){
    var wRow = ctx.measureText(vrows[ri].t).width;
    if (wRow > boxW) boxW = wRow;
  }
  boxW += 20;                              /* 色块 7 + 间隙 + 内边距 */
  var rowH = 14;
  var bx = x + 6;
  if (bx + boxW > cursorLayer.w - pad.right) bx = x - 6 - boxW;   /* 右侧放不下就翻到左侧 */
  if (bx < pad.left) bx = pad.left;
  var by = pad.top + 16;                   /* 让开顶部的时间标签 */
  if (by + vrows.length * rowH + 4 > cursorLayer.h - pad.bottom){
    by = Math.max(pad.top + 2, cursorLayer.h - pad.bottom - vrows.length * rowH - 4);
  }
  ctx.fillStyle = 'rgba(255,255,255,.92)';
  ctx.fillRect(bx - 4, by - 3, boxW + 8, vrows.length * rowH + 5);
  for (var rj = 0; rj < vrows.length; rj++){
    var ry = by + rj * rowH;
    ctx.fillStyle = vrows[rj].c;
    ctx.fillRect(bx, ry + 2, 7, 7);
    ctx.fillStyle = '#1B2434';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(vrows[rj].t, bx + 11, ry - 1);
  }
}

/* ---------- 框选区间统计（数据分析） ----------
 * 在趋势图上拖拽框选一段区间，计算每条可见曲线在该区间的
 * min / max / 均值，浮层显示。配合「暂停」使用可仔细分析。 */
function trendXToFrac(x, boxW){
  var pad = TREND_PAD;
  var frac = (x - pad.left) / Math.max(1, boxW - pad.left - pad.right);
  return frac < 0 ? 0 : frac > 1 ? 1 : frac;
}
function trendFracToIdx(frac, len){
  return Math.max(0, Math.min(len - 1, Math.round(frac * (len - 1))));
}
function calcSelStats(sel){
  var ids = Array.from(state.visible);
  var rows = [];
  for (var k = 0; k < ids.length; k++){
    var b = state.buf[ids[k]];
    if (!b || b.length < 2) continue;
    var lo = Infinity, hi = -Infinity, sum = 0, n = 0;
    var i0 = Math.max(0, sel.i0), i1 = Math.min(b.length - 1, sel.i1);
    for (var i = i0; i <= i1; i++){
      var v = b[i];
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      sum += v; n++;
    }
    if (n > 0) rows.push({id: ids[k], lo: lo, hi: hi, avg: sum / n});
  }
  return rows;
}
function showSelStats(sel){
  var len = trendBufLen();
  var box = $('selStats');
  if (!len || !box) return;
  var i0 = Math.max(0, sel.i0), i1 = Math.min(len - 1, sel.i1);
  var ago0 = (len - 1 - i0) * SAMPLE_MS / 1000;   /* 左边界（较早）距现在 */
  var ago1 = (len - 1 - i1) * SAMPLE_MS / 1000;   /* 右边界（较晚）距现在 */
  var rangeTxt = '区间 -' + ago0.toFixed(1) + 's ~ -' + ago1.toFixed(1) + 's'
               + ' · ' + (i1 - i0 + 1) + ' 点';
  var rows = calcSelStats(sel);
  var html = '<div class="ss-range">' + rangeTxt + '</div>';
  rows.forEach(function(r){
    var s = SIGS[r.id];
    html += '<div class="ss-row"><span class="ss-dot" style="background:' + (s ? s.color : '#888') + '"></span>'
          + '<span class="ss-name">' + (s ? s.label : r.id) + '</span>'
          + '<span class="ss-v">' + fmtSigVal(r.lo, s) + ' ~ ' + fmtSigVal(r.hi, s)
          + ' · 均 ' + fmtSigVal(r.avg, s) + '</span></div>';
  });
  box.innerHTML = html;
  box.style.display = 'block';
}
function hideSelStats(){
  var box = $('selStats');
  if (box) box.style.display = 'none';
}
function syncPanelHL(){
  var panel = $('sigPanel');
  if (!panel) return;
  var rows = panel.querySelectorAll('.sig-row');
  for (var i = 0; i < rows.length; i++){
    var on = rows[i].dataset.id === state.hl;
    if ((rows[i].className.indexOf('hl') >= 0) !== on){
      rows[i].className = 'sig-row' + (on ? ' hl' : '');
    }
  }
}
function toggleChip(id){
  if (!SIGS[id]) return;
  if (state.visible.has(id)) state.visible.delete(id);
  else state.visible.add(id);
  /* ★ 不再强制保留最后一条（原为 `if (state.visible.size === 0) state.visible.add(id);`）。
     那个兜底会让「只剩一条时点它」变成点了没反应 —— 正是用户报的
     「图例点击时好有时坏 / 点没反应」：离屏实测点前点后 visible.size 与
     .chip.on 数量都是 1，chip 外观完全没变。
     现在允许把通道全部关掉，空态由 drawTrend 画网格 + 提示兜底（不是白板）。 */
  /* ★ 高亮跟着可见集合走：被关掉的那条不能继续占着 state.hl，
     否则 drawTrend 会把其余可见曲线全部淡到 .13（详见 drawTrend 里那段注释）。
     这是「有时莫名其妙又出来了」的那一半。 */
  if (state.hl && !state.visible.has(state.hl)) state.hl = null;
  buildChips();
  trendLayer.sKey = '';   /* 量程可能变 -> 静态层失效 */
  trendDirty = true;
}
function buildRev(){
  var row = $('revRow');
  var html = '';
  for (var i = 0; i < UI_CFG.revSegs; i++){
    var cls = i < 7 ? 'c1' : i < 10 ? 'c2' : 'c3';
    html += '<div class="rev-seg ' + cls + '"></div>';
  }
  row.innerHTML = html;
}

/* ---------- 6. Canvas 图层（含"静态网格/刻度"缓存层） ----------
 * v2.0 是"离屏画整幅 -> drawImage 到可见画布"的双缓冲。实测在 1302×210 下
 * blit 只要 0.027ms、clear 0.045ms，双缓冲本身不贵，但它让每帧多一次全画布
 * 像素搬运；这里改成直接画可见画布，并把**不常变的网格与刻度**单独缓存，
 * 每帧只 blit 一次 + 画曲线。 */
/* 以前这里是写死的 var DPR_CAP = 2，和第 5 区 UI_CFG.dprCap 是两份真源，
   改那边的 dprCap 完全无效。现在统一从 UI_CFG 读，并把 UI_CFG 变化反映到画布。 */
function createLayer(canvas){
  var L = {
    cv:canvas, w:0, h:0, dpr:1, dirty:true, ok:false,
    stat:document.createElement('canvas'), sctx:null, sKey:''
  };
  if (typeof ResizeObserver !== 'undefined'){
    try { new ResizeObserver(function(){ L.dirty = true; L.sKey = ''; }).observe(canvas); }
    catch(e){ L.dirty = true; }
  }
  return L;
}
function syncLayer(L){
  if (!L.dirty) return L.ok;
  var dpr = Math.min(window.devicePixelRatio || 1, UI_CFG.dprCap || 2);
  var w = L.cv.clientWidth, h = L.cv.clientHeight;
  if (w < 8 || h < 8){ L.ok = false; return false; }
  var pw = Math.round(w * dpr), ph = Math.round(h * dpr);
  if (L.cv.width !== pw || L.cv.height !== ph){
    L.cv.width = pw; L.cv.height = ph;
    L.stat.width = pw; L.stat.height = ph;
    L.sctx = null;
  }
  L.w = w; L.h = h; L.dpr = dpr; L.ok = true; L.dirty = false;
  return true;
}
/* 签名兼容旧调用 paintLayer(L, fn)；fn(ctx, w, h, L) */
function paintLayer(L, fn){
  if (!syncLayer(L)) return;
  var ctx = L.cv.getContext('2d');
  ctx.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  ctx.clearRect(0, 0, L.w, L.h);
  fn(ctx, L.w, L.h, L);
}
/* ★ 位图是否"落后于"CSS 盒子（2026-10-15 resize 闪屏的根因判据）。
 *   canvas 是 `width:100%;height:100%`，拖动分区高度时它的 CSS 盒子每帧都在变，
 *   而位图只在 syncLayer 里重建（那会**清空**画布，所以只能跟着重绘走）。
 *   主循环重绘最多 30Hz 且还要求 trendDirty —— 中间那些帧浏览器会把旧位图**缩放**贴到新盒子上，
 *   就是用户看到的"调区域大小时闪/发虚"（实测：拖 zoneTrend 时 5/10 采样帧处于这种状态）。
 *   返回值：true = 需要（且已被标脏准备好）重绘。 */
function layerStale(L){
  if (!L || !L.cv || !L.cv.clientWidth || !L.cv.clientHeight) return false;
  var dpr = Math.min(window.devicePixelRatio || 1, UI_CFG.dprCap || 2);
  if (L.cv.width === Math.round(L.cv.clientWidth * dpr)
      && L.cv.height === Math.round(L.cv.clientHeight * dpr)) return false;
  L.dirty = true;          /* syncLayer 只在 dirty 时才重新量尺寸 */
  return true;
}

var trendLayer = createLayer($('trendCanvas'));
/* 十字光标层：独立画布盖在趋势画布上。mousemove 只重画这一层
   （清空 + 1 条虚线 + 1 个时间标签，<0.2ms），不碰下面 30fps 的趋势重绘。 */
var cursorLayer = createLayer($('cursorCanvas'));
var trendDirty = true;
var domDirty = true;   /* 有新数据才刷新 DOM：空闲时零开销 */

/* ---------- 7. 趋势曲线绘制 ---------- */
function autoRange(ids){
  var lo = Infinity, hi = -Infinity;
  ids.forEach(function(id){
    var b = state.buf[id];
    for (var i = 0; i < b.length; i++){
      if (b[i] < lo) lo = b[i];
      if (b[i] > hi) hi = b[i];
    }
  });
  if (lo === Infinity) return {lo:0, hi:1};
  if (hi - lo < 1) hi = lo + 1;
  return {lo:lo, hi:hi};
}

var TREND_PAD = {top:12, right:14, bottom:26, left:46};

/* ---------- 7.1 量程三档：归一化 / 自适应 / 分组分带 ----------
   解决「不同数据的比例不合适」：
     norm 每条按注册量程映射 0–100% —— 看形状，但注册量程不准时会贴底成平线
     auto 每条按窗口内自身 min/max 独立缩放 —— 细节最大，适合看单一信号量级
     band P0/P1/P2 各占一条带 —— 量纲差一个数量级也互不压扁，适合跨包对照 */
var RANGE_MODES  = ['norm', 'auto', 'band'];
var RANGE_LABELS = {norm:'归一化量程', auto:'自适应量程', band:'分组分带'};

/* 自适应量程：每条曲线一个独立区间。
   ★ 250ms 缓存：一是省掉每帧全缓冲扫描，二是避免区间每帧微动导致曲线「呼吸」。 */
var _perCache = {t:0, map:null};
function perCurveRange(ids){
  var now = performance.now();
  if (_perCache.map && now - _perCache.t < 250) return _perCache.map;
  var out = {};
  for (var n = 0; n < ids.length; n++){
    var id = ids[n], b = state.buf[id];
    if (!b || b.length < 2) continue;
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < b.length; i++){
      if (b[i] < lo) lo = b[i];
      if (b[i] > hi) hi = b[i];
    }
    if (lo === Infinity) continue;
    var mv = (hi - lo) * 0.08 || Math.max(1e-6, Math.abs(hi) * 0.02);
    if (hi - lo < 1e-9){ lo -= 0.5; hi += 0.5; mv = 0; }
    out[id] = {lo: lo - mv, hi: hi + mv};
  }
  _perCache = {t: now, map: out};
  return out;
}

/* 分组分带：按 P0/P1/P2 分组，每组一条带。
   带内量程取该组信号的注册 min/max（不是窗口数据），保证带高度不随时间抖动。
   plotTop/plotH 给了才算像素坐标（drawTrend 用）；不给时只算 lo/hi（自检用）。 */
var GROUP_ORDER = ['P0', 'P1', 'P2'];
function bandPlan(ids, plotTop, plotH){
  var groups = {};
  for (var n = 0; n < ids.length; n++){
    var s = SIGS[ids[n]];
    if (!s) continue;
    var g = s.group || 'OTHER';
    (groups[g] = groups[g] || []).push(ids[n]);
  }
  var keys = Object.keys(groups).sort(function(a, b){
    var ia = GROUP_ORDER.indexOf(a), ib = GROUP_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  var out = [];
  keys.forEach(function(g){
    var members = groups[g], lo = Infinity, hi = -Infinity;
    members.forEach(function(id){
      var s2 = SIGS[id];
      if (s2.min < lo) lo = s2.min;
      if (s2.max > hi) hi = s2.max;
    });
    if (lo === Infinity){ lo = 0; hi = 1; }
    if (hi - lo < 1e-9) hi = lo + 1;
    out.push({group:g, ids:members, lo:lo, hi:hi, y0:0, y1:0});
  });
  if (typeof plotH === 'number' && plotH > 0 && out.length){
    var gap = 6, bandH = (plotH - gap * (out.length - 1)) / out.length;
    out.forEach(function(b2, i){
      b2.y0 = plotTop + i * (bandH + gap);
      b2.y1 = b2.y0 + bandH;
    });
  }
  return out;
}

/* 静态层：网格 + 坐标轴 + 刻度文字。
 * 只在尺寸 / 量程模式 / 量程区间（量化后）/ 时间窗变化时重画，
 * 稳态下每帧只做一次 drawImage（实测 ~0.03ms）。 */
function drawStatic(L, w, h, o, winSec){
  var pad = TREND_PAD;
  var plotW = w - pad.left - pad.right, plotH = h - pad.top - pad.bottom;
  var mode = o.mode, yMin = o.yMin, yMax = o.yMax, bands = o.bands;

  /* 量程量化到 1/40 跨度：自动量程每帧都在微动，不量化就每帧重画静态层 */
  var qstep = Math.max(1e-6, (yMax - yMin) / 40);
  var qyMin = Math.round(yMin / qstep) * qstep;
  var qyMax = Math.round(yMax / qstep) * qstep;
  var bandKey = '';
  if (mode === 'band' && bands){
    bandKey = bands.map(function(b){ return b.group + b.lo + b.hi; }).join(',');
  }
  var key = [Math.round(w), Math.round(h), L.dpr, mode,
             qyMin.toFixed(3), qyMax.toFixed(3), Math.round(winSec), bandKey].join('|');
  if (L.sKey === key) return;
  L.sKey = key;

  if (!L.sctx) L.sctx = L.stat.getContext('2d');
  var c = L.sctx;
  c.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  c.clearRect(0, 0, w, h);

  /* ---- 分组分带：每条带画自己的框 + 组名 + 带内量程，互不干扰 ---- */
  if (mode === 'band' && bands && bands.length){
    c.font = '11px Consolas, monospace';
    for (var bi = 0; bi < bands.length; bi++){
      var bd = bands[bi];
      /* 淡背景条 + 上下边界 */
      c.fillStyle = 'rgba(15,23,42,.028)';
      c.fillRect(pad.left, bd.y0, plotW, bd.y1 - bd.y0);
      c.strokeStyle = 'rgba(15,23,42,.16)';
      c.lineWidth = 1;
      c.beginPath();
      c.moveTo(pad.left, bd.y0 + .5); c.lineTo(w - pad.right, bd.y0 + .5);
      c.moveTo(pad.left, bd.y1 + .5); c.lineTo(w - pad.right, bd.y1 + .5);
      c.stroke();
      c.strokeStyle = 'rgba(15,23,42,.07)';
      for (var bg = 1; bg < 4; bg++){
        var gy = bd.y0 + (bd.y1 - bd.y0) * bg / 4;
        c.beginPath(); c.moveTo(pad.left, gy); c.lineTo(w - pad.right, gy); c.stroke();
      }
      /* 左侧：组名 + 该带的量程上下限 */
      c.fillStyle = '#6B7686';
      c.textAlign = 'right'; c.textBaseline = 'middle';
      c.fillText(fmtTick(bd.hi), pad.left - 7, bd.y0 + 6);
      c.fillText(fmtTick(bd.lo), pad.left - 7, bd.y1 - 6);
      c.textAlign = 'left'; c.textBaseline = 'top';
      c.fillStyle = '#9AA3AF';
      c.fillText(bd.group + ' · ' + bd.ids.length + ' 路', pad.left + 5, bd.y0 + 4);
    }
  } else {
    c.strokeStyle = 'rgba(15,23,42,.07)';
    c.lineWidth = 1;
    for (var g2 = 1; g2 < 5; g2++){
      var y = pad.top + plotH * g2 / 5;
      c.beginPath(); c.moveTo(pad.left, y); c.lineTo(w - pad.right, y); c.stroke();
    }
    c.strokeStyle = 'rgba(15,23,42,.18)';
    c.beginPath(); c.moveTo(pad.left, h - pad.bottom); c.lineTo(w - pad.right, h - pad.bottom); c.stroke();

    c.font = '11px Consolas, monospace';
    c.fillStyle = '#6B7686';
    c.textAlign = 'right';
    c.textBaseline = 'middle';
    for (var t2 = 0; t2 <= 5; t2++){
      var ty = pad.top + plotH - plotH * t2 / 5;
      var label;
      if (mode === 'norm') label = String(t2 * 20) + '%';
      else if (mode === 'auto') label = '—';   /* 每条形程不同，绝对刻度无意义 */
      else label = String(Math.round(qyMin + (qyMax - qyMin) * t2 / 5));
      c.fillText(label, pad.left - 7, ty);
    }
  }
  /* 时间轴（三种模式共用） */
  c.font = '11px Consolas, monospace';
  c.fillStyle = '#6B7686';
  c.textAlign = 'center';
  c.textBaseline = 'top';
  var tick = winSec / 6;
  for (var s2 = 0; s2 <= 6; s2++){
    var xx = pad.left + plotW * (1 - s2 / 6);
    c.fillText(s2 === 0 ? '0s' : '-' + Math.round(s2 * tick) + 's', xx, h - pad.bottom + 7);
  }
}

function drawTrend(ctx, w, h, L){
  var pad = TREND_PAD;
  var plotW = w - pad.left - pad.right, plotH = h - pad.top - pad.bottom;
  var ids = Array.from(state.visible);
  /* ★ 全部通道都被关掉时不再直接 return（那样是一块纯白板，用户不知道发生了什么）。
     改为画归一化网格 + 一句提示：位置/字号与下面「暂无数据」空态完全一致，
     不是新增布局，只是让「点了最后一条」真的给出可见反馈（toggleChip 已允许全关）。 */
  if (!ids.length){
    drawStatic(L, w, h, {mode:'norm', yMin:0, yMax:100, bands:null}, BUF * SAMPLE_MS / 1000);
    ctx.drawImage(L.stat, 0, 0, w, h);
    ctx.font = '13px "Segoe UI","Microsoft YaHei",sans-serif';
    ctx.fillStyle = '#6B7686';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('未选择通道 · 点上方通道名打开曲线', pad.left + plotW / 2, pad.top + plotH / 2);
    return;
  }
  /* 空态判定：可见通道没有任何一条攒够 2 个点 -> 画占位提示，不画假数据 */
  var anyData = false;
  for (var ai2 = 0; ai2 < ids.length; ai2++){
    if (state.buf[ids[ai2]].length >= 2){ anyData = true; break; }
  }

  /* 量程三档：mode 决定每条曲线怎么映射到画布高度 */
  var mode = state.scaleMode || 'norm';
  var yMin = 0, yMax = 100, bands = null, per = null;
  if (mode === 'band'){
    bands = bandPlan(ids, pad.top, plotH);
    if (!bands.length) bands = null;        /* 没分组信息时退回归一化 */
  } else if (mode === 'auto'){
    per = perCurveRange(ids);
  } else if (mode === 'norm'){
    yMin = 0; yMax = 100;
  } else {
    var r = autoRange(ids);
    var mv = (r.hi - r.lo) * 0.12 || 10;
    yMin = r.lo - mv; yMax = r.hi + mv;
    if (yMax - yMin < 1) yMax = yMin + 1;
  }

  var winSec = BUF * SAMPLE_MS / 1000;
  drawStatic(L, w, h, {mode:mode, yMin:yMin, yMax:yMax, bands:bands}, winSec);
  ctx.drawImage(L.stat, 0, 0, w, h);

  if (!anyData){
    /* 空态：无连接且未开演示 -> 仅网格 + 提示，不出现零线假数据 */
    ctx.font = '13px "Segoe UI","Microsoft YaHei",sans-serif';
    ctx.fillStyle = '#6B7686';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    /* ★ F4：文案必须分语境。回放中 seek 到**最左端**时后端只回填 1 个槽（后端自己的
       docstring 也写明"seek 到 0 时只有 1 个槽"），缓冲区凑不出 2 个点 -> 走到这里。
       最左端恰恰是用户很容易拖到的位置；此时若显示"暂无数据 · 连接串口或开启演示模式"，
       在回放语境下就是错的，用户会以为回放坏了。 */
    ctx.fillText(state.replaying
                   ? '回放位置太靠前 · 往右拖一点即可看到曲线'
                   : '暂无数据 · 连接串口或在「设置」里开启演示模式',
                 pad.left + plotW / 2, pad.top + plotH / 2);
    return;
  }

  /* 曲线 + 末端光点。
   * 数值标注不再画在画布上：14 路时全部挤在右缘 60px 内互相覆盖，
   * 等于没有 —— 读数交给右侧「当前值面板」（sigPanel），画布只管形状。 */
  ctx.lineWidth = 1.6;
  ctx.lineJoin = 'bevel';          /* round 在数百段折线上最贵，bevel 视觉几乎无差 */
  ctx.lineCap = 'butt';
  var hl = state.hl;               /* 面板行 hover -> 只高亮这一条，其余淡出 */
  /* ★ 悬空高亮兜底（用户报的「图例点击时好时坏」真因之一）：
     面板行 hover 会把 state.hl 设成那条通道；若此时该通道被点掉（从 state.visible 删除），
     state.hl 仍指着它，而下面没有任何 id 与之相等 -> 所有可见曲线一起 alpha .13，
     主观感受就是"曲线全没了 / 点了没反应"；指针一离开面板 hl 清零，又"莫名其妙全回来了"。
     离屏实测：hover adc1 的行再点掉 adc1 -> 剩余两条 alpha 均为 .13。
     这里按「高亮只对可见通道有意义」的口径兜底，任何路径把通道关掉都不会再全淡。 */
  if (hl && !state.visible.has(hl)) hl = null;
  for (var k = 0; k < ids.length; k++){
    var id = ids[k];
    var b = state.buf[id];
    var len = b.length;
    if (len < 2) continue;
    var s = SIGS[id];
    /* 每条曲线的映射参数提到循环外算好，循环内只做算术 */
    var smin = s.min, sspan = (s.max - s.min) || 1;
    var gspan = (yMax - yMin) || 1;
    var bd = null, pr = null;
    if (bands){
      for (var bi2 = 0; bi2 < bands.length; bi2++){
        if (bands[bi2].ids.indexOf(id) >= 0){ bd = bands[bi2]; break; }
      }
      if (bd){ bd = {lo:bd.lo, span:(bd.hi - bd.lo) || 1, y0:bd.y0, y1:bd.y1}; }
    } else if (per){
      pr = per[id];
      if (pr) pr = {lo:pr.lo, span:(pr.hi - pr.lo) || 1};
    }
    var step = Math.max(1, Math.ceil(len / (plotW * 2)));
    ctx.strokeStyle = s.color;
    if (hl){ ctx.globalAlpha = (id === hl) ? 1 : 0.13; }
    ctx.lineWidth = (id === hl) ? 2.4 : 1.6;
    ctx.beginPath();
    var first = true, ly = 0;
    for (var i = 0; i < len; i += step){
      var x = pad.left + (i / (len - 1)) * plotW;
      var yy;
      if (bd){
        var fb = (b[i] - bd.lo) / bd.span;
        if (fb < 0) fb = 0; else if (fb > 1) fb = 1;
        yy = bd.y1 - fb * (bd.y1 - bd.y0);
      } else if (pr){
        var fp = (b[i] - pr.lo) / pr.span;
        if (fp < 0) fp = 0; else if (fp > 1) fp = 1;
        yy = pad.top + plotH - fp * plotH;
      } else if (mode === 'norm'){
        var f = (b[i] - smin) / sspan;
        if (f < 0) f = 0; else if (f > 1) f = 1;
        yy = pad.top + plotH - f * plotH;
      } else {
        yy = pad.top + plotH - ((b[i] - yMin) / gspan) * plotH;
      }
      if (first){ ctx.moveTo(x, yy); first = false; } else ctx.lineTo(x, yy);
      ly = yy;
    }
    ctx.lineTo(pad.left + plotW, ly);
    ctx.stroke();
    /* 末端光点：标出当前值位置（高亮路稍大） */
    ctx.globalAlpha = 1;
    ctx.fillStyle = s.color;
    ctx.beginPath();
    ctx.arc(pad.left + plotW, ly, id === hl ? 4 : 3, 0, 6.2832);
    ctx.fill();
  }

  /* 时间游标（最新点竖线） */
  ctx.strokeStyle = 'rgba(15,23,42,.12)';
  ctx.beginPath();
  ctx.moveTo(pad.left + plotW, pad.top);
  ctx.lineTo(pad.left + plotW, h - pad.bottom);
  ctx.stroke();
}

/* ---------- 7.5 数据可视化工作台（可添加图表 + 拖拽数据） ---------- */
var vcharts = [];        /* [{id, sigs, card, canvas, layer}] */
var vchartSeq = 0;

function buildVisChannels(){
  var box = $('visChannels');
  if (!box) return;
  var html = '<div class="vc-cap">数据通道 · 拖入图表</div>';
  Object.keys(SIGS).forEach(function(id){
    var s = SIGS[id];
    html += '<div class="vc-item" draggable="true" data-sig="' + id + '" title="拖到右侧图表">'
          + '<span class="vc-dot" style="background:' + s.color + '"></span><span>' + s.label + '</span></div>';
  });
  box.innerHTML = html;
  Array.prototype.forEach.call(box.querySelectorAll('.vc-item'), function(el){
    el.addEventListener('dragstart', function(e){
      e.dataTransfer.setData('text/plain', el.getAttribute('data-sig'));
      e.dataTransfer.effectAllowed = 'copy';
    });
  });
}

function updateVChartSigs(chart){
  var box = chart.card.querySelector('.vc-sigs');
  var html = '';
  chart.sigs.forEach(function(sig){
    var s = SIGS[sig];
    html += '<span class="vc-sig" data-sig="' + sig + '"><span class="vc-dot" style="background:'
          + (s ? s.color : '#888') + '"></span>' + (s ? s.label : sig) + '</span>';
  });
  box.innerHTML = html;
  var empty = chart.card.querySelector('.vc-empty');
  if (empty) empty.style.display = chart.sigs.length ? 'none' : 'flex';
}

/* 立即重画可视化图表 —— **交互/离散事件专用**，不经过主循环。
 * 为什么需要它：主循环里 `if (!state.frozen && trendDirty && …) vcharts.forEach(drawVChart)`
 * 有两重门禁 —— 暂停时（frozen）或数据不流动时（trendDirty 为 false）图表都不会刷新。
 * 于是"暂停中首次进入可视化区 / 暂停中增删信号 / 悬停"都会看到空白或旧画面。
 * 这里只在**离散事件**里调用（切区、拖入、移除、悬停、配置下发），
 * 不改变主循环结构、不引入每帧开销。
 * 传 one 就只画那一张（悬停用）；不传就全部画一遍（切区/配置变更用）。
 * 隐藏的分区里 clientWidth 为 0，syncLayer 会直接失败返回，等于白调——无副作用。 */
function paintVCharts(one){
  if (one){ drawVChart(one); return; }
  for (var i = 0; i < vcharts.length; i++) drawVChart(vcharts[i]);
}

/* ---------- 7.6 加速度矢量图（用户需求：矢量图 + 合成总加速度） ----------
 * 位置：**「总览 → 姿态与惯性」#zoneP1** 内（zone-head 与 .dyn-grid 之间）。
 *      2026-10-15 由用户指令从 zoneVisual 搬来 —— 搬区**没有**改任何绘制/数据逻辑，
 *      因为下面的可见性门控用的是"画布当前尺寸"而不是写死的分区名：
 *      分区隐藏时 clientWidth=0 -> 不画且保留脏标记；分区可见后一次离散/节拍调用就能补上。
 *      （比 zoneVisible('zoneP1') 更稳：不会被 IntersectionObserver 的异步时机坑，
 *        以后再有搬区也不用改判据。）
 * 数据：state.latest 的 accelLat / accelLon / accelMag（applyPayload 每批重算合成值）。
 * 满量程：ACC_FS（= GAUGE_RANGES.ACCEL，或 ACC_RAW_FS × ACCEL scale，全配置驱动），
 *         与 P1「合成加速度」G 条**同一口径** —— 同一份 state.latest，两处读数不会互相打脸。
 * 轴向：横轴 = 横向 LAT（右为正）、纵轴 = 纵向 LON（上为正）；同心圈 = 25/50/75/100% 满量程。
 * 重绘：accVecDirty 脏标记 + 离散入口（showZone 切区 / onReplayBackfill / applyUiConfig /
 *       悬停画布）+ updateDom() 的 20Hz 节拍。**不放进 rAF 主循环**（60Hz 无必要）。
 *       → 暂停时鼠标互动（悬停画布 / 切区 / seek 回填）仍能立刻刷新。 */
var accVecLayer = createLayer($('accVecCanvas'));

function paintAccVec(){
  if (!accVecDirty) return;
  var cv = $('accVecCanvas');
  if (!cv) return;
  /* ★ 可见性门控：看画布**当前**尺寸（矢量图现在在 #zoneP1，总览默认可见）。
     隐藏时直接返回并保留 accVecDirty，等可见后再补画 —— 不给隐藏分区白画。 */
  if (cv.clientWidth < 8 || cv.clientHeight < 8){
    accVecLayer.dirty = true;     /* 可见后重新量一次尺寸（syncLayer 会缓存 ok） */
    return;
  }
  if (!syncLayer(accVecLayer)) return;
  accVecDirty = false;
  var L = accVecLayer;
  var ctx = L.cv.getContext('2d');
  ctx.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  ctx.clearRect(0, 0, L.w, L.h);
  var w = L.w, h = L.h, cx = w / 2, cy = h / 2;
  var R = Math.max(10, Math.min(w, h) / 2 - 14);
  var has = !!state.hasData.nav;            /* 没收到 NAV 前不画假点 */
  var lat = has ? (Number(state.latest.accelLat) || 0) : 0;
  var lon = has ? (Number(state.latest.accelLon) || 0) : 0;
  var mag = has ? (Number(state.latest.accelMag) || 0) : 0;
  var fs = ACC_FS > 0 ? ACC_FS : 1;

  /* 同心圈 + 十字准星（G 值表样式，配色/线宽与既有 canvas 图表一个路子） */
  ctx.lineWidth = 1;
  for (var i = 1; i <= 4; i++){
    ctx.strokeStyle = (i === 4) ? 'rgba(15,23,42,.26)' : 'rgba(15,23,42,.11)';
    ctx.beginPath(); ctx.arc(cx, cy, R * i / 4, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(15,23,42,.24)';
  ctx.beginPath();
  ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
  ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R);
  ctx.stroke();

  /* 轴向标签 + 满量程刻度（写清哪边为正，免得读反方向）
     ★ 字号 9px→10px：9px 在本机笔记本屏上几乎看不清（排版统一后的最小字号是 11px，
       画布里的辅助刻度允许 10px，但不再低于它）。 */
  ctx.font = '10px Consolas, monospace';
  ctx.fillStyle = '#6B7686';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  ctx.fillText('LAT+', cx + R, cy - 7);
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  ctx.fillText('LON+', cx, cy - R + 1);
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.fillText(fmtFs(ACC_FS), 1, h - 1);

  /* 三个数字读数（DOM）：与画布同一份数据；位数走 fmtSigVal（= 配置的 dec） */
  setNum($('accVecLat'), has ? fmtSigVal(lat, SIGS.accelLat) : '—');
  setNum($('accVecLon'), has ? fmtSigVal(lon, SIGS.accelLon) : '—');
  setNum($('accVecMag'), has ? fmtSigVal(mag, SIGS.accelMag) : '—');
  setNum($('accVecUnit'), ACC_UNIT ? (' ' + ACC_UNIT) : '');
  setNum($('accVecChip'), fmtScaleChip(ACC_SCALE));
  setNum($('accVecNote'), has
    ? ('满量程 ±' + fmtFs(ACC_FS) + ' · 圈 = 25/50/75/100%')
    : '等待 NAV 数据…');

  if (!has) return;

  /* 命中点：超出满量程时夹到外圈（**读数仍显示真值**，另标"超满量程"） */
  var px = cx + (lat / fs) * R, py = cy - (lon / fs) * R;
  var dx = px - cx, dy = py - cy, d = Math.sqrt(dx * dx + dy * dy);
  if (d > R && d > 0){ var k = R / d; dx *= k; dy *= k; px = cx + dx; py = cy + dy; }

  /* 合成幅值所在半径（虚线圆）：一眼看出"总的有多大" */
  ctx.strokeStyle = 'rgba(220,38,38,.45)';
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.arc(cx, cy, R * Math.min(1, mag / fs), 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([]);

  /* 合成矢量：箭头 = (横向, 纵向) 的合成 */
  ctx.strokeStyle = '#DC2626'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();
  if (d > 3){
    var ux = dx / d, uy = dy / d, aLen = 7, aW = 3.4;
    ctx.fillStyle = '#DC2626';
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(px - ux * aLen - uy * aW, py - uy * aLen + ux * aW);
    ctx.lineTo(px - ux * aLen + uy * aW, py - uy * aLen - ux * aW);
    ctx.closePath(); ctx.fill();
  }
  /* 命中点（用横向那路的颜色，便于和读数行对应） */
  ctx.fillStyle = '#B45309';
  ctx.beginPath(); ctx.arc(px, py, 3.2, 0, Math.PI * 2); ctx.fill();

  /* ★ 合成幅值的"画布内标注"已移除（2026-10-16 排版改版）：
     面板右侧现在是**三列 KPI**（横向 / 纵向 / 合成），合成幅值已经有一个
     大号数字在那儿；原来再在画布上写一遍，且只能贴在数据点旁 ——
     点在圆心附近时那串字正好压在十字准星与 "LON+/LAT+" 轴标签上（实测遮挡）。
     数值只在一处出现、且位置固定，就不再有两处读数互相打脸的可能。 */

  /* 超满量程是**状态**而不是数值，保留在右下角（左下角是满量程刻度、顶部是 LON+） */
  if (mag > fs){
    ctx.font = '11px Consolas, monospace';
    ctx.fillStyle = '#9A4A06';
    ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
    ctx.fillText('超满量程', w - 1, h - 1);
  }
}
/* 离散入口统一走这里：标脏 + 立刻画一次 */
function refreshAccVec(){ accVecDirty = true; paintAccVec(); }

function removeChart(id){
  for (var i = 0; i < vcharts.length; i++){
    if (vcharts[i].id === id){ vcharts[i].card.remove(); vcharts.splice(i, 1); return; }
  }
}

function addChart(){
  var grid = $('visGrid');
  if (!grid) return;
  vchartSeq++;
  var id = 'vc' + vchartSeq;
  var card = document.createElement('div');
  card.className = 'vchart';
  card.innerHTML =
    '<div class="vc-head"><span class="vc-title">图表 ' + vchartSeq + '</span>'
    + '<button class="vc-del" type="button" title="删除">×</button></div>'
    + '<div class="vc-canvas-wrap"><canvas class="vc-canvas"></canvas>'
    + '<div class="vc-empty">拖入数据通道</div></div>'
    + '<div class="vc-sigs"></div>';
  grid.appendChild(card);
  var chart = {id: id, sigs: [], card: card, canvas: card.querySelector('.vc-canvas'), hover: null};
  chart.layer = createLayer(chart.canvas);
  vcharts.push(chart);
  /* hover：光标移动到图上显示该点各曲线的具体数值。
     ★ 问题五（暂停时悬停没有数值标注）：主循环里的 `if (!state.frozen && trendDirty …)`
     只在**未暂停**时才执行 vcharts.forEach(drawVChart)，所以暂停时这里设的
     chart.hover + layer.dirty 永远不会被消费 —— 标注自然不出现。
     修法：交互自己触发一次重绘（只重画被悬停的这一张），不改主循环的 frozen 门禁
     （那个门禁是性能优化：暂停时不该 30Hz 空转重画；而 pushSample 在暂停时也不推点，
      所以"缓冲被实时数据刷新"不会发生）。
     注意 trendDirty 为 false（无数据流入）时主循环同样不画，所以这里**不能**
     只在 frozen 时补画，必须无条件补画。实测单张图表重绘 ~0.1ms（21 路满数据），
     而 mousemove 只在指针位于图上时触发。 */
  var wrap = card.querySelector('.vc-canvas-wrap');
  wrap.addEventListener('mousemove', function(e){
    var r = wrap.getBoundingClientRect();
    var frac = (e.clientX - r.left - 48) / Math.max(1, r.width - 48 - 12);
    frac = Math.max(0, Math.min(1, frac));
    chart.hover = frac;
    chart.layer.dirty = true;
    paintVCharts(chart);
  });
  wrap.addEventListener('mouseleave', function(){
    chart.hover = null;
    chart.layer.dirty = true;
    paintVCharts(chart);
  });
  card.querySelector('.vc-del').addEventListener('click', function(){ removeChart(id); });
  card.addEventListener('dragover', function(e){
    e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; card.classList.add('dragover');
  });
  card.addEventListener('dragleave', function(){ card.classList.remove('dragover'); });
  card.addEventListener('drop', function(e){
    e.preventDefault(); card.classList.remove('dragover');
    var sig = e.dataTransfer.getData('text/plain');
    if (sig && SIGS[sig] && chart.sigs.indexOf(sig) < 0){
      chart.sigs.push(sig);
      updateVChartSigs(chart);
      chart.layer.sKey = '';
      chart.layer.dirty = true;
      paintVCharts(chart);      /* 暂停/无数据流动时主循环不画 -> 交互自己补画 */
    }
  });
  card.querySelector('.vc-sigs').addEventListener('click', function(e){
    var chip = e.target.closest('.vc-sig');
    if (!chip) return;
    var sig = chip.getAttribute('data-sig');
    var idx = chart.sigs.indexOf(sig);
    if (idx >= 0){
      chart.sigs.splice(idx, 1); updateVChartSigs(chart);
      chart.layer.sKey = ''; chart.layer.dirty = true;
      paintVCharts(chart);      /* 同上 */
    }
  });
}

/* nice 步长：把 range 拆成 target 段，步长从 {1,2,5}×10ⁿ 里选（专业绘图工具的刻度算法） */
function niceStep(range, target){
  if (!(range > 0)) return 1;
  var raw = range / target;
  var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
  var norm = raw / mag;
  var step;
  if (norm < 1.5) step = 1;
  else if (norm < 3) step = 2;
  else if (norm < 7) step = 5;
  else step = 10;
  return step * mag;
}

function drawVChart(chart){
  var L = chart.layer;
  if (!syncLayer(L)) return;
  var ctx = L.cv.getContext('2d');
  ctx.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  ctx.clearRect(0, 0, L.w, L.h);
  var pad = {top: 10, right: 12, bottom: 24, left: 48};
  var plotW = L.w - pad.left - pad.right, plotH = L.h - pad.top - pad.bottom;
  var ids = chart.sigs.filter(function(id){
    return SIGS[id] && state.buf[id] && state.buf[id].length >= 2;
  });
  if (!ids.length) return;
  /* 自适应量程 */
  var r = autoRange(ids);
  var mv = (r.hi - r.lo) * 0.1 || 1;
  var yMin = r.lo - mv, yMax = r.hi + mv;
  if (yMax - yMin < 1e-9){ yMin -= 0.5; yMax += 0.5; }
  /* 坐标轴框 */
  ctx.strokeStyle = 'rgba(15,23,42,.22)';
  ctx.lineWidth = 1;
  ctx.strokeRect(pad.left + .5, pad.top + .5, plotW - 1, plotH - 1);
  /* 网格 + 刻度：nice 步长（自动选 1/2/5×10ⁿ），像专业绘图工具 */
  var yStep = niceStep(yMax - yMin, 6);
  var y0 = Math.ceil(yMin / yStep) * yStep;
  var y1 = Math.floor(yMax / yStep) * yStep;
  ctx.font = '11px Consolas, monospace';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (var v = y0; v <= y1 + yStep * 0.5; v += yStep){
    var frac = (v - yMin) / (yMax - yMin);
    var gy = pad.top + plotH - frac * plotH;
    ctx.strokeStyle = 'rgba(15,23,42,.07)';
    ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(pad.left + plotW, gy); ctx.stroke();
    ctx.fillStyle = '#6B7686';
    ctx.fillText(fmtTick(v), pad.left - 6, gy);
  }
  /* X 轴时间刻度（nice 步长） */
  var winSec = BUF * SAMPLE_MS / 1000;
  var xStep = niceStep(winSec, 8);
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  for (var t = 0; t <= winSec + 1e-9; t += xStep){
    var frac = t / winSec;
    var gx = pad.left + plotW * (1 - frac);
    ctx.strokeStyle = 'rgba(15,23,42,.07)';
    ctx.beginPath(); ctx.moveTo(gx, pad.top); ctx.lineTo(gx, pad.top + plotH); ctx.stroke();
    ctx.fillStyle = '#6B7686';
    var lbl = t < 1e-9 ? '0s' : '-' + Math.round(t) + 's';
    ctx.fillText(lbl, gx, pad.top + plotH + 6);
  }
  /* 曲线 */
  ctx.lineJoin = 'bevel';
  ctx.lineWidth = 1.6;
  var curvePts = {};   /* id -> {x, y}[] 保存像素坐标，供 hover 用 */
  ids.forEach(function(id){
    var b = state.buf[id];
    var s = SIGS[id];
    var len = b.length;
    var step = Math.max(1, Math.ceil(len / (plotW * 2)));
    ctx.strokeStyle = s.color;
    ctx.beginPath();
    var first = true;
    var pts = [];
    for (var i = 0; i < len; i += step){
      var x = pad.left + (i / (len - 1)) * plotW;
      var f = (b[i] - yMin) / (yMax - yMin);
      if (f < 0) f = 0; else if (f > 1) f = 1;
      var y = pad.top + plotH - f * plotH;
      if (first){ ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      pts.push({i: i, x: x, y: y});
    }
    ctx.stroke();
    curvePts[id] = pts;
  });
  /* 左上角图例：颜色代表哪条曲线 */
  /* 图例字号 11px→12px：与新版排版里"12px = 卡片标签/控件"这一档对齐，
     曲线图例是"哪条线是什么"的唯一入口，太小就失去意义。 */
  ctx.font = '12px "Segoe UI","Microsoft YaHei",sans-serif';
  var maxW = 0;
  ids.forEach(function(id){ var w = ctx.measureText(SIGS[id].label).width; if (w > maxW) maxW = w; });
  var lx = pad.left + 6, ly = pad.top + 6, lh = 16;
  ctx.fillStyle = 'rgba(255,255,255,.85)';
  ctx.fillRect(lx - 4, ly - 3, maxW + 34, ids.length * lh + 5);
  ids.forEach(function(id, i){
    var s = SIGS[id];
    var yy = ly + i * lh;
    ctx.fillStyle = s.color;
    ctx.fillRect(lx, yy + 2, 10, 10);
    ctx.fillStyle = '#1B2434';
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(s.label, lx + 15, yy);
  });
  /* hover：竖线 + 每条曲线该点的具体数值 */
  if (chart.hover != null && chart.hover !== undefined){
    var hx = pad.left + chart.hover * plotW;
    ctx.strokeStyle = 'rgba(15,23,42,.32)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 2]);
    ctx.beginPath(); ctx.moveTo(hx, pad.top); ctx.lineTo(hx, pad.top + plotH); ctx.stroke();
    ctx.setLineDash([]);
    var hly = pad.top + 4;
    ids.forEach(function(id){
      var b = state.buf[id];
      var s = SIGS[id];
      var pts = curvePts[id];
      /* 找离竖线最近的点 */
      var best = null, bd = 1e9;
      for (var q = 0; q < pts.length; q++){
        var d = Math.abs(pts[q].x - hx);
        if (d < bd){ bd = d; best = pts[q]; }
      }
      if (!best) return;
      ctx.fillStyle = s.color;
      ctx.fillRect(hx + 6, hly, 7, 7);
      ctx.fillStyle = '#1B2434';
      ctx.textAlign = 'left'; ctx.textBaseline = 'top';
      ctx.fillText(fmtSigVal(b[best.i], s) + ' ' + (s.unit || ''), hx + 17, hly - 1);
      hly += 14;
    });
  }
}

/* ---------- 8. 仪表 DOM 更新（脏值比对） ---------- */
var SPD_ARC_LEN = 251.33;   /* 半圆表盘弧长（r=80） */
var SPD_FS = 40;            /* 车速表盘满量程，由配置下发覆盖（GAUGE_RANGES.SPEED） */
var SPD_SCALE = 1.0;        /* SPEED 字段的 scale，只用来显示副标题里的系数角标 */
var SPD_UNIT = 'm/s';
/* ★ task-27：原 SVG 车速表盘的 updateSpeedDial() 已删除 —— 车速/转速改为
   canvas 环形表（drawRingGauge），指针用 canvas 画，天然绕开
   "SVG translate()+rotate() 被 overflow:hidden 裁掉"那个坑（见 tools/_ux_needle_probe.py）。
   环形表的重绘入口是 paintGauges()，由主循环的迷你节拍按脏标记驱动。 */
/* 车速表盘的量程也跟配置走：上面 0–40 只是「还没收到后端配置」时的占位。
   收到 GAUGE_RANGES.SPEED 后，这里会把表盘比例和副标题的量程文案一起刷掉。 */
/* 其余仪表的默认量程写在上面 defSig 里；如果第 5 区 GAUGE_RANGES 给它们配了值，
   就以配置为准（同样是为了「改了系数界面不跟着变」不再复发）。
   加速度和车速上面有专门的同步函数，因为它们还要刷 DOM 文案，所以不在这里。 */
/* ★ 完整的「配置信号名 -> 前端信号 id」映射（含 NAV 三个姿态量）：
      以前只有下面这 6 个动力量，ROLL/PITCH/YAW/ACCEL/SPEED 的单位压根没通道传过来，
      于是「配置里改了单位，界面还是 ° 」—— 单位失效的根因就在这。 */
var FIELD_SIG = {
  ROLL:'roll', PITCH:'pitch', YAW:'yaw',
  /* ★ 协议把单路 ACCEL 拆成 ACCEL_LAT / ACCEL_LON（NAV 20B→22B）：
     单位(unit)/系数(scale)/小数位(dec) 从配置这两个名字同步过来。
     合成量 accelMag 没有对应的配置字段（前端派生），它的 unit/fmt 在 applyUiConfig 里
     跟随这两路（见那里的"派生量跟随"）。 */
  ACCEL_LAT:'accelLat', ACCEL_LON:'accelLon',
  MOTOR_TEMP: 'temp', MOTOR_RPM: 'rpm', TORQUE: 'torque',
  THROTTLE: 'throttle', VOLTAGE: 'volt', CURRENT: 'amp', SPEED: 'speed'
};
var GAUGE_SIGS = {
  TORQUE: 'torque', MOTOR_TEMP: 'temp', MOTOR_RPM: 'rpm',
  THROTTLE: 'throttle', VOLTAGE: 'volt', CURRENT: 'amp',
  SPEED: 'speed', ROLL: 'roll', PITCH: 'pitch', YAW: 'yaw'
};
function syncSpeedLabels(){
  if (SIGS['speed']){ SIGS['speed'].min = 0; SIGS['speed'].max = SPD_FS; }
  /* ★ 表盘中央的单位后缀也走配置（以前这个 m/s 写死在 index.html） */
  var ue = $('speedUnit');
  if (ue){
    var ut = SPD_UNIT || '';
    if (ue._t !== ut){ ue.textContent = ut; ue._t = ut; }
  }
  var sub = $('speedSub');
  /* ★ task-27：车速改成 canvas 环形表后，刻度线由 drawRingGauge 按 SIGS.speed.max 现算
     （不再需要 SVG 的 spdTick 文案节点）；这里只保留"量程数字"这一处显式文案。 */
  setNum($('speedFs'), fmtFs(SPD_FS));
  if (!sub) return;
  var s = SPD_SCALE === 1 ? '原始码直显'
        : '原始码 ×' + String(Number(Number(SPD_SCALE).toPrecision(3)));
  sub.textContent = '0 – ' + fmtFs(SPD_FS) + ' ' + SPD_UNIT + ' · ' + s;
}
syncSpeedLabels();
/* 温度分区阈值：按【当前量程】的比例算，不写死 70 / 90 ℃。
   量程改成 90 ℃ 或 200 ℃ 时，"偏高 / 过热"仍然落在同一位置。
   （120 ℃ 量程 → 0.583×120=70、0.75×120=90，与原来的手感一致） */
function tempZone(t){
  var mx = (SIGS.temp && SIGS.temp.max) ? SIGS.temp.max : 120;
  return t < mx * 0.583 ? 'ok' : t < mx * 0.75 ? 'warn' : 'danger';
}

/* ---------- 8.2 所有仪表的「刻度 + 单位后缀」统一按配置渲染 ----------
   ★ 以前这些文案全是写死在 index.html 里的（0/300/600 V、0/70/90/120、10k…），
     结果就是：配置里改了量程，条形比例变了、界面上的刻度数字却纹丝不动 ——
     也就是「改了 config 界面没变化」的真凶。
   现在统一由 SIGS[id].min/max/unit 渲染，配置一改，刻度与单位跟着改。 */
var GAUGE_UI = [
  {sig:'speed',    scale:null,        unit:'speedUnit', special:'speed'},
  {sig:'rpm',      scale:'rpmScale',  unit:'rpmUnit'},
  {sig:'throttle', scale:'thrScale',  unit:'thrUnit'},
  {sig:'torque',   scale:'tqScale',   unit:'tqUnit'},
  {sig:'temp',     scale:'tempScale', unit:'tempUnit'},
  {sig:'volt',     scale:'voltScale', unit:'voltUnit'},
  {sig:'amp',      scale:'ampScale',  unit:'ampUnit'},
  {sig:'roll',     scale:null,        unit:'rollUnit'},
  {sig:'pitch',    scale:null,        unit:'pitchUnit'},
  {sig:'yaw',      scale:null,        unit:'yawUnit'}
];

/* 刻度数字：10000 -> 10k，3.8k 保留一位，小数按需 */
function fmtTick(v){
  var a = Math.abs(v);
  if (a >= 1000){
    var k = v / 1000;
    return (Math.abs(k - Math.round(k)) < 0.05 ? String(Math.round(k)) : k.toFixed(1)) + 'k';
  }
  if (a >= 10 || a === 0) return String(Math.round(v));
  return String(Number(v.toFixed(1)));
}
/* 按 min..max 均分重写一根 bar-scale 的所有 <span>，末刻度带单位 */
function syncBarScale(elId, mn, mx, unit){
  var el = $(elId);
  if (!el) return;
  var spans = el.querySelectorAll('span');
  var n = spans.length;
  if (n < 2) return;
  for (var i = 0; i < n; i++){
    var v = mn + (mx - mn) * i / (n - 1);
    var txt = fmtTick(v) + ((i === n - 1 && unit) ? ' ' + unit : '');
    if (spans[i]._t !== txt){ spans[i].textContent = txt; spans[i]._t = txt; }
  }
}
function syncGaugeLabels(){
  for (var i = 0; i < GAUGE_UI.length; i++){
    var g = GAUGE_UI[i], s = SIGS[g.sig];
    if (!s) continue;
    if (g.special === 'speed'){ syncSpeedLabels(); continue; }
    if (g.scale) syncBarScale(g.scale, s.min || 0, s.max || 0, s.unit);
    var ue = g.unit ? $(g.unit) : null;
    if (!ue) continue;
    /* 保留原生 HTML 的前导空格习惯（h-unit 用 CSS margin，其余靠空格撑开） */
    if (ue._lead === undefined) ue._lead = /^\s/.test(ue.textContent) ? ' ' : '';
    var t = s.unit ? ue._lead + s.unit : '';
    if (ue._t !== t){ ue.textContent = t; ue._t = t; }
  }
}

/* ---------- 8.3 （原「量程 / 单位在线编辑器」已于 2026-10-06 整块移除） ----------
   按用户要求删除。免打包改配置的入口现在只剩 exe 旁的 telemetry_user.json：
   改完重启生效 —— 后端 load_user_cfg() 仍然按
       telemetry_user.json  >  telemetry_config.py  >  app.js 兜底值
   的优先级读取。界面上不再提供编辑 / 保存 / 恢复内置 的按钮。 */
/* （原 CFG_USER / RANGE_ROWS 状态随编辑器移除；toast 直接用 cfg.userCfg 判断） */


var SPARK_FLAT = 'M0,20 L0,10 L100,10 L100,20 Z';   /* 无数据时的平线占位 path */
var lastSparkT = 0;   /* sparkline 降频：≥250ms 才重建一次 path（≈4Hz） */

/* ---------- 8.5 可见区节流 ----------
   IntersectionObserver 给每个分区打可见标记。滚出视口的分区不再做 20Hz 的
   DOM 刷新 —— 原来不管你看没看见，366 个节点每 50ms 全量刷一遍，滚动时这笔
   开销会和滚动重绘叠加放大。rootMargin 提前 120px 预热，滚进来就是最新值。 */
var zoneVis = {};      /* 未观测到时默认是「可见」，保证老浏览器/初始化安全 */
var ZONE_IDS = ['zoneP2', 'zoneP1', 'zoneP0', 'zoneTrend', 'zoneVisual',
                'zoneSerial', 'zoneRecord', 'zoneSettings'];
function zoneVisible(id){ return zoneVis[id] !== false; }
function initZoneVisibility(){
  var stage = $('stage');
  if (!stage || !('IntersectionObserver' in window)) return;
  var io = new IntersectionObserver(function(entries){
    for (var i = 0; i < entries.length; i++)
      zoneVis[entries[i].target.id] = entries[i].isIntersecting;
  }, {root: stage, rootMargin: '120px 0px'});
  for (var i = 0; i < ZONE_IDS.length; i++){
    var el = $(ZONE_IDS[i]);
    if (el) io.observe(el);
  }
  /* 滚动中给 body 打标记：CSS 里暂停脉冲动画与条形过渡（滚动本身就在刷屏，
     别再抢主线程）；JS 侧同时按 scrollUntil 降频（见主循环）。
     ★ 只在这里记时间戳，不在回调里做别的事 —— scroll 事件触发极频繁。 */
  var t = 0;
  stage.addEventListener('scroll', function(){
    document.body.classList.add('scrolling');
    scrollUntil = performance.now() + (UI_CFG.scrollQuietMs || 200);
    if (t) clearTimeout(t);
    t = setTimeout(function(){ document.body.classList.remove('scrolling'); }, 160);
  }, {passive:true});
}

function initZoneResize(){
  /* 每个功能区加底部拖拽手柄，上下拖动调整该区高度 */
  Array.prototype.forEach.call(document.querySelectorAll('.zone'), function(zone){
    if (zone.querySelector('.zone-resize')) return;
    var h = document.createElement('div');
    h.className = 'zone-resize';
    h.title = '拖动调整高度';
    zone.appendChild(h);
    h.addEventListener('mousedown', function(e){
      e.preventDefault();
      var sy = e.clientY, sh = zone.getBoundingClientRect().height;
      var raf = null;
      function mv(ev){
        var nh = Math.max(140, sh + ev.clientY - sy);
        /* rAF 节流：mousemove 频率远高于帧率，合并到下一帧再改高度，避免拖动卡顿 */
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function(){
          zone.style.height = nh + 'px';
          trendLayer.dirty = true; trendLayer.sKey = '';
          vcharts.forEach(function(c){ c.layer.dirty = true; c.layer.sKey = ''; });
          /* ★★ resize 闪屏修复（2026-10-15，实测定位后加）：
             高度一变，canvas 的 CSS 盒子跟着变（trendCanvas 是 width/height:100%），
             但**位图**只在主循环重绘时才重建 —— 中间那些帧浏览器把旧位图缩放贴上去，
             用户看到的就是"调区域大小时闪/发虚"。实测拖动 320ms：
               canvas 尺寸写入 16 次、drawTrend 只跑了 5 次、5/10 采样帧位图是陈旧的；
               而拖 zoneP2/P1/Serial/Visual 时尺寸写入=0、陈旧帧=0（其它区不闪）。
             修法：在**同一个 rAF** 里把陈旧的层补画一次（只画位图确实落后的层）。
             · 画完顺手推进主循环的节流与 FPS 计数，避免同一帧画两遍 / FPS 掉到 0；
             · 不动主循环门禁、不加 transition/will-change（B08/B10）、不改 GPU 参数；
             · 暂停(frozen)与无数据时同样补画 —— 否则那两种情况下陈旧位图会一直留着。 */
          if (layerStale(trendLayer)){
            trendLayer.sKey = '';
            paintLayer(trendLayer, drawTrend);
            drawCursorLayer();
            lastDraw = performance.now();   /* 与主循环的 1000/trendFps 节流对齐 */
            state._frames++;
            trendDirty = false;             /* 刚画的就是最新数据 */
          } else if (layerStale(cursorLayer)){
            drawCursorLayer();
          }
          if (zoneVisible('zoneVisual'))
            vcharts.forEach(function(c){
              if (layerStale(c.layer)){ c.layer.sKey = ''; drawVChart(c); }
            });
          if (accVecDirty) paintAccVec();
        });
      }
      function up(){
        if (raf) cancelAnimationFrame(raf);
        document.removeEventListener('mousemove', mv);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', mv);
      document.addEventListener('mouseup', up);
    });
  });
}

function updateDom(){
  /* ★ 只刷新看得见的分区 */
  var L = state.latest, HD = state.hasData;
  if (zoneVisible('zoneP2')) updateMotorDom(L, HD.motor);
  if (zoneVisible('zoneP1')) updateNavDom(L, HD.nav);
  if (zoneVisible('zoneP0')) updateAdcDom(L, HD.adc);
  if (zoneVisible('zoneTrend')) updateSigPanel();   /* 右侧当前值面板 20Hz 跟随 */
  /* ★ 加速度矢量图：走**脏标记 + 20Hz 数据节拍**（不是 rAF 每帧）。
     updateDom 本身最多 20Hz、且只在 !state.frozen 时被主循环调用；
     矢量图在 #zoneP1（总览默认可见），所以这里不加分区名判断 ——
     paintAccVec 内部按"画布当前尺寸"自己判可见性，隐藏时保留脏标记直接返回。
     暂停时由离散入口负责刷（切区/回填/悬停画布）—— 见 refreshAccVec()。 */
  if (accVecDirty) paintAccVec();
}

/* ============ P2 动力（MOTOR 包未收到 -> 全部 "—" + 仪表归零） ============ */
function updateMotorDom(L, hm){
  /* ★ task-27：车速/转速改成 canvas 环形表 —— 数值不再由 updateSpeedDial 驱动，
     而是"DOM 写数值 + 标脏 → 主循环按 ≤10fps 重画环形"（与 accVec 同一套脏标记思路）。 */
  setNum($('speedNum'), hm ? fmtNum('speed', L.speed || 0) : '—');
  setScale($('speedBar'), hm ? (L.speed || 0) / (SIGS.speed.max || 40) : 0);
  setCellState($('speedState'), relState('speed', L.speed || 0), !hm);
  var rpm = hm ? Number(L.rpm || 0) : 0;
  /* 所有条形的分母都取「当前生效的量程」，不再写死 10000 —— 否则改了
     GAUGE_RANGES.MOTOR_RPM 只有刻度变、条还是按旧量程画。 */
  var rpmMax = SIGS.rpm.max || 10000;
  setNum($('rpmNum'), hm ? fmtNumSep('rpm', rpm) : '—');
  setScale($('rpmBar'), rpm / rpmMax);
  setCellState($('rpmState'), relState('rpm', rpm), !hm);
  /* 环形表：数值一帧一变 → 标脏（真正重画在主循环 ≤10fps 的迷你节拍里） */
  gaugeDirty = true; miniDirty = true; heatDirty = true;

  /* 换挡灯：只在"点亮段数"变化时改 className（原来 12 段 × 20Hz 无条件重写）
     阈值按转速量程的百分比算（0.09 / 0.93 / 0.98），改量程后自动跟着缩放。 */
  var lit = Math.round(Math.max(0, Math.min(1,
    (rpm - rpmMax * 0.09) / (rpmMax * 0.98 - rpmMax * 0.09))) * 12);
  var revRow = $('revRow');
  var blink = rpm > rpmMax * 0.93;
  if (revRow._lit !== lit || revRow._blink !== blink){
    revRow._lit = lit; revRow._blink = blink;
    var segs = revRow.children;
    for (var i = 0; i < segs.length; i++){
      var on = i < lit;
      var want = 'rev-seg ' + (i < 7 ? 'c1' : i < 10 ? 'c2' : 'c3') +
                 (on ? ' on' : '') + (on && i >= 10 && blink ? ' blink' : '');
      if (segs[i]._c !== want){ segs[i].className = want; segs[i]._c = want; }
    }
  }

  setNum($('thrNum'), hm ? fmtNum('throttle', L.throttle || 0) : '—');
  setScale($('thrBar'), hm ? (L.throttle || 0) / (SIGS.throttle.max || 100) : 0);
  setCellState($('thrState'), hm && (L.throttle || 0) >= (SIGS.throttle.max || 100) * 0.95 ? 'warn' : 'ok', !hm);
  setNum($('tqNum'), hm ? fmtNum('torque', L.torque || 0) : '—');
  setScale($('tqBar'), hm ? (L.torque || 0) / (SIGS.torque.max || 150) : 0);
  setCellState($('tqState'), relState('torque', L.torque || 0), !hm);
  /* 扭矩峰值刻线：从既有缓冲取 max（"这一脚最大来了多少"） */
  var tqPeak = state._tqPeak || 0, tb2 = state.buf.torque;
  if (tb2 && tb2.length && performance.now() - (state._tqPeakT || 0) > 250){
    tqPeak = 0;
    for (var qi = 0; qi < tb2.length; qi++) if (tb2[qi] > tqPeak) tqPeak = tb2[qi];
    state._tqPeak = tqPeak; state._tqPeakT = performance.now();   /* 4Hz 节流：20Hz 下没必要每 tick 扫 600 点 */
  }
  var tpk = $('tqPeakTick');
  if (tpk){
    var tpq = SIGS.torque.max || 150;
    var want = tqPeak > 0 ? 'left:' + Math.min(100, tqPeak / tpq * 100).toFixed(1) + '%;display:block' : 'display:none';
    if (tpk._s !== want){ tpk.setAttribute('style', want); tpk._s = want; }
  }
  setNum($('tempNum'), hm ? fmtNum('temp', L.temp || 0) : '—');
  setScale($('tempBar'), hm ? (L.temp || 0) / (SIGS.temp.max || 120) : 0);
  var tz = hm ? tempZone(L.temp || 0) : 'ok';
  var tb = $('tempBar');
  var tc = 'bar-fill ' + (tz === 'ok' ? 'fill-ok' : tz === 'warn' ? 'fill-warn' : 'fill-danger');
  if (tb._c !== tc){ tb.className = tc; tb._c = tc; }
  var ts2 = $('tempState');
  var tTxt = !hm ? '—' : tz === 'ok' ? '正常' : tz === 'warn' ? '偏高' : '过热';
  var tCls = !hm ? 'st' : tz === 'ok' ? 'st' : tz === 'warn' ? 'st is-warn' : 'st is-over';
  if (ts2._t !== tTxt){ ts2.textContent = tTxt; ts2._t = tTxt; }
  if (ts2._c !== tCls){ ts2.className = tCls; ts2._c = tCls; }
  /* 过热阈值刻线：按当前量程的比例定位（与 tempZone 的 0.75 同源） */
  var tht = $('tempHotTick');
  if (tht){
    var want2 = 'left:' + ((SIGS.temp.max || 120) * 0.75 / (SIGS.temp.max || 120) * 100).toFixed(2) + '%';
    if (tht._s !== want2){ tht.setAttribute('style', want2); tht._s = want2; }
  }
  setNum($('voltNum'), hm ? fmtNum('volt', L.volt || 0) : '—');
  setScale($('voltBar'), hm ? (L.volt || 0) / (SIGS.volt.max || 600) : 0);
  /* 电压没有"越高越坏"的语义：只在超安全区时标黄（380–520 V，与格子里的刻线同源） */
  var vmax2 = SIGS.volt.max || 600;
  var vLo = tickValue('voltLoTick', vmax2), vHi = vmax2 * (520 / 600);
  setCellState($('voltState'), hm && ((L.volt || 0) < vLo || (L.volt || 0) > vHi) ? 'warn' : 'ok', !hm);
  setNum($('ampNum'), hm ? fmtNum('amp', L.amp || 0) : '—');
  setScale($('ampBar'), hm ? (L.amp || 0) / (SIGS.amp.max || 200) : 0);
  setCellState($('ampState'), relState('amp', L.amp || 0), !hm);
  /* 电池功率卡片（派生量 + 阈值告警）：与其它读数同一个 20Hz 节拍，
     判定/切类名都在这里，不额外起定时器、不画 canvas。 */
  updatePowerDom(L, hm);
}

/* ============ P1 姿态（NAV 包未收到 -> 全部 "—" + 仪表回中） ============ */
function updateNavDom(L, hn){
  /* ★ 姿态仪表的「可视角度」也按注册量程算，不写死 ±45 / ±20 / 360：
       量程一改（比如横滚只显示 ±90），指针/条形还按旧角度画就会撞边不动。 */
  var rv = Math.max(1, (SIGS.roll && SIGS.roll.max ? SIGS.roll.max : 180) * 0.25);
  var pv = Math.max(1, (SIGS.pitch && SIGS.pitch.max ? SIGS.pitch.max : 90) * 0.222);
  var yv = Math.max(1, (SIGS.yaw && SIGS.yaw.max ? SIGS.yaw.max : 360));
  setNum($('rollNum'), hn ? fmtNum('roll', L.roll || 0) : '—');
  var rn = $('rollNeedle');
  var rl = hn ? Math.max(-rv, Math.min(rv, L.roll || 0)) : 0;
  /* ★★ 中心式 rotate(角度 cx cy)：与车速表盘同一个坑 —— 原来写的是
     `translate(60 58) rotate(角度)`，实测该组合会把指针画到 SVG 盒子外面被
     overflow:hidden 裁掉（QtWebEngine 实测：rect y=458 而 SVG 盒子只有 y=349..413）
     → **横滚指针从头到尾都是不可见的**，界面上只剩一条虚线零位线 + 圆心点。
     中心改成 (60,56)：与 index.html 里 `<g id="rollNeedle" transform="rotate(0 60 56)">`
     的初始值、指针底端 (60,56)、圆心 hub (cy=56) 三处一致（原来是 60,58，差 2px）。 */
  var rr2 = 'rotate(' + rl.toFixed(1) + ' 60 56)';
  if (rn._t !== rr2){ rn.setAttribute('transform', rr2); rn._t = rr2; }
  setNum($('pitchNum'), hn ? fmtNum('pitch', L.pitch || 0) : '—');
  var ph = hn ? Math.max(-pv, Math.min(pv, L.pitch || 0)) : 0;
  setScaleY($('pitchUp'), Math.max(0, ph) / pv);
  setScaleY($('pitchDn'), Math.max(0, -ph) / pv);
  setNum($('yawNum'), hn ? fmtNum('yaw', L.yaw || 0) : '—');
  var yp = $('yawPtr');
  /* ★ 写 transform 而不是 left（left 是布局属性，20Hz 更新会每秒强制重排）。
     指针容器宽 100%，所以 translateX(百分比) == 原来的 left:百分比。 */
  var yl = 'translateX(' + (hn ? (L.yaw || 0) / yv * 100 : 0).toFixed(2) + '%)';
  if (yp._tf !== yl){ yp.style.transform = yl; yp._tf = yl; }
  /* 位数跟随配置（SIGS.accelMag.fmt），不跟随 scale —— 见 fmtNum 说明。
     ★ 协议拆分后这一格显示的是**合成总加速度**（accelMag），与可视化区的矢量图同源，
       两者读数必然一致（同一个 state.latest）。 */
  setNum($('accNum'), hn ? fmtNum('accelMag', L.accelMag || 0) : '—');
  var av = hn ? Math.max(-ACC_FS, Math.min(ACC_FS, L.accelMag || 0)) : 0;
  setScale($('accPos'), Math.max(0, av) / ACC_FS);     /* 满量程正好占满半侧 */
  setScale($('accNeg'), Math.max(0, -av) / ACC_FS);
}

/* ============================================================================
 * ★★ task-27 落地：环形表（车速/转速）+ 历史热条 + 双轴 + 散点
 * ----------------------------------------------------------------------------
 * 用户裁定：总体用 B「数据表」，**车速与转速改用 A 的环形表**，亮色。
 *
 * 三条设计/工程约束（照 task 要求）：
 *  1) **环形表用 canvas 画**（图形：弧/刻度/指针），中心数值仍是 DOM
 *     （#speedNum / #rpmNum）—— 20Hz 只写 textContent，探针按 id 取节点也不受影响。
 *     ★ 用 canvas 而不是 SVG，正是为了绕开上一轮查明的坑：SVG 里
 *       `translate(a b) rotate(deg)` 会把指针画到盒子外，被 overflow:hidden 裁掉
 *       （证据 tools/_ux_needle_probe.py）。canvas 没有坐标系映射问题。
 *  2) **刻度/量程全部读配置**（SIGS[id].min / SIGS[id].max，与 syncSpeedLabels 同一套约定）；
 *     限扭/报警刻线的位置直接由 index.html 里现有 `.bar-tick` 的 left 百分比换算 ——
 *     **单一来源**，不在 JS 里再写一份 3800/180 之类的常量。
 *  3) **性能**：复用既有 createLayer/syncLayer/paintLayer + 脏标记 + 主循环节流，
 *     不另起并行重绘架构。热条走 UI_CFG.sparkMs（250ms）节流且内容不变则跳过写 DOM；
 *     环形/双轴/散点走主循环里新增的一个"迷你图层"节拍（≤10fps，滚动时跳过）。
 * ========================================================================== */

var PAL = null;                  /* 画布用的主题色（从 CSS 变量读，主题/配置变化时刷新） */
function refreshPalette(){
  var cs = getComputedStyle(document.documentElement);
  var g = function(n, fb){ var v = cs.getPropertyValue(n); return (v && v.trim()) || fb; };
  PAL = {
    ink:   g('--text', '#141C29'),
    dim:   g('--dim', '#404C60'),
    faint: g('--faint', '#647089'),
    line:  g('--line', '#DFE4EC'),
    track: g('--track', '#E3E6EB'),
    panel: g('--panel', '#FFFFFF'),
    ok:    g('--teal', '#0E8A62'),
    warn:  g('--amber', '#B4783F'),
    over:  g('--red', '#C8393B'),
    cyan:  g('--cyan', '#0B6EA8'),
    blue:  g('--blue', '#3B5FD0'),
    violet:g('--violet', '#7C5CC4'),
    lime:  g('--lime', '#5E8F2A')
  };
}

/* 环形/迷你图图层：与 accVec 同一套"可见性门控 + 脏标记 + 离散补画"
   ★ 必须在建图层与首次绘制**之前**建好画布用色 PAL
   （曾漏调 -> PAL=null -> 环形表/双轴/散点/热条全不画，而数字照常更新，很难一眼看出）。 */
refreshPalette();
var gaugeSpeed = createLayer($('speedGauge'));
var gaugeRpm = createLayer($('rpmGauge'));
var dualLayer = createLayer($('dualCanvas'));
var scatterLayer = createLayer($('scatterCanvas'));
var gaugeDirty = true, miniDirty = true, heatDirty = true;

/* 阈值刻线（百分比）→ 数值：直接读现有 .bar-tick 的内联 left，避免第二份常量 */
function tickValue(tickId, fs){
  var el = $(tickId);
  if (!el || !el.style || !el.style.left) return 0;
  var pct = parseFloat(el.style.left);
  return isFinite(pct) ? fs * pct / 100 : 0;
}

/* 状态徽标（正常/偏高/超限）：B 设计里每个格子右上角那一小块 */
function setCellState(el, st, noData){
  if (!el) return;
  var t = noData ? '—' : st === 'over' ? '超限' : st === 'warn' ? '偏高' : '正常';
  var c = 'st' + (noData ? '' : st === 'over' ? ' is-over' : st === 'warn' ? ' is-warn' : '');
  if (el._t !== t) el.textContent = t;
  if (el._c !== c) el.className = c;
  el._t = t; el._c = c;
}
/* 阈值比例**与 index.html 里刻线的百分比同源**（改配置量程时按满量程比例判定，
   与温度那套 tempZone(0.583/0.75) 同一个做法）。 */
var WARN_REL = { temp: [70 / 120, 90 / 120], power: [68 / 120, 80 / 120], amp: [180 / 200, 195 / 200],
                 rpm: [8000 / 10000, 9500 / 10000], speed: [34 / 40, 38 / 40],
                 torque: [135 / 150, 148 / 150], throttle: [95 / 100, 1.01] };
function relState(id, v){
  var s = SIGS[id]; if (!s) return 'ok';
  var fs = (typeof s.max === 'number' && s.max > 0) ? s.max : 1;
  var r = WARN_REL[id];
  if (r){ var f = v / fs; return f >= r[1] ? 'over' : f >= r[0] ? 'warn' : 'ok'; }
  return 'ok';
}

/* ---------- 环形表绘制（弧 + 刻度 + 指针；中心数值由 DOM 负责） ---------- */
function drawRingGauge(ctx, w, h, id, warnAt, limAt){
  var s = SIGS[id]; if (!s) return;
  var lo = (typeof s.min === 'number') ? s.min : 0;
  var hi = (typeof s.max === 'number' && s.max > lo) ? s.max : 1;
  var v = Number(state.latest[id]) || 0;
  var f = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  var st = relState(id, v);
  var col = st === 'over' ? PAL.over : st === 'warn' ? PAL.warn : PAL.ok;

  var cx = w / 2, cy = h * 0.54;
  var R = Math.max(12, Math.min(w / 2 - 8, cy - 6, h - cy - 6));
  var sweep = 260 * Math.PI / 180;
  var a0 = -Math.PI / 2 - sweep / 2, a1 = -Math.PI / 2 + sweep / 2;
  var av = a0 + (a1 - a0) * f;
  var rw = Math.max(7, Math.min(14, R * 0.16));

  ctx.lineCap = 'round';
  ctx.lineWidth = rw; ctx.strokeStyle = PAL.track;
  ctx.beginPath(); ctx.arc(cx, cy, R - rw / 2, a0, a1); ctx.stroke();
  ctx.strokeStyle = col;
  ctx.beginPath(); ctx.arc(cx, cy, R - rw / 2, a0, av); ctx.stroke();
  /* 刻度：10 小格（5 大格），位置全部由 min/max 推导 */
  for (var i = 0; i <= 10; i++){
    var a = a0 + (a1 - a0) * i / 10, big = (i % 2 === 0);
    var r1 = R - rw - 1, r2 = r1 - (big ? 7 : 4);
    ctx.strokeStyle = big ? PAL.faint : PAL.line;
    ctx.lineWidth = big ? 1.5 : 1;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
    ctx.stroke();
  }
  function mark(val, color){
    if (!(val > lo) || val > hi) return;
    var a = a0 + (a1 - a0) * ((val - lo) / (hi - lo));
    var rr = R - rw / 2;
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * (rr - rw / 2 - 3), cy + Math.sin(a) * (rr - rw / 2 - 3));
    ctx.lineTo(cx + Math.cos(a) * (rr + rw / 2 + 3), cy + Math.sin(a) * (rr + rw / 2 + 3));
    ctx.stroke();
  }
  mark(warnAt, PAL.over);        /* 报警线（画在弧上，与刻线同宽区域） */
  mark(limAt, PAL.faint);        /* 限扭线（转速） */
  /* 指针：**锥形**（尖端指向弧）+ 起点在"数字盒之外"。
     ★ D1（task-29 修复）：以前固定从 `R*0.40` 起画，而数字盒半宽≈0.88R
       → 车速 24~34 m/s 巡航段（针尖落进数字盒）**整根指针被 DOM 数字盖住、等于不显示**。
     现在：沿指针方向算出"离开数字盒"的距离，从盒外 2px 起画 →
     任何取值下指针都**完整可见、不被遮挡**；数字盒尺寸取 fitRingVal() 缓存的
     **最宽文本**尺寸（与数值无关，所以指针长度不会随数值抖动）。 */
  /* 数字/单位整块的**真实**包围盒（相对环心）：`.ring-val` 是 translateY(-52%)，
     并不与环心重合（实测偏上 ~2% 高）。指针起点必须按**真实**盒算，
     否则"以为在盒外"其实还在盒里（第一版就是这样，D1 仍被判相交）。 */
  var tm = (ctx.canvas && ctx.canvas._txt) || {w: R * 0.9, h: R * 0.42, dx: 0, dy: 0};
  var hw = tm.w / 2;
  var hh = Math.max(6, tm.h * 0.72);                   /* 数字 + 单位整块的半高 */
  var dy = tm.dy || 0, dx = tm.dx || 0;
  var ux2 = Math.cos(av), uy2 = Math.sin(av);
  var rOut = R - rw - 1;
  /* 沿指针方向**逐像素**推进，找到离开数字块的最远点（1px 精度、≤rOut 次循环，代价可忽略） */
  var boxExit = 0;
  for (var tt = 1; tt <= rOut; tt++){
    var px2 = ux2 * tt, py2 = uy2 * tt;
    if (Math.abs(px2 - dx) <= hw && Math.abs(py2 - dy) <= hh) boxExit = tt;
    else if (boxExit > 0) break;
  }
  var rIn = boxExit + 2;
  if (!(rOut - rIn >= 4)) rIn = Math.max(1, rOut - 8);   /* 极端尺寸保底：宁可短一点，绝不整根不画 */
  var nc = (st === 'ok') ? PAL.ink : col;
  var half = Math.max(1.4, Math.min(2.6, R * 0.045));
  ctx.save();
  ctx.translate(cx, cy); ctx.rotate(av + Math.PI / 2);
  ctx.fillStyle = nc;
  ctx.beginPath();
  ctx.moveTo(0, -rOut);                       /* 尖端 */
  ctx.lineTo(-half, -rIn);
  ctx.lineTo(half, -rIn);
  ctx.closePath(); ctx.fill();
  ctx.restore();
  ctx._needleR = [rIn, rOut];                 /* 供探针/调试读取 */
}

/* ★ 中心读数自适应（task-28 修"溢出环"；task-29 修 D2"字号随位数跳变"）
 *   D2 现象（实测）：按**当前文本宽度**反算字号 → 车速 "0.0"→43px、"10.10"→34px；
 *   转速 "999"→45px、"1,000"→32px、"10,000"→26px（差 21~42%）→ 20Hz 下数字一涨一缩。
 *   修法：取该量程下**最宽的可能文本**（min/max 两端的格式化结果）算一次字号并**缓存**，
 *         缓存键 = 环内径(2px 量化) + 最宽文本 + 字体 + 目标宽度 → 与"当前数值"无关，
 *         所以刷新时字号恒定；只有环尺寸/量程/单位/字体变化才会重算。
 *   另外把目标宽度收到内径的 **72%**（原 ~94%）：给指针留出径向通道（D1 的配套）。 */
/* 环的几何：与 drawRingGauge 共用（探针也用它，保证"量的就是画的"） */
function ringGeom(cv){
  var w = cv.clientWidth, h = cv.clientHeight;
  var cy = h * 0.54;
  var R = Math.max(12, Math.min(w / 2 - 8, cy - 6, h - cy - 6));
  var rw = Math.max(7, Math.min(14, R * 0.16));
  return {R: R, rw: rw, rin: R - rw, cx: w / 2, cy: cy};
}
function fitRingVal(cv, numEl, unitEl){
  if (!cv || !numEl || cv.clientWidth < 8) return;
  var g = ringGeom(cv);
  if (g.rin < 10) return;
  var sigId = String(numEl.id || '').replace(/Num$/, '');
  var s = SIGS[sigId];
  var ctx = cv.getContext('2d');
  var cs = getComputedStyle(numEl);
  var family = cs.fontFamily || 'monospace';
  var weight = cs.fontWeight || '700';
  /* 最宽可能文本：按**实测宽度**取最大（不能比字符数 —— "10,000" 有千分位，
     字符数比 fmtNum 出来的 "10000" 多，按长度比会在换挡点改变 widest → 字号又跳）。 */
  var cands = [numEl.textContent || '0'];
  if (s && typeof s.max === 'number') cands.push(fmtNum(sigId, s.max));
  if (s && typeof s.min === 'number') cands.push(fmtNum(sigId, s.min));
  if (s && typeof s.max === 'number') cands.push(fmtNumSep(sigId, s.max));   /* 千分位版本 */
  var rinQ = Math.round(g.rin / 2) * 2;                 /* 2px 死区：尺寸微变不重排 */
  var targetW = Math.max(24, rinQ * 2 * 0.72);          /* 数字可用宽度（留 28% 给指针通道） */
  var REF = 40;
  function wAt(txt, size){
    ctx.font = weight + ' ' + size + 'px ' + family;
    return ctx.measureText(txt).width;
  }
  /* ★ 所有候选都按参考字号量一遍，取**最宽**的那个来定字号 → 与"当前数值"完全无关 */
  var maxWref = 0;
  for (var wi2 = 0; wi2 < cands.length; wi2++){
    var ww = wAt(String(cands[wi2]), REF);
    if (ww > maxWref) maxWref = ww;
  }
  var key = rinQ + '|' + family + '|' + weight + '|' + Math.round(maxWref) + '|' + Math.round(targetW);
  if (cv._fitKey === key) return;                       /* ★ 缓存命中：字号与盒尺寸都保持不变 */
  var size = maxWref > 0 ? REF * (targetW / maxWref) : REF;
  size = Math.max(15, Math.min(Math.min(46, rinQ * 0.85), Math.floor(size)));
  var us = Math.max(9, Math.round(size * 0.34));
  var vbox = rinQ * 2 - 12;                              /* 竖直也要落在内接区（留 6px） */
  var total = size + us * 1.3;
  if (total > vbox){
    var k2 = vbox / total;
    size = Math.max(15, Math.floor(size * k2));
    us = Math.max(9, Math.round(size * 0.34));
  }
  cv._fitKey = key;
  cv._fs = size;
  /* 数字+单位整块的真实偏移（相对环心）—— 与"几位数"无关，量一次即可 */
  var dyBox = 0, dxBox = 0;
  try {
    var cr0 = cv.getBoundingClientRect();
    var r1 = numEl.getBoundingClientRect();
    var r2 = unitEl ? unitEl.getBoundingClientRect() : r1;
    var bl = Math.min(r1.left, r2.left), bt = Math.min(r1.top, r2.top);
    var br = Math.max(r1.right, r2.right), bb = Math.max(r1.bottom, r2.bottom);
    dxBox = (bl + br) / 2 - (cr0.left + g.cx);
    dyBox = (bt + bb) / 2 - (cr0.top + g.cy);
  } catch (e) {}
  cv._txt = {w: maxWref * (size / REF), h: size, dx: dxBox, dy: dyBox};   /* 与数值无关 → 指针长度稳定 */
  if (numEl._fs !== size){ numEl.style.fontSize = size + 'px'; numEl._fs = size; }
  if (unitEl && unitEl._fs !== us){ unitEl.style.fontSize = us + 'px'; unitEl._fs = us; }
}

function paintGauges(){
  if (!$('speedGauge')) return;
  if (zoneVisible('zoneP2')){
    if (gaugeSpeed && gaugeSpeed.cv.clientWidth > 8){
      paintLayer(gaugeSpeed, function(ctx, w, h){
        var fs = (SIGS.speed && SIGS.speed.max) || 40;
        drawRingGauge(ctx, w, h, 'speed', tickValue('speedWarnTick', fs), 0);
      });
      /* ★ 中心读数自适应：必须在 canvas 尺寸确定之后（paintLayer 里 syncLayer 已量过） */
      fitRingVal(gaugeSpeed.cv, $('speedNum'), $('speedUnit'));
    }
    if (gaugeRpm && gaugeRpm.cv.clientWidth > 8){
      paintLayer(gaugeRpm, function(ctx, w, h){
        var fs = (SIGS.rpm && SIGS.rpm.max) || 10000;
        drawRingGauge(ctx, w, h, 'rpm', tickValue('rpmWarnTick', fs), tickValue('rpmLimitTick', fs));
      });
      fitRingVal(gaugeRpm.cv, $('rpmNum'), $('rpmUnit'));
    }
  }
}

/* ---------- 历史热条（近 30 s，34 段；250ms 节流 + 内容签名不变则完全不写 DOM） ---------- */
var HEAT_N = 34;
function buildHeats(){
  var els = document.querySelectorAll('.heat[data-heat]');
  for (var i = 0; i < els.length; i++){
    var el = els[i];
    if (el._built) continue;
    var h = '';
    for (var k = 0; k < HEAT_N; k++) h += '<i></i>';
    el.innerHTML = h;
    el._built = true; el._kids = el.children; el._sig = '';
  }
}
function updateHeats(){
  if (!PAL) refreshPalette();
  var els = document.querySelectorAll('.heat[data-heat]');
  if (!els.length) return;
  for (var i = 0; i < els.length; i++){
    var el = els[i];
    if (!el._built){ buildHeats(); }
    var id = el.getAttribute('data-heat');
    var b = state.buf[id];
    var kids = el._kids;
    /* ★ D5(c)：len<2 时**显式清空**（旧代码 `continue` 会保留上一帧的陈旧段——缓冲清空后
       热条还亮着，看起来像"数据在动"）。 */
    if (!b || b.length < 2){
      for (var z = 0; z < HEAT_N; z++){
        var it0 = kids[z];
        if (it0._h !== 0){ it0.style.height = '0%'; it0._h = 0; }
        if (it0._c !== 'clear'){ it0.style.background = PAL.line; it0._c = 'clear'; }
      }
      el._sig = id + '|empty';
      continue;
    }
    var n = b.length, vals = new Array(HEAT_N);
    /* ★ D5(b)：等距取样，**最右段 = 当前值**。
       旧代码 `idx=k*floor(len/34)`：len=600 时最大 idx=561 → 末 38 个样本（≈1.9s）
       永远画不到，最右一格 ≠ 当前读数（实测阶跃分界落在 52.9% 而真实是 50%）。 */
    for (var k = 0; k < HEAT_N; k++){
      vals[k] = b[Math.round(k * (n - 1) / (HEAT_N - 1))];
    }
    /* ★ D5(a)：按**本窗口 min..max** 归一化 —— 旧代码用信号量程(0..max)，低值被 4% 下限
       钳成一排 2~3px 短横（油门 10% 时像加载失败）。 */
    var mn = Infinity, mx = -Infinity;
    for (var k2 = 0; k2 < HEAT_N; k2++){ if (vals[k2] < mn) mn = vals[k2]; if (vals[k2] > mx) mx = vals[k2]; }
    var span = mx - mn;
    var sig = id + '|' + n + '|' + Math.round(mx * 100) + '|' + Math.round(mn * 100) + '|' + Math.round(b[n - 1] * 100);
    if (el._sig === sig) continue;
    el._sig = sig;
    for (var k3 = 0; k3 < HEAT_N; k3++){
      var v = vals[k3];
      /* 恒定信号给中位实心（而不是虚线）；下限 12% 保证低值也看得见 */
      var f = span > 1e-9 ? (v - mn) / span : 0.55;
      f = f < 0.12 ? 0.12 : (f > 1 ? 1 : f);
      var it = kids[k3];
      if (it._h !== f){ it.style.height = (f * 100).toFixed(1) + '%'; it._h = f; }
      /* ★ D5(d)：按**该段当时**的状态着色（跨阈值只染尾段，不再整条一起变色） */
      var segSt = relState(id, Number(v) || 0);
      var c = segSt === 'over' ? PAL.over : segSt === 'warn' ? PAL.warn : PAL.ok;
      if (it._c !== c){ it.style.background = c; it._c = c; }
    }
  }
}

/* ---------- 双轴（转速×车速）与散点（功率×电流）：都从既有 state.buf 取切片 ---------- */
function paintMini(){
  if (!PAL) refreshPalette();
  if (!zoneVisible('zoneP2')) return;
  if (!PAL) refreshPalette();
  if (dualLayer && dualLayer.cv.clientWidth > 8){
    paintLayer(dualLayer, function(ctx, w, h){
      var pad = { l: 4, r: 4, t: 6, b: 6 };
      var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
      ctx.strokeStyle = PAL.line; ctx.lineWidth = 1;
      for (var g = 1; g < 4; g++){
        var gy = pad.t + ph * g / 4;
        ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
      }
      /* ★ D10（task-29）：原来 lo/hi 取自**该序列自身**的窗口 min/max → 两条线各铺满整幅高度，
         车速 30~31 的波动看起来和转速 2000~9000 一样剧烈（语义误导）。
         现在按**各信号注册量程**（SIGS.min/max）归一化 —— 与趋势图 norm 模式同口径，
         两条线的高度可以直接横向比较。 */
      function line(arr, col, sigId){
        if (!arr || arr.length < 2) return;
        var sg = SIGS[sigId];
        var lo = (sg && typeof sg.min === 'number') ? sg.min : 0;
        var hi = (sg && typeof sg.max === 'number' && sg.max > lo) ? sg.max : lo + 1;
        var i;
        ctx.beginPath();
        for (i = 0; i < arr.length; i++){
          var x = pad.l + pw * i / (arr.length - 1);
          var fr = (arr[i] - lo) / (hi - lo);
          if (fr < 0) fr = 0; else if (fr > 1) fr = 1;     /* 越程夹住，不画到框外 */
          var y = pad.t + ph - ph * fr;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.lineJoin = 'round'; ctx.stroke();
      }
      line(state.buf.rpm, PAL.cyan, 'rpm');
      line(state.buf.speed, PAL.warn, 'speed');
      /* 图上直接标当前值（canvas 文字，不动 DOM 结构）—— 两条线各自量纲/量程一目了然 */
      ctx.font = '600 10px Consolas,monospace';
      ctx.fillStyle = PAL.cyan; ctx.textAlign = 'left';
      ctx.fillText('转速 ' + fmtSigVal(state.latest.rpm, SIGS.rpm), pad.l + 2, 10);
      ctx.fillStyle = PAL.warn; ctx.textAlign = 'right';
      ctx.fillText('车速 ' + fmtSigVal(state.latest.speed, SIGS.speed), w - pad.r - 2, 10);
    });
  }
  if (scatterLayer && scatterLayer.cv.clientWidth > 8){
    paintLayer(scatterLayer, function(ctx, w, h){
      var pad = 6, xs = state.buf.amp, ys = state.buf.power;
      if (!xs || !ys || xs.length < 2) return;
      var n = Math.min(xs.length, ys.length);
      var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, i;
      for (i = 0; i < n; i++){
        if (xs[i] < x0) x0 = xs[i]; if (xs[i] > x1) x1 = xs[i];
        if (ys[i] < y0) y0 = ys[i]; if (ys[i] > y1) y1 = ys[i];
      }
      /* ★ D7（task-29）：真机 current≡0 时 x 轴退化（所有点 px=pad）→ 整卡只剩左边一根 1~2px
         竖条、还压住「电流 →」，看起来像"图坏了"。现在给**显式空态 + 原因**（canvas 文字，
         不改 DOM 结构），并跳过网格/坐标点。 */
      if (x1 - x0 < 1e-9){
        ctx.fillStyle = PAL.faint;
        ctx.font = '600 10px Consolas,monospace';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('电流恒为 0 · 该图暂不可用', w / 2, h / 2 - 6);
        ctx.font = '400 9px Consolas,monospace';
        ctx.fillText('（电流通道无数据，改看「功率 × 转速」才有相关性）', w / 2, h / 2 + 8);
        ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
        return;
      }
      if (y1 - y0 < 1e-9) y1 = y0 + 1;
      ctx.strokeStyle = PAL.line; ctx.lineWidth = 1;
      for (var g = 1; g < 4; g++){
        var gy = pad + (h - pad * 2) * g / 4;
        ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(w - pad, gy); ctx.stroke();
      }
      for (i = 0; i < n; i += 2){
        var px = pad + (w - pad * 2) * (xs[i] - x0) / (x1 - x0);
        var py = h - pad - (h - pad * 2) * (ys[i] - y0) / (y1 - y0);
        ctx.fillStyle = 'rgba(59,95,208,' + (0.16 + 0.6 * i / n).toFixed(2) + ')';
        ctx.beginPath(); ctx.arc(px, py, 1.7, 0, Math.PI * 2); ctx.fill();
      }
      ctx.fillStyle = PAL.faint; ctx.font = '600 10px Consolas,monospace';
      ctx.textAlign = 'left';  ctx.fillText('电流 →', pad + 2, h - 2);
      ctx.textAlign = 'right'; ctx.fillText('↑ 功率 kW', w - pad - 2, 10);
    });
  }
}

/* 离散入口：切区/回填/配置变更/解冻/拖动结束后立刻补一次（与 refreshAccVec 同一批调用点） */
function refreshMini(){
  gaugeDirty = true; miniDirty = true;
  paintGauges(); paintMini(); updateHeats();
}

/* ============ P0 卡片（ADC 包未收到 -> "—" + 平线 sparkline） ============ */
function updateAdcDom(L, ha){
  var nowT = performance.now();
  /* 迷你趋势图：滚动期间整段跳过。10 张卡的 SVG path 重建是纯重绘开销，
     滚动时用户看不清，停手后立刻补一次（lastSparkT 不推进，下次必中）。 */
  var doSpark = !isScrolling() && (nowT - lastSparkT) >= (UI_CFG.sparkMs || 250);
  if (doSpark) lastSparkT = nowT;
  var cards = $('adcGrid').children;
  for (var c = 0; c < ADC_COUNT; c++){
    var card = cards[c];
    if (!card._val){
      card._val = card.querySelector('[data-val]');
      card._spark = card.querySelector('[data-spark]');
      /* ★ D6（task-29）：原来这条 path 只有 `fill` + 22% opacity、且 d 结尾是 `Z`
         → 10 个通道各是一块**纯色圆角实心块**（实测 path=M0,20 L0,9.9 … L100,20 Z、stroke:none）。
         这里补上 1px 实色描边（`vector-effect` 保证不被 viewBox 非等比缩放拉粗），
         并把 d 改成**开路径**（描边只画折线，填充仍按隐式闭合 = 保留柔和面积）。 */
      if (card._spark){
        card._spark.setAttribute('stroke', ADC_COLORS[c] || '#2563EB');
        card._spark.setAttribute('stroke-width', '1');
        card._spark.setAttribute('vector-effect', 'non-scaling-stroke');
        card._spark.setAttribute('stroke-linejoin', 'round');
      }
    }
    if (!ha){
      setNum(card._val, '—');
      if (card._spark._d !== SPARK_FLAT){ card._spark.setAttribute('d', SPARK_FLAT); card._spark._d = SPARK_FLAT; }
      continue;
    }
    var cur = L['adc'+c] || 0;
    /* ★ 小数位必须听第 3 区 ADC_DECIMALS（经 ui_adc_config 的 dec 下发到 SIGS.fmt）。
       以前写的是 toLocaleString()，位数跟着数值大小漂（13.7 显示 13.7、0 显示 0），
       和 CSV 落盘口径也对不上 —— 界面和录出来的文件两个样。 */
    var sg = SIGS['adc'+c];
    /* ★ 夹紧：sg.fmt 是配置下发的小数位（第 3 区 ADC_DECIMALS），写 200 会抛 RangeError */
    setNum(card._val, cur.toFixed(clampDec(sg && sg.fmt, ADC_DEC_FALLBACK)));
    /* sparkline：降频重建 path（path 字符串比对命中时也直接跳过 setAttribute） */
    if (!doSpark) continue;
    var b = state.buf['adc'+c];
    var len = Math.min(b.length, 26);
    if (len >= 2){
      var st = b.length - len;
      /* ★ D6（task-29）：归一化改**本窗口 min..max**（原来用配置量程 0..4095 →
         各点 y 只在 9.8~10.2 间抖，等于一条直线）。带**最小量程保护**：窗口几乎恒定
         （span 过小）时用 ±(量程的 0.5%) 兜底，避免把噪声放大成锯齿。 */
      var s2 = SIGS['adc'+c];
      var vmin = (s2 && typeof s2.min === 'number') ? s2.min : 0;
      var vmax = (s2 && typeof s2.max === 'number') ? s2.max : 4095;
      var wlo = Infinity, whi = -Infinity;
      for (var wi = 0; wi < len; wi++){ var wv = b[st + wi]; if (wv < wlo) wlo = wv; if (wv > whi) whi = wv; }
      var minGuard = Math.max(1e-9, (vmax - vmin) * 0.005);
      if (whi - wlo < minGuard){ var mid = (whi + wlo) / 2; wlo = mid - minGuard; whi = mid + minGuard; }
      var wspan = (whi - wlo) || 1;
      var d = 'M0,20';
      for (var q = 0; q < len; q++){
        var x = (q / (len - 1) * 100).toFixed(1);
        var fr = (b[st+q] - wlo) / wspan;
        if (fr < 0) fr = 0; else if (fr > 1) fr = 1;      /* 越界夹住，别画到框外 */
        var y3 = (20 - fr * 18 - 1).toFixed(1);
        d += ' L' + x + ',' + y3;
      }
      /* 开路径（不写 `Z`）：描边=折线、填充=隐式闭合的面积 */
      if (card._spark._d !== d){ card._spark.setAttribute('d', d); card._spark._d = d; }
    }
  }
}

/* ---------- 9. 主循环 ---------- */
var lastDraw = 0, lastDom = 0, lastFpsT = performance.now(), lastRaf = 0;
/* ★ task-27：环形表 / 双轴散点 的独立节拍（只在标脏时画）+ 热条节流时间戳。
   · 环形表 10fps：粗读元素，节拍太慢会显得指针一顿一顿；
   · 双轴/散点是分析辅助，5fps 足够 —— 实测离屏软件渲染下把这两张图从 10fps 降到 5fps，
     p95 单帧能拉回约 10ms（证据见 tools/_t27_perf_probe.py 的三阶段报告）。 */
var GAUGE_FPS = 10, MINI_FPS = 5, lastGauge = 0, lastMini = 0, lastHeat = 0;
/* 滚动静默期：performance.now() < scrollUntil 表示"用户正在滚"。
   由 #stage 的 scroll 回调维护（见 initZoneVisibility）。 */
var scrollUntil = 0;
function isScrolling(){ return performance.now() < scrollUntil; }
function render(ts){
  requestAnimationFrame(render);
  lastRaf = ts;
  /* ★ 滚动期间降频（2026-10-06）
     实测：滚动 + 20Hz 数据更新 → 每秒约 19 次布局，滚动帧 max 39.6ms、
     jank 1.7%；冻结数据后同样的滚动只有 25.1ms / 0.3%。
     所以滚动时把刷新率压下来，把主线程让给合成与光栅。
     停手 200ms 后自动恢复满帧，数字不会"停在旧值"。 */
  var rolling = ts < scrollUntil;
  var trendFps = UI_CFG.trendFps || 30, domHz = UI_CFG.domHz || 20;
  if (rolling){
    trendFps = Math.min(trendFps, UI_CFG.trendFpsScroll || 10);
    domHz    = Math.min(domHz,    UI_CFG.domHzScroll    || 6);
  }
  /* 趋势帧率以前写死 1000/30，第 5 区 UI_CFG.trendFps 改了根本没人读。 */
  if (!state.frozen && trendDirty && ts - lastDraw >= 1000 / trendFps){
    /* ★ 滚动期间完全跳过 canvas 重绘（trendDirty 保持 true，停下立即刷新）。
       滚动时用户看的是结构不是曲线，零绘制能让滚动合成更跟手。 */
    if (!rolling){
      lastDraw = ts;
      paintLayer(trendLayer, drawTrend);
      drawCursorLayer();   /* 高亮/淡出变了 -> 光标层跟着刷一次（<0.2ms） */
      if (zoneVisible('zoneVisual')) vcharts.forEach(drawVChart);
      state._frames++;
      trendDirty = false;
    }
  }
  /* ★ task-27：新增的环形表 / 双轴 / 散点 —— 走**独立节拍 + 脏标记**，
     与趋势同一个"滚动时跳过"策略（滚动时用户看的是结构不是图形）。
     ★ 只在标脏时画 —— 空闲/无数据时这两块**一次都不画**（tools/_t27_perf_probe.py 有计数证据：
       空闲 1.5s 内 speedGauge/rpmGauge/dualCanvas/scatterCanvas 重绘次数都是 0）。 */
  if (!rolling && gaugeDirty && ts - lastGauge >= 1000 / GAUGE_FPS){
    lastGauge = ts; paintGauges(); gaugeDirty = false;
  }
  if (!rolling && miniDirty && ts - lastMini >= 1000 / MINI_FPS){
    lastMini = ts; paintMini(); miniDirty = false;
  }
  /* 数字/仪表刷新：有新数据才更新，上限 20 Hz（滚动时降为 6 Hz）。
   * 原来是固定 120ms（8.3Hz）无条件刷新——空闲时白干，有数据时又偏慢。 */
  if (!state.frozen && domDirty && ts - lastDom >= 1000 / domHz){
    lastDom = ts; updateDom(); domDirty = false;
    /* 热条与 DOM 同一个节拍标脏；真正重写在 updateHeats 里按 sparkMs 再节流一次 */
    if (!isScrolling() && heatDirty && ts - lastHeat >= (UI_CFG.sparkMs || 250)){
      lastHeat = ts; updateHeats(); heatDirty = false;
    }
  }
  if (ts - lastFpsT >= 1000){
    state.fps = state._frames; state._frames = 0;
    state.rx = state._rx; state._rx = 0;
    lastFpsT = ts;
    setNum($('fpsTag'), String(state.fps));
    setNum($('rxTag'), String(state.rx));
    setNum($('rxFoot'), String(state.rx));
  }
}

/* ---------- 10. 真实数据入口（QWebChannel 推送） ---------- */
function onDataBatch(json){
  var arr;
  try { arr = JSON.parse(json); } catch(e){ return; }
  if (!arr || !arr.length) return;
  var applied = false;
  for (var i = 0; i < arr.length; i++){
    if (applyPayload(arr[i])) applied = true;
  }
  state._rx += arr.length;
  if (applied){ pushSample(); domDirty = true; }
}

/* 把**一个 payload** 映射到 state.latest（+ hasData），返回是否应用成功。
 * payload 格式与后端 dataReady 的元素完全一致：
 *   {"type":"adc","values":[10 个原始码]}
 *   {"type":"nav","roll":..,"pitch":..,"yaw":..,"accel":..}
 *   {"type":"motor","speed":..,"rpm":..,"throttle":..,"torque":..,"temp":..,"volt":..,"amp":..}
 * ★ 抽出来的原因：回放跳转的"回填"（replayBackfill）必须用**同一套**映射，
 *   否则两个入口会各写一份、日后加字段必漏一处（这个项目反复踩过同族坑）。
 */
function applyPayload(f){
  if (!f || !f.type) return null;
  var L = state.latest;
  if (f.type === 'adc' && f.values){
    for (var k = 0; k < 10 && k < f.values.length; k++){
      if (f.values[k] !== null) L['adc'+k] = f.values[k];
    }
    state.hasData.adc = true;
    return 'adc';
  }
  if (f.type === 'nav'){
    if (f.roll !== null && f.roll !== undefined) L.roll = f.roll;
    if (f.pitch !== null && f.pitch !== undefined) L.pitch = f.pitch;
    if (f.yaw !== null && f.yaw !== undefined) L.yaw = f.yaw;
    /* ★ 新协议（NAV 20B→22B）：加速度拆成横向 ACCEL_LAT / 纵向 ACCEL_LON。
       后端 `_payload_of` 对未在 FRONTEND_KEYS 里的字段用 nm.lower() 兜底，
       所以键名就是 accel_lat / accel_lon。
       · 两路可能**不同批**到达：各自更新 latest，合成值每批都用"各自的最新值"重算；
       · 合成总加速度 accelMag = sqrt(横向² + 纵向²)，是**真正的派生信号**：
         进 SIGS / state.buf -> 可勾选、可画趋势、进数值面板、进可视化 hover 读数；
       · 老键 f.accel（旧 .bin 回放 / protocol.py 的兼容垫片）映射到**纵向**，
         与后端垫片把单路 ACCEL 落到 ACCEL_LON、ACCEL_LAT 记 0 的口径一致。
       ★ P2：见到旧键时，另一路必须按"**无从得知**"处理 —— 归 0，绝不留上一次的残留值。
         否则"先 lat=12、再只来 {accel:7}"会算出 mag=13.89 这种虚假合成值（质检实测）。
         混合流（先新协议再旧键）同理：旧键那一批的横向就是没有数据，必须归 0。 */
    var _hasLat = (f.accel_lat !== null && f.accel_lat !== undefined);
    var _hasLon = (f.accel_lon !== null && f.accel_lon !== undefined);
    var _hasOld = (f.accel !== null && f.accel !== undefined);
    if (_hasLat) L.accelLat = f.accel_lat;
    if (_hasLon) L.accelLon = f.accel_lon;
    if (!_hasLon && _hasOld) L.accelLon = f.accel;      /* 旧单路 -> 纵向 */
    if (_hasOld && !_hasLat) L.accelLat = 0;            /* 横向无从得知 -> 归 0（P2） */
    var _lat = Number(L.accelLat) || 0, _lon = Number(L.accelLon) || 0;
    L.accelMag = Math.sqrt(_lat * _lat + _lon * _lon);
    accVecDirty = true;    /* 只标脏：20Hz 数据路径上不画（零绘制开销） */
    state.hasData.nav = true;
    return 'nav';
  }
  if (f.type === 'motor'){
    if (f.speed !== null && f.speed !== undefined) L.speed = f.speed;
    if (f.rpm !== null && f.rpm !== undefined) L.rpm = f.rpm;
    if (f.throttle !== null && f.throttle !== undefined) L.throttle = f.throttle;
    if (f.torque !== null && f.torque !== undefined) L.torque = f.torque;
    if (f.temp !== null && f.temp !== undefined) L.temp = f.temp;
    if (f.volt !== null && f.volt !== undefined) L.volt = f.volt;
    if (f.amp !== null && f.amp !== undefined) L.amp = f.amp;
    /* ★ 派生量：电池输出功率 = 电压 × 电流 / 1000（kW）。
       两路同属这一帧 MOTOR（偏移 13-14 电压 / 15-16 电流）→ **天然时间对齐**，
       不做插值、不跨帧拼；用上面刚更新过的 latest，保证与本帧一致。
       进 state.latest 之后由 pushSample() 自动进环形缓冲（与 accelMag 同一机制），
       所以趋势图能勾选、右侧数值面板能读、可视化图表能拖入，无需额外接线。 */
    L.power = (Number(L.volt) || 0) * (Number(L.amp) || 0) / 1000;
    state.hasData.motor = true;
    return 'motor';
  }
  return null;
}

/* 清空所有曲线的环形缓冲（趋势区与「数据可视化」区共用 state.buf，清一处两边都干净） */
function clearTrendBuffers(){
  for (var k in SIGS){
    if (state.buf[k]) state.buf[k].length = 0;
  }
}

/* 回放跳转回填（用户问题二：拉进度条后曲线显示的不是当前时刻的数据）
 * 后端接口（冻结，最终形态）：
 *   replayBackfill(jsonStr) -> {
 *     "sampleMs": 50,
 *     "slots": [ {"ts": <设备毫秒>, "items": [<payload>, <payload>, ...]}, ... ]
 *   }
 *   · 槽数 ≈ bufPoints；末槽 = 跳转点那一帧所在时刻；ts 与槽内 payload 同一时间基（设备毫秒）
 *   · items 可能少于 3 个（某类型到该时刻还没出现过，正常）；seek 到文件开头时槽数会很少
 *
 * ★ 处理语义（必须逐槽，不能逐 payload）：
 *   1) 先清空各曲线缓冲（否则新数据接在旧数据后面 -> 一条"旧位置+新位置"的拼接曲线）；
 *   2) 对每个槽：先对该槽 items 里**每个 payload** 更新 state.latest（复用 applyPayload），
 *      然后**这个槽只 push 一个采样点**；
 *      ⚠️ 绝不能在槽内每个 payload 各 push 一个点 —— items 多的信号（ADC）点数会变成
 *         NAV 的 3 倍，时间轴整体错位。
 *   3) 槽边界**只看 slots 数组结构**，不用 ts 差值去推（sampleMs 仅供参考）；
 *   4) 全部处理完 -> state.latest 停在最后一槽的值 -> 置脏重画。
 */
function onReplayBackfill(json){
  var obj;
  try { obj = JSON.parse(json); } catch(e){ return; }
  if (!obj) return;
  /* ★ 单一代码路径：只认冻结后的嵌套形态 {sampleMs, slots:[{ts,items:[...]}]}。
     刻意**不**兼容旧的扁平数组形态 —— "同一份数据有两套解析语义"正是这次
     "半密度"问题的同类风险源；格式不符就直接什么都不做（宁可不刷新，
     也不要静默走另一条语义不同的路把曲线画错）。
     replayBackfill 的签名是 QString，做不到抛错，只能靠"结构不对就退出"显式失败。 */
  if (Array.isArray(obj)) return;
  var slots = obj.slots;
  if (!slots || !slots.length) return;

  /* 先扫一遍：本窗口出现过哪些包类型，以及每个类型**第一次出现**的那个 payload。
     ⚠️ 为什么要"首次出现"：某类型可能在窗口中途才出现（items 少于 3 个是正常的），
        若直接拿 state.latest 里 **seek 之前**的值去填前几个槽，开头就会多出一段与
        当前时刻无关的直线 —— 那正是用户抱怨的"拼接 / 中间一段直线"。
        用"该类型在窗口内的首个已知值"前向填充，既保证所有信号点数一致（时间轴对齐，
        drawTrend 是按各自 buffer 长度映射 x 的），又不会把 seek 前的旧值灌进来。 */
  var groupsSeen = {}, firstOf = {};
  for (var s0 = 0; s0 < slots.length; s0++){
    var it0 = (slots[s0] && slots[s0].items) || [];
    for (var j0 = 0; j0 < it0.length; j0++){
      var p0 = it0[j0];
      if (p0 && p0.type){
        groupsSeen[p0.type] = true;
        if (!firstOf[p0.type]) firstOf[p0.type] = p0;
      }
    }
  }
  if (!groupsSeen.adc && !groupsSeen.nav && !groupsSeen.motor) return;

  clearTrendBuffers();
  var L = state.latest;
  /* 预置各类型在窗口内的首个值：窗口前段尚未出现该类型时，前向填充用的就是它 */
  for (var gp in firstOf) applyPayload(firstOf[gp]);
  for (var i = 0; i < slots.length; i++){
    var items = (slots[i] && slots[i].items) || [];
    /* ① 该槽内每个 payload 只更新 latest（不推点） */
    for (var j = 0; j < items.length; j++) applyPayload(items[j]);
    /* ② 该槽**只推一个点**（与 pushSample 同口径，只推本窗口确实出现过的包类型） */
    for (var k in SIGS){
      if (!groupsSeen[GROUP_PKT[SIGS[k].group]]) continue;
      var b = state.buf[k];
      if (!b) continue;
      b.push(L[k]);
      if (b.length > BUF) b.splice(0, b.length - BUF);
    }
  }
  /* 回填刚造了一整窗"历史"，别让紧随其后的真实样本立刻又推一个点（会多出一个等距点） */
  lastSampleT = performance.now();
  trendDirty = true;
  domDirty = true;

  /* ★ F2：暂停时主循环那两处 `!state.frozen` 门控不会放行（trendDirty 会一直停在 true），
     于是"暂停中 seek"什么都不会刷新。这里在**离散事件**里直接补画一次 ——
     与 paintVCharts() 同一模式，**不动主循环门禁**（那个门禁是防暂停时空转的）：
       · 趋势层 + 光标层：立刻看到跳转点的曲线；
       · updateDom()：把右侧数值面板 / 卡片 / 仪表也刷成**跳转点**的值 ——
         否则曲线是新数据、数字还是旧值，用户照样判"数据不对"（需求二的本质）。
     pushSample() 在暂停时直接 return，所以这里刷出来的是跳转点快照，不会被实时数据覆盖。 */
  trendLayer.sKey = '';         /* 跳转后量程几乎一定变 -> 静态网格/刻度缓存失效 */
  paintLayer(trendLayer, drawTrend);
  drawCursorLayer();
  updateDom();
  refreshAccVec();              /* seek 后加速度矢量图也立刻换成跳转点的值 */
  refreshMini();                /* 环形表/双轴/散点同一批离散入口 */
  if (state.frozen){
    /* 暂停中 seek：画面已经换成跳转点的数据，但**回放不会继续推进**。
       刻意不复用"回放画面不会更新"那句 —— 有了上面的补画，画面其实是更新的，
       照抄旧文案会自相矛盾。 */
    toast('当前处于暂停状态：已显示跳转点的数据，回放不会继续推进，点「继续」恢复', 'warn', 6000);
  }
}

/* 趋势取样：定频 20 Hz，与「数据到达节拍」解耦。
 *
 * 为什么必须定频：早期实现是「每来一批推一个点」。后端若因串口攒块只推
 * 4.9 Hz（实测），600 点缓冲就变成 123 秒，而坐标轴还标着 30 秒 —— 曲线
 * 看着像卡住，时间轴也是错的。定频后无论数据是 5 Hz 还是 100 Hz 到达，
 * 600 点恒等于 30 秒，坐标轴永远对得上。
 */
var SAMPLE_MS = UI_CFG.sampleMs;   /* 走 UI_CFG，别写死 —— 服务端第 5 区 UI_CFG /
                                      exe 旁 telemetry_user.json 都能覆写它 */
var lastSampleT = -1e9;
function pushSample(){
  if (state.frozen) return;   /* 暂停：冻结缓冲，曲线停在当前窗口便于分析 */
  var nowT = performance.now();
  if (nowT - lastSampleT < SAMPLE_MS) return;   /* 限频：高频数据不会把缓冲打快 */
  lastSampleT = nowT;
  for (var k in SIGS){
    if (!state.hasData[GROUP_PKT[SIGS[k].group]]) continue;
    var b = state.buf[k];
    b.push(state.latest[k]);
    if (b.length > BUF) b.splice(0, b.length - BUF);
  }
  trendDirty = true;
  if (state.recording && !backend) localRecSample();
}

/* ---------- 11. HEX 监视器（单 <pre> + 贴底才滚，最小化布局压力） ----------
 * v2.0：每 200ms 整块重写 + 读 scrollHeight（强制同步布局 1.6ms/次）。
 * v2.7：60 节点池 + column-reverse，消掉了读 scrollHeight，但带来两个新问题：
 *   ① 每 200ms 最多重写 60 个节点的 textContent —— 60 次 DOM 写，
 *      把整块 flex 容器的布局反复打脏；用户滚动主舞台时被不断 invalidate，
 *      实测是「滚动卡顿」的主要持续开销之一；
 *   ② column-reverse 的滚动容器回看历史时行为诡异（滚动锚定反向），
 *      新行插入时滚动位置乱跳，主观感受就是「滑动 HEX 会死机」。
 * 现在：一行 HTML 的 <pre>，整块内容一次 textContent 写入（60 次 -> 1 次）；
 *   只有「本来就贴在底部」才自动滚到底 —— 回看历史时完全不被打断。
 *   代价是每次 flush 读一次 scrollHeight（同一帧内只触发一次布局，~1.6ms/200ms）。 */
var HEX_MAX = UI_CFG.hexMax;
var hexBuf = [], hexTimer = null, hexPre = null;
/* ★ 「是否贴底」用 scroll 事件维护，不在 flush 里读 scrollHeight（2026-10-06）。
   scrollTop/clientHeight/scrollHeight 三个一读就是一次强制同步布局（~1.6ms），
   5 次/秒 = 8ms/s 纯浪费，而且正好会打断主舞台的滚动。
   scroll 事件里读是安全的：那时浏览器本来就要更新滚动状态。 */
var hexAtBottom = true;

function ensureHexPre(box){
  if (hexPre) return hexPre;
  hexPre = document.createElement('pre');
  hexPre.className = 'hexpre';
  var idle = $('hexIdle');
  if (idle) box.insertBefore(hexPre, idle); else box.appendChild(hexPre);
  hexPre._t = null;
  box.addEventListener('scroll', function(){
    hexAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 32;
  }, {passive:true});
  return hexPre;
}
function flushHex(){
  hexTimer = null;
  var box = $('hexBox');
  if (!box || !hexBuf.length) return;
  var keep = hexBuf.slice(-HEX_MAX);
  hexBuf.length = 0;
  var pre = ensureHexPre(box);
  var s = '';
  for (var i = 0; i < keep.length; i++){
    s += (keep[i].tx ? '>> ' : '') + keep[i].s + '\n';
  }
  if (pre._t !== s){ pre.textContent = s; pre._t = s; }
  var idle = $('hexIdle');
  if (idle && idle.style.display !== 'none') idle.style.display = 'none';
  /* 贴底才自动滚（回看历史时完全不打断）；滚动位置变化会触发上面的
     scroll 回调把 hexAtBottom 刷新，所以这里不用再读一次 scrollHeight。 */
  if (hexAtBottom) box.scrollTop = box.scrollHeight;
}
function pushHexLines(text, isTx){
  var lines = String(text).split('\n');
  for (var i = 0; i < lines.length; i++){
    if (lines[i]) hexBuf.push({s:lines[i], tx:!!isTx});
  }
  /* 缓冲本身也设上限：后端突发时不要无限攒字符串 */
  if (hexBuf.length > HEX_MAX * 8) hexBuf.splice(0, hexBuf.length - HEX_MAX * 8);
  if (hexTimer === null) hexTimer = setTimeout(flushHex, 200);
}
function onRaw(text){ pushHexLines(text, false); }

/* ---------- 12. 连接 / 状态 ---------- */
function setLive(mode, info){
  /* mode: 'off' | 'live' | 'demo' | 'replay' */
  var dot = $('liveDot');
  dot.className = mode === 'off' ? 'dot rec-off' : 'dot live';
  var txt = mode === 'live' ? ('LIVE · ' + (info || '已连接'))
          : mode === 'demo' ? 'LIVE · 演示数据'
          : mode === 'replay' ? 'REPLAY · 回放中' : '未连接';
  setNum($('liveText'), txt);
  setNum($('connText'), mode === 'off' ? '未连接' : (mode === 'demo' ? '演示模式' : mode === 'replay' ? '回放' : '已连接'));
  $('connDot').className = mode === 'off' ? 'dot rec-off' : 'dot live';
  setNum($('connMeta'), mode === 'live' ? ('已连接 ' + (info || '')) : mode === 'demo' ? '演示模式（本地模拟数据）' : mode === 'replay' ? '数据回放中' : '未连接');
  setNum($('connectBtn'), mode === 'live' ? '断开' : '连接');
}

/* ---------- 回放进度条（需求1：快速跳到任意时刻） ----------
 * 后端接口（冻结）：
 *   replayProgress(index, total, cur_secs, total_secs)   # 约 100ms 一次
 *   seekReplay(frac)                                     # frac ∈ [0,1]
 *
 * ⚠️ 两个**实测**出来的事件顺序陷阱（后端验证得出，前端必须避开）：
 *   ① startReplay 会先发一次 (0,0,0.0,0.0)（内部 stopReplay 复位用），
 *      再发 (0,n,0.0,total)，最后才发 replayStateChanged(True)。
 *      ⇒ 收到 (0,0,...) **绝不能**把滑块永久禁用，否则真正开始回放后滑块还是灰的。
 *   ② 停止与完成也都发 (0,0,0.0,0.0)，"归零"在开始前和结束后都出现，
 *      ⇒ 不能拿它当状态机。
 * 因此本文件的约定是：**「是否处于回放态」只由 onReplayState（replayStateChanged）判定**，
 * onReplayProgress 只负责更新数值与滑块位置。
 */
function fmtReplayPos(cur, total){
  var c = Number(cur) || 0;
  if (c < 0) c = 0;
  return c.toFixed(1) + 's / ' + (Number(total) || 0).toFixed(1) + 's';
}
/* ★ 加载（解析）反馈（U06 后续，2026-10-15）：
 *   后端把旧 .bin 的解析改成时间片分片后界面不再白屏，但解析期间**没有任何反馈** ——
 *   16 分钟录播约 7 秒、1 小时约 20 秒，用户点「数据回放」会像"没反应"。
 *   后端新增冻结接口 `replayLoadingChanged(bool, str)`：
 *       (True, "正在解析… 12345 帧")  -> 在 #replayPos 显示该文案，滑块保持禁用
 *       (False, "")                    -> 恢复正常显示
 *   ⚠️ **这不是回放态**：回放态唯一判据仍是 replayStateChanged（state.replaying）。
 *      这里只碰 #replayPos 文本与滑块可用性，绝不改 state.replaying、不动底栏版式。
 *   为什么复用 #replayPos 而不新增元素：底栏高度/版式不动、既有元素零位移，
 *   而进度位在加载时本来就只显示"— / —"，没有信息量。 */
function onReplayLoadingChanged(loading, text){
  state.replayLoading = !!loading;
  var pos = $('replayPos'), rr = $('replayRange');
  if (loading){
    state.replayLoadingText = text || '正在解析…';
    if (pos) setNum(pos, state.replayLoadingText);
    if (rr){ rr.disabled = true; state.replayDragging = false; }
    return;
  }
  state.replayLoadingText = '';
  /* 恢复：回放中 -> 重新可用；不在回放中 -> 维持"未回放"的禁用态并回到占位文案。
     ★ 判据只用 state.replaying（loading 不是回放态）。 */
  setReplayEnabled(!!state.replaying);
  if (!state.replaying && pos) setNum(pos, '— / —');
}
/* 回放暂停/继续的**按钮外观**（问题四）。
 * ⚠️ 与顶栏 #freezeBtn 的"冻结缓冲供分析"完全无关：那个冻结的是 state.frozen（曲线缓冲），
 *    这个暂停的是**回放推进**（后端 _replay_idx 不再前进）。两者文案也刻意不同。
 * 按钮只在"回放中"可用（见 setReplayEnabled）；不在回放中时显示 ⏸ 且禁用。 */
function setReplayPausedUI(paused){
  var b = $('replayPauseBtn');
  if (!b) return;
  b.textContent = paused ? '▶' : '⏸';
  var t = paused ? '继续回放' : '暂停回放';
  b.setAttribute('aria-label', t);
  b.setAttribute('title', t);
  b.setAttribute('aria-pressed', paused ? 'true' : 'false');
}
/* 后端确认了暂停态（唯一权威来源）；顺带清掉在途请求 */
function onReplayPausedChanged(paused){
  state.replayPaused = !!paused;
  state.replayPausedWant = null;
  setReplayPausedUI(state.replayPaused);
}
/* 回放态开关：唯一改 disabled 的地方（拖动态与暂停态一并复位） */
function setReplayEnabled(on){
  var rr = $('replayRange');
  if (rr){
    rr.disabled = !on;
    if (!on){ state.replayDragging = false; rr.value = '0'; }
  }
  var pb = $('replayPauseBtn');
  if (pb){
    pb.disabled = !on;
    /* 停止/完成 -> 回到"未回放"不可用态；回放开始时保持后端已下发的暂停态
       （replayPausedChanged(True) 可能先于 replayStateChanged(True) 到达） */
    if (!on){ state.replayPaused = false; state.replayPausedWant = null; setReplayPausedUI(false); }
  }
  if (!on){
    state.replayTotal = 0;
    var pos = $('replayPos');
    /* ★ 加载中不要覆盖解析文案（用户停了回放但解析还在跑，反馈要继续显示） */
    if (pos && !state.replayLoading) setNum(pos, '— / —');
  }
}
function onReplayProgress(idx, total, curSecs, totalSecs){
  var rr = $('replayRange'), pos = $('replayPos');
  if (!rr || !pos) return;
  var n = Number(total) || 0;        /* 总帧数 */
  var tsec = Number(totalSecs) || 0; /* 总时长（秒）—— 标签直接用，不估算 */
  /* ★ 复位判据只能是「没有帧」(n <= 0)。
     ⚠️ 不能用 tsec <= 0：只有 1 帧、或所有帧 ts 相同的 .bin，
        后端算出的总时长**就是** 0.0（合法的真实值，不是复位帧）——
        以前这里把 tsec<=0 也当复位，导致这类文件的标签永远停在占位符、
        滑块数值也不刷新（QA 编号 R4）。 */
  if (n <= 0){
    /* 复位帧（开始前 / 停止后）：只归零数值，**绝不动 disabled**（陷阱①）。
       ★ 加载（解析）中的复位帧也不能覆盖"正在解析…"文案 —— 否则用户又看不到反馈了。 */
    if (!state.replayDragging) rr.value = '0';
    if (!state.replaying && !state.replayLoading) setNum(pos, '— / —');
    return;
  }
  state.replayTotal = tsec;
  /* ★ 分母必须是 (n-1)，与后端 seekReplay 的 `idx = round(frac*(n-1))` 互逆。
     ⚠️ 写成 idx/n 会系统性地把位置压低：
        · 最后一帧 idx=n-1 只能到 (n-1)/n —— n=5 时滑块永远到不了最右端(0.8)；
        · 拖动松手后回包会把滑块往回拽（n=10 请求 0.5 只回报 0.400，-10%）。
     n<=1 时只有一帧，位置恒为 0，避免除零。 */
  var frac = n > 1 ? (Number(idx) || 0) / (n - 1) : 0;
  if (!isFinite(frac)) frac = 0;
  if (frac < 0) frac = 0; else if (frac > 1) frac = 1;
  /* 拖动期间回包不抢滑块，也不抢标签：位置与读数一律以用户手上的滑块为准，
     否则 100ms 一次的旧位置会把滑块来回拽、标签来回跳（需求明确要求避免）。 */
  if (state.replayDragging) return;
  rr.value = String(Math.round(frac * 1000));
  setNum(pos, fmtReplayPos(curSecs, tsec));
  /* 兜底：后端若没有 replayStateChanged 信号，收到真实进度就启用滑块。
     注意这条只在没有 state 信号时生效，且**从不禁用** —— 陷阱①依然不会踩。 */
  if (!state._replayStateWired && rr.disabled) rr.disabled = false;
}

function onReplayState(on, info){
  state.replaying = !!on;
  setNum($('replayBtn'), on ? '停止回放' : '数据回放');
  /* ★ 回放态的唯一判据在这里，不在 onReplayProgress 里 */
  setReplayEnabled(!!on);
  if (on){
    setLive('replay', info);
    setNum($('recMeta'), '回放中 · ' + (info || ''));
    /* ★ 回放开始先清一次曲线缓冲（问题二）：
       否则新文件的曲线会接在上一个文件/上一次会话的残留后面 —— 一眼看去就是"拼接线"。
       seek 之后由 replayBackfill 负责把一窗历史重新灌满。 */
    clearTrendBuffers();
    trendLayer.sKey = '';
    trendDirty = true;
    /* ★ U09：**冻结（暂停）中开始回放 -> 自动解除冻结**。
       理由（Lead 裁决 2026-10-15）：用户点「数据回放」就是要看数据，
       而 pushSample() 与主循环的两处 `!state.frozen` 门控会让曲线/数值一动不动，
       等于功能失效 —— 所以自动恢复实时，并给一条说明性 toast。
       （旧行为只弹"请先点「继续」"，把问题丢回给用户。） */
    if (state.frozen){
      setFrozen(false, true);      /* 静默解冻：UI/角标/aria 一起同步，不弹"已恢复实时" */
      toast('已自动恢复实时：回放需要数据继续推进，原来的「暂停」已解除', 'ok', 6000);
    }
    toast('开始回放 ' + (info || ''), 'ok');
  } else {
    if (info && info.indexOf('完成') >= 0) toast(info, 'ok');
    setLive('off');
    setNum($('recMeta'), '未在记录');
  }
}

function onStatus(connected, info){
  state.connected = !!connected;
  if (connected){
    if (state.demo){ state.demo = false; $('demoMode').checked = false; stopSim(); }
    clearTelemetry();   /* 新会话：各类包首帧到达前对应分区保持 "—" */
    setLive('live', info);
    toast('已连接 ' + (info || ''), 'ok');
    if ($('autoRecord').checked && !state.recording) toggleRecord(true);
  } else {
    if (!state.demo) clearTelemetry();   /* 断开连接 -> 回到全占位状态 */
    setLive(state.demo ? 'demo' : 'off');
    if (state.recording) updateRecUI(false);
  }
}

/* 后端错误：一律走 Toast，不再 alert（alert 会阻塞渲染进程） */
function onError(msg){ toast(msg, 'err'); }

function doConnect(){
  if (state.connected){ if (backend) backend.disconnect(); return; }
  if (state.demo){
    toast('演示模式运行中。请先在「设置」里关闭演示模式，再连接真实串口。', 'warn');
    return;
  }
  if (!backend){
    toast('通信后端未就绪（浏览器模式）。请勾选「演示模式」体验，或从打包程序打开。', 'warn');
    return;
  }
  var port = $('portSelect').value;
  if (!port){ toast('未检测到可用串口。请确认 E34 接收端已插入，然后点「刷新端口」。', 'warn'); return; }
  backend.connect(port, parseInt($('baudSelect').value, 10));
}

/* ---------- 13. 录制 / 导出 / 记录列表 ---------- */
var recTimer = null;
function updateRecUI(on, path){
  state.recording = on;
  $('recDot').className = on ? 'dot rec' : 'dot rec-off';
  setNum($('recBtn'), on ? '停止记录' : '开始记录');
  var pill = $('recPill');
  if (pill) pill.setAttribute('aria-pressed', on ? 'true' : 'false');
  if (on){
    state.recStart = Date.now();
    state.recRows = 0;
    setRecMeta(path);
    if (!recTimer) recTimer = setInterval(function(){
      if (!state.recording) return;
      var s = Math.floor((Date.now() - state.recStart) / 1000);
      setNum($('recText'), 'REC ' + mmss(s));
      /* 有后端时行数由后端每 500ms 推；这里是本地/演示模式的兜底 */
      if (!backend) setRecMeta('');
    }, 500);
  } else {
    setNum($('recText'), 'REC');
    setNum($('recMeta'), '未在记录');
    if (recTimer){ clearInterval(recTimer); recTimer = null; }
  }
}
/* 录制反馈：时间 + 行数，一次 setNum，只在文本变化时写 DOM */
function setRecMeta(path){
  var f = String(path || '').split(/[\\/]/).pop();
  var txt = '正在记录';
  if (f) txt += ' · ' + f;
  var s = Math.floor((Date.now() - state.recStart) / 1000);
  txt += ' · ' + mmss(s);
  if (state.recRows > 0) txt += ' · ' + state.recRows.toLocaleString('en-US') + ' 行';
  setNum($('recMeta'), txt);
}
/* 后端推送的录制进度（rows, "mm:ss"）：让"正在记录"的反馈每 500ms 平滑跳动，
 * 而不是只有 1Hz 的秒数变化 —— 这是"保存提示流畅度"的另一半。 */
function onRecProgress(rows, elapsed){
  if (!state.recording) return;
  state.recRows = rows | 0;
  var txt = '正在记录';
  if (state.recPath) txt += ' · ' + state.recPath;
  txt += ' · ' + (elapsed || mmss((Date.now() - state.recStart) / 1000));
  txt += ' · ' + state.recRows.toLocaleString('en-US') + ' 行';
  setNum($('recMeta'), txt);
  setNum($('recText'), 'REC ' + (elapsed || mmss((Date.now() - state.recStart) / 1000)));
}

function toggleRecord(on){
  if (on && !state.connected && !state.demo && backend){
    toast('请先连接串口（或在「设置」里勾选演示模式），再开始录制。', 'warn');
    return;
  }
  if (state.recording === on) return;
  if (backend && !state.demo){
    if (on) backend.startRecording(); else backend.stopRecording();
    return;   /* 按钮状态等后端 recordStateChanged 回调再刷新 */
  }
  /* 无后端 / 演示模式：内存攒行，停止后下载 */
  if (on){
    state.recLines = [CSV_HEADER_LOCAL];
    updateRecUI(true);
    toast('开始记录（本地模式，停止后自动下载 CSV）', 'ok');
  } else {
    updateRecUI(false);
    if (state.recLines.length > 1){
      var name = 'telemetry_' + stamp() + '.csv';
      blobDownload(state.recLines.join('\n') + '\n', name);
      state.logs.unshift({name:name, path:'', rows:state.recLines.length - 1, t0:new Date().toLocaleString('zh-CN')});
      renderLogs();
      toast('已保存 ' + (state.recLines.length - 1) + ' 行 · ' + name, 'ok');
    } else {
      toast('本次未收到数据，未生成文件。', 'warn');
    }
    state.recLines = [];
  }
}

/* 本地录制采样（无后端时）：以当前最新值成行 */
function localRecSample(){
  var L = state.latest;
  /* ★ 列顺序/位数全部来自 csvDataCols()（CSV_COLS 唯一来源）——
     以前这里手抄一份 order，与表头/导出各写一遍，必然漏改。 */
  var cols = [hms(new Date())].concat(csvDataCols(function(id){ return L[id]; }));
  state.recLines.push(cols.join(','));
  state.recRows = state.recLines.length - 1;
}

function blobDownload(text, name){
  var blob = new Blob([text], {type:'text/csv;charset=utf-8'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function(){ URL.revokeObjectURL(a.href); }, 3000);
}

function exportCsv(){
  if (backend && !state.demo){
    backend.exportRecording('telemetry_' + stamp() + '.csv');
    return;
  }
  /* 无后端：导出当前屏幕缓冲快照 */
  var keys = Object.keys(SIGS);
  var n = state.buf[keys[0]] ? state.buf[keys[0]].length : 0;
  if (!n){ toast('暂无数据可导出，请先连接或开启演示模式。', 'warn'); return; }
  var out = CSV_HEADER_LOCAL + '\n';
  var t0 = Date.now() - (n - 1) * 50;
  for (var i = 0; i < n; i++){
    /* ★ 与表头/本地记录行**同源**（csvDataCols + CSV_COLS）：
       以前这里手抄了第三份列序，本轮就漏改过 -> 导出的 CSV 里 torque/motor_rpm 值互换。 */
    var cols = [hms(new Date(t0 + i * 50))]
      .concat(csvDataCols(function(id){ return state.buf[id] ? state.buf[id][i] : 0; }));
    out += cols.join(',') + '\n';
  }
  blobDownload(out, 'telemetry_export_' + stamp() + '.csv');
  toast('已导出屏幕缓冲快照（' + n + ' 行）', 'ok');
}

function renderLogs(){
  var box = $('logList');
  if (!state.logs.length){
    box.innerHTML = '<div class="hex-idle">尚无记录。选择保存目录并点击「开始记录」后，数据实时写入 CSV。</div>';
    return;
  }
  box.innerHTML = state.logs.map(function(l, i){
    return '<div class="log-item">' +
      '<div style="min-width:0"><div class="nm">' + l.name + '</div>' +
      '<div class="mt">' + l.t0 + ' · ' + l.rows + ' 行' + (l.path ? ' · ' + l.path : '') + '</div></div>' +
      (l.path ? '<button class="btn" type="button" data-idx="' + i + '">打开目录</button>' : '') +
    '</div>';
  }).join('');
}

/* ---------- 14. QWebChannel 初始化 ---------- */
/* ---------- 13.5 接收后端下发的遥测配置 ----------
 * 来源：telemetry_config.py（后端把它 JSON 化后从 uiConfig 信号推过来）
 *
 * 为什么要这一步：以前「通道中文名」在 protocol.py、launcher_web.py、
 * app.js 里各写了一份，换一个传感器要同步改三处，漏一处界面就张冠李戴。
 * 现在配置只有 telemetry_config.py 一个源头，这里收到后自动重建界面，
 * 改完配置文件重新打包，卡片名 / 曲线名 / 导出表头全部同步更新。 */
function applyUiConfig(cfg){
  if (!cfg) return;

  /* 12bit 解包顺序：设置里那个下拉要显示当前生效值（可能已被 telemetry_user.json
     覆盖），否则用户切完再打开界面看到的是旧值，会以为没生效。 */
  if (typeof cfg.adc12bitOrder === 'string' && cfg.adc12bitOrder) ADC_ORDER_NOW = cfg.adc12bitOrder;
  if (typeof cfg.motorEndian === 'string' && cfg.motorEndian) MOTOR_ENDIAN_NOW = cfg.motorEndian;

  /* ---- 串口参数：波特率候选与默认值也来自配置第 1 区 ---- */
  if (cfg.serial && Array.isArray(cfg.serial.baud_choices)){
    var sel = $('baudSelect');
    if (sel){
      var def = String(cfg.serial.default_baud || 115200);
      sel.innerHTML = cfg.serial.baud_choices.map(function(b){
        return '<option' + (String(b) === def ? ' selected' : '') + '>' + b + '</option>';
      }).join('');
    }
  }

  /* ---- 加速度：量程、小数位、单位跟着配置的 scale 走 ---- */
  if (typeof cfg.accRawFs === 'number' && cfg.accRawFs > 0) ACC_RAW_FS = cfg.accRawFs;
  if (Array.isArray(cfg.nav)){
    var _accSeen = false;
    for (var ni = 0; ni < cfg.nav.length; ni++){
      /* ★ 兼容两种协议：老配置字段叫 ACCEL；新配置拆成 ACCEL_LAT / ACCEL_LON。
         取**第一个**加速度字段当这一格的量程/单位来源（同一次改协议里两个通道 scale 相同）。
         写死 'ACCEL' 会让改名后 ACC_SCALE 永远停在 1.0 → 表盘量程差 1000 倍、指针几乎不动。 */
      if (!_accSeen && String(cfg.nav[ni].name || '').indexOf('ACCEL') === 0){
        _accSeen = true;
        ACC_SCALE = Number(cfg.nav[ni].scale) || 1;
        ACC_UNIT  = cfg.nav[ni].unit || '';         /* 单位也走配置，不写死 raw */
        /* ★ 三路加速度的**单位**统一由 syncAccLabels() 写（配置的 unit 优先，空则退回 'g'），
           这里只同步位数；避免两个地方都写单位、日后打架。 */
        ['accelLat','accelLon','accelMag'].forEach(function(id){
          if (!SIGS[id]) return;
          if (typeof cfg.nav[ni].dec === 'number') SIGS[id].fmt = clampDec(cfg.nav[ni].dec, SIGS[id].fmt);
        });
      }
    }
  }
  if (Array.isArray(cfg.motor)){
    for (var mi = 0; mi < cfg.motor.length; mi++){
      if (cfg.motor[mi].name === 'SPEED'){
        SPD_SCALE = Number(cfg.motor[mi].scale) || 1;
        SPD_UNIT  = cfg.motor[mi].unit || 'm/s';
      }
    }
  }
  /* ★ 单位：把第 5 区 UNITS 下发的单位写进信号表（界面上所有后缀都从这里来） */
  ['nav', 'motor'].forEach(function(grp){
    if (!Array.isArray(cfg[grp])) return;
    cfg[grp].forEach(function(f){
      var id = FIELD_SIG[f.name];
      if (!id || !SIGS[id]) return;
      if (typeof f.unit === 'string') SIGS[id].unit = f.unit;
      /* ★ 系数也要同步：否则「设置」里的系数列永远显示兜底的 1，
         用户改了 telemetry_user.json 的 scales 却看不出来（同族坑：单位失效） */
      if (isFinite(Number(f.scale))) SIGS[id].scale = Number(f.scale);
      /* ★★ 小数位同步（第 5 区 FIELD_DECIMALS / telemetry_user.json 的 decimals）。
         这是唯一决定位数的地方 —— 与 scale 无关：
         改 scale 只改大小和量程，位数纹丝不动；改位数也不碰 scale。 */
      if (typeof f.dec === 'number') SIGS[id].fmt = clampDec(f.dec, SIGS[id].fmt);
    });
  });
  /* ★ 派生量跟随：accelMag = sqrt(lat²+lon²) 没有配置字段，单位/位数跟 lat/lon 走，
     否则合成那一路会一直用 defSig 的兜底位数，和后端下发的 dec 不一致。
     量程由 updateAccScale() 统一按 ACC_FS 重设（下面会调用它）。 */
  if (SIGS.accelLat && SIGS.accelMag){
    SIGS.accelMag.unit = SIGS.accelLat.unit || '';
    if (typeof SIGS.accelLat.fmt === 'number') SIGS.accelMag.fmt = SIGS.accelLat.fmt;
  }

  /* ---- 界面显示参数：曲线窗口/取样/帧率/默认曲线（也能在 exe 旁的
         telemetry_user.json 里改，不用重新打包） ---- */
  if (cfg.ui && typeof cfg.ui === 'object'){
    /* ★ 全量吸收，不再写白名单。
       以前这里只认 ['bufPoints','sampleMs','trendFps','domHz','sparkMs'] 五个键，
       结果第 5 区新加的键通通成了「改了没反应」的孤儿键。
       现在配置里有什么就接什么 —— 以后加显示参数只需改 telemetry_config.py。 */
    Object.keys(cfg.ui).forEach(function(k){
      if (k === 'defaultVisible') return;             /* 数组单独处理 */
      var v = cfg.ui[k];
      if (typeof v === 'number' && isFinite(v) && v > 0) UI_CFG[k] = v;
      else if (typeof v === 'boolean' || typeof v === 'string') UI_CFG[k] = v;
    });
    /* ★ bufPoints 单独处理（2026-10-16 质检 F2）：
       上面那条通用规则"只接受 >0 的有限数字"会把 0/负数**静默忽略** ——
       于是一份新配置写 bufPoints:0 之后，上一份配置的 20000 还留着，
       "配置明明改了但界面没反应"，很难排查。
       这里让配置值**总是生效**，再由 clampBuf 决定落点（0/负数/NaN -> 600）。 */
    if (typeof cfg.ui.bufPoints !== 'undefined') UI_CFG.bufPoints = clampBuf(cfg.ui.bufPoints);
    /* ★ 功率报警阈值：配置值**总是生效**（同样不吃上面"只接受 >0"的通用规则 ——
       用户若把阈值写成 0/负数，应该回退兜底 80 而不是静默沿用上一份配置）。
       唯一真源在 telemetry_config.py 第 5 区 UI_CFG["powerWarnKw"] /
       telemetry_user.json 的 "ui" 段；前端的 80 只是兜底。 */
    if (typeof cfg.ui.powerWarnKw !== 'undefined'){
      PWR_WARN_KW = cfg.ui.powerWarnKw;
      syncPwrScale();          /* 条形刻度 / 报警线位置 / SIGS 量程一起重算 */
    }
    /* HEX_MAX 是 UI_CFG.hexMax 的性能缓存，跟着一起刷新 */
    if (UI_CFG.hexMax > 0) HEX_MAX = UI_CFG.hexMax;
    if (Array.isArray(cfg.ui.defaultVisible) && !state._dvApplied){
      state._dvApplied = true;
      state.visible = new Set(cfg.ui.defaultVisible);
    }
    BUF = clampBuf(UI_CFG.bufPoints);      /* ★ 配置值不可信：夹 [10,20000]（与后端一致） */
    SAMPLE_MS = UI_CFG.sampleMs;
  }

  /* ---- 外部配置 telemetry_user.json：只弹一条提示说明「确实读到了外部文件」。
     （原来源状态条 / EXT 角标随量程编辑器一起移除，不再维护 CFG_USER 影子状态） ---- */
  if (cfg.userCfg){
    if (cfg.userCfg.error && typeof toast === 'function'){
      toast(cfg.userCfg.error);
    } else if (cfg.userCfg.loaded && !state._cfgToast && typeof toast === 'function'){
      state._cfgToast = true;
      toast('已加载外部配置 telemetry_user.json');
    }
  }

  /* ★ 第 5 区 GAUGE_RANGES 下发的显示量程：一键改掉所有写死的刻度 */
  var gr = cfg.gaugeRanges || {};
  if (typeof gr.ACCEL === 'number' && gr.ACCEL > 0) ACC_FS_SET = gr.ACCEL;
  if (typeof gr.SPEED === 'number' && gr.SPEED > 0) SPD_FS = gr.SPEED;
  Object.keys(GAUGE_SIGS).forEach(function(k){
    var s = SIGS[GAUGE_SIGS[k]];
    if (!s) return;
    var v = gr[k];
    /* 两种写法：单个数字 = 满量程 0~v；[下限, 上限] = 显式区间（有正负的量） */
    if (Array.isArray(v) && v.length === 2 && isFinite(Number(v[0])) && isFinite(Number(v[1]))){
      s.min = Number(v[0]); s.max = Number(v[1]);
      return;
    }
    var n = Number(v);
    if (n > 0) s.max = n;
  });
  updateAccScale();
  syncSpeedLabels();
  syncGaugeLabels();         /* ★ 刻度/单位后缀跟着一起刷，否则界面看着还是旧的 */
  trendLayer.sKey = '';     /* 量程可能变 -> 静态层失效 */

  if (!cfg.adc || !cfg.adc.length) return;
  ADC_NAMES  = cfg.adc.map(function(c){ return c.name; });
  ADC_COLORS = cfg.adc.map(function(c){ return c.color; });
  ADC_UNITS  = cfg.adc.map(function(c){ return c.unit || ''; });
  ADC_COUNT  = cfg.adc.length;

  /* 用新通道建/更新信号定义 */
  cfg.adc.forEach(function(c, i){
    var id = 'adc' + i;
    if (SIGS[id]){
      SIGS[id].label = c.short + ' ' + c.name;
      SIGS[id].color = c.color;
      SIGS[id].unit = c.unit || '';   /* ★ 单位也要更新，否则趋势面板永远显示兜底空串 */
      SIGS[id].min = c.min; SIGS[id].max = c.max;
      /* ★ 小数位也要更新：第 3 区 ADC_DECIMALS 改了才有反应，
           否则界面永远用 defSig 的兜底 fmt=0（整数），而 CSV 却是一位小数 —— 两边不一致 */
      if (typeof c.dec === 'number') SIGS[id].fmt = clampDec(c.dec, SIGS[id].fmt);
    } else {
      defSig(id, c.short + ' ' + c.name, c.unit, c.color, 'P0',
             (typeof c.dec === 'number' ? c.dec : 0), c.min, c.max);
      state.buf[id] = [];
      state.latest[id] = c.min || 0;
    }
  });
  /* 通道数变少时，清掉多余的信号与缓冲，避免幽灵曲线 */
  Object.keys(SIGS).forEach(function(k){
    if (k.indexOf('adc') !== 0) return;
    if (parseInt(k.slice(3), 10) >= ADC_COUNT){
      delete SIGS[k]; delete state.buf[k]; delete state.latest[k];
      state.visible['delete'](k);
    }
  });
  /* ★ 兜底清理 state.visible 里**已经不存在于 SIGS** 的 id。
     为什么必须有：drawTrend / trendBufLen 都会直接读 state.buf[id].length，
     悬空 id 会让整个主循环抛异常（曲线全停）。协议改动（accel → accel_lat/lon）
     或用户 telemetry_user.json 里留着旧通道名时都会产生这种悬空 id。
     与上面 ADC 的清理同源，只是这里覆盖所有信号、不只 adc*。 */
  Array.from(state.visible).forEach(function(id){
    if (!SIGS[id]) state.visible['delete'](id);
  });

  CSV_HEADER_LOCAL = cfg.csvHeader || buildCsvHeaderLocal();
  buildAdcCards();          /* 卡片按新名字重画 */
  buildChips();             /* 曲线通道开关同步 */
  /* ★ 「数据可视化」工作台也要跟着重建（需求3 / B12）。
     以前这里只重建了总览卡片与曲线 chips，**漏了 #visChannels**：
     init() 里三个都调，运行期收到 uiConfig 时却只调了两个，
     于是改了第 3 区通道名之后，可视化区左侧的「数据通道」还是旧名字。
     buildChips 只管 chipBar 与右侧数值面板，碰不到 vc-item。 */
  buildVisChannels();
  /* ② 已经拖进图表的信号：图例文字画在 canvas 上（drawVChart 里读 SIGS[id].label），
        下方的信号 chip 是 DOM。两者都得刷，否则图表里还显示旧名字。
        sKey/dirty 一并失效 —— 图例文字变了，缓存的静态层与新画布都要重来。 */
  vcharts.forEach(function(c){
    updateVChartSigs(c);
    c.layer.sKey = '';
    c.layer.dirty = true;
  });
  paintVCharts();           /* 通道名/配色变了要立刻反映到图表上（暂停时主循环不画） */
  refreshAccVec();          /* 量程/单位/标签变了 -> 矢量图一起刷（同一批离散入口） */
  refreshPalette();         /* task-27：主题色（canvas 用）跟着刷新 */
  refreshMini();            /* 量程/单位变了 -> 环形表刻度与迷你图一起刷 */
  trendLayer.sKey = '';     /* 量程可能变 -> 静态层失效 */
  trendDirty = true; domDirty = true;
}

function initWebChannel(){
  if (typeof QWebChannel === 'undefined'){ onNoBackend(); return; }
  if (!window.qt || !window.qt.webChannelTransport){
    setTimeout(initWebChannel, 200);
    return;
  }
  new QWebChannel(window.qt.webChannelTransport, function(channel){
    backend = channel.objects.backend;
    backend.dataReady.connect(onDataBatch);
    backend.rawReady.connect(onRaw);
    backend.statusChanged.connect(onStatus);
    backend.errorMsg.connect(onError);
    backend.portsReady.connect(function(json){
      var ports;
      try { ports = JSON.parse(json); } catch(e){ return; }
      var sel = $('portSelect');
      sel.innerHTML = '';
      ports.forEach(function(p){
        var o = document.createElement('option');
        o.value = p; o.textContent = p;
        sel.appendChild(o);
      });
      if (!state.connected && !state.demo)
        setNum($('connMeta'), ports.length ? ('未连接 · 检测到 ' + ports.length + ' 个端口') : '未连接 · 未检测到端口');
    });
    backend.saveDirReady.connect(function(d){
      saveDir = d || '';
      $('saveDirInput').value = saveDir;
      /* ★ D14（task-29）：后端给空目录时输入框是一片空白，用户不知道这里该是什么。
         只加 placeholder（不改 value / 不碰 id / 不影响"浏览…"与后端回填逻辑）。 */
      if (!saveDir){
        var sdi = $('saveDirInput');
        if (sdi && !sdi.placeholder) sdi.placeholder = '未选择 · 点「浏览…」或等连接后自动填入';
      }
    });
    backend.recordStateChanged.connect(function(on, path){
      state.recPath = String(path || '').split(/[\\/]/).pop();
      updateRecUI(!!on, path);
    });
    /* 录制进度：Python 每 500ms 推一次（行数 + mm:ss），让提示平滑跳动 */
    if (backend.recordProgress) backend.recordProgress.connect(onRecProgress);
    backend.recordSaved.connect(function(path, rows){
      if (rows > 0){
        state.logs.unshift({
          name:String(path).split(/[\\/]/).pop(),
          path:path, rows:rows,
          t0:new Date().toLocaleString('zh-CN')
        });
        renderLogs();
        toast('录制完成 · ' + rows.toLocaleString('en-US') + ' 行 · ' + String(path).split(/[\\/]/).pop(), 'ok', 5000);
      }
    });
    /* 数据回放状态：回放中 / 停止 / 完成 —— ★ 回放态的唯一判据 */
    if (backend.replayStateChanged){
      state._replayStateWired = true;
      backend.replayStateChanged.connect(onReplayState);
    }
    /* 回放进度：只更新滑块位置与「已播 / 总时长」标签 */
    if (backend.replayProgress) backend.replayProgress.connect(onReplayProgress);
    /* 回放暂停/继续（问题四）：后端 replayPausedChanged 是暂停态的唯一权威来源 */
    if (backend.replayPausedChanged){
      state._replayPausedWired = true;
      backend.replayPausedChanged.connect(onReplayPausedChanged);
    }
    /* 跳转回填（问题二）：seek 成功后会紧跟一包一窗历史采样 */
    if (backend.replayBackfill) backend.replayBackfill.connect(onReplayBackfill);
    /* 加载（解析）反馈：解析大 .bin 期间在 #replayPos 显示进度文案、滑块保持禁用。
       ★ 与回放态无关（回放态只看 replayStateChanged），所以单独接一个槽。 */
    if (backend.replayLoadingChanged) backend.replayLoadingChanged.connect(onReplayLoadingChanged);
    /* 遥测配置下发：同步配置文件里的通道名/单位/配色/CSV 表头 */
    if (backend.uiConfig) backend.uiConfig.connect(function(json){
      try { applyUiConfig(JSON.parse(json)); } catch(e){}
    });
    backend.saveResult.connect(function(msg){ toast(msg, 'ok', 4200); });
    /* 量程/单位在线编辑：后端写盘完成后回执 */
    if (backend.cfgSaved) backend.cfgSaved.connect(function(msg){ toast(msg, 'ok', 4500); });
    backend.getSaveDir();
    /* 主动拉一次配置（页面可能比信号早 loaded） */
    if (backend.getUiConfig) backend.getUiConfig();
  });
}

/* 无后端（浏览器直开 / 离屏自检）：保持空数据占位，
 * 不再自动进入演示——演示只能由用户在「设置」里手动开启 */
var _noBackendFired = false;
function onNoBackend(){
  if (_noBackendFired) return;
  _noBackendFired = true;
  $('demoMode').checked = false;
  setLive('off');
  updateDom();
}

/* ---------- 15. 演示模式开关（仅手动，无自动启动） ---------- */
function setDemo(on){
  if (on && state.connected){
    toast('已连接真实串口。请先断开连接，再开启演示模式。', 'warn');
    $('demoMode').checked = false;
    return;
  }
  state.demo = on;
  clearTelemetry();   /* 开/关演示都先清场：旧数据不残留 */
  if (on){ startSim(); setLive('demo'); }
  else { stopSim(); setLive('off'); updateDom(); }
}

/* ---------- 16. 事件绑定 ---------- */
/* role=button 的 pill（REC / 冻结）也要能键盘操作 */
function bindPillKey(el, fn){
  if (!el) return;
  el.addEventListener('click', fn);
  el.addEventListener('keydown', function(e){
    if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); fn(); }
  });
}

/* ★ 冻结（暂停）开关的**唯一写入口**：顶栏按钮与"开始回放时自动解冻"共用。
   以前这段"改 state.frozen + 同步按钮文字/角标/aria"的逻辑直接写在按钮回调里，
   第二处（回放自动解冻）只能再抄一遍 —— 两份迟早不一致（按钮写着"暂停"而缓冲已冻结）。
   silent=true：只同步状态与 UI，不弹"已暂停/已恢复实时"（调用方要给更贴切的说明）。 */
function setFrozen(v, silent){
  var nv = !!v;
  var changed = (state.frozen !== nv);
  /* ★ 状态没变就直接返回，**副作用一个都不能先执行**：
     下面会清用户框选（state.sel/hideSelStats）——以前这几行写在提前返回之前，
     等于"调一次 setFrozen 就把框选清掉"，对"唯一写入口"来说是个潜伏坑
     （当前两个调用点都保证有变化，所以不可达；但第三个调用点迟早会踩）。 */
  if (!changed) return false;
  state.frozen = nv;
  setNum($('freezeText'), nv ? '继续' : '暂停');
  var fb = $('freezeBtn');
  if (fb) fb.setAttribute('aria-pressed', nv ? 'true' : 'false');
  var pt = $('pauseTag');
  if (pt) pt.style.display = nv ? 'block' : 'none';
  state.sel = null;            /* 暂停/恢复都清掉旧框选 */
  hideSelStats();
  trendDirty = true;
  if (nv){
    /* 暂停：立即重绘一次（显示「已暂停」角标），此后数据缓冲与 DOM 都冻结 */
    paintLayer(trendLayer, drawTrend);
    if (!silent) toast('已暂停 · 拖动曲线可框选区间统计', 'ok');
  } else if (!silent){
    toast('已恢复实时', 'ok');
  }
  return true;
}

function bind(){
  /* 顶栏 */
  bindPillKey($('freezeBtn'), function(){ setFrozen(!state.frozen); });
  $('normBtn').addEventListener('click', function(){
    /* 三档循环：归一化 -> 自适应 -> 分组分带 -> 归一化 */
    var cur = RANGE_MODES.indexOf(state.scaleMode);
    state.scaleMode = RANGE_MODES[(cur < 0 ? -1 : cur) + 1 >= RANGE_MODES.length
                                  ? 0 : (cur < 0 ? 0 : cur + 1)];
    this.className = 'mode-btn' + (state.scaleMode === 'norm' ? ' on' : '');
    this.textContent = RANGE_LABELS[state.scaleMode] || '归一化量程';
    this.setAttribute('aria-pressed', state.scaleMode === 'norm' ? 'true' : 'false');
    trendLayer.sKey = '';
    trendDirty = true;
  });
  $('addChartBtn').addEventListener('click', function(){ addChart(); });
  /* 加速度矢量图：悬停画布就重画一次（脏标记 + 立刻画）。
     为什么需要：暂停时主循环的 updateDom 不跑，若只靠 20Hz 那条路径，
     暂停中鼠标互动就不会刷新 —— 这里给矢量图自己一个"鼠标互动入口"，
     而且只在指针位于该画布上时触发，不产生常驻开销。 */
  var accVecCv = $('accVecCanvas');
  if (accVecCv){
    accVecCv.addEventListener('mousemove', function(){ refreshAccVec(); });
    accVecCv.addEventListener('mouseenter', function(){ refreshAccVec(); });
  }

  /* ---- 趋势区交互：十字光标 + 拖拽框选区间统计 ---- */
  var chartBox = document.querySelector('.trend-wrap .chart-box');
  if (chartBox){
    var selDrag = null;   /* 框选拖拽状态 {frac0, frac1} */
    chartBox.addEventListener('mousedown', function(e){
      if (e.button !== 0) return;
      var len = trendBufLen();
      if (len < 2) return;
      var r = chartBox.getBoundingClientRect();
      var frac = trendXToFrac(e.clientX - r.left, chartBox.clientWidth);
      selDrag = {frac0: frac, frac1: frac};
      state.sel = {i0: trendFracToIdx(frac, len), i1: trendFracToIdx(frac, len)};
      hideSelStats();
      drawCursorLayer();
    });
    chartBox.addEventListener('mousemove', function(e){
      var r = chartBox.getBoundingClientRect();
      var frac = trendXToFrac(e.clientX - r.left, chartBox.clientWidth);
      if (selDrag){
        /* 拖拽框选中：更新选区并重画，不干扰十字光标 */
        selDrag.frac1 = frac;
        var len = trendBufLen();
        var a = Math.min(selDrag.frac0, selDrag.frac1);
        var b = Math.max(selDrag.frac0, selDrag.frac1);
        state.sel = {i0: trendFracToIdx(a, len), i1: trendFracToIdx(b, len)};
        drawCursorLayer();
        return;
      }
      /* 十字光标（悬停读数） */
      if (!state.cur || !state.cur.active ||
          Math.abs(state.cur.frac - frac) > 0.0005){
        state.cur = {frac:frac, active:true};
        drawCursorLayer();
        if (zoneVisible('zoneTrend')) updateSigPanel();   /* 面板列跟随游标 */
      }
    });
    chartBox.addEventListener('mouseup', function(e){
      if (!selDrag) return;
      selDrag = null;
      if (state.sel && state.sel.i1 - state.sel.i0 >= 1){
        showSelStats(state.sel);            /* 框选有效 → 计算并显示区间统计 */
      } else {
        state.sel = null;                   /* 只点了一下，不算框选 */
        hideSelStats();
      }
      drawCursorLayer();
    });
    chartBox.addEventListener('mouseleave', function(){
      if (state.cur){
        state.cur = null;
        drawCursorLayer();
        if (zoneVisible('zoneTrend')) updateSigPanel();   /* 恢复实时值 */
      }
      /* 拖拽中途出图 → 视作框选结束 */
      if (selDrag){
        selDrag = null;
        if (state.sel && state.sel.i1 - state.sel.i0 >= 1){
          showSelStats(state.sel);
        } else {
          state.sel = null;
          hideSelStats();
        }
        drawCursorLayer();
      }
    });
  }
  var panel = $('sigPanel');
  if (panel){
    panel.addEventListener('mouseover', function(e){
      var row = e.target.closest('.sig-row');
      var id = row ? row.dataset.id : null;
      if (state.hl !== id){
        state.hl = id;
        syncPanelHL();
        trendDirty = true;      /* 高亮/淡出变化 -> 重画趋势 */
      }
    });
    panel.addEventListener('mouseleave', function(){
      if (state.hl){
        state.hl = null;
        syncPanelHL();
        trendDirty = true;
      }
    });
    panel.addEventListener('click', function(e){
      var row = e.target.closest('.sig-row');
      if (row) toggleChip(row.dataset.id);
    });
    panel.addEventListener('keydown', function(e){
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var row = e.target.closest('.sig-row');
      if (row){ e.preventDefault(); toggleChip(row.dataset.id); }
    });
  }

  bindPillKey($('recPill'), function(){ toggleRecord(!state.recording); });

  /* 串口区 */
  $('connectBtn').addEventListener('click', doConnect);
  $('refreshPortsBtn').addEventListener('click', function(){
    if (backend) backend.refreshPorts();
    else if (!$('portSelect').children.length) toast('浏览器模式无法枚举串口，请从打包程序打开。', 'warn');
  });
  $('sendBtn').addEventListener('click', function(){
    var v = $('cmdInput').value.trim();
    if (!v) return;
    if (!backend || state.demo){ toast('未连接真实串口，无法发送。', 'warn'); return; }
    backend.send(v, $('cmdMode').value);
    pushHexLines('>> ' + v, true);
    $('cmdInput').value = '';
  });
  $('cmdInput').addEventListener('keydown', function(e){
    if (e.key === 'Enter') $('sendBtn').click();
  });

  /* 记录区 */
  $('recBtn').addEventListener('click', function(){ toggleRecord(!state.recording); });
  $('exportBtn').addEventListener('click', exportCsv);
  $('replayBtn').addEventListener('click', function(){
    if (state.replaying){
      if (backend) backend.stopReplay();
    } else {
      if (!backend){ toast('回放需要桌面程序支持（当前为浏览器模式）。', 'warn'); return; }
      if (state.demo){ toast('请先关闭演示模式再回放。', 'warn'); return; }
      backend.chooseReplayFile();
    }
  });
  /* 回放进度条：拖动 -> seekReplay(value/1000)。
     input 在拖动过程中连续触发（键盘操作也会触发），change 在松手/回车时触发，
     两者之间把 state.replayDragging 置位，让 100ms 一次的进度回包不去抢滑块。 */
  var replayRange = $('replayRange');
  if (replayRange){
    var lastLiveSeek = 0;
    replayRange.addEventListener('input', function(){
      state.replayDragging = true;
      var frac = (Number(replayRange.value) || 0) / 1000;
      var pos = $('replayPos');
      if (pos){
        /* 拖动中标签按滑块自身位置显示：总时长用后端给过的真值，不估算 */
        if (state.replayTotal > 0) setNum(pos, fmtReplayPos(frac * state.replayTotal, state.replayTotal));
        else setNum(pos, (frac * 100).toFixed(0) + '%');
      }
      /* ★ F1：拖动过程中只发 seekReplayLive —— 它只"跳转 + 报进度"，**不产生回填**（O(1)）。
         为什么绝不能在这里用 seekReplay：后端回填是 O(跳转位置)（从帧 0 扫一窗），
         真实 10 分钟录播（105k 帧）实测 frac0.5=180.7ms / frac1.0=357.3ms、中位数 119.7ms，
         拖动 30 次累计约 3.6 秒；1 小时录播外推 ≈2.1 秒/次 —— 每个 input 都发必然卡死。
         ⚠️ 后端还没有 seekReplayLive 时，这里**什么都不发**（只在松手时发一次全量 seek）。
            刻意不回退成"每个 input 都发 seekReplay"，那正是要修的缺陷本身。
         50ms 轻量节流：input 高频（每像素一次）时不必每次都跨进程；
         松手那一次由 endReplayDrag 保证发出，不会被节流吞掉。 */
      if (backend && backend.seekReplayLive){
        var nowT = performance.now();
        if (nowT - lastLiveSeek >= 50){ lastLiveSeek = nowT; backend.seekReplayLive(frac); }
      }
    });
    /* 松手：发一次"带回填"的权威 seek（清空 + 回填一窗），曲线才是跳转点的数据。
       用 replayDragging 当"本次是否真的拖过"的闸门 —— mouseup/touchend/blur 是全局监听，
       不能在随便一次鼠标松开时都发 seek。 */
    var endReplayDrag = function(){
      if (!state.replayDragging) return;
      state.replayDragging = false;
      if (backend && backend.seekReplay) backend.seekReplay((Number(replayRange.value) || 0) / 1000);
    };
    replayRange.addEventListener('change', endReplayDrag);
    /* 安全网：指针在滑块之外松开时 change 不一定会来 */
    window.addEventListener('mouseup', endReplayDrag);
    window.addEventListener('touchend', endReplayDrag);
    window.addEventListener('blur', endReplayDrag);
  }
  /* 回放暂停/继续按钮（问题四，底栏进度条旁）：
     目标是"回放推进"的暂停，与顶栏 #freezeBtn（冻结缓冲供分析）无关。
     ★ 连点处理：以"在途请求 replayPausedWant"为准算下一个目标值 ——
       否则连点两下会连发两次同一个值（后端幂等但表现为"点了没反应"）。
       按下后立刻本地反馈，随后由 replayPausedChanged 回包纠正/确认。 */
  var replayPauseBtn = $('replayPauseBtn');
  if (replayPauseBtn){
    replayPauseBtn.addEventListener('click', function(){
      if (!state.replaying) return;                       /* 不在回放中：按钮本就禁用 */
      var cur = (state.replayPausedWant == null) ? state.replayPaused : state.replayPausedWant;
      var next = !cur;
      state.replayPausedWant = next;
      if (backend && backend.setReplayPaused) backend.setReplayPaused(next);
      /* 立刻本地反馈；后端有 replayPausedChanged 时以它的回包为最终权威 */
      setReplayPausedUI(next);
    });
  }
  /* 峰值清零：卡片上那个"峰值 xx kW"是可点的（工程师最关心"这一跑最大拉到多少"，
     看完想重新量一段就点它）。按钮有 title 提示，键盘也能触发。 */
  var pwrPeakBtn = $('pwrPeak');
  if (pwrPeakBtn){
    pwrPeakBtn.addEventListener('click', function(){ resetPwrPeak(); });
  }
  $('browseDirBtn').addEventListener('click', function(){
    if (backend) backend.chooseSaveDir();
    else toast('路径选择需要桌面程序支持（当前为浏览器模式）。', 'warn');
  });
  $('openDirBtn').addEventListener('click', function(){
    if (backend && saveDir) backend.showInExplorer(saveDir);
    else toast('尚未设置保存目录。', 'warn');
  });
  $('logList').addEventListener('click', function(e){
    var btn = e.target.closest('[data-idx]');
    if (!btn) return;
    var log = state.logs[+btn.dataset.idx];
    if (log && backend && log.path) backend.showInExplorer(log.path);
  });

  /* 设置区 */
  $('demoMode').addEventListener('change', function(){ setDemo(this.checked); });
  $('bufInput').addEventListener('change', function(){
    /* ★ 设置区也是外部输入：保留原来的 100 下限，同时补上上限（否则手输 1e8 同样会无限增长） */
    BUF = clampBuf(Math.max(100, parseInt(this.value, 10) || 600));
    trendLayer.sKey = '';   /* 时间窗变了 -> 刻度重画 */
    trendDirty = true;
  });

  /* 侧栏导航（标签页切换：点击只显示当前功能区，总览显示全部） */
  (function(){
    var items = document.querySelectorAll('.rail-item');
    /* 顺序：总览 / 可视化 / 串口 */
    var map = [null, 'zoneVisual', 'zoneSerial'];
    var allZones = ['zoneVisual', 'zoneP0', 'zoneP1', 'zoneP2', 'zoneTrend', 'zoneSerial', 'zoneRecord', 'zoneSettings'];
    function showZone(zoneId){
      allZones.forEach(function(id){
        var el = $(id);
        if (!el) return;
        /* 总览：显示所有功能区，但「可视化」工作台不进总览 */
        var show = (zoneId === null) ? (id !== 'zoneVisual') : (id === zoneId);
        el.classList.toggle('hidden', !show);
      });
      /* 切换后回到顶部，避免残留滚动位置导致「显示不全」 */
      var st = $('stage');
      if (st) st.scrollTop = 0;
      /* 切换后 canvas 尺寸可能变化，标记脏让下次重绘重新同步 */
      trendLayer.dirty = true; trendLayer.sKey = '';
      vcharts.forEach(function(c){ c.layer.dirty = true; c.layer.sKey = ''; });
      /* ★ 立即补画一次：暂停时主循环不画，切到「数据可视化」会看到空白图表 */
      paintVCharts();
      refreshAccVec();      /* 加速度矢量图同一批离散入口（含暂停时切区） */
  refreshMini();        /* task-27：环形表/双轴/散点同一批离散入口 */
    }
    Array.prototype.forEach.call(items, function(it, i){
      it.addEventListener('click', function(){
        Array.prototype.forEach.call(items, function(x){
          x.classList.remove('active');
          x.removeAttribute('aria-current');
        });
        it.classList.add('active');
        it.setAttribute('aria-current', 'true');
        showZone(map[i]);
      });
    });
  })();

  window.addEventListener('resize', function(){ trendLayer.dirty = true; trendLayer.sKey = ''; });
}

/* ---------- 17. 时钟 + 兜底保活 ---------- */
setInterval(function(){
  var d = new Date();
  var p = function(n){ return String(n).padStart(2,'0'); };
  setNum($('clock'), p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds()));
  if (document.hidden) return;   /* 页面不可见：rAF 已停，兜底也不再绘制，省 CPU */
  if (domDirty && performance.now() - lastDom > 500) updateDom();
  if (performance.now() - lastRaf > 800 && !state.frozen && trendDirty){
    paintLayer(trendLayer, drawTrend);
    state._frames++;
    trendDirty = false;
  }
}, 500);

/* ---------- 18. 启动 ---------- */
function init(){
  buildAdcCards();
  buildChips();
  buildVisChannels();   /* 数据可视化工作台的通道列表 */
  /* 可视化工作台不进「总览」：初始即隐藏，只有点「可视化」导航才显示 */
  var _zv = $('zoneVisual');
  if (_zv) _zv.classList.add('hidden');
  buildRev();
  bind();
  /* ★ 可见区节流 + 滚动降频的初始化（2026-10-06 修）
     ⚠️ 这个函数以前**从来没被调用过** —— 后果有两个：
       ① IntersectionObserver 没建立，zoneVis 一直是空对象，
          zoneVisible() 对每个区都返回 true → 「只刷新看得见的分区」形同虚设，
          滚出视口的 P0/P1/P2/趋势区照样每 50ms 全量更新；
       ② #stage 的 scroll 回调没绑定 → body.scrolling 类与 scrollUntil
          从不更新 → 滚动期间降频与「滚动时暂停脉冲动画」全部失效。
     这就是「改了很多却还是卡」的根因：优化写好了，没接上。 */
  initZoneVisibility();
  initZoneResize();   /* 功能区拖拽调高度 */
  renderLogs();
  setLive('off');
  /* （原 initRangeEditor()：量程/单位在线编辑器，2026-10-06 已整块移除） */
  updateDom();   /* 首帧即渲染空数据占位（"—"），不等主循环 */
  initWebChannel();
  /* 不再自动进入演示模式：无后端时保持空态，由用户手动开启演示 */
  addChart();   /* 数据可视化工作台初始给一个空图表 */
  requestAnimationFrame(render);
}
document.addEventListener('DOMContentLoaded', init);
