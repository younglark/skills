# Agent 切图工作流

助手只准备材料；跑这个 skill 的 **agent**（大模型）阅读材料后编写 manifest（组件、部件、状态、规范名）。用户改 JSON 确认后，再由 `.psjs` 按 `layer_id` 导出 1x PNG，写出引擎无关的布局 JSON，并校验默认状态拼图。启动、守卫、`silent` 等规则见 [SKILL.md](../SKILL.md)。模板：[examples/export_slices.psjs](../examples/export_slices.psjs)、[examples/slicing_helper.py](../examples/slicing_helper.py)。

## 决策树

按这个顺序做，**确认前不要导出**。

1. **打开材料。** 先看 `full_render_preview.png`（若已有 Photoshop 导出的 `full_render.png`，以它为准），再对照 `crops_sheet.jpg`、`crops/<layer_id>.png` 和 `group_effects.json`。用 `crops_index.json` 把 id 对回 `tree.json`（可见性、路径、文本、效果）。不要猜测 id，也不要去读不存在的裁切图。选导出目标时，检查该元素最近的父组（或代表该元素的组）上有没有图层效果（如投影）：效果在组上、内层叶子没有时，导出该组，不要因为 `crops/` 里没有该组缩略图就退回叶子。
2. **识别组件。** 把页签组、底栏、按钮、列表行等交互控件收成组件，按槽位拆开，不要按 PSD 分组原样切开。
3. **分清共用零件和随状态变化的零件。** 图标、始终存在的底等跨状态不变的，放进该组件的 `shared`，文件名不加状态后缀。槽/底的明暗、选中高亮、只在某一态出现的标签等，放进 `states.<state>.parts`。
4. **区分状态变体和旧稿。** 主体/内容不变、只改明度、描边、发光、配色或缩放的，是状态变体，可以导出。同一槽位但换了内容或徽记的，当旧稿忽略。内容或母题对不上、吃不准时才写 `uncertain_hidden`，并附上两侧裁切图。
5. **只给会随状态变化的零件记 missing。** 以同组里状态最完整的那一项为模板（例如某选中项有 base + highlight + label），每个缺少其中一件的兄弟项都要列出，**包括 highlight**。不要因为共用图标没有另一态就标 missing。单个特殊样式的列表行（例如 self 高亮行）只是这一行的一个实例：不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都有成套变体。
6. **写好审核清单后停下。** 跑 `flatten` + `preview`，`status` 保持 `pending_review`，等用户改 JSON 确认后再导出。
7. **确认后一口气跑完，不要停在导出。** 导出 → `build-layout` → `validate` 是一个整体；`export_log.json` 写出不算完成，只有 `validate/validate_report.json` 写出并读过 `issues`、遮罩 PSNR / ΔE 后才算完成。每条长命令都设超时并把输出写进日志；某步超时或报错时，读日志、修正后重跑这一步，不要静默结束。

选中槽上的隐藏层：若它落在**当前选中项**的槽里，并且是同一主体、只是加了发光或高亮样式，应作为该态的 `highlight`。若默认态就是这一项被选中，即使该高亮层在 PSD 里仍是隐藏的，也要纳入默认选中态，否则拼图会缺一层。只有内容或徽记对不上时，才用 `uncertain_hidden`。

**叠加还是替换：** 判断选中槽上的隐藏层时，先问它是叠在已有资源上面，还是要取代某个资源。一层发光/气泡/光晕盖在当前选中槽之上（与可见的选中底同时存在、范围相近、颜色同系，图层名或所在组像「当前 / 选中 / current / selected / glow / bg」之类），就是**叠加的高亮**，即使形状和底不同也算该态的 `highlight`，不要因为「可能被可见的选中底取代」就挂进 `uncertain_hidden`。只有它与某个可见资源占同一角色、内容或徽记不同（同一位置另一枚图标、另一段文案）时，才是替换，按旧稿处理。认作高亮后，按第 5 步给缺高亮的兄弟项记 `missing`（`role: highlight`）。

助手子命令：

```text
python slicing_helper.py prepare --psd design.psb --out-dir out [--artboard <画板名或id>]
python slicing_helper.py flatten --manifest out/manifest.json
python slicing_helper.py preview --manifest out/manifest.json --out out/preview_annotated.png [--ref out/full_render.png]
python slicing_helper.py build-layout --manifest out/manifest.json --export-log out/export_log.json --slices-dir out/slices --out out/layout.json
python slicing_helper.py validate --layout out/layout.json --slices-dir out/slices --full-render out/full_render.png --out-dir out/validate [--fonts-dir <字体目录>]
# 可选：关键词草稿，只写 draft_manifest.json，不能代替 agent 编写的 manifest
python slicing_helper.py infer --psd design.psb --out-dir out [--artboard <画板名或id>] [--rules naming_rules.json]
```

`--artboard` 可选；省略则处理所有可见顶层画板，没有画板时处理整份画布。`--rules` 只是给 agent 看的项目命名约定，不再作为自动分类的主路径。

### Python 环境

