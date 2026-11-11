# Agent 切图工作流

用 `psd-tools` 离线推断、人工审核、再由 `.psjs` 按 `layer_id` 导出 1x PNG，最后写出引擎无关的布局 JSON 并做像素校验。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

助手子命令：

```text
python slicing_helper.py infer --psd design.psb --out-dir out --artboard 排行榜_总榜
python slicing_helper.py preview --manifest out/manifest.json --out out/preview_annotated.png [--ref out/full_render.png]
python slicing_helper.py build-layout --manifest out/manifest.json --export-log out/export_log.json --slices-dir out/slices --out out/layout.json
python slicing_helper.py validate --layout out/layout.json --slices-dir out/slices --full-render out/full_render.png --out-dir out/validate
```

依赖：`psd-tools`、Pillow、numpy。渐变填充的预览/九宫格分析还需要 `scipy`（`pip install scipy` 或 `psd-tools[composite]`）。大 PSD/PSB 打开时把 `ParseLimits.max_objects` 设为 `None`，否则默认 1,000,000 会直接失败（实测）。

---

## 1. 推断（只处理可见图层）

- 隐藏图层整棵子树都忽略。交互状态必须由美术在 PSD 里显式打开，助手不会把隐藏的 on/off 层标出来。
- 多画板文档必须用 `--artboard`（名称或 `layer_id`）指定目标画板，不要一次切整份画布。
- 含可见文字的组、带 `*_on` / `*_off` 状态子层的组、以及「大底板 + 小图标」的组要下钻，把底图、高亮、图标、文字拆成独立资源，不要烘焙成一张图。
- 纯图形组（如奖牌、头像框）以及叶子层（像素 / 形状 / 智能对象）连同其上方可见剪贴层，作为一条切图，记录 `select_ids`。
- 调整图层、空图层（宽或高为 0）跳过。
- `psd-tools` 的 bbox 是逻辑尺寸，**不含**图层效果。最终可见尺寸以 Photoshop 导出为准。

每条 manifest 记录：

| 字段 | 说明 |
|------|------|
| `name` | 规范名，见第 4 节 |
| `layer_id` | 与 UXP `layer.id` 一致，用来定位，不要按重名 |
| `select_ids` | 导出时要多选的图层（自身 + 可见剪贴层） |
| `source_name` / `source_path` | 仅供对照，禁止当作输出文件名 |
| `kind` | `image` \| `nine_slice` \| `dynamic_text` \| `art_text` |
| `logical_bounds` | `[left, top, right, bottom]`，文档坐标，不含效果 |
| `nine_slice` | `{left, top, right, bottom}` 边距，或 `null` |
| `text` | 初步文本属性（完整属性在 PS 导出时回填） |
| `parent` | 层级里的容器名 |

`dynamic_text` 与 `art_text` 的启发式：纯数字/分数/倒计时/玩家名 → `dynamic_text`；带明显装饰性描边或渐变的标题（如「Ranking」）→ `art_text`。审核时以 JSON 为准。

---

## 2. 审核门闩

推断只写 `status: "pending_review"`。必须同时给出：

- 标注预览 PNG：在画板合成图上框出每条切图，写规范名和类型，九宫格画参考线。`psd-tools` 的合成可能缺效果，预览以结构核对为主。
- manifest JSON：用户直接改这份文件（改名、改类型、改边距、删条目）。

**确认之前不要启动导出。** 确认后把 `status` 改为 `confirmed` 再跑 `.psjs`。授权测试可以在已生成预览的前提下，把自动清单视为已确认。

---

## 3. 导出

用 [examples/export_slices.psjs](../examples/export_slices.psjs)。把文件顶部的 `ROOT_URL`、`SOURCE_URL`、`SOURCE_PATH_HINT` 改成任务目录；不要加额外 CLI 参数。

