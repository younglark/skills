---
name: Photoshop UXP scripts (.psjs)
description: "Use when driving Adobe Photoshop with standalone UXP scripts (.psjs): write, launch, poll, and verify document create/edit/export from an agent. Covers executeAsModal, batchPlay, file tokens, doc-id safety, and psjs vs UXP plugins vs ExtendScript/JSX. Chinese triggers: psjs驱动Photoshop, UXP脚本, batchPlay, executeAsModal, 用脚本改PSD, 从命令行跑Photoshop脚本, 图层样式脚本, 导出切片, 切图工作流, 九宫格, 切图清单, 布局JSON. Do NOT use for UXP plugin/panel or MCP-bridge development, ExtendScript/JSX-only work, or read-only PSD inspection that psd-tools/ag-psd can do without launching Photoshop."
---

# 用 UXP 脚本 (.psjs) 驱动 Photoshop

面向 agent 的可执行配方：在 Photoshop 里创建、修改、导出文档。事实分两类：

- **官方**：来自 Adobe UXP 文档，文末有链接。
- **实测**：在 Windows 和 macOS、Adobe Photoshop 2026 上用 `.psjs` 跑通的经验。未核实的标为 **未核实**，不要当成官方 API。

**成功标准：** 脚本写完结果 JSON（含 `done` / `ok` / `fatal` / `errs`）；只改动任务指定的目标文档，其他打开的文档不受影响；需要像素对齐时，导出 PNG 并与金图做 ΔE / 像素差后迭代。

**先停下来：** 任务没说清要改哪个文档，或者要覆盖保存一个有用户未保存改动的文档，而任务没明确允许。

---

## 1. 何时用 / 何时不用

**用 `.psjs` 当：**

- 需要 Photoshop 才能做的事：渲染带图层样式的像素、按图层导出切片、用 batchPlay 建形状/文字/效果、把结果存成 PSD/PNG。
- 从 PSD/PSB 推断切图清单、审核后导出 1x PNG 和引擎无关的布局 JSON：见第 9 节。
- agent 要一次性跑完任务（写脚本 → 启动 → 轮询日志 → 拷回产物），不需要常驻面板。

**不要启动 Photoshop 当：**

- 只读图层树、名称、id、文本、大致 bounds：用 `psd-tools` 或 `ag-psd` 离线解析即可。实测：`psd-tools` 的 `layer_id` 与 UXP `layer.id` 一致，可用离线结构定位，只把渲染/导出交给 PS。注意 psd-tools 的 bbox **不含**图层效果，最终视觉 bounds 仍要在 PS 里算。

**改用 UXP 插件当：**

- 需要长驻、双向通道（例如 MCP 桥、面板、WebSocket）。官方：脚本 **没有** manifest / 面板；**Network、LaunchProcess、WebView、IPC 均不可用**。这些能力要做插件并在 manifest 里声明权限。
- 官方：不能从插件里调用另一个 `.psjs`；但脚本里的 UXP 代码一般可以搬进插件运行。

**改用 ExtendScript / JSX 当：**

- 目标是 Photoshop 23.5 以下，或只能走 Windows COM `DoJavaScript` / macOS AppleScript `do javascript` / CEP `evalScript`。JSX 是 ES3、同步 API，维护模式。不要把 `psx.jsx`（xbytor 的 ExtendScript 助手库）当成一种运行时。

---

## 2. 官方要点（先记住这些）