优先使用**项目虚拟环境**，不要把包装进系统 Python。系统解释器里没有依赖时，不要对系统 Python 执行 `pip install`。

```text
python3 -m venv .venv
# Windows：.venv\Scripts\activate    POSIX：source .venv/bin/activate
pip install "psd-tools[composite]" pillow numpy
# 之后一律用该环境的解释器调用助手，例如：
#   .venv\Scripts\python.exe examples/slicing_helper.py prepare ...
#   .venv/bin/python examples/slicing_helper.py prepare ...
```

若工作区附近已有装好这些包的 venv，直接用它的 `python`，不必再装一份。需要的包：`psd-tools`、Pillow、numpy；预览合成与九宫格分析还要 `scipy`（已包含在 `psd-tools[composite]` 里）。

打开 PSD/PSB 时传入 `ParseLimits`。已在 **psd-tools 1.26.1** 核实的签名是：

```python
from psd_tools import PSDImage
from psd_tools.psd.parse_limits import ParseLimits  # 也可 from psd_tools import ParseLimits

# ParseLimits(max_read_bytes=None, max_total_bytes=None, max_objects=1_000_000, max_nesting_depth=64)
# max_objects 默认为 1_000_000；大文档会触发 ParseLimitError。传入 None 可关闭这一上限。
# 不要写 ParseLimits.max_objects = None（那不是正确用法）。
PSDImage.open(path, parse_limits=ParseLimits(max_objects=None))
```

助手的 `open_psd()` 已经这样调用。不要猜参数名，也不要对系统 Python 安装依赖。

---

## 1. 分工

| 谁 | 做什么 |
|----|--------|
| 助手 `prepare` | 准备材料：默认状态整图预览、带 id 的逐层裁切缩略图、含隐藏层的图层树 JSON、基于像素的九宫格候选。不做命名，不组组件，不判定状态。 |
| **Agent** | 同时看整图、裁切图和图层树，编写 `manifest.json`：组件、部件、规范名、类型、状态和语义层级。 |
| 用户 | 看标注预览，改 JSON 确认；`status` 在确认前保持 `pending_review`。真正缺失的状态列在 `missing` 里，由用户决定要不要补图。 |
| 助手 `flatten` + `.psjs` 导出 | 把组件树展平为导出条目；按 `layer_id` 出 1x PNG。属于已识别交互组件某状态的隐藏层可临时显示后导出，然后恢复可见性，文档不保存。 |
| 助手 `build-layout` / `validate` | 写出按组件语义组织的布局 JSON；把**默认状态**拼回去与 PS 整图对比；再为每个交替状态单独出预览图。 |

隐藏层里与交互状态无关的内容忽略，不要导出。只有按设计会随状态变化的部件，在 PSD 里又找不到合格变体时，才写入 `missing`，**禁止捏造**。跨状态不变的零件（例如两个状态下都用同一枚图标）放在组件的 `shared` 里，不要因为「没有另一态的图标」就把图标标成 missing。

`infer` 仍可用，但只写出 `draft_manifest.json`（`status: draft_for_agent_review`）。那是关键词草稿，agent 必须审过或重写，不能直接当确认清单导出。

---

## 2. 准备材料：要看什么

`prepare` 在 `--out-dir` 写出：

| 文件 | 用途 |
|------|------|
| `full_render_preview.png` | 画板**当前可见/默认状态**的合成（psd-tools，部分图层效果可能缺失）。若已有 Photoshop 导出的 `full_render.png`，审核和校验以 PS 整图为准。 |
| `tree.json` | 图层树：`name`、`path`、`kind`、文本内容、效果摘要、`visible`（含隐藏层）、`bbox`、`layer_id`。 |
| `crops/<layer_id>.png` | 主要是可导出叶子（及剪贴单元）的裁切图，文件名就是 `layer_id`。隐藏层只要与画板相交且有像素也会出图。带图层效果的组在能合成时也会出一张组裁切图（psd-tools 缩略图可能画不出烘焙效果，仍请用组 id 导出）。空层、不相交或合成后无像素的层不会出图。 |
| `crops_index.json` | 已写出的裁切图，以及带图层效果的组（即使暂无缩略图）：id、可见性、路径、效果、`select_ids` / `suggested_select_ids`、九宫格候选。叶子若挂在带效果的父组下，会标注 `parent_group_id`。查 id 用这份索引，不要猜文件名。 |
| `crops_sheet.jpg` | 所有裁切图的拼图，隐藏项标 `HID`，带效果的组标 `FX`，便于快速扫。 |
| `group_effects.json` | 带图层效果的组：效果列表、子叶子 id、建议的 `select_ids`（即组 id）、可选的组裁切路径。选导出目标时先看这份。 |
| `nine_slice_candidates.json` | 像素启发式给出的九宫格候选（最长的均匀相邻行/列；两边边距都小于 `min_run` 则拒绝）。agent 决定采不采纳。 |
| `materials.json` | 上述路径和数量统计。 |

阅读顺序建议：先看整图，理解屏幕结构；再对照 `crops_sheet.jpg` / 单张裁切图看每个零件长什么样；用 `crops_index.json` 和 `group_effects.json` 把 id 对回 `tree.json` 的路径、可见性、文本和效果。不要只根据图层名做决定，名称经常不准。