- 只打开（或按路径复用）任务指定的那份 PSD/PSB 副本，核对 `doc.path`。用完若是脚本自己打开的，`closeWithoutSaving()`；不要覆盖保存。
- 每条描述符 `_options.dialogOptions = "silent"`。`executeAsModal` + 顶层 `await`，对源文档 `suspendHistory`。
- 对 `image` / `nine_slice` / `art_text`：按 `select_ids` 多选，`make` 新文档，修剪透明边，必要时把 16 位转 8 位，存 1x PNG。效果烘焙进图。重复实例各导一份，不去重。
- 对 `dynamic_text`：只 `get` 图层，写出 `textKey` 里的内容、`fontPostScriptName`、字号、颜色（绿色字段是 `grain`）、对齐、描边等，**不出图**。
- 同时导出目标画板的整图 PNG（`full_render.png`）供校验。若「从图层新建文档」仍是整份多画板画布，按 manifest 里的 `artboard.bounds`（文档坐标）裁切，不要裁 `0,0,w,h`（那会切到最左上角的画板）。
- 逻辑尺寸用 `boundsNoEffects`（已核实）。`bounds` / DOM `layer.bounds` **不一定**等于含效果的可见框（有时是整份画布或有偏移）。可见框以修剪后的 PNG 尺寸为准：若与逻辑尺寸一致，沿用逻辑框；否则用报告的原点加上 PNG 宽高。
- 写 `export_log.json`（含 `done` / `ok` / `fatal` / `errs` / `docs_before` / `docs_after`），启动后轮询这份文件。

然后：

```text
python slicing_helper.py build-layout ...
```

会按导出日志和 PNG 回填可见/逻辑框，并用导出图重新检测九宫格，写成布局 JSON。

---

## 4. 命名规则

规范名：`模块_类型_用途_状态`，全小写，下划线分隔，**禁止**直接使用 PSD 图层名。

- **模块**：从画板或路径推断，如 `rank` / `shop` / `popup` / `common`（顶栏、底栏等公共铬）。
- **类型**：`bg` / `btn` / `icon` / `frame` / `tab` / `item` / `deco` / `img` / `mask`。文本切图用 `img`。
- **用途**：`navbar`、`topbar`、`item3`、`avatar_item1`、`medal`、`score_item2`、`timer` 等。列表实例把序号编进用途，不去重。
- **状态**：有才写，如 `on` / `off` / `gold` / `silver` / `bronze` / `self` / `empty` / `full`。

重名时追加 `_2`、`_3`。助手会做中英文关键词映射，映射不到就用稳定的 `l{layer_id}` 兜底，仍然不要回退成原始图层名。

---

## 5. 九宫格启发式

对候选图（最小边 ≥ 48px）做像素分析：在相邻列/行里找最长的「几乎相同」连续区段（默认容差 `tol=1`，最短 8px），把这段当作可拉伸区，四边剩下的就是边距 `{left, top, right, bottom}`（对应圆角、描边、不可拉伸的边）。

拒绝以下情况：没有足够长的均匀段、拉伸后中心几乎没了、或四边距都小于 8px（接近铺满的纯色会被误判成 1px 边）。小图标不要标成 `nine_slice`。

推断阶段用 `psd-tools` 合成（可能缺效果，渐变需要 scipy）。导出后再用 PS 的 1x PNG 重测，并写进布局 JSON。审核时可改边距。

---

## 6. 布局 JSON schema（`psjs-layout/v1`）

引擎无关的中间格式，坐标是**画板坐标系**、1x 像素。不包含任何引擎导入器。

```json
{
  "schema": "psjs-layout/v1",
  "source": {
    "file": "design.psb",
    "doc_size": [9340, 4036],
    "artboard": {
      "id": 5771,
      "name": "排行榜_总榜",
      "bounds": [1180, 0, 2260, 2340],
      "size": [1080, 2340]
    }
  },
  "nodes": [
    {
      "id": "rank_bg_item1_normal",
      "name": "rank_bg_item1_normal",
      "kind": "nine_slice",
      "layer_id": 5996,
      "select_ids": [5996],
      "parent": "group_rank_item_list_1",
      "source_name": "panel_list",
      "source_path": "排行榜_总榜/排行榜/list/list 1/panel_list",
      "visible_bounds": {"left": 14, "top": 512, "right": 1076, "bottom": 734},
      "logical_bounds": {"left": 14, "top": 523, "right": 1064, "bottom": 723},
      "anchor": {"x": 539, "y": 623},
      "pivot": {"x": 0.5, "y": 0.5},
      "nine_slice": {"left": 30, "top": 30, "right": 29, "bottom": 29},
      "image": "rank_bg_item1_normal.png",
      "text": null
    }
  ]
}
```

