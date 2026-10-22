---
name: Agent CLI design
description: >-
  Use when designing, reviewing, or rewriting a command-line tool meant to be
  called by AI agents (no TTY, pipe-friendly, machine-readable). Covers stdout/stderr,
  exit codes, dry-run, non-interactive mode, output limits, secrets, and CLI vs MCP vs API.
  Chinese triggers: 设计CLI, 设计给agent调用的命令行, AI友好CLI, agent-native CLI,
  stdout stderr, 退出码, dry-run, 非交互, 幂等, 最小权限, CLI vs MCP.
  Do NOT use for writing an MCP server only, or for ordinary human-only CLI polish
  with no agent/CI constraints.
---

# 给 Agent 调用的 CLI 设计

综合多源调研（clig.dev、clispec、aclig、Anthropic、Cloudflare、gh/aws/gcloud/kubectl/stripe 等）的**可执行规范**。先落实「多源共识」；标为 **有争议** / **单一来源** / **开放问题** 的条目**不是定论**，选型时写进 ADR 或产品说明。

**资料来源：** [给 Agent 调用的 CLI 设计调研](https://cursor.com/canvas/store-03b47b9f-5ab6-4503-9be6-9a3296edc4b3/e0d3cc21-562b-4d05-90a1-dd08aab16a97)（多源综合，非厂商标准；资料截至 2026-10-22）。

**成功标准：** agent 在无 TTY、管道、硬超时与输出截断的宿主里能可靠调用：不挂起、能分支、能纠错、不泄密、破坏性操作可预览。

**停下来问用户：** 是否默认只读 / 默认沙盒、生产变更开关命名、秘密是否允许环境变量——这些属有争议项。

---

## 1. 何时用 / 何时不用

**用本 skill 当：**

- 要做或评审「给 agent / CI / 无 TTY shell」调用的 CLI
- 现有 CLI 在 agent 下挂起、解析失败、退出码含糊、输出撑爆上下文
- 在 CLI / MCP / 直连 API 之间做取舍

**不用当：**

- 只做人机交互 TUI、无自动化诉求
- 只写 MCP server、不暴露 CLI
- 把有争议项当成必须实现的硬标准

**建议落地顺序：** 先防卡死（非交互、stdout/stderr、`--json`、退出码表、默认 limit）→ 错误指引与 dry-run / 幂等 → 再考虑 schema 内省、本地 job 账本（有争议）。

---

## 2. 共识检查清单（动手前扫一遍）

设计或评审时，下列项应全部满足（多源共识或请求方强调的硬约束）：

- [ ] **stdout 只放数据**；进度 / 警告 / 提示 / 错误 → **stderr**；无 TTY 无 ANSI / spinner；尊重 `NO_COLOR`
- [ ] **无 TTY 绝不阻塞**；关掉 pager；缺参数立即失败并指出该传的 flag
- [ ] **错误**含：错误码 + 说明 + **合法取值或下一步命令**（`--json` 时错误也用 JSON 写 stderr）
- [ ] **退出码按失败类别**区分并写入 `--help` / 文档，含义不改
- [ ] **列表默认有上限**；提供 `--limit` / 游标 / 字段投影；截断时 `truncated` + `hint`
- [ ] **破坏性操作**：可 `--dry-run`（或等价预览）+ **显式确认**（资源名确认优于万能 `--yes`）
- [ ] **秘密不进 argv、不回显** stdout；创建密钥提供 `--output none` 或只返回引用
- [ ] **默认只读或最小权限**；变更需显式 flag / 全局开关；被拒时结构化错误 + 专用退出码
- [ ] **幂等**：创建支持幂等键或自然键；重复调用标明已存在
- [ ] **接口只增不改**：`--json` 字段稳定；未知子命令不当 run；禁止任意前缀缩写

---

## 3. 十四条原则（规则 → 正例 → 反例 → 共识度）

示意命令用 `mycli`；真实 CLI 名以各项目文档为准。

### P1. stdout 只放数据，其余进 stderr — **多源共识**

**规则：** agent 把 stdout 当结果解析。进度、警告、提示、错误一律 stderr。非 TTY 无颜色/动画；尊重 `NO_COLOR`。

**正例：**
```bash
mycli post list --json 2>/dev/null | jq '.[0].id'
# 进度只在 stderr
```

**反例：** spinner / `Fetching...` 写进 stdout，JSON 解析失败。

### P2. 统一机器可读输出（常用 `--json`），字段当契约 — **有争议**

**规则（共识部分）：** 每个返回数据的命令支持**同一种**机器可读 flag；字段名稳定。  
**争议：** 默认 JSON 还是精简文本；flag 叫 `--json` 还是允许 `--format`（Cloudflare 强制只用 `--json`；HN 有人认为小量数据文本更省 token）。  
**实践建议：** 至少提供 `--json`；可用字段投影 / `--jq` 省 token；**不要**同一 CLI 混用 `--format` / `--output` / `--json` 三种写法。

**正例：** `gh pr list --json number,title --jq '.[] | "\(.number) \(.title)"'`  
**反例：** `a --format=json` / `b --output json` / `c --json` 并存。

### P3. 退出码按失败类别区分 — **多源共识**

**规则：** 至少区分：成功、用法错误、认证、未找到、可重试（限流/瞬时）、被拒绝的变更。写入文档后**不改含义**。可标注是否可重试、是否已产生副作用。

**正例：**
```text
0 ok | 1 error | 2 usage | 3 empty | 4 auth | 5 not_found | 8 retryable
```

**反例：** 认证失败、网络抖动、参数错全是 `1`。

### P4. 没有 TTY 时绝不阻塞等待输入 — **有争议**（原则共识，开关命名有争议）

**规则（共识）：** 管道/无 TTY 下禁止确认提示与 pager；缺输入立即失败并说明 flag。提供环境变量或 flag 关闭提示（如 `GH_PROMPT_DISABLED=1`、`--quiet`、`CLOUDSDK_CORE_DISABLE_PROMPTS=1`）。  
**争议：** 是否要显式 `--no-input` / `--agent`；跳过确认叫 `--yes` 还是 `--force`（Cloudflare 用 `--force`；有人认为 `--force` 应留给覆盖语义）。

**正例：** `gcloud ... --quiet`；`GH_PROMPT_DISABLED=1 gh pr create --title "x" --body "y"`  
**反例：** `Are you sure? [y/N]:` 在无 stdin 下永久挂起；AWS CLI v2 默认 pager。

**必做：** 无 TTY 时禁用 pager（`PAGER=cat` / `--no-cli-pager` / 等价）。

### P5. 错误告诉 agent 下一步 — **多源共识**

**规则：** 错误 = 机器可读码 + 人话 + 下一步（合法枚举或正确命令）。枚举很大时描述规则优于全量列表。`--json` 时错误 JSON 写 **stderr**。

**正例：**
```text
error: --visibility must be one of: public, private, unlisted (got: "secret")
```
```json
{"error":{"code":"INVALID_ARG","hint":"mycli post create --visibility=public"}}
```

**反例：** `invalid visibility` 或整段 traceback，无合法值。

### P6. 输出有界：默认上限、分页、截断必须明示 — **多源共识**

**规则：** 列表默认 limit；提供 `--limit`、游标、字段投影。截断时返回 `truncated` / `next` / `hint`。大量输出优先 **NDJSON**（截断后逐行仍可解析）。结论与错误放输出**头或尾**（部分宿主截掉中间）。

**正例：**
```json
{"posts":[...],"truncated":true,"next":"abc","hint":"add --limit=N or --filter=..."}
```

**反例：** 默认自动翻完所有页，一次灌入数千对象。

### P7. 可发现：help 先给示例，命名全局一致 — **有争议**

**规则（共识）：** `noun verb`；动词/flag 全 CLI 统一（get≠info，list≠ls）。`--help` 含 EXAMPLES 与 EXIT CODES。  
**争议：** 是否要 `schema` 子命令做机器可读内省（aclig/clispec 主张；HN 有人认为静态 skill 文档更省推理轮次）。

**正例：** help 含 EXAMPLES + EXIT CODES。  
**反例：** 同义动词混用；help 只有营销文案无示例。

### P8. 幂等与安全重试 — **多源共识**

**规则：** 创建支持幂等键或自然键；重复调用返回已有资源并标 `existing`；结果说明是否改过状态；共享配置/缓存用原子写与锁。

**正例：** `--idempotency <key>`；`{"id":"...","existing":true}`  
**反例：** 超时重试产生重复记录且输出看不出。

### P9. 破坏性操作：可预览、需显式授权 — **有争议**（dry-run/显式确认是共识）

**规则（共识）：** 提供 `--dry-run`（或等价）；严重操作要求输入资源名（如 `--confirm=<name>`）。  
**争议（默认行为，非定论）：** 交互提示 + 脚本要 `--force`（clig）；默认只读 + `--allow-mutations`（aclig）；默认 dry-run + `--commit`（HN）；默认沙盒 + `--live`（Stripe）。  
**实践建议：** 至少 dry-run + 显式确认；**避免**单一万能 `--yes` 就能删生产；预览必须纯（勿像「仍可能 push」的半 dry-run）。

**正例：** `kubectl ... --dry-run=client -o yaml`；`mycli app destroy --confirm=my-app`；Stripe 默认 sandbox + `--live`  
**反例：** `destroy --yes` 无预览直接删生产。

### P10. 秘密：不走 argv，不回显 — **有争议**（不进 argv / 不回显是共识）

**规则（共识）：** token 不进 argv（进 `ps`、shell 历史、agent 记录）；stdout 不打印秘密。用 stdin、凭据文件或钥匙串。  
**争议：** 环境变量可否传秘密（clig 反对；clispec/aclig/gh 的 `GH_TOKEN` 接受）。选型写进文档，勿 silently 混用。

**正例：** `echo "$TOKEN" | mycli login --token-stdin`；`mycli key create --output none`  
**反例：** `--token sk_live_...`；默认 JSON 回显 secret 进 CI 日志。

### P11. 长任务可观测：进度、超时、异步 — **有争议**

**规则（共识）：** 进度写 stderr；支持 `--timeout`；超过宿主硬超时（常见约 1–2 分钟）应返回 job id + 查询，或 `--wait --timeout`。  
**争议：** 是否维护本地 job 账本（有人认为把无状态 API 变成有状态负担）。

**正例：** `mycli video render --wait --timeout 600` → `{"job_id":"...","status":"complete"}`  
**反例：** 十分钟无输出 → 超时重提 → 双跑。

### P12. 高信号字段 + concise / detailed — **单一来源**（非定论）

**规则：** Anthropic 内部结论：返回能直接用于下一步的字段；少给低信号 UUID/mime；提供 concise（省 token）与 detailed。未见广泛独立复现——**可选优化，勿当硬性标准**。

**正例：** `--view concise` vs `--view detailed --json`  
**反例：** 默认堆满 `uuid` / `mime_type` / 缩略图 URL。

### P13. 标记外部不可信内容 — **单一来源**（非定论）

**规则：** aclig 要求：issue/邮件/日志/网页等外部正文在输出中标记 `untrusted`。风险模型见 Simon Willison「致命三要素」（私有数据 + 不可信内容 + 对外通信）。具体标记格式尚无跨项目共识——**建议采纳，格式自定并文档化**。

**正例：** `{"body":{"untrusted":true,"text":"..."}}`  
**反例：** 外部正文原样拼进上下文，含注入指令。

### P14. 接口只增不改 — **多源共识**

**规则：** 命令、flag、JSON 字段只增不改；破坏性变更先程序内预警。人类可读输出可迭代，`--json` 须稳定。未知子命令不当默认 run；禁止任意前缀缩写。

**正例：** `schema_version`；新增字段 OK  
**反例：** `mycli i` 任意缩写成 install，堵死未来 `i*` 子命令。

---

## 4. CLI vs MCP vs API（短决策）

| 场景 | 倾向 |
|------|------|
| 开发者本人凭据、模型熟悉的工具（gh/aws/kubectl）、有 shell | **CLI** + 短 skill 文档（通常更省 token） |
| 代表多用户、OAuth、审计、无 shell / 浏览器 / WASM | **MCP** 或自建授权层 |
| 复杂过滤/循环/组合且要少过模型上下文 | **库 + 代码执行**，或「一个接受代码的 MCP 工具」 |
| 产品长期形态 | **同一核心库**，薄封装 CLI；需要时再加 `mcp` 子命令，避免两套实现 |

基准数据样本小且作者常有利益相关——**仅作参考，非定论**。贵的是「一次加载全部工具定义 + 中间结果过上下文」，不一定是协议本身。

---

## 5. 安全与权限

宿主沙盒**不能**替代 CLI 自身安全（例如部分沙盒默认仍可读 home 下凭据文件）。

1. **默认只读或默认沙盒**；变更要显式 flag；拒绝时结构化错误 + 专用退出码（勿弹 TTY）。
2. **读写子命令前缀可区分**（如 `get`/`list` vs `delete`/`apply`），便于宿主 allow/deny。
3. **无浏览器登录路径**：token 文件、stdin、服务账号模拟；需浏览器的流程会卡死 agent。
4. **凭据优先钥匙串**；明文配置文件视为可被宿主读到。
5. **不回显秘密**；支持 `--output none`。
6. **标记不可信外部内容**；避免单命令同时具备私有数据 + 不可信输入 + 外发。
7. 破坏性 / 生产路径与只读路径在帮助与退出码上可区分。

---

## 6. 开放问题 / 有争议项（明确非定论）

选型时选一派并写进文档，**不要假装已有标准**：

| 议题 | 分歧摘要 |
|------|----------|
| 默认输出 | 管道自动 JSONL vs 默认文本 + 显式 `--json` |
| agent 模式开关 | `--agent` / `--no-input` vs `isatty` vs 读 `CLAUDECODE`/`GEMINI_CLI`（无跨宿主标准） |
| schema 内省 | clispec / aclig / cli-agent-spec 未收敛；或改用静态 skill 文档 |
| 环境变量传秘密 | clig 反对 vs 多数 agent 向规范与 gh 接受 |
| 跳过确认命名 | `--yes` vs `--force` vs `--confirm` / `--allow-mutations` / `--commit` / `--live` |
| 破坏性默认 | 提示+脚本 force / 默认只读 / 默认 dry-run / 默认沙盒 |
| 幻觉 flag 别名 | 有助纠错 vs 污染接口 |
| 本地 job 账本 | 可观测 vs 有状态负担 |
| MCP 按需加载普及后 CLI 的 token 优势 | 现有基准不足 |

**单一来源（可选）：** P12 高信号/concise；P13 untrusted 标记格式。

---

## 7. 发版前评审清单

- [ ] 无 TTY：`... < /dev/null` 与管道下无挂起；pager 关闭
- [ ] `cmd --json 2>err.log | jq .` 可解析；err 中无必要数据混入 stdout
- [ ] 故意传错枚举：stderr 含合法值或 hint
- [ ] 模拟各类失败：退出码与文档表一致
- [ ] 大列表：默认截断且有 `next`/`hint`；非默认翻完全库
- [ ] `delete`/`destroy`/`apply`：有 dry-run；确认非万能 `--yes` 即可生产毁灭
- [ ] 用 `ps`/`history` 视角检查：argv 无 token；stdout 无 secret
- [ ] 只读子命令可被宿主单独放行
- [ ] `--help` 有 EXAMPLES + EXIT CODES；动词命名一致
- [ ] 有争议默认值已文档化（并标「非行业定论」）
- [ ] 无硬编码账号、路径、助手名；无秘密入库

---

## 8. 现有 CLI 可抄 / 宜避（速查）

**可抄：** gh `--json` 字段 + `--jq`；gcloud `--quiet` + 环境变量双通道；Stripe 默认沙盒 + `--idempotency` + `--confirm`；kubectl `--dry-run=client`；Cloudflare schema 层强制命名/`--json`。  
**宜避：** AWS CLI v2 默认 pager、默认翻完所有页；半吊子 dry-run（仍有副作用）；粒度只有 0/1 的退出码。

---

## 9. 来源说明

本 skill 综合自公开指南与多 CLI 文档对照，**不是**单一厂商标准。完整正反例、对比表与链接见调研页：

https://cursor.com/canvas/store-03b47b9f-5ab6-4503-9be6-9a3296edc4b3/e0d3cc21-562b-4d05-90a1-dd08aab16a97

主要参考入口：clig.dev、clispec.dev、aclig.dev/invariants、cli-agent-spec、Anthropic *Writing effective tools for agents*、Trevin Chow *10 Principles for Agent-Native CLIs*、Cloudflare CLI 预览、以及 gh/aws/gcloud/kubectl/stripe/az 文档与相关 HN 讨论。