`crops/` 里不是树上每一个 `layer_id` 都有文件。查某个候选 id 时，先在 `crops_index.json`、`group_effects.json`（或 `crops_sheet.jpg` 上的 id）里找，不要直接拼 `crops/<id>.png` 去猜。若某个组 id 没有裁切图，或打开 `crops/<group_id>.png` 得到 404：**不要**因此改用内层叶子。先在 `tree.json` / `group_effects.json` 看该组有没有图层效果（如 DropShadow、外发光）。若效果在组上、内层叶子（智能对象、形状等）没有，应导出该组：`layer_id` 和 `select_ids` 用组 id。没有裁切图不是退回子层的理由。可见框必须包含烘焙后的效果，不要裁到叶子的逻辑框（`logical_bounds` / `boundsNoEffects`）。助手会尽量写出组裁切图，并在叶子条目上标注 `parent_group_id` 与 `suggested_select_ids`；psd-tools 缩略图可能仍画不出投影，Photoshop 按组导出时才会把效果烘焙进去。

若索引里没有这个 id，也不要反复尝试打开不存在的文件，更不要把它当成「缺了一态」。回到 `tree.json`：无效果的分组可以继续用其中有裁切图的叶子；空层、在画板外、或合成后没有像素的，跳过并在 notes 里说明。没有裁切图本身不能证明存在或缺失某个状态。

---

## 3. 识别组件与状态

把「同一控件的不同零件、不同状态」收成组件，而不是按 PSD 分组原样切开。

常见交互组件：

- **页签 / 分段开关**（`tab_group` → 若干 `tab`）：每个页签拆成独立部件——底/背景、选中高亮、图标、文案；每个页签有自己的状态（如 `normal` / `selected`）。两个页签的开关：一组 `tab_group`，两个 `tab`，各有 normal 与 selected；选中高亮是单独资源，文案也是单独资源。
- **底栏导航**（`navbar` → 若干 `nav_item`）：同样按项拆图标、文案、选中态，不要把整条导航烘焙成一张图。
- **按钮、列表行、开关**：底、高亮、图标、文字分开；状态（normal / pressed / selected / disabled）分开。

先分清哪些零件是**跨状态共用**的，哪些是**随状态变化**的。判断依据是设计本身：看已经识别出的成对状态（例如某导航项的 selected 对 normal），哪些图层换了，哪些没换。没换的（同一枚图标、同一段始终存在的底）放进该组件的 `shared`（也作 `common_parts` 或 `parts`），规范名**不加状态后缀**。会换的（槽/底的明暗、选中高亮、只在选中时出现的标签）放进 `states.<state>.parts`，文件名带上状态段。

规则：

1. **不要把状态烘焙在一起。** 选中描边不要画进底图；on/off 不要合成一张。
2. **看整图里当前显示的是哪一态**，把它标为该组件的 `default_state`。
3. **到隐藏层里找交替状态。** 先判断它是「同一控件的另一态」还是「旧版 / 备选稿」，不要因为位置重叠就当成状态。
   - **状态变体**：主体/内容不变（同一个形象、同一句文案、同一块底），差别只在状态样式，例如明度、饱和度、高亮、描边、发光、缩放、配色。这类可以纳入该组件，导出时临时显示。
   - **旧版或备选稿**：同一位置的隐藏层换了内容或换了母题（例如换了一枚不同的徽记，或数字与符号不是同一套图形），应忽略，不要导出，也不要用它去填某一态。若该零件本身是跨状态共用的，把已经在用的那份真资源放进 `shared`，**不要**因为忽略了旧稿就把图标写成 missing。
   - **吃不准**：不要擅自选边。在审核清单里写 `uncertain_hidden`，带上两边的 `layer_id` 和裁切图路径，让用户决定。
   隐藏层若与任何已识别组件的状态无关，同样忽略。

   落在**当前选中项槽位**上的隐藏层（先做文首的「叠加还是替换」判断）：若主体/内容与该项正在使用的资源相同，只是加了发光、描边或高亮样式，应作为该选中态的 `highlight`（不要只用 `uncertain_hidden` 挂起）。若默认态就是这一项被选中，即便该层在 PSD 里仍隐藏，也要纳入默认选中态，否则校验拼图会少一层。只有内容或徽记明显不同、无法判断是高亮还是旧稿时，才写 `uncertain_hidden`。
4. **只有按设计会随状态变化的部件，找不到合格变体时，才记 missing。** 先看同组里哪一项的某一态最完整（零件最多），用它的角色集合当模板。例如某个选中项有 base + highlight + label，则每个缺少其中一件的兄弟项都要在 `missing` 里列出对应 `{ "state": "selected", "role": "..." }`，**包括 highlight**，等用户确认要不要补。跨状态不变的零件即使只有一份，也不是缺失，不要写「没有另一态的图标」。禁止捏造未画出的变体。

   单个特殊样式的列表行（例如只有一行画了 self 高亮）只是**这一行的一个实例**，不是「每一行都该有 self / 这一行还缺 normal」。不要给其它行推断缺失的 self，也不要给这一行推断缺失的 normal，除非设计里每行都画出了成套状态变体。
