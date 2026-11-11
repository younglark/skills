# Agent 切图工作流

用 `psd-tools` 离线推断、人工审核、再由 `.psjs` 按 `layer_id` 导出 1x PNG，最后写出引擎无关的布局 JSON 并做像素校验。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

助手子命令：

```text
python slicing_helper.py infer --psd design.psb --out-dir out --artboard 排行榜_总榜
python slicing_helper.py preview --manifest out/manifest.json --out out/preview_annotated.png [--ref out/full_render.png]
python slicing_helper.py build-layout --manifest out/manifest.json --export-log out/export_log.json --slices-dir out/slices --out out/layout.json
python slicing_helper.py validate --layout out/layout.json --slices-dir out/slices --full-render out/full_render.png --out-dir out/validate [--fonts-dir path/to/fonts]
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

`dynamic_text` 与 `art_text` 按内容语义判断：

- 分数、倒计时、玩家名、以及 **1–2 位**名次数字 → `dynamic_text`。三位及以上数字（如 `"007"`）当作玩家名，也是 `dynamic_text`，不要当成名次。
- 固定 UI 文案（如 Daily / Total、导航上的 Ranking / Home / Shop）若带描边、渐变、投影、外发光、斜面等较重效果 → `art_text`，由 PS 栅格化，避免拼图缺字。
- 同一文案角色在 on/off 或页签成对状态里必须同一 `kind`。若组里任一条是 `art_text`，其余一并升为 `art_text`（例如 Daily 与 Total）。导航下的 Ranking 与顶栏标题 Ranking 是不同角色，不必强行相同。
- 审核时以 JSON 为准。

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
- 逻辑尺寸用 `boundsNoEffects`（已核实）。`bounds` / DOM `layer.bounds` **不一定**等于含效果的可见框（有时是整份画布，或比 trim 后的不透明区域更松）。
- 可见框必须按 trim 内缩量计算，不要把 trim 前较松的 `layer.bounds` 左上角当成 PNG 原点。从图层新建的临时文档与源画布对齐时：先记下 trim 前的 `layer.bounds`，trim 后 `layer.bounds.left/top` 是被裁掉的左边/上边像素（常为负数，如 `-5,-6`），紧致原点 = `before.left - after.left`、`before.top - after.top`，尺寸用 PNG 宽高。`slicing_helper.py build-layout` 遇到 `visible_bounds_from_trim_insets` 时不要再改回逻辑框。
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

1. 按 `visible_bounds` 和 z 序把非 `dynamic_text` 的 PNG 用 **src-over**（`Image.alpha_composite`，不是 `paste+mask`）拼回画板大小的画布。不模拟 PS 混合模式。
2. 另外用 Pillow 按导出的文本属性把 `dynamic_text` 画进拼图（`--fonts-dir` 有对应 TTF/OTF 时用原字体）。`click_point` 是相对整份文档的百分比，需换算到画板坐标；字号已含 `transform.yy`。Pillow 不能复现 PS 的 hinting、外描边位置和图层样式，文字区仍会有残差。
3. 与 PS 整图对比，**同时报告两组数字**：`diff_text_rendered`（拼图含 Pillow 文字）和 `diff_text_masked`（不画动态文字，并遮罩其框）。指标：通道 MAE、PSNR、像素差占比（最大通道差 >16 / >48），CIEDE2000 的 mean / p95 / 占比（ΔE>2、ΔE>5）。
4. 按每条切图的可见框计算误差，报告 mean ΔE 最差的 10 条（文字渲染版与遮罩版各一份）。
5. 检查漏切（manifest 有条目但没有 PNG）、重名、空图（尺寸为 0 或 alpha 全 0）。`dynamic_text` 检查是否写出了文本属性。
6. 写出 `recomposite.png`（含文字）、`recomposite_text_masked.png`、`validate_side_by_side.jpg`、`validate_side_by_side_masked.jpg`、`validate_report.json`。

页签、底栏等重叠层按普通 src-over 叠放也会和 PS 的混合结果有差，这是预期限制，不是漏切。

---

## 8. 实测（Windows / Photoshop 2026）

对象：`排行榜.psb` 的副本（未改、未保存、未关闭用户可能打开的原文档或「排行榜-恢复的.psb」）。目标画板「排行榜_总榜」（`layer_id` 5771，1080×2340）。时间均为 Asia/Shanghai（UTC+8）。

### 8.1 第一轮（修前）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 82.9 秒（打开 2.4 s，条目合成 64.6 s，画板合成 15.8 s）。初步类型：image 23、nine_slice 16、dynamic_text 24、art_text 2。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 24、art_text 2。 |
| 预览 | 已写出标注预览。测试中将自动清单视为已确认。 |
| 导出 | 65/65 成功（41 张 PNG + 24 条仅属性的动态文字），另写出 1080×2340 整图。墙钟约 43.1 秒（15:36:48–15:37:31）。脚本打开副本（doc id 59）后不保存关闭；`docs_before` / `docs_after` 均为空。 |
| 校验 | 漏切 / 重名 / 空图：0。全图：MAE 10.225，PSNR 17.65 dB，mean ΔE 4.082。遮罩动态文字后：MAE 6.277，PSNR 20.54 dB，mean ΔE 2.599，p95 ΔE 13.569，ΔE>2 占 16.423%，ΔE>5 占 9.01%。 |

当时的问题：Daily 为 `art_text`、Total 为 `dynamic_text`，拼图缺 Total；「007」被当成名次（助手随后改为仅 1–2 位数字是名次，本轮清单未重跑）；几乎每条切图周围有一圈描边状残差。

描边残差的根因（已用逐条 best-fit 和一次 PS probe 核实，不是色管、JPEG 或预乘）：「从图层新建」得到的临时文档与源画布对齐，DOM `layer.bounds` 比 trim 后的不透明区域更松。第一轮把这个较松框的左上角当成 PNG 原点，没有加上被 trim 掉的左边/上边像素。trim 后 `layer.bounds.left/top` 是内缩量（常为负数，如奖牌 `-5,-6`）。紧致原点 = `before.left - after.left`。不透明像素（A≥250）在正确原点上与 PS 整图完全重合（mae=0）。偏移不是全局 +2,+2：头像约 (0,+2)，奖牌约 (+5,+6) 或 (+3,+4)，卡片/美术字约 (+2,+2)。

### 8.2 第二轮（修后重跑，同副本）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 21.9 秒（15:54:32–15:54:54；打开 2.5 s，条目 1.7 s，画板合成 17.7 s）。类型：image 23、nine_slice 16、dynamic_text 22、art_text 4。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 22、art_text 4。 |
| 预览 | 已重写标注预览。测试中将自动清单视为已确认。 |
| 分类 | Daily 与 Total 均为 `art_text`（页签成对一致）。顶栏 Ranking 与底栏 Ranking 都是带效果的固定文案，各自为 `art_text`（角色不同，不必成对）。「007」为 `dynamic_text`（玩家名，不是名次）。分数、倒计时、名字、1–2 位名次均为 `dynamic_text`。 |
| 导出 | 65/65 成功（43 张 PNG + 22 条仅属性的动态文字；新增 Total 与底栏 Ranking 的美术字）。墙钟约 42.1 秒（15:55:34–15:56:16），整图 5.3 秒。43 条光栅切图都记下了 `visible_bounds_from_trim_insets`（如金牌原点 1205,579 = 1200-(-5), 573-(-6)）。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 均为空。日志里 `full_render.out_size` 仍可能是整布 9340×4036（crop 后 `doc.width` 不变），但 `saveAs.png` 写出的文件是当前画板 1080×2340。 |
| 校验 | 漏切 / 重名 / 空图：0。约 10.4 秒（15:57:17–15:57:27）。用 Exo-Black / Exo-ExtraBold 渲染了全部 22 条动态文字。 |

校验数字对比（同画板、同副本）：

| 指标 | 修前（遮罩动态文字） | 修后（遮罩动态文字） | 修后（Pillow 文字，全图） | 修后（Pillow 文字，排除文字框） |
|------|----------------------|----------------------|---------------------------|----------------------------------|
| MAE | 6.277 | **0.076** | 2.440 | 0.125 |
| PSNR | 20.54 dB | **46.24 dB** | 23.39 dB | 39.01 dB |
| mean ΔE | 2.599 | **0.047** | 1.041 | 0.067 |
| p95 ΔE | 13.569 | 0.153 | 0.429 | 0.166 |
| ΔE>2 | 16.423% | 0.295% | 2.786% | 0.338% |
| ΔE>5 | 9.01% | 0.157% | 2.547% | 0.199% |
| 像素差>16 | 10.893% | 0.147% | 2.527% | 0.190% |

Pillow 文字版 mean ΔE 最差的 10 条全部是 `dynamic_text`（分数、名字、名次），来自 Pillow 无法复现 PS 的 hinting、字框锚点和外描边，不是切图错位。遮罩后残留的 >16 差像素共 3429 个（约占遮罩后像素的 0.15%），几乎都叠在底栏：`common_bg_navbar`、`common_btn_navtab_*`、末行列表底板、`common_icon_rank`。这是普通 src-over 不模拟 PS 混合模式造成的，属预期残差，不是漏切或原点错误。

**一并核实的 API / 行为：**

- `boundsNoEffects` 确是不含效果的逻辑框。同次 `get` 还可能出现 `bounds`、`boundsNoMask`，部分层有 `pathBounds` 或 `boundingBox`。不要把 `bounds` 直接当成「含效果可见框」。
- DOM `layer.bounds` 在「从图层新建」的栅格副本上往往比 trim 更松；trim 后 left/top 是内缩量，可见原点用 `before - after`。
- 对多画板文档做「从图层新建」时，新文档可能仍是整份画布；`saveAs.png` 有时会按当前画板写出 1080×2340 的文件，但 `doc.width` 仍是整布尺寸。模板会尝试按 `artboard.bounds` 裁切；校验以实际 PNG 尺寸为准。
- 16 位文档导出前需转 8 位（trim 前就转，使内缩量与导出 PNG 一致）。
- `psd-tools` 1.26 默认 `max_objects=1_000_000`，这份 PSB 会超限；渐变九宫格分析需要 scipy。
- 校验拼图用 `Image.alpha_composite`（src-over）。`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。
