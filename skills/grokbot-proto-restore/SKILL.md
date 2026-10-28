---
name: grokbot-proto-restore
description: 在需要从 Grok Bot.app（macOS，Electron/Anysphere sand 内核）还原出完整可用的 .proto 文件时使用：解包 app.asar 取出 proto.cjs，把 @bufbuild/protobuf 紧凑描述符还原成真正可编译的 .proto，用版本对齐的 protoc-gen-es / protoc-gen-connect-es 生成强类型客户端，并用描述符级 + wire 级双重对拍证明结果与 app 内 proto.cjs 完全一致。也用于 Grok Bot 版本更新后重新还原协议、对比两版新增/删除/变更了哪些 RPC 与消息。关键词：Grok Bot 升级、还原 proto、proto.cjs、protobuf-es、asar 解包、RPC 变化、GrokBotService；English triggers: restore Grok Bot proto, proto.cjs descriptor, protobuf-es codegen, Grok Bot RPC diff.
---

# Grok Bot proto 还原

## Overview

Grok Bot.app 把整个 gRPC/Connect 协议以 `@bufbuild/protobuf` 的**紧凑描述符**形式打包在
`dist/electron-main/proto.cjs` 里（约 1.3–1.4 MB，零外部依赖，可直接 `require`）。
本 skill 把这份描述符还原成真正的 `.proto`，生成强类型客户端，并**证明**还原结果与原
app 逐字段、逐字节一致 —— 而不是"看起来对"。

已实测通过两个版本：0.75.8（2567 messages / 328 enums / 551 方法）与
0.77.0（2688 / 343 / 589），两版均为 0 差异。

## 何时使用

- Grok Bot 更新后，需要重新拿到新版 proto / 新版 RPC 列表
- 需要知道两个版本之间新增、删除、改了哪些 RPC 与消息
- 需要把协议重新生成为 TypeScript/JavaScript 客户端
- 还原结果需要可信证据（对拍）而不是目测

## 前置依赖

```bash
pip install grpcio-tools     # 提供可用的 protoc；本机 homebrew 的 protoc 是坏的
node >= 18                   # npx @electron/asar 解包用
```

## 一键流程

```bash
scripts/restore.sh                                  # 用 /Applications/Grok Bot.app
scripts/restore.sh "/Applications/Grok Bot.app" ./out
scripts/restore.sh --proto-cjs /path/proto.cjs ./out      # 已解包过
scripts/restore.sh --baseline ./old/proto ./out           # 额外输出与上一版的差异
scripts/restore.sh --no-codegen ./out                     # 只还原 .proto 并校验
```

脚本依次执行：解包 app.asar → 定位 proto.cjs → 读取 app 内锁定的
`@bufbuild/protobuf` / `@connectrpc/connect` 版本 → 抽取 .proto → protoc 校验 →
安装**版本对齐**的生成器并生成代码 → 描述符对拍 → wire 对拍 → （可选）版本差异报告。

## 分步骤（需要排查或只做某一步时）

```bash
# 1. 解包
npx --yes @electron/asar extract "/Applications/Grok Bot.app/Contents/Resources/app.asar" out

# 2. 抽取 .proto（输出目录同时会得到 snapshot.json）
python3 scripts/extract_proto.py out/dist/electron-main/proto.cjs ./proto

# 3. protoc 校验（必须 0 error）
cd proto && python3 -m grpc_tools.protoc -I. --descriptor_set_out=../desc.pb ./*.proto

# 4. 循环诊断（包被拆成几个文件、有没有后向边）
python3 scripts/diagnose_cycles.py out/dist/electron-main/proto.cjs

# 5. 生成代码（版本必须对齐，见 references/toolchain.md）
# 6. 对拍
cd codegen && node compare.mjs  <proto.cjs> <protoDir>
cd codegen && node cross-wire.mjs <proto.cjs>
```

生成器版本**必须**与 app 内 `package.json` 的 `@bufbuild/protobuf` 一致
（0.75.8 与 0.77.0 都是 1.10.1 → 用 `protoc-gen-es@1.10.1` +
`protoc-gen-connect-es@1.6.1`）。若 major 版本不再是 1.x，先读
`references/troubleshooting.md`。

## 验收标准（必须全绿，否则不要交付）

| 检查 | 期望 |
|---|---|
| `extract_proto.py` 的 `types relocated to another package` | **0** |
| `unresolved refs` | **0** |
| protoc | 0 error，产出 descriptor |
| `segmentation ... backward cross-file edges` | **0** |
| `compare.mjs` | message/enum/service **差异 0** |
| `cross-wire.mjs` | binary + JSON **失败 0** |

任何一项不为 0 都说明还原有偏差。此时不要靠"手工改字段名"蒙混，先读
`references/troubleshooting.md` 定位根因 —— 历史上所有差异都指向抽取脚本的
真实缺陷，而不是协议本身。

## 输出物

```
<out>/proto/*.proto          # 还原的 .proto（包可能被拆成 <pkg>.proto / <pkg>_2.proto）
<out>/proto/snapshot.json    # 源码级描述符快照，供对拍与版本 diff 使用
<out>/extract.log            # 抽取阶段日志，restore.sh 用它做验收闸门
<out>/desc.pb                # protoc descriptor
<out>/codegen/src/gen/*.ts   # TypeScript
<out>/codegen/src/js/*_pb.js # JavaScript + *_connect.js 服务客户端
<out>/APP_VERSION
```

注意 `<pkg>_2.proto`：**一个 proto package 可以拆成多个文件**，这是打破包级
import 循环的正确手段，不是还原失败。类型全名不受影响。

## 资源

### scripts/
- `restore.sh` — 一键全流程（解包→还原→校验→生成→对拍→版本 diff）
- `extract_proto.py` — 紧凑描述符 → `.proto`，含 SCC 缩点 + 拓扑分段
- `diagnose_cycles.py` — 跨包依赖 / 循环 / 分段诊断
- `diff_snapshot.py` — 比较两个 snapshot.json，给出新增/删除/变更的消息与 RPC
- `verify/compare.mjs` — 描述符级对拍（字段号/名/类型/map/oneof/enum/service）
- `verify/cross-wire.mjs` — wire 级对拍（一边序列化、另一边解码，逐字节）

### references/
- `descriptor-format.md` — 紧凑描述符的语法、枚举命名规则、protobuf-es 运行时事实
- `toolchain.md` — 版本对齐表与生成命令
- `troubleshooting.md` — 已知故障模式与根因（升级适配时先读这个）