5. **导出目标要看效果在哪一层。** 图层效果（如投影、外发光、描边）经常加在代表该元素的**父组**上，内层叶子（智能对象、形状）自身没有效果。选 `layer_id` / `select_ids` 时，检查最近的父组（或代表该元素的组）：组有效果而叶子没有，就选组，不要只选内层叶子。组 id 没有裁切图时同样如此。可见框用组的含效果框，不要裁到叶子的逻辑框。例如某个图标组 `<item>` 上有 DropShadow、内层叶子没有：manifest 里应写该组 id。
6. 底图、高亮、图标、文字始终是独立 `parts`。类型仍是 `image` | `nine_slice` | `dynamic_text` | `art_text`。可变文案（分数、名字、倒计时、1–2 位名次）用 `dynamic_text`（只出属性，不出图）；带效果的固定标签（页签名、导航名、标题）用 `art_text`（出图）。
7. 列表重复行各导一份，不去重。全部 1x。

---

## 4. 命名

规范名：`模块_类型_用途_状态`，**小写**，用下划线。不要直接复制 PSD 图层名。

| 段 | 含义 | 例子 |
|----|------|------|
| 模块 | 功能模块或画板的拉丁/拼音缩写 | `main`、`<module>`、`common` |
| 类型 | 资源种类 | `bg`、`btn`、`icon`、`img`、`tab` |
| 用途 | 这个零件干什么 | `highlight`、`title`、`item_a`、`avatar`、`item3` |
| 状态 | 只给随状态变化的零件加这一段；跨状态共用的零件不加 | `normal`、`selected`、`on`、`off` |

同一组件下，不同状态的同一零件应共用前三段、只改状态段，例如 `main_tab_left_normal` 与 `main_tab_left_selected`。跨状态不变的零件去掉状态段，例如 `common_icon_item_a`，不要写成 `common_icon_item_a_selected`。可选的 `--rules` / [naming_rules.example.json](../examples/naming_rules.example.json) 只提供项目用词习惯（模块词、固定标签等），供 agent 参考，不是自动分类器。

---

## 5. Manifest schema（`psjs-slice-manifest/v2`）

Agent 编写的清单顶层如下。`flatten` 会根据 `components` 生成 `entries`（导出用的扁平列表），不要手写 entries 作为主源。若清单里带上 `layer_tree`（`prepare` 写出的图层树），`flatten` 和 `build-layout` 会按 PSD 自下而上的绘制顺序排列条目；默认状态校验的 src-over 拼图依赖这个顺序，顺序不对会明显拉低 PSNR。

```json
{
  "schema": "psjs-slice-manifest/v2",
  "status": "pending_review",
  "source": {
    "file": "design.psb",
    "doc_size": [1080, 1920],
    "artboard": { "id": 100, "name": "screen", "bounds": [0, 0, 1080, 1920], "size": [1080, 1920] }
  },
  "components": [],
  "missing": [],
  "notes": []
}
```

组件节点常用字段：

| 字段 | 含义 |
|------|------|
| `id` | 稳定 id，用于布局和状态预览文件名，如 `main_tab_group` |
| `type` | `screen`、`tab_group`、`tab`、`navbar`、`nav_item`、`list_item`、`button` 等 |
| `name` | 规范名或语义名 |
| `default_state` | PSD **当前显示**的状态名 |
| `shared`（亦作 `common_parts` / `parts`） | 跨状态不变的部件（图标、始终存在的底等）。规范名不加状态后缀 |
| `states.<state>.parts` | 只放随该状态变化的部件（底/槽的明暗、选中高亮、仅某态出现的标签） |
| `states.<state>.from_hidden` | 这些部件来自隐藏层时为 `true` |
| `missing` | **仅**用于按设计会随状态变化、但 PSD 里没有合格变体的部件。写成 `{ "state", "role"?, "reason"? }` 或状态名字符串。不要把共用零件的「没有另一态」写成 missing |
| `uncertain_hidden` | 吃不准是状态变体还是旧稿时填写：`layer_ids`、对应 `crops`、以及要问用户的问题。不要在这里擅自选用其中一侧 |
| `ignored_hidden` | 已认定为无关或旧稿而忽略的隐藏层（`layer_id` + `reason`），供审核对照 |
| `children` | 子组件 |
| `source_path` / `layer_id` | 仅供对照的源图层引用 |

部件（`part`）常用字段：`name`（规范文件名）、`role`（`base`、`highlight`、`icon`、`text` 等）、`kind`、`layer_id`、`select_ids`、`hidden`、`nine_slice`、`text`、`logical_bounds`、`source_name`、`source_path`。

`dynamic_text` 的 `text` 在导出时由 Photoshop 补全（内容、字体 PostScript 名、字号、颜色、描边、对齐、`click_point` 为相对整份文档的百分比）。

### 5.1 示例：双页签开关 + 底栏导航

