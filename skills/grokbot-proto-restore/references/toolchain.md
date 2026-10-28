# 工具链与版本对齐

## 版本锁定表

生成器版本**必须**与 app 内 `dist/package.json` 的依赖一致，否则生成代码的结构
（尤其枚举值名）与 app 内那份对不上，对拍会红。

读取方式：

```bash
python3 -c "
import json;d=json.load(open('<unpacked>/package.json'))
for k in ('dependencies','devDependencies'):
    v=d.get(k) or {}
    for a,b in v.items():
        if 'bufbuild' in a or 'connect' in a: print(a,b)
"
```

| app 版本 | `@bufbuild/protobuf` | `@connectrpc/connect` | `protoc-gen-es` | `protoc-gen-connect-es` |
|---|---|---|---|---|
| 0.75.8 | 1.10.1 | ^1.6.1 | 1.10.1 | 1.6.1 |
| 0.77.0 | 1.10.1 | ^1.6.1 | 1.10.1 | 1.6.1 |

规则：`protoc-gen-es` = protobuf 版本；`protoc-gen-connect-es` = connect 版本去掉 `^`。
`restore.sh` 会自动读取并写入 `codegen/package.json`。

## 为什么不用系统 protoc

本机 homebrew 的 protoc 坏了（`libabseil_die_if_null` dylib 版本不匹配）。
统一用 grpcio-tools 里带的那份：

```bash
pip install grpcio-tools
python3 -m grpc_tools.protoc -I proto --descriptor_set_out=desc.pb proto/*.proto
```

它自带 `google/protobuf/*.proto`，WKT import 能解析。

## 生成命令

```bash
P=codegen/node_modules/.bin

# TypeScript（供编辑器 / 类型检查）
python3 -m grpc_tools.protoc -I proto \
  --plugin=protoc-gen-es="$P/protoc-gen-es" \
  --es_out=codegen/src/gen --es_opt=target=ts proto/*.proto

# JavaScript + 服务客户端（可直接跑）
python3 -m grpc_tools.protoc -I proto \
  --plugin=protoc-gen-es="$P/protoc-gen-es" \
  --plugin=protoc-gen-connect-es="$P/protoc-gen-connect-es" \
  --es_out=codegen/src/js --es_opt=target=js \
  --connect-es_out=codegen/src/js --connect-es_opt=target=js proto/*.proto
```

不要加 `import_extension=js`：ESM 下会生成不带扩展名的 import，Node 解析不了。

## 产物命名

- 类型文件：`<pkg>_pb.js`，如 `aiserver.v1_pb.js`、`aiserver.v1_2_pb.js`
- 服务文件：`svc_<full.ServiceName>_connect.js`，如 `svc_aiserver.v1.GrokBotService_connect.js`

服务被单独放文件是刻意的：service 是依赖图的汇点，放在最后就不会与任何类型文件成环。

## 连接真实后端（最小可用）

```js
import { createConnectTransport } from "@connectrpc/connect-node";
import { createPromiseClient } from "@connectrpc/connect";
import { GrokBotService } from "./src/js/svc_aiserver.v1.GrokBotService_connect.js";

const t = createConnectTransport({
  baseUrl: "https://api2.cursor.sh", httpVersion: "1.1",
  interceptors: [(n) => async (r) => {
    r.header.set("authorization", `Bearer ${accessToken}`);
    r.header.set("x-cursor-client-type", "sand");
    r.header.set("x-cursor-client-version", "0.77.0");
    r.header.set("x-ghost-mode", "true");
    r.header.set("x-cursor-checksum", checksum(machineId));
    return n(r);
  }],
});
const c = createPromiseClient(GrokBotService, t);
```

注意：unary 用 `application/json`，**流式必须用 `application/connect+json`**，
且流式**请求体**也要按 Connect envelope 分帧（`[1B flags][4B BE len][payload]`），
否则服务端报 `protocol error: incomplete envelope`。
