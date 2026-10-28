# 紧凑描述符格式（protobuf-es bundle 内部表示）

`dist/electron-main/proto.cjs` 没有外部依赖 —— protobuf 运行时被一起打包进去了，
所以可以直接 `require()` 拿到真实的 `MessageType` / `EnumType` / `ServiceType`。
还原 .proto 用的是**源码里的紧凑描述符字符串**，运行时对象只用于对拍。

## 三个构造

```js
// 消息：类名可带 minified 内部名（class e extends n）
C1 = class e extends n {
  static $p() { return ot }                       // ot = "aiserver.v1."  包前缀
  static $()  { return ["SendGrokBotUserMessageRequest|1 agent_id 9|13 source #0", Kg, Hg, Wg] }
}

// 枚举：作用域参数可以是 s / S / $$ 等任意标识符
Kg = a(s, S, "GrokBotClientSurface", [[0,"UNSPECIFIED"],[1,"DESKTOP"]], 1)

// 服务
R_ = { typeName: "aiserver.v1.AgentStoreService", methods: { ... } }
```

## 字段串语法

`$()[0]` 中 `|` 之前是类型名（可能含 `.` 表示嵌套），之后按 `|` 切分字段：

```
"<no> <name> <type>[?|*][ <oneof>]"
```

- `<type>` 是 protobuf `ScalarType` 数字：1=double 2=float 3=int64 4=uint64
  5=int32 8=bool 9=string 12=bytes 13=uint32 14=enum 17=sint32 18=sint64
- `#N` 指向 `$()[N+1]`：该项是**函数**则 message，否则 enum
- 含 `,` 即 map 字段，形如 `9,#3`（key 标量在前，value 在后）
- `?` = optional，`*` = repeated，末尾词 = oneof 名
- 包名 = 包基类的 `$p()`

## 枚举值名规则

```
name = UPPER_SNAKE(枚举短名) + "_" + 值短名
```

**嵌套枚举不含父消息路径**：`ErrorDetails.Error` 的值名是 `ERROR_BAD_API_KEY`，
不是 `ERROR_DETAILS_ERROR_BAD_API_KEY`。flags === 1 时适用（实测 30/30 命中）。

## 运行时对象的事实（对拍脚本依赖这些）

- `MessageType` **没有**静态 `create / toBinary`；只有静态 `fromBinary / fromJson / equals`
- 实例才有 `toBinary / toJson / clone`；构造用 `new Type(init)`，`init` 接受普通对象
- 枚举顶层导出是**双向映射**普通对象（`{0:"NAME", NAME:0}`），**没有** `typeName`，
  且只给**短名**；真实带前缀的 `name` 只出现在生成代码的源码里
  （`proto3.makeEnum("full.Name", [{no, name, localName}]`，结尾是 `],\n);` 有尾随逗号）
- map 字段的 key/value **平铺在 field 上**：`f.K`（ScalarType 数字）与 `f.V`（`{kind,T}`），
  不在 `f.T` 里
- service **运行时导出的是裁剪版**（如 AgentStoreService 只有 6/24 个方法，
  AiService 3/8）。完整的 `ServiceType` 定义只在源码里 → 对拍必须以源码为准
- method kind 有 4 种，源码里写作 `i.BiDiStreaming`（**注意大小写**）
- proto.cjs 里定义了一批**运行时不可达**的类型（既未导出也未被任何可达类型引用），
  例如 `AgentStore*` 系列；它们只有源码级紧凑描述符，需静态补验

## 分段：为什么一个包会有多个文件

proto 的两条规则：

1. 一个 package 可以拆成多个 `.proto` 文件
2. **同一个文件内**的类型可以互相引用、没有顺序要求，被禁止的只有**文件级** import 循环

所以包级循环（如 `agent.v1 ↔ aiserver.v1`）不需要搬类型，只需要切文件：

```
原始引用图 → Tarjan SCC 缩点 → 按顶层家族并查集合并（嵌套必须跟父走）
          → 迭代再缩点 → Kahn 拓扑序（同包优先）→ 按拓扑序连续切段
```

段是拓扑序的连续切片，跨段引用必然指向后面的段 → 天然无环。
实测 0.77.0：5 段，0 后向边，`aiserver.v1.proto → agent.v1.proto → aiserver.v1_2.proto(13)`。

两个实现陷阱：

- 引用图必须在 ref 被改写成 `named` **之前**取，否则 `refs_of()` 返回空列表，
  分段静默失效（仍然生成文件，但文件间成环）
- 家族合并会在组之间制造**新的环**，Kahn 跑不完会丢节点 → 要迭代缩点并断言节点数守恒