- 独立 UXP 脚本从 **Photoshop 23.5** 起可用，扩展名 `.psjs`。调试可用 UXP Developer Tool ≥ 1.6，但运行脚本不要求 UDT。
- 官方启动方式：`File > Scripts > Browse...`；把 `.psjs` 拖到 Dock 图标（Mac）或 Photoshop 窗口里非打开文档的区域（Mac / Win）；用 Action 播放脚本。双击打开曾标注为 “Coming soon”。
- 脚本是现代 JS，支持 **顶层 `await`**。官方示例和实测都依赖它；不要写成「定义了 `async main` 却没人 `await main()`」然后立刻退出。
- 任何会改文档或应用状态的操作，必须包在 `require("photoshop").core.executeAsModal(fn, { commandName })` 里。`commandName` 会显示在进度条上（脚本超过约 2–3 秒会出现进度条，用户可取消）。
- DOM 不够用时用 `require("photoshop").action.batchPlay(descriptors, options)`。描述符是 actionJSON。官方建议优先 DOM。用「开发者模式」下 Actions 面板的 **Copy As JavaScript**，或 `Plugins > Development > Record Action Commands...` 录描述符，**不要编造键名**。
- `dialogOptions` 写在**每条描述符**的 `_options` 里，取值 `"silent"`（默认，出错也不弹窗）、`"dontDisplay"`（一般不弹，缺参或出错时可能弹）、`"display"`。**实测（PS 2026）：agent 必须用 `"silent"`。** `"dontDisplay"` 在命令失败时仍会弹出阻塞错误框（如 `命令“获取”当前不可用。`），脚本会一直等到有人点确定；点掉后返回 `{_obj:"error", message:"", result:-128}`（-128 是官方的用户取消码）。`"silent"` 不弹窗，约 1–5ms 返回 `{_obj:"error", message:"脚本名: 命令“…”当前不可用。", result:-25920}`。曾把 `{ dialogOptions: "dontDisplay" }` 作为 `batchPlay` 第二个参数传入且未报错，但官方 options 表没有这个键，也无法安全地用 `"display"` 验证它是否生效。新代码把 `dialogOptions` 写在描述符 `_options` 里，取值用 `"silent"`。
- `batchPlay` 参数非法会 **reject**；目标不存在等运行期失败则 **resolve**，返回 `{_obj:"error", message, result}`。`result === -128` 表示用户取消。务必检查返回数组。
- 文件系统：脚本无 manifest，权限由 Photoshop 内置。官方脚本章节仍写：可用文件选择器和 `getTemporaryFolder()`。`getEntryWithUrl` / `createSessionToken` 是官方 FileSystemProvider API（见第 4 节实测）。
- `require("uxp").script` 提供 `executionContext`、`args`、`setResult(result)`。官方 FAQ（首发说明）曾写：**第一版不支持向脚本传参**。不要依赖命令行参数或 `script.args` 把任务配置传进去；把路径和参数写进脚本，或让脚本读旁边的 JSON。
  **实测（Windows / PS 2026，`Photoshop.exe script.psjs`）：** `script.args` 为 `[]`。若在脚本路径后再跟额外 CLI 参数，脚本不会执行（3 分钟内无结果文件），启动器仍立即返回、退出码 0、stdout 无脚本输出。不要传额外 CLI 参数。`setResult` 可调用，返回 `undefined`，不会出现在 `Photoshop.exe` 的退出码或 stdout 里。始终用结果文件。
- 同一时刻只能跑一个脚本，脚本不能再调另一个脚本。
- 官方 `executionContext`：`isCancelled` / `onCancel` 用于响应用户取消，`reportProgress({ value, commandName })` 更新进度条，`hostControl.suspendHistory({ documentID, name })` + `resumeHistory(id, commit)` 把一批修改合成一步历史记录，方便美术一次撤销。
  **实测：** `isCancelled` 是 boolean；`reportProgress` 可调用，返回 `undefined`。`suspendHistory({documentID, name})` 返回 `{historySuspensionID}`，`resumeHistory(id)` 会把随后多次修改合成一步，名称出现在 `doc.historyStates` 里。`hostControl` 的可枚举 key 可能为空，用 `Object.getOwnPropertyNames`。读历史用 DOM（`doc.historyStates` / `doc.activeHistoryState`），不要 batchPlay `get` `historyStatesCount`（该命令不可用，见第 7 节）。

---

## 3. Agent 启动与轮询（Windows 已实测）

官方没有把「命令行把 `.psjs` 传给 `Photoshop.exe`」写成正式接口。**实测（Windows / Photoshop 2026）：**

```powershell
& "C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe" "D:\work\ps-job\run_job.psjs"
```

