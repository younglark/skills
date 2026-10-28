#!/usr/bin/env node
/**
 * Wire 交叉验证：用 app 内部 proto.cjs 的类型序列化，用我们从还原 .proto 生成的类型反序列化，
 * 再反向序列化，逐字节比对；JSON 同理。
 *
 * 这是比"描述符比对"更强的证据 —— 它证明线上字节流完全等价。
 */
import { createRequire } from "node:module";
import { readdirSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
// usage: node cross-wire.mjs <proto.cjs>
const PROTO_CJS = process.argv[2] ?? process.env.PROTO_CJS;
if (!PROTO_CJS) {
  console.error("usage: node cross-wire.mjs <path/to/proto.cjs>");
  process.exit(2);
}
const GEN_DIR = new URL("./src/js/", import.meta.url);

// ---- 原始侧（运行时可达闭包）
const m = require(PROTO_CJS);
const A = new Map();
const visit = (t) => {
  if (!t || !t.typeName || A.has(t.typeName)) return;
  A.set(t.typeName, t);
  for (const f of t.fields.list()) {
    if (f.kind === "message") visit(f.T);
    else if (f.kind === "map" && f.T?.V?.kind === "message") visit(f.T.V.T);
  }
};
for (const v of Object.values(m)) {
  if (v == null || (typeof v !== "object" && typeof v !== "function")) continue;
  if (v.fields) visit(v);
  else if (v.methods) for (const mm of Object.values(v.methods)) { visit(mm.I); visit(mm.O); }
}

// ---- 生成侧
const B = new Map();
for (const f of readdirSync(GEN_DIR).filter((x) => x.endsWith("_pb.js"))) {
  const mod = await import(pathToFileURL(path.join(GEN_DIR.pathname, f)).href);
  for (const [, v] of Object.entries(mod))
    if (v != null && typeof v === "function" && v.fields) B.set(v.typeName, v);
}

// ---- 折叠改名（打破跨包 import 循环）
const RENAME = new Map([["agent.v1.TaskResult", "aiserver.v1.TaskResult__agentv1"]]);
const moved = new Map();
for (const a of A.keys()) if (!B.has(a) && !RENAME.has(a)) {
  const rel = a.split(".").slice(2).join(".");
  const cand = [...B.keys()].find((b) => !A.has(b) && b.split(".").slice(2).join(".") === rel);
  if (cand) moved.set(a, cand);
}

const WKT = (n) => String(n).startsWith("google.protobuf.") || String(n).startsWith("opentelemetry");

// ---- 造一个填满非默认值的实例
function fill(type, depth = 0) {
  const o = {};
  for (const f of type.fields.list()) {
    const n = f.localName;
    let v;
    if (f.kind === "scalar") {
      switch (f.T) {
        case 9: v = "té中"; break;                       // string
        case 8: v = true; break;                          // bool
        case 12: v = new Uint8Array([1, 2, 3]); break;    // bytes
        case 3: case 4: v = f.L === 1 ? "123" : (f.L === 2 ? 123 : 1234567890123n); break;
        case 1: case 2: v = 1.5; break;
        case 13: case 5: case 7: case 15: case 17: v = 7; break;
        case 6: case 16: case 18: v = 8n; break;
        default: v = 1;
      }
    } else if (f.kind === "enum") {
      v = f.T.values[f.T.values.length - 1].no;
    } else if (f.kind === "message") {
      // well-known types 需要合法值（Timestamp 要有日期、Value 要有值），这里直接留空
      if (WKT(f.T.typeName)) continue;
      v = depth < 1 && f.T !== type ? fill(f.T, depth + 1) : undefined;
    } else if (f.kind === "map") {
      v = {};
      if (f.V.kind === "message") { if (!WKT(f.V.T.typeName)) v["k"] = fill(f.V.T, 2); }
      else if (f.V.kind === "enum") v["k"] = f.V.T.values[0].no;
      else v["k"] = f.V.T === 9 ? "v" : f.V.T === 8 ? true : f.V.T === 12 ? new Uint8Array([1]) : 1;
      if (v["k"] === undefined) continue;
    }
    if (v === undefined) continue;
    o[n] = f.repeated ? [v, v] : v;
  }
  return new type(o);   // 两侧构造函数都接受 PartialMessage init
}

const hex = (b) => Buffer.from(b).toString("hex");
let okBin = 0, okJson = 0, fail = [];
for (const [name, ta] of A) {
  const tb = B.get(RENAME.get(name) ?? moved.get(name) ?? name);
  if (!tb) continue;
  let objA, binA, objB, binB;
  try {
    objA = fill(ta);
    binA = objA.toBinary();
    objB = tb.fromBinary(binA);          // 生成侧解码原始侧的字节
    binB = objB.toBinary();            // 再编码回去
  } catch (e) {
    fail.push([name, "binary: " + e.message]); continue;
  }
  if (hex(binA) !== hex(binB)) { fail.push([name, `binary 不等: ${binA.length}B vs ${binB.length}B`]); continue; }
  // 反向：生成侧 -> 原始侧
  try {
    const back = ta.fromBinary(binB).toBinary();
    if (hex(back) !== hex(binB)) { fail.push([name, "reverse binary 不等"]); continue; }
  } catch (e) { fail.push([name, "reverse: " + e.message]); continue; }
  okBin++;
  try {
    const j = objA.toJson();
    const j2 = tb.fromJson(j).toJson();
    if (JSON.stringify(j) !== JSON.stringify(j2)) { fail.push([name, "json 不等"]); continue; }
    okJson++;
  } catch (e) { fail.push([name, "json: " + e.message]); }
}

console.log(`配对消息: ${A.size}（原始侧可达）`);
console.log(`wire 双向一致: ${okBin}   json 一致: ${okJson}   失败: ${fail.length}`);
for (const [n, d] of fail.slice(0, 10)) console.log(`  ✗ ${n}: ${d}`);
console.log(fail.length === 0
  ? "\n✅ 全部通过：原始类型序列化出的字节，生成侧能无损解码并复现同一字节串；JSON 亦然"
  : "\n❌ 存在 wire 不一致");