- `visible_bounds`：含图层效果的可见像素框。
- `logical_bounds`：不含效果（来自 `boundsNoEffects`，缺则回退 `psd-tools` bbox）。
- `anchor`：逻辑框中心（画板坐标）。`pivot`：归一化到逻辑框，默认 `(0.5, 0.5)`。
- `dynamic_text` 的 `image` 为 `null`，属性放在 `text`（`content`、`font`、`size`、`color`、`alignment`、`stroke` 等）。
- 层级见 `tree` / `parent`。

manifest schema 为 `psjs-slice-manifest/v1`，字段见第 1 节，另含 `status`、`naming_rule`、`review_gate`。

---

## 7. 校验

`validate` 会：

1. 按 `visible_bounds` 和 z 序把非 `dynamic_text` 的 PNG 拼回画板大小的画布（普通 alpha 叠放，不模拟 PS 混合模式）。
2. 与 PS 整图做对比：通道 MAE、PSNR、像素差占比（最大通道差 >16 / >48），以及 CIEDE2000 的 mean / p95 / 占比（ΔE>2、ΔE>5）。同时给出遮罩动态文字区域后的同一组数字。
3. 检查漏切（manifest 有条目但没有 PNG）、重名、空图（尺寸为 0 或 alpha 全 0）。`dynamic_text` 检查是否写出了文本属性。
4. 写出 `recomposite.png`、`validate_side_by_side.jpg`、`validate_report.json`。

动态文字本来就不会出现在拼图里，所以「全图 ΔE」会包含文字空洞；对照时以遮罩后的数字为主。页签、底栏等重叠层按普通叠放也会和 PS 的混合结果有差，这是预期限制，不是漏切。

---

## 8. 实测（Windows / Photoshop 2026）

对象：`排行榜.psb` 的副本（未改、未保存、未关闭用户可能打开的原文档或「排行榜-恢复的.psb」）。目标画板「排行榜_总榜」（`layer_id` 5771，1080×2340）。时间均为 Asia/Shanghai（UTC+8）。

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 82.9 秒（打开 2.4 s，条目合成 64.6 s，画板合成 15.8 s）。初步类型：image 23、nine_slice 16、dynamic_text 24、art_text 2。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 24、art_text 2。 |
| 预览 | 已写出标注预览（框、规范名、九宫格线）。测试中将自动清单视为已确认，未等人工改 JSON。 |
| 导出 | 65/65 成功（41 张 PNG + 24 条仅属性的动态文字），另写出 1080×2340 整图。墙钟约 43.1 秒（15:36:48–15:37:31），条目耗时合计 33.3 秒（均 512 ms），整图 6.1 秒。脚本打开副本（doc id 59）后不保存关闭；`docs_before` / `docs_after` 均为空。 |
| 校验 | 漏切 / 重名 / 空图：0。全图：MAE 10.225，PSNR 17.65 dB，>16 的像素 14.117%，>48 的 8.308%，mean ΔE 4.082，p95 ΔE 34.063，ΔE>2 占 19.257%，ΔE>5 占 12.376%。遮罩动态文字后：MAE 6.277，PSNR 20.54 dB，>16 占 10.893%，>48 占 4.901%，mean ΔE 2.599，p95 ΔE 13.569，ΔE>2 占 16.423%，ΔE>5 占 9.01%。约 2.5 秒。 |

**一并核实的 API / 行为：**

- `boundsNoEffects` 确是不含效果的逻辑框。同次 `get` 还可能出现 `bounds`、`boundsNoMask`，部分层有 `pathBounds` 或 `boundingBox`。不要把 `bounds` 直接当成「含效果可见框」。
- 对多画板文档做「从图层新建」时，新文档可能仍是整份画布；`saveAs.png` 有时会按当前画板写出 1080×2340 的文件，但 `doc.width` 仍是整布尺寸。模板改为按 `artboard.bounds` 裁切后再记尺寸。
- 16 位文档导出前需转 8 位。
- `psd-tools` 1.26 默认 `max_objects=1_000_000`，这份 PSB 会超限；渐变九宫格分析需要 scipy。

**已知限制（这次测试里出现的）：**

- 「007」曾被当成名次（`place`）而不是名字；助手里已改为「1–2 位数字才是名次」，本次清单未重跑。
- 「Daily」判为 `art_text`（有装饰描边），「Total」判为 `dynamic_text`，拼图里 Total 页签缺字。
- 剩余像素差主要来自：动态文字不出图、页签/底栏重叠层按普通 alpha 叠放（不模拟 PS 混合）、投影边缘，以及个别列表行（如自己那一行）的填充差异。