- 不需要 Creative Cloud 桌面端、UDT 或开发者模式。
- 若 Photoshop 已打开，脚本在**已有实例**里执行。
- **启动器立即返回**，脚本在 PS 里异步继续跑。必须轮询脚本自己写的结果 JSON（建议含 `done`、`ok`、`fatal`、`errs`），不要看进程退出码判断成败。
- 曾测：一个导出脚本把一个 `.psb` 导出为 28 张切片大约 18 秒（其中打开文件约 10 秒），并写 `export_log.json`。
- **实测（Windows / PS 2026）：** 文件关联存在（`.psjs=Photoshop.PSJSFileType.200`，ftype 为 `Photoshop.exe "%1"`）。`Start-Process script.psjs` 会实际执行脚本并写结果文件。agent 仍建议显式传 `Photoshop.exe` + 脚本路径，并以结果文件为准。
- 启动前确认 PS 没有挂着模态对话框（崩溃后的「恢复文档」提示、存储对话框等）。有对话框时脚本不会按预期执行，轮询只会超时；超时后先截图看 PS 界面，不要盲目重跑。

**远程 agent 流程：** 在 agent 侧写好 `.psjs` → 拷到装有 Photoshop 的机器（只用用户授权的目录）→ 用 shell 启动并轮询日志 → 把 PNG/PSD/日志拷回 → 需要时做像素对比。

轮询示例（PowerShell）：

```powershell
$ps = "C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
$script = "D:\work\ps-job\run_job.psjs"
$log = "D:\work\ps-job\job_log.json"
if (Test-Path $log) { Remove-Item $log }
& $ps $script
$deadline = (Get-Date).AddMinutes(10)
do {
  Start-Sleep -Seconds 2
  if (Test-Path $log) {
    $j = Get-Content $log -Raw | ConvertFrom-Json
    if ($j.done) {
      $j | ConvertTo-Json -Depth 8
      if (-not $j.ok) { exit 1 }
      break
    }
  }
  if ((Get-Date) -gt $deadline) { throw "timeout waiting for $log" }
} while ($true)
```

**macOS（实测，Photoshop 2026，Mac mini）：** 官方只写了拖到 Dock / 窗口；下面的命令行启动未见官方文档，但已实测可用：

```bash
open -a "Adobe Photoshop 2026" /path/to/run_job.psjs
# 已关联 .psjs 时也可：open /path/to/run_job.psjs
```

- **冷启动**：PS 未运行时，`open -a` 约 0.3 秒返回（exit 0），PS 启动后自动执行脚本，约 8 秒写出结果文件。
- **热启动**：PS 已运行时，脚本在现有实例里执行，几秒内写出结果。
- 不带 `-a`、走文件关联的 `open xxx.psjs` 同样会执行。
- `getEntryWithUrl("file:/Users/<user>/...")` 无选择器可写文件。
- 和 Windows 一样：启动器立即返回，必须轮询结果文件，不要加额外命令行参数。

轮询示例（zsh）：

```bash
log=~/ps-job/job_log.json; rm -f "$log"
open -a "Adobe Photoshop 2026" ~/ps-job/run_job.psjs
for i in {1..300}; do [ -f "$log" ] && grep -Eq '"done": ?true' "$log" && break; sleep 2; done
cat "$log"
```

骨架脚本见 [examples/run_job.psjs](examples/run_job.psjs)（顶层 await、`executeAsModal`、文档 id 守卫、结果文件、`try/catch`）。**实测：** 按此骨架跑通：写出 `done`/`ok`、导出 `preview.png`、关闭自己建的文档，其它已开文档的 id 集合不变。

---

## 4. 文件访问

官方 FileSystemProvider：`getFileForOpening` / `getFileForSaving` / `getFolder`、`getTemporaryFolder`、`getEntryWithUrl(url)`、`createEntryWithUrl`、`createSessionToken(entry)`。

给 batchPlay 的 `_path` 必须是 **session token**，不能是裸系统路径。官方说明：走 Photoshop DOM（如 `app.open(file)`、`doc.saveAs.psd(file, …)`）时可直接传 File/Folder entry，宿主会自动转 token。

