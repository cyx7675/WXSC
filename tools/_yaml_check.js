// _yaml_check.js —— profile patch 的严格校验：YAML 合法性 + 「上位机开发」预设有无问题 + 与 standard 的能力对齐
// 用法: node tools/_yaml_check.js      （退出码 = 问题数）
// 为什么需要自定义 schema：patch 里合法使用 `!!js <expr>`，js-yaml 默认 schema 不认识该标签会直接报错。
// 为什么直接读 asar：要拿出厂 standard 预设的插件清单做"能力不减"的机器证明。
const fs = require('fs');
const path = require('path');

const PROFILE = 'C:\\Users\\Administrator\\.dsh\\profiles\\desktop';
const PATCH = path.join(PROFILE, 'cordis.patch.yml');
const ASAR = 'F:\\deepseek harness\\resources\\app.asar';
const PRESET_ID = 'preset-host-development';
const STD_IN_ASAR = 'dsh/node_modules/@deepseek-ai/dsh-web-app/presets/standard.patch.yml';

const yaml = require(path.join(PROFILE, 'node_modules', 'js-yaml'));
const jsType = new yaml.Type('tag:yaml.org,2002:js', {
  kind: 'scalar',
  construct: (d) => ({ __jsExpr: String(d) }),
  predicate: () => true,
  represent: (d) => (d && d.__jsExpr ? d.__jsExpr : ''),
});
const SCHEMA = yaml.DEFAULT_SCHEMA.extend([jsType]);

let problems = 0;
const ok = (label, cond, extra = '') => {
  console.log(`   ${cond ? '✅' : '❌'} ${label}${extra ? '  ' + extra : ''}`);
  if (!cond) problems++;
};

// ── asar 读取（极小实现：JSON 头 + 顺序数据段）──────────────────────────────
// 实测本机 app.asar 的布局：0..3=4 / 4..7=h2 / 8..11=jsz ；JSON 从 **16** 开始、长度为 **jsz-4**
// （数据段起点 = 8 + h2）。少减这 4 字节会让 JSON.parse 报 "Unexpected non-whitespace character"。
function asarRead(innerPath) {
  const fd = fs.openSync(ASAR, 'r');
  const head = Buffer.alloc(16);
  fs.readSync(fd, head, 0, 16, 0);
  const h2 = head.readUInt32LE(4);
  const jsz = head.readUInt32LE(8);
  const jsonLen = jsz - 4;
  const buf = Buffer.alloc(jsonLen);
  fs.readSync(fd, buf, 0, jsonLen, 16);
  const hdr = JSON.parse(buf.toString('utf8'));
  const base = 8 + h2;
  const parts = innerPath.split('/').filter(Boolean);
  let node = hdr;
  for (const p of parts) {
    node = node.files && node.files[p];
    if (!node) { fs.closeSync(fd); return null; }
  }
  if (node.files) { fs.closeSync(fd); return null; }
  const data = Buffer.alloc(node.size);
  fs.readSync(fd, data, 0, node.size, base + Number(node.offset));
  fs.closeSync(fd);
  return data;
}

// ── 1. YAML 合法性 ─────────────────────────────────────────────────────────
console.log('【1】YAML 严格校验（js-yaml + !!js 自定义 schema）');
let doc;
try {
  doc = yaml.load(fs.readFileSync(PATCH, 'utf8'), { schema: SCHEMA });
  ok('patch 解析通过', true);
} catch (e) {
  ok('patch 解析通过', false, e.message);
  console.log(`\n结果: 存在 ${problems} 个问题`);
  process.exit(problems);
}
ok('顶层是数组', Array.isArray(doc), `条目数 ${doc.length}`);

// ── 2. 预设结构 ─────────────────────────────────────────────────────────────
// ★ patch 的两种形状（读自出厂 base patch 的头注释）：
//     · `- id: <已有行>`   —— 按 id 覆盖/禁用一个**已存在**的行（不能新建！）
//     · `- insert: [ … ]` —— 新增行（出厂预设就是用这个）
//   所以新增预设必须放在 insert 列表里；校验要能两者都找。
console.log('\n【2】预设条目结构');
const findRow = (arr, id) => {
  for (const e of arr || []) {
    if (!e) continue;
    if (e.id === id) return e;
    if (Array.isArray(e.insert)) {
      const hit = e.insert.find((r) => r && r.id === id);
      if (hit) return hit;
    }
  }
  return undefined;
};
const preset = findRow(doc, PRESET_ID);
ok(`找到 ${PRESET_ID}`, !!preset);
if (preset) {
  const c = preset.config || {};
  ok('name = @deepseek-ai/dsh-agent-preset', preset.name === '@deepseek-ai/dsh-agent-preset');
  ok('config.id 非空（会话保存的标识符）', typeof c.id === 'string' && c.id.length > 0, c.id);
  ok('config.name 非空（展示名）', typeof c.name === 'string' && c.name.length > 0, c.name);
  ok('config.description 非空', typeof c.description === 'string' && c.description.length > 0);
  ok('config.order 是数字', typeof c.order === 'number', String(c.order));
  ok('config.plugins 是非空数组', Array.isArray(c.plugins) && c.plugins.length > 0, `${(c.plugins || []).length} 项`);
  const bad = (c.plugins || []).filter((p) => !p || typeof p.name !== 'string');
  ok('所有子插件都有 name', bad.length === 0);
  const persona = (c.plugins || []).find((p) => p.name === '@deepseek-ai/dsh-persona');
  ok('存在 persona 行', !!persona);
  if (persona) {
    const len = (persona.config && persona.config.prefix || '').length;
    ok('persona.prefix 足够长（>200 字符）', len > 200, `${len} 字符`);
    ok('persona.suffix 已设置（否则会遮蔽全局后缀）', typeof persona.config.suffix === 'string');
  }
  // ★ 必须位于 insert 列表内 —— 否则它只会去"覆盖一条不存在的行"，永远不生效
  const inInsert = (doc || []).some((e) => Array.isArray(e.insert) && e.insert.some((r) => r && r.id === PRESET_ID));
  ok('★ 位于 insert 列表内（裸 -id: 无法新建条目）', inInsert);
}

// ── 3. 与出厂 standard 的能力对齐（"能力不减"的机器证明）────────────────────
console.log('\n【3】与 standard 预设的插件能力对比');
let stdNames = [];
try {
  const raw = asarRead(STD_IN_ASAR);
  if (!raw) throw new Error('asar 内找不到 standard.patch.yml');
  const std = yaml.load(raw.toString('utf8'), { schema: SCHEMA });
  const row = (std[0].insert || []).find((r) => r.id === 'preset-standard');
  stdNames = (row.config.plugins || []).map((p) => p.name);
} catch (e) {
  console.log('   ⚠ 无法读取 standard 预设:', e.message);
  problems++;
}
if (stdNames.length && preset) {
  const mine = (preset.config.plugins || []).map((p) => p.name);
  const setMine = new Set(mine);
  const setStd = new Set(stdNames);
  const missing = stdNames.filter((n) => !setMine.has(n));
  const added = mine.filter((n) => !setStd.has(n));
  ok('standard 的插件我全都有（能力不减）', missing.length === 0, missing.join(', '));
  console.log(`        standard ${stdNames.length} 项 / 本预设 ${mine.length} 项；本预设独有: ${added.length ? added.join(', ') : '（无，另外多了项目技能目录与 persona 内容）'}`);
}

console.log(problems === 0 ? '\n结果: 全部通过' : `\n结果: 存在 ${problems} 个问题`);
process.exit(problems === 0 ? 0 : problems);