下面是一份**虚构**界面，不要把它当成某个项目的图层名。屏幕当前显示：左侧页签选中、底栏第一项选中。隐藏层里有右侧页签的选中文案、左侧页签的未选中文案，以及第一项的未选中底。右侧页签没有单独的未选中高亮（高亮是 group 上的共享资源）。导航图标在两种状态下不变，放在各 `nav_item` 的 `shared` 里。以第一项的 selected（base + highlight + label）为模板，第二项缺少的 selected 底、高亮和标签都记入 `missing`，不要把共用图标标成缺失。

```json
{
  "schema": "psjs-slice-manifest/v2",
  "status": "pending_review",
  "components": [
    {
      "id": "main_screen",
      "type": "screen",
      "name": "main_screen",
      "children": [
        {
          "id": "main_tab_group",
          "type": "tab_group",
          "name": "main_tab_group",
          "default_state": "left",
          "parts": [
            {
              "name": "main_tab_highlight_selected",
              "role": "highlight",
              "kind": "nine_slice",
              "layer_id": 201,
              "select_ids": [201],
              "hidden": false,
              "source_name": "tab highlight",
              "source_path": "tabs/highlight"
            }
          ],
          "children": [
            {
              "id": "main_tab_left",
              "type": "tab",
              "name": "main_tab_left",
              "default_state": "selected",
              "states": {
                "selected": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_tab_left_selected",
                      "role": "text",
                      "kind": "art_text",
                      "layer_id": 211,
                      "hidden": false,
                      "source_name": "Left on"
                    }
                  ]
                },
                "normal": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_tab_left_normal",
                      "role": "text",
                      "kind": "art_text",
                      "layer_id": 212,
                      "hidden": true,
                      "source_name": "Left off"
                    }
                  ]
                }
              },
              "missing": []
            },
            {
              "id": "main_tab_right",
              "type": "tab",
              "name": "main_tab_right",
              "default_state": "normal",
              "states": {
                "normal": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_tab_right_normal",
                      "role": "text",
                      "kind": "art_text",
                      "layer_id": 221,
                      "hidden": false,
                      "source_name": "Right off"
                    }
                  ]
                },
                "selected": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_tab_right_selected",
                      "role": "text",
                      "kind": "art_text",
                      "layer_id": 222,
                      "hidden": true,
                      "source_name": "Right on"
                    }
                  ]
                }
              },
              "missing": []
            }
          ]
        },
        {
          "id": "main_navbar",
          "type": "navbar",
          "name": "main_navbar",
          "default_state": "item_a",
          "shared": [
            {
              "name": "main_bg_navbar",
              "role": "base",
              "kind": "nine_slice",
              "layer_id": 301,
              "hidden": false,
              "source_name": "nav bg"
            }
          ],
          "children": [
            {
              "id": "main_nav_item_a",
              "type": "nav_item",
              "name": "main_nav_item_a",
              "default_state": "selected",
              "shared": [
                {
                  "name": "main_icon_item_a",
                  "role": "icon",
                  "kind": "image",
                  "layer_id": 311,
                  "hidden": false
                }
              ],
              "states": {
                "selected": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_a_selected",
                      "role": "base",
                      "kind": "nine_slice",
                      "layer_id": 310,
                      "hidden": false
                    },
                    {
                      "name": "main_img_item_a_selected",
                      "role": "text",
                      "kind": "art_text",
                      "layer_id": 312,
                      "hidden": false
                    },
                    {
                      "name": "main_nav_item_a_highlight_selected",
                      "role": "highlight",
                      "kind": "nine_slice",
                      "layer_id": 314,
                      "hidden": true
                    }
                  ]
                },
                "normal": {
                  "from_hidden": true,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_a_normal",
                      "role": "base",
                      "kind": "nine_slice",
                      "layer_id": 313,
                      "hidden": true
                    }
                  ]
                }
              },
              "missing": []
            },
            {
              "id": "main_nav_item_b",
              "type": "nav_item",
              "name": "main_nav_item_b",
              "default_state": "normal",
              "shared": [
                {
                  "name": "main_icon_item_b",
                  "role": "icon",
                  "kind": "image",
                  "layer_id": 321,
                  "hidden": false
                }
              ],
              "states": {
                "normal": {
                  "from_hidden": false,
                  "parts": [
                    {
                      "name": "main_bg_nav_item_b_normal",
                      "role": "base",
                      "kind": "image",
                      "layer_id": 322,
                      "hidden": false
                    }
                  ]
                }
              },
              "missing": [
                {
                  "state": "selected",
                  "role": "base",
                  "reason": "The most complete selected sibling has base + highlight + label; this item has no selected base drawn."
                },
                {
                  "state": "selected",
                  "role": "highlight",
                  "reason": "The most complete selected sibling has a highlight; this item has none drawn."
                },
                {
                  "state": "selected",
                  "role": "text",
                  "reason": "The most complete selected sibling has a label; this item has none drawn."
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "missing": []
}
```

