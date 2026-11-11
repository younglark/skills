# batchPlay 备忘（实测描述符）

先用 DOM（`app.createDocument`、`doc.createLayer`、`createTextLayer`、`createLayerGroup`、`saveAs`、`closeWithoutSaving`）。下面这些是 DOM 不够、并在 Photoshop 2026 的 `.psjs` 里跑通过的 actionJSON。发现新命令请用官方「Copy As JavaScript」或「Record Action Commands」，不要编造键。

`bp()` 应：1) 确认 `app.activeDocument.id` 属于本脚本；2) 检查返回数组里是否有 `{_obj:"error"}`。

官方推荐把 `dialogOptions` 写在每条描述符的 `_options` 上。**agent 跑批处理一律用 `"silent"`**，不要用 `"dontDisplay"`：后者在命令失败时仍会弹出阻塞错误框，点确定后只得到 `{_obj:"error", message:"", result:-128}`；`"silent"` 不弹窗，返回带消息的 `{_obj:"error", result:-25920}`。实测里也曾把 `{ dialogOptions: "dontDisplay" }` 作为 `batchPlay` 的**第二个参数**传入且未报错，但该写法**不在**官方 options 表（`synchronousExecution`、`continueOnError`、`immediateRedraw`）里，也无法安全验证它是否生效。新代码把 `dialogOptions` 写在描述符 `_options` 里。

## 单位与颜色

```js
const px  = (v) => ({ _unit: "pixelsUnit", _value: v });
const pct = (v) => ({ _unit: "percentUnit", _value: v });
const ang = (v) => ({ _unit: "angleUnit", _value: v });
// 注意绿色字段名为 grain
const rgb = (h) => ({
  _obj: "RGBColor",
  red: parseInt(h.slice(1, 3), 16),
  grain: parseInt(h.slice(3, 5), 16),
  blue: parseInt(h.slice(5, 7), 16)
});
const mode = (m) => ({ _enum: "blendMode", _value: m });
```

## 选中图层（按 id，不要按重名）

```js
{ _obj: "select", _target: [{ _ref: "layer", _id: layerId }], makeVisible: false }
```

## 圆角矩形 / 椭圆（实时形状路径）

```js
{
  _obj: "make",
  _target: [{ _ref: "contentLayer" }],
  using: {
    _obj: "contentLayer",
    type: { /* solidColorLayer 或 gradientLayer */ },
    shape: {
      _obj: "rectangle",
      unitValueQuadVersion: 1,
      top: px(t), left: px(l), bottom: px(b), right: px(r),
      topLeft: px(tl), topRight: px(tr), bottomRight: px(br), bottomLeft: px(bl)
    }
  },
  _options: { dialogOptions: "silent" }
}
```

椭圆把 `shape._obj` 换成 `"ellipse"`，去掉四角半径。

## 自定义闭合路径（一般不是可调半径的实时形状）

先写入 work path，再从当前路径生成内容图层，最后删掉 work path：

```js
{
  _obj: "set",
  _target: [{ _ref: "path", _property: "workPath" }],
  to: {
    _obj: "pathClass",
    pathComponents: [{
      _obj: "pathComponent",
      shapeOperation: { _enum: "shapeOperation", _value: "add" },
      subpathListKey: [{
        _obj: "subpathsList",
        closedSubpath: true,
        points: [
          { _obj: "pathPoint",
            anchor:   { _obj: "paint", horizontal: px(x), vertical: px(y) },
            forward:  { _obj: "paint", horizontal: px(fx), vertical: px(fy) },
            backward: { _obj: "paint", horizontal: px(bx), vertical: px(by) },
            smooth: true }
        ]
      }]
    }]
  },
  _options: { dialogOptions: "silent" }
}
{ _obj: "make", _target: [{ _ref: "contentLayer" }], using: { _obj: "contentLayer", type: { /* fill */ } } }
{ _obj: "delete", _target: [{ _ref: "path", _property: "workPath" }] }
```

这样的路径通常没有 `keyOriginType`，属性栏改不了圆角。

## 渐变填充（含 shapeburst 描边要用的 gradient 对象）