**实测（PS 2026 脚本，未弹选择器）：**

```js
const fs = require("uxp").storage.localFileSystem;
const root = await fs.getEntryWithUrl("file:/D:/work/ps-job"); // 目录必须已存在
const out = await root.createFile("job_log.json", { overwrite: true });
await out.write(JSON.stringify(log, null, 2));
```

`file:/D:/...` 这种 URL 在 Windows 上可用。脚本章节仍只保证选择器 + 临时目录；把「任意路径、无选择器」当成 PS 2026 实测行为，换版本先做一次写文件冒烟。不要调用网络模块——官方脚本环境是禁止的。

---

## 5. 文档范围与安全（必须）

原则：**脚本只碰任务指定的目标文档**。目标可以是脚本新建的文档，也可以是任务点名的已有 PSD（迭代现有设计稿是常见场景）。其余打开的文档一律不改、不存、不关。

**确定目标集合 `targets`：**

- 新建：`app.createDocument()` 返回的 id 加入 `targets`。
- 迭代已有 PSD：按任务给的**路径**定位。先在 `app.documents` 里按 `doc.path` 找是否已打开；已打开就直接用这个实例（不要再开第二份），没打开就 `app.open(entry)`。只按名字匹配不可靠（重名、「-恢复的」副本），优先路径，其次任务给的 doc id。**实测：** 按路径能复用同一实例（同一 doc id，不会打开第二份）；未保存编辑后 `doc.saved === false`；`saveAs(..., true)` 另存新版本时原文件的 mtime 和大小不变，文档保持打开且仍指向原路径。Windows 上 `doc.path` 形如 `D:\\work\\file.psd`。恢复出来的文档（如「xxx-恢复的」）可能 `path` 为空、`saved === false`，按路径找不到，要用任务给的 doc id。
- 临时文档（如「从图层新建」切片）加入 `targets`，用完 `closeWithoutSaving()`。

**守卫：** 所有 `batchPlay`、保存、关闭之前检查 `app.activeDocument.id` 是否在 `targets` 里。这个检查只管「隐式指向当前文档」的描述符；描述符里显式写了 `{ _ref: "document", _id }` 时，那个 id 也要检查。跑前跑后各记一次 `app.documents.map(d => ({ name: d.name, id: d.id, path: d.path }))` 写进日志。
  **实测：** `make` 图层时带上非活动文档的 `{ _ref: "document", _id }`，会在该文档上建图层，且 `activeDocument` 不变，所以必须检查显式 id。但 `set` 对非活动文档不可用（见第 7 节）；要改属性，先 `app.activeDocument = target` 再 `set`。

**改已有文档时：**

- 目标文档已打开且有未保存改动（官方 `doc.saved === false`），说明用户正在手动改。默认可以在其上继续编辑（这正是迭代任务），但**不要覆盖保存**，除非任务明确要求；先在日志里写明它运行前就是 dirty 状态。
- 用 `hostControl.suspendHistory` 把本次脚本的修改合成一步历史记录，用户一次 Ctrl+Z 就能撤回。
- 保存策略按任务定：默认另存新版本（如 `xxx_v14.psd`），保留原文件；任务明确要求「原地更新」时再 `doc.save()`。
- 脚本结束时不要关闭用户原本就打开的目标文档；只关脚本自己打开或新建、且任务不要求保留的文档。

**自动清理：** 官方 `executionContext.hostControl.registerAutoCloseDocument(docID)` 可在用户取消或 modal 结束时自动关闭临时文档，只用于脚本新建的文档；要留给美术继续改时，先 `unregisterAutoCloseDocument`。**不要**对用户已有的文档注册自动关闭。

---

## 6. 验证循环

1. 脚本导出 PNG（必要时再存 PSD）。
2. 在 agent 侧与金图对比（CIEDE2000 ΔE、像素差、局部放大）。
3. 根据差异改脚本（路径、渐变、图层样式），不要在已打开的用户文档上手改然后指望可复现。
4. 重跑、再导出、再比，直到达标或说明剩余差距。