要点：高亮是 `tab_group` 上的共享部件，没有和某个页签的文案烤在一起。导航图标若在各状态下不变，放在该 `nav_item` 的 `shared` 里，文件名不加 `_selected` / `_normal`。`missing` 只记录按设计会变却没画出来的零件（例如某项没有 selected 底或高亮），不要因为「没有另一态的图标」去标共用图标。忽略旧稿不会让共用图标变成 missing。`flatten` 之后，隐藏的状态部件会带 `hidden: true`，导出脚本会临时显示再恢复；共用部件会出现在每一个状态的预览里。

---

## 6. 审核门闩

确认前不要导出。

1. 用 `flatten` 把组件树展成 `entries`，再用 `preview` 画标注预览（框出每个切图、标规范名和九宫格线；来自隐藏态的条目会计入标题）。
2. 把 `preview_annotated.png` 和 `manifest.json` 交给用户。用户改 JSON：改名、改类型、改九宫格、拆/合并部件、确认或删除 `missing`、把不该导出的隐藏层拿掉。
3. `status` 在用户确认前保持 `pending_review`，确认后改为 `confirmed`。导出脚本拒绝其它状态（测试授权下允许 `pending_review`）。
4. 用户确认后才进入导出。

---

## 7. 导出

[examples/export_slices.psjs](../examples/export_slices.psjs) 只硬编码一个 `CONFIG_URL`，从作业 JSON 读路径（见 [export_config.example.json](../examples/export_config.example.json)）。按 `layer_id`（及 `select_ids` 里的剪贴层）导出 1x PNG，图层效果烘焙进图；重复实例各导一份。`dynamic_text` 不出图，只写文本属性；`art_text` 出图。

若条目 `hidden`（或 `from_hidden`）为真，脚本先显示该层及其被隐藏的祖先，再「从图层新建」、trim、保存，然后把可见性改回去。整次作业在 `suspendHistory` 里进行；脚本自己打开的文档一律 `closeWithoutSaving`。每条描述符 `_options.dialogOptions = "silent"`。只操作作业打开的文档，绝不改、存、关用户已打开的其它文档。

可见框记录规则不变：临时文档与源画布对齐时，紧致可见原点 = trim 前 `layer.bounds.left/top` 减 trim 后的 left/top。不要把 trim 前的松框左上角当成 PNG 原点。

---

## 8. 布局 JSON schema（`psjs-layout/v2`）

层级按**组件语义**重组，例如 `screen > tab_group > tab > state > part`。源图层的 `layer_id`、`source_name`、`source_path` 只作对照，不是树的结构依据。

顶层：

```json
{
  "schema": "psjs-layout/v2",
  "source": { "artboard": { "id": 100, "name": "screen", "bounds": [0, 0, 1080, 1920], "size": [1080, 1920] } },
  "components": [],
  "nodes": [],
  "missing": [],
  "notes": []
}
```

- `components`：与 manifest 相同的语义树，但每个 part 已补上导出后的 `image`、`visible_bounds`、`logical_bounds`、`nine_slice`、文本属性。
- `nodes`：扁平列表，便于引擎遍历和校验放置。每条含 `name`、`kind`、`component_id`、`part_role`、`state`、`default`、`hidden`、`visible_bounds`（含效果，画板坐标）、`logical_bounds`（不含效果）、`anchor`（逻辑框中心）、`pivot`（0.5, 0.5）、`nine_slice`、`image`、`text`。
- 坐标是画板像素（1x）。各引擎导入器自己写，不在本 skill 里。

`tab_group` / `navbar` 默认视为互斥：预览某个子项的 `selected` 时，兄弟项改用 `normal`（或 `off`）。

---

## 9. 校验

1. **默认状态**：只拼 `default: true` 的部件（即 PSD 里当前显示的那一态，加上始终可见的 chrome），用 src-over（`Image.alpha_composite`）叠回画板，与 Photoshop 整图比像素差 / CIEDE2000 ΔE。同时报告 Pillow 渲染动态文字后的数字、以及遮罩动态文字区域后的数字，并列出误差最大的 10 条。再检查漏切、重名、空图。
2. **交替状态**：对每个交互组件的非默认状态（含仅存在于 `missing` 中的状态），按「共用部件 + 该状态部件」拼图，互斥组的兄弟项切到对应态，写出 `validate/states/<component_id>_<state>.png`。只对真正缺失的、按设计随状态变化的零件画红框并标 `missing`。这些预览**不**和默认态整图比 PSNR（对不上是正常的）。