```js
// stops: [location 0-4096, "#RRGGBB", opacityPercent]
function grad(stops, smooth) {
  return {
    _obj: "gradientClassEvent",
    name: "custom",
    gradientForm: { _enum: "gradientForm", _value: "customStops" },
    interfaceIconFrameDimmed: smooth, // 渐变「平滑度」，0–4096 对应 0–100%（键名是 PS 历史遗留，照抄即可）
    colors: stops.map((s) => ({
      _obj: "colorStop",
      color: rgb(s[1]),
      type: { _enum: "colorStopType", _value: "userStop" },
      location: s[0],
      midpoint: 50
    })),
    transparency: stops.map((s) => ({
      _obj: "transferSpec",
      opacity: pct(s[2]),
      location: s[0],
      midpoint: 50
    }))
  };
}

const type = {
  _obj: "gradientLayer",
  angle: ang(90),
  type: { _enum: "gradientType", _value: "linear" }, // 或 "shapeburst"
  gradient: grad([[0, "#000000", 100], [4096, "#FFFFFF", 100]], 4096),
  dither: true, align: true, scale: pct(100), reverse: false,
  gradientsInterpolationMethod: { _enum: "gradientInterpolationMethodType", _value: "classic" }
};
```

色标 `location` 范围是 0–4096。`interfaceIconFrameDimmed` 为 0 和 4096 时出图不同（实测黑白线性渐变：0 接近线性，4096 是 S 曲线，256×64 图约 98% 像素有差异，单通道最大差 12）。默认用 4096，除非要对齐某个特定观感。

## 图层样式：描边 / 内阴影 / 投影

写入目标图层的 `layerEffects`。多描边用 `frameFXMulti`；单条可用 `frameFX`。内阴影必须用 `innerShadowMulti`，否则会被静默忽略。

```js
{
  _obj: "set",
  _target: [
    { _ref: "property", _property: "layerEffects" },
    { _ref: "layer", _id: layerId }
  ],
  to: {
    _obj: "layerEffects",
    scale: pct(100),
    frameFXMulti: [
      {
        _obj: "frameFX", enabled: true, present: true, showInDialog: true,
        style: { _enum: "frameStyle", _value: "outsetFrame" }, // 或 "insetFrame"
        paintType: { _enum: "frameFill", _value: "gradientFill" },
        mode: mode("normal"), opacity: pct(100), size: px(10), overprint: false,
        gradient: grad([[0, "#FFFFFF", 20], [4096, "#FFFFFF", 0]], 0),
        angle: ang(90),
        type: { _enum: "gradientType", _value: "shapeburst" },
        reverse: false, dither: true, scale: pct(100), align: true,
        offset: { _obj: "paint", horizontal: pct(0), vertical: pct(0) }
      }
    ],
    innerShadowMulti: [{
      _obj: "innerShadow", enabled: true, present: true, showInDialog: true,
      mode: mode("multiply"), color: rgb("#000000"), opacity: pct(40),
      useGlobalAngle: false, localLightingAngle: ang(90),
      distance: px(4), chokeMatte: pct(0), blur: px(8),
      noise: pct(0), antiAlias: true
    }],
    dropShadow: {
      _obj: "dropShadow", enabled: true, present: true, showInDialog: true,
      mode: mode("multiply"), color: rgb("#000000"), opacity: pct(50),
      useGlobalAngle: false, localLightingAngle: ang(90),
      distance: px(4), chokeMatte: pct(0), blur: px(8),
      noise: pct(0), antiAlias: true, layerConceals: true
    }
  },
  _options: { dialogOptions: "silent" }
}
```

改完建议 `get` 回读，确认 `innerShadowMulti` / `frameFXMulti` 真的写上了。

## 矢量蒙版羽化、从图层新建文档、修剪透明边

```js
{ _obj: "set", _target: [{ _ref: "layer", _id: layerId }],
  to: { _obj: "layer", vectorMaskFeather: px(1.5) } }

{ _obj: "make", _target: [{ _ref: "document" }],
  name: "slice_name",
  using: { _ref: "layer", _enum: "ordinal", _value: "targetEnum" },
  version: 5 }

{ _obj: "trim",
  trimBasedOn: { _enum: "trimBasedOn", _value: "transparency" },
  top: true, bottom: true, left: true, right: true }
```

新建出的切片文档必须纳入文档 id 守卫；导出后 `closeWithoutSaving()`，再切回主文档。

## 读取图层属性

```js
{ _obj: "get", _target: [{ _ref: "layer", _id: layerId }] }
```

开发期可看全部属性；正式脚本应只取需要的字段，或用官方 `multiGet`。读历史状态请用 DOM（`doc.historyStates`），不要 `get` `historyStatesCount`（该命令不可用）。

对非活动文档：`set` 图层属性会失败（命令“设置”当前不可用）；先把该文档设为 `app.activeDocument`。`make` 图层加上 `{ _ref: "document", _id }` 则可以在非活动文档上建图层，且不会切换当前文档。
