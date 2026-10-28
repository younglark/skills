#!/usr/bin/env node
/**
 * 严格对拍：app 内 proto.cjs 的运行时描述符  vs  我们从还原的 .proto 用 protoc-gen-es 生成的代码。
 *
 * 比对维度（全部按 wire 语义）：
 *   - message: 字段号 -> (proto 名, json 名, kind, repeated, optional, packed, long 表示, 引用类型全名 / scalar 编号)
 *   - enum   : 值号 -> 值名
 *   - service: 方法名 -> (输入全名, 输出全名, 流类型)
 *   - 额外   : typeName 集合差集（并自动识别因打破循环引用而"搬家"的类型）
 */
import { createRequire } from "node:module";
import { readdirSync, readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
// usage: node compare.mjs <proto.cjs> [protoDir]
const PROTO_CJS = process.argv[2] ?? process.env.PROTO_CJS;
if (!PROTO_CJS) {
  console.error("usage: node compare.mjs <path/to/proto.cjs> [protoDir]");
  process.exit(2);
}
const PROTO_DIR = process.argv[3] ?? process.env.PROTO_DIR ??
  new URL("../proto/", import.meta.url).pathname;
const GEN_DIR = new URL("./src/js/", import.meta.url);

// ---------------------------------------------------------------- 收集原始侧
function collectOriginal() {
  const m = require(PROTO_CJS);
  const messages = new Map();  // typeName -> MessageType
  const enums = new Map();     // typeName -> EnumType
  const services = new Map();  // typeName -> ServiceType

  const visitMsg = (t) => {
    if (!t || !t.typeName || messages.has(t.typeName)) return;
    messages.set(t.typeName, t);
    for (const f of t.fields.list()) {
      if (f.kind === "message") visitMsg(f.T);
      else if (f.kind === "enum" && f.T?.values) visitEnum(f.T);
      else if (f.kind === "map" && f.T?.V?.kind === "message") visitMsg(f.T.V.T);
      else if (f.kind === "map" && f.T?.V?.kind === "enum" && f.T.V.T?.values) visitEnum(f.T.V.T);
    }
  };
  const visitEnum = (t) => {
    if (!t || !t.typeName || enums.has(t.typeName)) return;
    enums.set(t.typeName, t);
  };

  for (const [, v] of Object.entries(m)) {
    if (v == null || (typeof v !== "object" && typeof v !== "function")) continue;
    if (typeof v.typeName === "string") {
      if (v.methods) services.set(v.typeName, v);
      else if (v.fields) visitMsg(v);
      else if (v.values) visitEnum(v);
    }
  }
  // 从 service 输入/输出再兜一轮
  for (const svc of services.values()) {
    for (const mm of Object.values(svc.methods)) {
      if (mm.I?.typeName) visitMsg(mm.I);
      if (mm.O?.typeName) visitMsg(mm.O);
    }
  }
  return { messages, enums, services };
}

// ---------------------------------------------------------------- 收集生成侧
async function collectGenerated() {
  const messages = new Map(), enums = new Map(), services = new Map();
  for (const f of readdirSync(GEN_DIR).filter((x) => x.endsWith("_pb.js"))) {
    const file = path.join(GEN_DIR.pathname, f);
    const mod = await import(pathToFileURL(file).href);
    for (const [, v] of Object.entries(mod)) {
      if (v != null && typeof v === "function" && v.fields) messages.set(v.typeName, v);
    }
  }
  // enum: makeEnum() 运行时对象是"短名双向映射"，真实 value name 只在源码里，故读源码
  for (const f of readdirSync(GEN_DIR).filter((x) => x.endsWith("_pb.js"))) {
    const src = readFileSync(path.join(GEN_DIR.pathname, f), "utf8");
    for (const m of src.matchAll(/proto3\.makeEnum\(\s*"([^"]+)",\s*\[([\s\S]*?)\]\s*,?\s*\)/g)) {
      const vals = [];
      for (const vm of m[2].matchAll(/\{no: (-?\d+), name: "([^"]+)"/g))
        vals.push({ no: Number(vm[1]), name: vm[2] });
      enums.set(m[1], vals);
    }
  }
  for (const f of readdirSync(GEN_DIR).filter((x) => x.endsWith("_connect.js"))) {
    const mod = await import(pathToFileURL(path.join(GEN_DIR.pathname ?? new URL(GEN_DIR).pathname, f)).href);
    for (const [, v] of Object.entries(mod)) {
      if (v && (typeof v === "object" || typeof v === "function") &&
          typeof v.typeName === "string" && v.methods) services.set(v.typeName, v);
    }
  }
  return { messages, enums, services };
}

// ---------------------------------------------------------------- 归一化
function normField(f) {
  const o = {
    no: f.no, name: f.name, json: f.jsonName,
    kind: f.kind, repeated: !!f.repeated, opt: !!f.opt,
    packed: !!f.packed, long: f.L ?? 0, delimited: !!f.delimited,
  };
  if (f.kind === "scalar") o.T = f.T;
  else if (f.kind === "message" || f.kind === "enum") o.T = f.T?.typeName ?? null;
  else if (f.kind === "map") {
    // map 的 key/value 平铺在 field 上：f.K 是 ScalarType，f.V 是 {kind, T}
    o.T = { K: f.K, V: f.V?.kind === "scalar" ? `s${f.V.T}` : f.V?.T?.typeName ?? null };
  }
  return o;
}
function normMessage(t) {
  const out = new Map();
  for (const f of t.fields.list()) out.set(f.no, normField(f));
  return out;
}
function normEnum(t) {
  const out = new Map();
  if (Array.isArray(t)) { for (const v of t) out.set(v.no, v.name); return out; }
  if (Array.isArray(t.values)) { for (const v of t.values) out.set(v.no, v.name); return out; }
  // makeEnum() 双向映射: {"0":"NAME", NAME:0, ...}
  for (const [k, v] of Object.entries(t)) if (/^\d+$/.test(k)) out.set(Number(k), v);
  return out;
}
function normService(t) {
  const out = new Map();
  for (const [name, mm] of Object.entries(t.methods))
    out.set(name, { I: mm.I?.typeName ?? null, O: mm.O?.typeName ?? null, kind: mm.kind });
  return out;
}
const short = (n) => n.split(".").slice(2).join(".") || n;   // 去掉包前缀后的相对名

// ---------------------------------------------------------------- 主流程
const A = collectOriginal();
const B = await collectGenerated();
console.log(`原始侧 : ${A.messages.size} messages / ${A.enums.size} enums / ${A.services.size} services`);
console.log(`生成侧 : ${B.messages.size} messages / ${B.enums.size} enums / ${B.services.size} services\n`);

// --- typeName 差集 ---
const onlyA_m = [...A.messages.keys()].filter((k) => !B.messages.has(k));
const onlyB_m = [...B.messages.keys()].filter((k) => !A.messages.has(k));
const onlyA_e = [...A.enums.keys()].filter((k) => !B.enums.has(k));
const onlyB_e = [...B.enums.keys()].filter((k) => !A.enums.has(k));

// 自动识别"搬家"：短名相同、包不同
function matchMoved(onlyA, onlyB, storeB) {
  const moved = [], restA = [], restB = [...onlyB];
  const bByShort = new Map();
  for (const b of onlyB) bByShort.set(short(b), b);
  for (const a of onlyA) {
    const b = bByShort.get(short(a));
    if (b && !storeB.get(b).__claimed) {
      storeB.get(b).__claimed = true;
      moved.push([a, b]);
      restB.splice(restB.indexOf(b), 1);
    } else restA.push(a);
  }
  return { moved, restA, restB };
}
const mv = matchMoved(onlyA_m, onlyB_m, B.messages);
const mvE = matchMoved(onlyA_e, onlyB_e, B.enums);

console.log(`=== message typeName 差集 ===`);
console.log(`  仅原始侧: ${mv.restA.length}   仅生成侧: ${mv.restB.length}   疑似搬家: ${mv.moved.length}`);
if (mv.moved.length) console.log("  搬家对:", mv.moved.slice(0, 20).map(([a, b]) => `${a} -> ${b}`).join("\n           "));
if (mv.restA.length) console.log("  仅原始:", mv.restA.slice(0, 25));
if (mv.restB.length) console.log("  仅生成:", mv.restB.slice(0, 25));
console.log(`\n=== enum typeName 差集 ===`);
console.log(`  仅原始侧: ${mvE.restA.length}   仅生成侧: ${mvE.restB.length}   疑似搬家: ${mvE.moved.length}`);
if (mvE.restA.length) console.log("  仅原始:", mvE.restA.slice(0, 25));
if (mvE.restB.length) console.log("  仅生成:", mvE.restB.slice(0, 25));

// --- 逐消息比对 ---
const pairs = [];
for (const [name, ta] of A.messages) {
  let tb = B.messages.get(name);
  if (!tb) { const hit = mv.moved.find(([a]) => a === name); if (hit) tb = B.messages.get(hit[1]); }
  if (tb) pairs.push([name, ta, tb]);
}

let okMsgs = 0, badMsgs = 0;
const issues = [];
for (const [name, ta, tb] of pairs) {
  const fa = normMessage(ta), fb = normMessage(tb);
  const diffs = [];
  for (const [no, x] of fa) {
    const y = fb.get(no);
    if (!y) { diffs.push(`字段 ${no} (${x.name}) 生成侧缺失`); continue; }
    for (const k of ["name", "json", "kind", "repeated", "opt", "packed", "long", "delimited"]) {
      if (JSON.stringify(x[k]) !== JSON.stringify(y[k]))
        diffs.push(`字段 ${no} (${x.name}).${k}: 原始=${JSON.stringify(x[k])} 生成=${JSON.stringify(y[k])}`);
    }
    const xa = JSON.stringify(x.T), xb = JSON.stringify(y.T);
    if (xa !== xb) {
      // 引用的类型自身搬家了也算一致
      const reloc = (s) => {
          const hit = mv.moved.find(([a]) => a === s) || mvE.moved.find(([a]) => a === s);
        return hit ? hit[1] : s;
      };
      const fix = (o) => typeof o === "string" ? reloc(o)
        : (o && o.V ? { K: o.K, V: reloc(o.V) } : o);
      if (JSON.stringify(fix(x.T)) !== JSON.stringify(fix(y.T)))
        diffs.push(`字段 ${no} (${x.name}).T: 原始=${xa} 生成=${xb}`);
    }
  }
  for (const [no, y] of fb) if (!fa.has(no)) diffs.push(`字段 ${no} (${y.name}) 原始侧缺失`);

  if (diffs.length === 0) okMsgs++;
  else { badMsgs++; issues.push({ name, diffs }); }
}
console.log(`\n=== message 字段比对 ===`);
console.log(`  完全一致: ${okMsgs} / ${pairs.length}   有差异: ${badMsgs}`);
for (const it of issues.slice(0, 12)) {
  console.log(`\n  ✗ ${it.name}`);
  for (const d of it.diffs.slice(0, 6)) console.log(`     ${d}`);
  if (it.diffs.length > 6) console.log(`     … 共 ${it.diffs.length} 处`);
}

// --- enum 比对 ---
let okE = 0; const badE = [];
for (const [name, ta] of A.enums) {
  let tb = B.enums.get(name);
  if (!tb) { const hit = mvE.moved.find(([a]) => a === name); if (hit) tb = B.enums.get(hit[1]); }
  if (!tb) continue;
  const va = normEnum(ta), vb = normEnum(tb);
  const d = [];
  for (const [no, n] of va) if (vb.get(no) !== n) d.push(`值 ${no}: 原始=${n} 生成=${vb.get(no)}`);
  for (const [no, n] of vb) if (!va.has(no)) d.push(`值 ${no} (${n}) 原始侧缺失`);
  if (d.length === 0) okE++; else badE.push({ name, d });
}
console.log(`\n=== enum 值比对 ===`);
console.log(`  完全一致: ${okE} / ${A.enums.size}   有差异: ${badE.length}`);
for (const it of badE.slice(0, 10)) {
  console.log(`  ✗ ${it.name}`);
  for (const d of it.d.slice(0, 5)) console.log(`     ${d}`);
}

// --- service 比对 ---
// 注意：proto.cjs 运行时导出的是"裁剪版" service（如 AgentStoreService 只暴露 6/24 个方法），
// 因此以源码里的完整 ServiceType 定义（snapshot.json）为 ground truth。
const SNAP = JSON.parse(readFileSync(path.join(PROTO_DIR, "snapshot.json"), "utf8"));
const KIND = { Unary: 0, ServerStreaming: 1, ClientStreaming: 2, BidiStreaming: 3, BiDiStreaming: 3 };
const camel = (n) => n[0].toLowerCase() + n.slice(1);
console.log(`\n=== service 方法比对（源码完整定义 vs 生成代码）===`);
let okS = 0, totS = 0;
for (const [name, meths] of Object.entries(SNAP.services)) {
  const tb = B.services.get(name);
  if (!tb) { console.log(`  ✗ ${name} 生成侧不存在`); continue; }
  const sb = normService(tb);
  const seen = new Set();
  const d = [];
  for (const mt of meths) {
    totS++;
    const mn = camel(mt.name); seen.add(mn);
    const y = sb.get(mn);
    if (!y) { d.push(`方法 ${mn} 生成侧缺失`); continue; }
    if (mt.I !== y.I) d.push(`方法 ${mn}.I: ${mt.I} vs ${y.I}`);
    if (mt.O !== y.O) d.push(`方法 ${mn}.O: ${mt.O} vs ${y.O}`);
    if (KIND[mt.kind] !== y.kind) d.push(`方法 ${mn}.kind: ${mt.kind}(${KIND[mt.kind]}) vs ${y.kind}`);
  }
  for (const [mn] of sb) if (!seen.has(mn)) { totS++; d.push(`方法 ${mn} 原始侧缺失`); }
  if (d.length === 0) { okS++; console.log(`  ✓ ${name}: ${meths.length} 方法全部一致`); }
  else { console.log(`  ✗ ${name} (${meths.length} 方法)`); for (const x of d.slice(0, 8)) console.log(`     ${x}`); }
}
console.log(`  合计方法: ${totS}`);

// --- 不可达类型补验：从 proto.cjs 源码抽紧凑描述符，与生成侧逐字段比对 ---
// 运行时 BFS 只能覆盖"被导出/被引用"的类型；源码里还定义了一批未被触达的类型。
// 对它们改用静态紧凑描述符验证（no / proto 名 / 标量编号 / 是否引用类型）。
function staticDescriptors() {
  const src = require("node:fs").readFileSync(PROTO_CJS, "utf8");
  const out = new Map();
  for (const m of src.matchAll(/"([A-Za-z_][A-Za-z0-9_.]*)\|([^"]*)"/g)) {
    const [, name, rest] = m;
    if (!out.has(name)) out.set(name, rest);
  }
  return out;
}
function normCompact(rest) {
  const out = new Map();
  for (const seg of rest.split("|")) {
    const t = seg.trim().split(/\s+/);
    if (t.length < 3) continue;
    const no = Number(t[0]), nm = t[1], ty = t[2];
    out.set(no, { name: nm, scalar: /^\d+$/.test(ty) ? Number(ty) : null,
                  ref: ty.startsWith("#"), map: ty.includes(",") });
  }
  return out;
}
const staticDesc = staticDescriptors();
const unreach = mv.restB.filter((b) => !B.messages.has(b) || true);
let uOk = 0, uNo = 0; const uBad = [];
for (const b of unreach) {
  const t = B.messages.get(b); if (!t) continue;
  const leaf = b.split(".").pop();
  let rest = staticDesc.get(leaf);
  if (rest === undefined) { uNo++; continue; }
  const sa = normCompact(rest), fb = normMessage(t);
  const d = [];
  for (const [no, x] of sa) {
    const y = fb.get(no);
    if (!y) { d.push(`字段 ${no} (${x.name}) 生成侧缺失`); continue; }
    if (x.name !== y.name) d.push(`字段 ${no} 名: 源码=${x.name} 生成=${y.name}`);
    if (x.map) { if (y.kind !== "map") d.push(`字段 ${no} 类型: 源码=map 生成=${y.kind}`); }
    else if (x.ref) { if (y.kind !== "message" && y.kind !== "enum") d.push(`字段 ${no} 类型: 源码=引用 生成=${y.kind}`); }
    else if (y.kind !== "scalar") d.push(`字段 ${no} 类型: 源码=标量${x.scalar} 生成=${y.kind}`);
    else if (x.scalar !== null && x.scalar !== y.T) d.push(`字段 ${no} 标量: 源码=${x.scalar} 生成=${y.T}`);
  }
  for (const [no, y] of fb) if (!sa.has(no)) d.push(`字段 ${no} (${y.name}) 源码缺失`);
  if (!d.length) uOk++; else uBad.push({ name: b, d });
}
console.log(`\n=== 不可达类型静态补验（源码紧凑描述符 vs 生成侧） ===`);
console.log(`  可验证并一致: ${uOk}   源码中查无此名: ${uNo}   有差异: ${uBad.length}`);
for (const it of uBad.slice(0, 8)) {
  console.log(`  ✗ ${it.name}`);
  for (const d of it.d.slice(0, 5)) console.log(`     ${d}`);
}

const total = badMsgs + badE.length + (A.services.size - okS) + uBad.length;
console.log(`\n=== 结论 ===`);
console.log(total === 0
  ? `✅ 严格一致：${okMsgs} messages / ${okE} enums / ${okS} services（含 ${totS} 个方法）逐字段比对无差异`
  : `❌ 存在差异：${badMsgs} messages, ${badE.length} enums, ${A.services.size - okS} services`);