`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。Pillow 无法复现 PS 的混合模式、文字 hinting 和描边，遮罩后若残差集中在重叠层或文字本身，属于预期。

---

## 10. 实测（Windows / Photoshop 2026）

对象：一份多画板 UI PSB 的**副本**（未改、未保存、未关闭用户可能打开的原文档或其它未指定文档）。实测画板是一个 1080×2340 的列表界面，65 个可见元素。时间均为 Asia/Shanghai（UTC+8）。项目词表不进技能仓库，回归时用仓库外的 `--rules` 文件。

### 10.1 第一轮（修前）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 82.9 秒（打开 2.4 s，条目合成 64.6 s，画板合成 15.8 s）。初步类型：image 23、nine_slice 16、dynamic_text 24、art_text 2。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 24、art_text 2。 |
| 预览 | 已写出标注预览。测试中将自动清单视为已确认。 |
| 导出 | 65/65 成功（41 张 PNG + 24 条仅属性的动态文字），另写出 1080×2340 整图。墙钟约 43.1 秒（15:36:48–15:37:31）。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 均为空。 |
| 校验 | 漏切 / 重名 / 空图：0。全图：MAE 10.225，PSNR 17.65 dB，mean ΔE 4.082。遮罩动态文字后：MAE 6.277，PSNR 20.54 dB，mean ΔE 2.599，p95 ΔE 13.569，ΔE>2 占 16.423%，ΔE>5 占 9.01%。 |

当时的问题：成对页签里同一文案角色一种判为 `art_text`、另一种判为 `dynamic_text`，拼图缺字；带前导零的数字串被当成名次；几乎每条切图周围有一圈描边状残差。

描边残差的根因（已用逐条 best-fit 和一次 PS probe 核实，不是色管、JPEG 或预乘）：「从图层新建」得到的临时文档与源画布对齐，DOM `layer.bounds` 比 trim 后的不透明区域更松。第一轮把这个较松框的左上角当成 PNG 原点，没有加上被 trim 掉的左边/上边像素。trim 后 `layer.bounds.left/top` 是内缩量（常为负数，如 `-5,-6`）。紧致原点 = `before.left - after.left`。不透明像素（A≥250）在正确原点上与 PS 整图完全重合（mae=0）。偏移不是全局 +2,+2：有的切图约 (0,+2)，有的约 (+5,+6) 或 (+3,+4)，卡片/美术字约 (+2,+2)。

### 10.2 第二轮（修后重跑，同副本）

| 步骤 | 结果 |
|------|------|
| 推断 | 65 条；约 21.9 秒（15:54:32–15:54:54；打开 2.5 s，条目 1.7 s，画板合成 17.7 s）。类型：image 23、nine_slice 16、dynamic_text 22、art_text 4。用导出 PNG 重测九宫格后：image 18、nine_slice 21、dynamic_text 22、art_text 4。 |
| 预览 | 已重写标注预览。测试中将自动清单视为已确认。 |
| 分类 | 成对页签文案均为 `art_text`。顶栏标题与导航标签都是带效果的固定文案，各自为 `art_text`（角色不同，不必成对）。带前导零的数字串为 `dynamic_text`（按名字，不是名次）。分数、倒计时、名字、1–2 位名次均为 `dynamic_text`。 |
| 导出 | 65/65 成功（43 张 PNG + 22 条仅属性的动态文字；页签与导航上先前缺字的固定文案改为美术字出图）。墙钟约 42.1 秒（15:55:34–15:56:16），整图 5.3 秒。43 条光栅切图都记下了 `visible_bounds_from_trim_insets`。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 均为空。日志里 `full_render.out_size` 仍可能是整份多画板画布的尺寸（crop 后 `doc.width` 不变），但 `saveAs.png` 写出的文件是当前画板 1080×2340。 |
| 校验 | 漏切 / 重名 / 空图：0。约 10.4 秒（15:57:17–15:57:27）。用文档所用字体（`--fonts-dir`）渲染了全部 22 条动态文字。 |

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

Pillow 文字版 mean ΔE 最差的 10 条全部是 `dynamic_text`（分数、名字、名次），来自 Pillow 无法复现 PS 的 hinting、字框锚点和外描边，不是切图错位。遮罩后残留的 >16 差像素共 3429 个（约占遮罩后像素的 0.15%），几乎都叠在底栏、导航按钮与末行列表底板等重叠层。这是普通 src-over 不模拟 PS 混合模式造成的，属预期残差，不是漏切或原点错误。

### 10.3 规则文件外置后的回归（同副本）

项目词表放在仓库外的 `--rules` JSON（不进技能仓库）。带规则完整重跑：推断 65 条（约 25.8 秒，16:11:01–16:11:28），类型与 8.2 相同（导出后 image 18 / nine_slice 21 / dynamic_text 22 / art_text 4）；导出 65/65 成功、43 张 PNG（16:11:56–16:12:39，约 42.4 秒）；校验约 10.6 秒，漏切/重名/空图为 0。遮罩动态文字：MAE 0.076，PSNR **46.24 dB**，mean ΔE **0.047**（与 10.2 一致）。Pillow 文字全图：PSNR 23.39 dB，mean ΔE 1.041。`docs_before` / `docs_after` 为空。

不传 `--rules` 时，对同一画板只跑推断也成功，且 **kind 完全一致**（同样 22 条动态文字、4 条美术字），说明分类核心不依赖项目词表。规范名则更泛化：画板名无法拉丁化时模块落在 `main`，徽章/头像等缺少用途词，只能编成 `item1_3` 这类序号。举例（带规则 → 无规则）：

- `<module>_bg_item1_normal` → `main_bg_item1`
- `<module>_icon_badge_1` → `main_bg_item1_3`
- `<module>_icon_avatar_item1` → `main_bg_item1_2`
- `<module>_img_tab_off` / `<module>_img_tab_on` → `main_img_name_off` / `main_img_name_on`
- `<module>_img_title`（标题或导航标签）→ `main_img_name`
- 分数、倒计时、1–2 位名次、带前导零的名字等，无规则时仍能识别用途（`main_img_score_item1`、`main_img_timer`、`main_img_place_item4`、`main_img_name_item6`），只是模块变成 `main`。

**一并核实的 API / 行为：**

- `boundsNoEffects` 确是不含效果的逻辑框。同次 `get` 还可能出现 `bounds`、`boundsNoMask`，部分层有 `pathBounds` 或 `boundingBox`。不要把 `bounds` 直接当成「含效果可见框」。
- DOM `layer.bounds` 在「从图层新建」的栅格副本上往往比 trim 更松；trim 后 left/top 是内缩量，可见原点用 `before - after`。
- 对多画板文档做「从图层新建」时，新文档可能仍是整份画布；`saveAs.png` 有时会按当前画板写出目标尺寸的文件，但 `doc.width` 仍是整布尺寸。模板会尝试按 `artboard.bounds` 裁切；校验以实际 PNG 尺寸为准。
- 16 位文档导出前需转 8 位（trim 前就转，使内缩量与导出 PNG 一致）。
- `psd-tools` 1.26 默认 `max_objects=1_000_000`，大 PSB 会超限；渐变九宫格分析需要 scipy。
- 校验拼图用 `Image.alpha_composite`（src-over）。`Image.paste(..., mask=)` 不是正确的 Porter-Duff over。

### 10.4 Agent 驱动的组件 / 状态切图（同副本）

改成「助手只准备材料，agent 看图写清单」后再跑同一份列表界面副本、同一块 1080×2340 画板。时间均为 Asia/Shanghai（UTC+8）。

| 步骤 | 结果 |
|------|------|
| 准备材料 | `prepare` 约 25.3 秒。图层树含隐藏层；裁切图 169 张，其中 50 张来自隐藏层或隐藏祖先下的层（psd-tools 需临时打开祖先可见性才能合成出像素，只改内存、不写回 PSD）。 |
| Agent 清单 | 同时阅读整图、裁切图和图层树后编写 v2 manifest，不是关键词推断。交互组件按状态拆开，跨状态不变的零件进 `shared`，隐藏的交替状态纳入，无关隐藏层与旧稿忽略。默认态仍为 65 条，类型与 10.2 / 10.3 相同：image 18 / nine_slice 21 / dynamic_text 22 / art_text 4。 |
| 双页签开关 | `tab_group` 下两个 `tab`。默认：右侧页签为 selected（高亮 + 美术字），左侧为 normal（未选中高亮 + 美术字）。隐藏层里找到了右侧的 normal（高亮 + 文案）和左侧的 selected（高亮 + 文案），均已导出。没有缺失的页签状态。共享底槽与中缝是 group 级部件。预留的第三页签组与其它废弃页签组忽略。 |
| 底栏导航 | `navbar` 下三个 `nav_item`。从已有成对状态看出：槽/底和高亮随状态变化，图标跨状态共用，标签只出现在 selected。三项的图标都进 `shared`（文件名不加状态后缀）。右侧项 selected：槽、标签、以及一层默认下仍隐藏的高亮；normal：另一明度的槽（隐藏层，同主体）；图标始终是那份真资源。同一位置另有一张不同徽记的隐藏图标，按旧稿忽略，**不**把共用图标写成 missing。中、左项：图标共用，normal 槽在可见层；selected 的槽/高亮没有画，记入 `missing`（`role: base` / `highlight`）。中间项另有隐藏的 selected 标签。左侧项没有 selected 标签，一并记入 `missing`。隐藏祖先下的锁与其它预留锁忽略。 |
| 审核 / 导出 | 已写标注预览。测试中将 agent 清单视为已确认（`pending_review`）。导出 75/75 成功（约 57 秒，16:39:18–16:40:16）；当时 10 条隐藏态均临时显示后恢复。随后把跨状态共用的图标从单一状态中抽出（不重跑 Photoshop，只重命名已有切图），并按「只记录随状态变化却没画的零件」重写 `missing`。脚本打开副本后不保存关闭；`docs_before` / `docs_after` 为空。 |
| 校验 | 约 12.7 秒。漏切 / 重名 / 空图：0。按 PSD 绘制顺序拼默认态后，遮罩动态文字：MAE 0.076，PSNR **46.24 dB**，mean ΔE **0.047**（与 10.2 / 10.3 一致）。Pillow 文字全图：PSNR 23.39 dB，mean ΔE 1.041。另写出 4 张交替状态预览（右侧页签 normal、左侧页签 selected、中间导航 selected、右侧导航 normal），只供目视，不和默认整图比 PSNR。 |

要点：默认态数字要和以前对齐，清单条目必须按 PSD 自下而上的顺序绘制；`flatten` / `build-layout` 在提供 `layer_tree` 时会排这个序。顺序不对时（例如把页签中缝画在高亮下面），遮罩 PSNR 会掉到约 38 dB。
