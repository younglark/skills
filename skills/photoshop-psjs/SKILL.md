---
name: Photoshop UXP scripts (.psjs)
description: "Use when driving Adobe Photoshop with standalone UXP scripts (.psjs): write, launch, poll, and verify document create/edit/export from an agent. Covers executeAsModal, batchPlay, file tokens, doc-id safety, and psjs vs UXP plugins vs ExtendScript/JSX. Chinese triggers: psjs驱动Photoshop, UXP脚本, batchPlay, executeAsModal, 用脚本改PSD, 从命令行跑Photoshop脚本, 图层样式脚本, 导出切片. Do NOT use for UXP plugin/panel or MCP-bridge development, ExtendScript/JSX-only work, or read-only PSD inspection that psd-tools/ag-psd can do without launching Photoshop."
---

# 用 UXP 脚本 (.psjs) 驱动 Photoshop

面向 agent 的可执行配方：在 Photoshop 里创建、修改、导出文档。事实分两类：

- **官方**：来自 Adobe UXP 文档，文末有链接。
- **实测**：在 Windows、Adobe Photoshop 2026 上用 `.psjs` 跑通的经验。未核实的标为 **未核实**，不要当成官方 API。

**成功标准：** 脚本写完结果 JSON（含 `done` / `ok` / `fatal` / `errs`），只关闭自己创建的文档，用户已打开的未保存文档未被改动；需要像素对齐时，导出 PNG 并与金图做 ΔE / 像素差后迭代。

**先停下来：** 目标机上的 Photoshop 正在编辑用户未保存的文档，且任务可能打开、保存或关闭「当前文档」而不是脚本自己创建的文档。

---

## 1. 何时用 / 何时不用

**用 `.psjs` 当：**

- 需要 Photoshop 才能做的事：渲染带图层样式的像素、按图层导出切片、用 batchPlay 建形状/文字/效果、把结果存成 PSD/PNG。
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
- `dialogOptions` 写在**每条描述符**的 `_options` 里，取值 `"silent"`（默认，出错也不弹窗）、`"dontDisplay"`（一般不弹，缺参或出错时可能弹）、`"display"`。
- `batchPlay` 参数非法会 **reject**；目标不存在等运行期失败则 **resolve**，返回 `{_obj:"error", message, result}`。`result === -128` 表示用户取消。务必检查返回数组。
- 文件系统：脚本无 manifest，权限由 Photoshop 内置。官方脚本章节仍写：可用文件选择器和 `getTemporaryFolder()`。`getEntryWithUrl` / `createSessionToken` 是官方 FileSystemProvider API（见第 4 节实测）。
- `require("uxp").script` 提供 `executionContext`、`args`、`setResult(result)`。官方 FAQ（首发说明）曾写：**第一版不支持向脚本传参**。不要依赖命令行参数或 `script.args` 把任务配置传进去；把路径和参数写进脚本，或让脚本读旁边的 JSON。`setResult` 对「`Photoshop.exe` 启动后 agent 如何读返回值」**未核实**，用结果文件。
- 同一时刻只能跑一个脚本，脚本不能再调另一个脚本。

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
- 社区有报告：仅靠 Windows 文件关联双击 `.psjs` 可能只启动 PS 却不执行脚本；同一文件用 `File > Scripts > Browse` 正常。agent 启动请显式传 `Photoshop.exe` + 脚本路径，并始终以结果文件为准。

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

**macOS：** 官方支持拖到 Dock / 窗口。下面这条启动方式 **未核实**（未见官方文档，本仓库也未在 macOS 上实测）：

```bash
open -a "Adobe Photoshop 2026" /path/to/run_job.psjs
```

同样必须轮询结果文件。

骨架脚本见 [examples/run_job.psjs](examples/run_job.psjs)（顶层 await、`executeAsModal`、文档 id 守卫、结果文件、`try/catch`）。

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

## 5. 文档安全（必须）

- **禁止**改、存、关用户已打开且未保存的文档。
- 跑脚本前记下 `app.documents.map(d => ({name: d.name, id: d.id}))`，结束后再记一次，写进日志。
- 只把**本脚本创建或本脚本打开**的文档 id 放进 `ours`；所有 `batchPlay`、保存、关闭之前检查 `app.activeDocument.id` 是否在集合里。
- 切片时用「当前图层新建文档」会多出一个文档：新文档 id 也要加入 `ours`，用完 `closeWithoutSaving()`，再把 `app.activeDocument` 切回主文档。失败时同样只关 `ours` 里的文档。
- 官方：`executionContext.hostControl.registerAutoCloseDocument(docID)` 可在用户取消或 modal 结束时自动关闭临时文档；成功保存并打算留给美术继续改时，先 `unregisterAutoCloseDocument`。

---

## 6. 验证循环

1. 脚本导出 PNG（必要时再存 PSD）。
2. 在 agent 侧与金图对比（CIEDE2000 ΔE、像素差、局部放大）。
3. 根据差异改脚本（路径、渐变、图层样式），不要在已打开的用户文档上手改然后指望可复现。
4. 重跑、再导出、再比，直到达标或说明剩余差距。

---

## 7. 实测坑

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