---

## 7. 实测坑

- **`dialogOptions: "dontDisplay"` 失败时仍会弹阻塞错误框。** agent 跑的每条描述符都应写 `_options: { dialogOptions: "silent" }`。轮询超时先看 PS 是否弹了错误框，不要盲目重跑；有对话框时不要接着启动下一个脚本。
- **读历史用 DOM，不要用 batchPlay `get` `historyStatesCount`。** 后者会报「命令“获取”当前不可用。」`dontDisplay` 时弹窗，`silent` 时返回 `result: -25920`。用 `doc.historyStates` / `doc.activeHistoryState`。
- **对非活动文档做 `set` 会失败**（「命令“设置”当前不可用。」）。先把目标设为 `app.activeDocument` 再 `set`。同一写法的 `make` 图层却可以作用到非活动文档，而且不会切换当前文档——描述符里的 document `_id` 必须纳入守卫。

- **多层描边**必须用 `frameFXMulti`（单条可用 `frameFX`）。渐变沿形状外扩用 `gradientType: "shapeburst"`。
- **`innerShadow` 单键会被静默忽略**，应写成 `innerShadowMulti: [{ _obj: "innerShadow", ... }]`。改完用 `get` 读回 `layerEffects`，确认键真的在。
- 自定义路径：先 `set` `workPath` 再 `make contentLayer`。这样做出的形状通常 **没有** `keyOriginType`，不是属性栏可改圆角的实时形状。要可编辑圆角，用 `make contentLayer` + `shape._obj = "rectangle"` / `"ellipse"` 且带 `unitValueQuadVersion: 1` 和四角半径。
- 字体赋的是 **PostScript 名**（如 `textItem.characterStyle.font = "MuseoSansRounded-1000"`），且必须已安装；按用户安装即可，不必装成机器级。
- RGB 描述符里绿色字段叫 **`grain`**，不是 `green`。不要「修正」这个名字。
- `executeAsModal` 必须 `await`。漏掉的话，后面的代码会在 modal 尚未拿到时继续跑。
- 用户取消后 `batchPlay` 会抛异常；不要空 `catch` 把取消吞掉，否则脚本停不下来。

图层样式与形状的 actionJSON 见 [references/batchplay.md](references/batchplay.md)。

---

## 8. 资料来源

官方：

