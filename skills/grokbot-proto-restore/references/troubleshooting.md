# 故障排查

历史上出现的每一处差异都指向**抽取脚本的真实缺陷**，不是协议本身的问题。
对拍不通过时，按这里的清单定位，不要手工改字段名掩盖。

## 1. protoc: 循环 import

```
xxx.proto: Import "yyy.proto" was not found or had errors.
```

- 若 `yyy.proto` 根本不存在 → 引用解析失败，见第 3 条
- 若两边互相 import → 分段失效。先跑 `diagnose_cycles.py` 看 `backward cross-file edges`
  - 最常见根因：**引用图在 ref 被改写成 `named` 之后才取**，`refs_of()` 返回空列表，
    图退化成无边，分段静默失效。图必须在 resolve 之前取
  - 次常见：家族合并后没有迭代再缩点，Kahn 丢节点（脚本里有节点数守恒断言）
- 绝不要用"把类型搬进另一个包"来破循环：会级联扩散并造成撞名
  （历史教训：一次搬 14 个，`agent.v1.TaskResult` 撞上 `aiserver.v1.TaskResult`）

## 2. `unresolved refs` 不为 0

引用变量解析不到全名，字段退化成 `string`。根因通常是：

- **自引用用的是类的内部名**：`C1 = class e extends n { ... }` 里 `#N` 指向的是 `e`
  而不是 `C1` → 捕获内部名做 self 解析
- **标识符正则只认 `\w`**：minified 的基类/作用域参数可能是 `$$`、`$`，
  必须放宽到 `[A-Za-z_$][A-Za-z0-9_$]*`
- 目标类型在显式 `typeName` 映射里（如 `google.protobuf.*`）→ 走 `explicit` 兜底

## 3. 字段名/类型对不上

- 字段串可能带 `?`（optional）/`*`（repeated）/oneof 名，三者都要解析
- map 的类型 token 含逗号，要先判 map 再判标量
- 嵌套类型（`Outer.Inner`）必须与顶层父在同一个文件，否则父子被拆到两个文件
  会重复定义 → 家族并查集合并

## 4. enum 值名全红

`proto3.makeEnum()` 的**运行时对象只有短名**，真实 `name`（带前缀）只存在于生成代码的
源码文本里。两侧都要取源码级 `name`，不能比较运行时对象。

## 5. service 方法数对不上

`proto.cjs` 运行时导出的 service 是**裁剪版**（AgentStoreService 6/24，AiService 3/8）。
对拍与计数必须以**源码里的完整 ServiceType 定义**为准（`snapshot.json` 就是这份）。

## 6. method kind 判错

源码里是 `i.BiDiStreaming`（**大小写不同**）。4 种 kind 要分别生成签名：

| kind | 签名 |
|---|---|
| Unary | `(In) returns (Out)` |
| ServerStreaming | `(In) returns (stream Out)` |
| ClientStreaming | `(stream In) returns (Out)` |
| BiDiStreaming | `(stream In) returns (stream Out)` |

## 7. `protoc` 起不来

homebrew 的 protoc 坏（abseil dylib 不匹配）。用 `python3 -m grpc_tools.protoc`
（需 `pip install grpcio-tools`）。

## 8. wire 对拍失败但描述符对拍通过

几乎总是**构造测试数据**的问题，不是协议问题：

- `google.protobuf.Timestamp` / `Value` 等 WKT 需要合法值，不能给空对象 → 跳过 WKT
- map 的 value 要按标量类型给值（bool/bytes/string/int 各不相同）

## 9. 对拍报"仅生成侧 N 个类型"

`proto.cjs` 里定义了一批运行时不可达的类型（未被导出、也未被任何可达类型引用，
BFS 触达不到，典型是 `AgentStore*` 系列）。它们的可信性靠
`compare.mjs` 里的**静态补验**（直接读源码紧凑描述符比对）。这是预期行为，不是缺陷。

## 10. 外部类型未还原（可接受）

`google.protobuf.*`（5 个）与 `opentelemetry.proto.*`（14 个）运行时来自
protobuf-es 内置与 OTEL 包，不在 app 的 bundle 里。它们不影响我方 wire
（wire 只认 service 名 + field number）。

## 11. 版本 major 变化（protobuf 2.x 等）

若 app 内 `@bufbuild/protobuf` 不再是 1.x：

1. `restore.sh` 会打印警告并不中断
2. 检查紧凑描述符形态是否变化：抓一个类型看 `static $()` 的返回结构
   `$p()`（包前缀）是否还在
3. 枚举构造是否还是 `a(scope, scope, "Name", [[no,"VALUE"]], flags)`
4. 若形态变了，改 `extract_proto.py` 的解析正则，然后**必须**让
   `unresolved refs = 0` + `compare.mjs` 差异 0 才算通过

## 12. 拿到新版后要做的第一件事

```bash
scripts/restore.sh --baseline ./previous/proto ./new
```

先看 RPC 增删（`--- service / RPC changes ---`），再决定调度端要不要适配。
例如 0.75.8 → 0.77.0：GrokBotService 367 → 401 方法，新增
`CallGrokBotMcpAppTool` / `ClaimGrokBotSlackConnect` / `AcceptGrokBotAgentAccessInvite`
等，DashboardService 删除了 3 个 GitHub Connect 相关方法。