- [UXP Scripting in Photoshop](https://developer.adobe.com/photoshop/uxp/2022/ps-reference/media/uxpscripting)（`.psjs`、23.5、启动方式）
- [Quickstart](https://developer.adobe.com/photoshop/uxp/2022/scripting/getting-started/)
- [How it works](https://developer.adobe.com/photoshop/uxp/2022/scripting/how-it-works/)（顶层 await、模块权限、FAQ：首发不支持传参、不能脚本调脚本、无网络）
- [Scripting reference](https://developer.adobe.com/photoshop/uxp/2022/scripting/reference/)（`executionContext` / `hostControl`）
- [`require("uxp").script`](https://developer.adobe.com/photoshop/uxp/2022/uxp-api/reference-js/modules/uxp/plugin-manager/script)（`args`、`setResult`）
- [executeAsModal](https://developer.adobe.com/photoshop/uxp/2022/ps-reference/media/executeasmodal)
- [batchPlay](https://developer.adobe.com/photoshop/uxp/2022/ps-reference/media/batchplay)
- [FileSystemProvider](https://developer.adobe.com/photoshop/uxp/2022/uxp-api/reference-js/modules/uxp/persistent-file-storage/file-system-provider)
- [File access](https://developer.adobe.com/photoshop/uxp/2022/guides/uxp-guide/uxp-misc/file-access/)
- [Samples](https://developer.adobe.com/photoshop/uxp/2022/scripting/samples/)

实测补充见上文各节，来自用 `.psjs` 批量建 UI 组件并与金图对像素的工作，不是 Adobe 标准。

---

## 9. Agent 切图工作流

把可见图层切成游戏引擎可导入的 1x PNG，并产出一份引擎无关的中间布局 JSON。细节、schema、命名规则、九宫格启发式和实测见 [references/slicing.md](references/slicing.md)。脚本与 Python 助手： [examples/export_slices.psjs](examples/export_slices.psjs)、[examples/slicing_helper.py](examples/slicing_helper.py)、[examples/naming_rules.example.json](examples/naming_rules.example.json)、[examples/export_config.example.json](examples/export_config.example.json)。

**流程（确认前不导出）：**

1. **推断（离线）**：用 `psd-tools` 读图层树，只看可见图层（隐藏的完全忽略，状态要靠美术在 PSD 里显式打开）。`--artboard <画板名或id>` 可选；省略则处理所有可见顶层画板，没有画板时处理整份画布。项目命名词表通过 `--rules` 传入，不写进助手核心。为每条切图生成 `layer_id`、源图层名/路径（仅供对照）、规范名 `模块_类型_用途_状态`（小写，禁止直接用 PSD 图层名）、类型 `image` | `nine_slice` | `dynamic_text` | `art_text`，以及按像素分析算出的九宫格边距。底图、高亮、图标、文字是独立资源，不要烘焙到一张图里。
2. **审核门闩**：输出标注预览图（框出每个切图、标规范名和九宫格线）和 manifest JSON。用户改 JSON 确认；`status` 在确认前保持 `pending_review`。
3. **导出（`.psjs`）**：脚本只硬编码一个 `CONFIG_URL`，从旁边的作业 JSON 读路径。按 `layer_id`（及可见剪贴层）导出 1x PNG，图层效果烘焙进图；重复实例各导一份，不去重。`dynamic_text` 不出图，只导出文本、字体 PostScript 名、字号、颜色、描边、对齐等属性；`art_text` 出图。
4. **布局 JSON**：引擎无关的中间格式，记录层级、可见尺寸（含效果）、逻辑尺寸（不含效果）、按逻辑尺寸中心计算的锚点、九宫格边距、文本属性和对应图片。各引擎导入器自己写，不在本 skill 里。
5. **校验**：按元数据把切图用 src-over 拼回去，和 PS 整图画板对比像素差 / CIEDE2000 ΔE；同时报告 Pillow 渲染动态文字后的数字、以及遮罩动态文字区域后的数字，并列出误差最大的 10 条。再检查漏切、重名、空图。

沿用第 2–5 节的规则：每条描述符 `_options.dialogOptions = "silent"`，轮询结果 JSON，文档 id 守卫，不加额外 CLI 参数，顶层 `await`，`executeAsModal`，需要时 `suspendHistory`。

**实测（Windows / PS 2026，一份列表界面 PSB 副本上的 1080×2340 画板，65 个可见元素，Asia/Shanghai）：** 修后重跑推断 65 条（最终 image 18 / nine_slice 21 / dynamic_text 22 / art_text 4；成对页签与带效果的固定文案为 `art_text`，带前导零的数字串为动态玩家名），约 22 秒（需提高 `psd-tools` 的 `ParseLimits`，渐变合成需 scipy）；审核预览已生成，测试中将自动清单视为已确认；导出 65/65 成功、43 张 PNG，约 42 秒；漏切/重名/空图为 0。可见框按 trim 内缩量对齐后，遮罩动态文字：mean ΔE **0.047**、PSNR **46.24 dB**（修前为 2.599 / 20.54 dB）。用文档字体 Pillow 近似动态文字后全图 mean ΔE 1.041、PSNR 23.39 dB，残差几乎都在文字本身（hinting / 描边，非切图错位）。遮罩后约 0.15% 像素差 >16，集中在底栏重叠层，属未模拟 PS 混合模式的预期残差。细节与第一轮对比见 [references/slicing.md](references/slicing.md) 第 8 节。随后把项目命名词表移到仓库外的 `--rules` 再跑同一副本，遮罩后 PSNR 仍为 46.24 dB、mean ΔE 0.047、问题数为 0；不传 `--rules` 时 kind 不变，但规范名更泛化（模块落在 `main`，缺少用途词）。

