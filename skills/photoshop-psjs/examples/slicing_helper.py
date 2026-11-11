#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent slicing helper: infer a manifest, annotate a preview, build layout JSON, validate.

Requires: psd-tools, Pillow, numpy

Subcommands:
  infer       Read a PSD/PSB with psd-tools and write manifest JSON + annotated preview.
  preview     Redraw the annotated preview from a (possibly edited) manifest.
  build-layout  Merge confirmed manifest + Photoshop export log + PNGs into layout JSON.
  validate    Recomposite slices from layout metadata and compare with a full PS render.

Visible layers only. Hidden layers are ignored entirely. Normalized names follow
模块_类型_用途_状态 and never copy the raw PSD layer name.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from psd_tools import PSDImage
except ImportError:  # infer is the only subcommand that needs it
    PSDImage = None

try:
    from psd_tools.psd.parse_limits import ParseLimits
except ImportError:
    ParseLimits = None

SCHEMA_MANIFEST = "psjs-slice-manifest/v1"
SCHEMA_LAYOUT = "psjs-layout/v1"

# ---------------------------------------------------------------------------
# Naming dictionaries (Chinese / English tokens -> slug parts)
# ---------------------------------------------------------------------------

MODULE_TOKENS = {
    "排行榜": "rank", "总榜": "rank", "日榜": "rank", "排行": "rank",
    "ranking": "rank", "rank": "rank",
    "商店": "shop", "shop": "shop",
    "弹窗": "popup", "popup": "popup",
    "公共": "common", "common": "common",
    "底部ui": "common", "顶部ui": "common", "底部": "common", "顶部": "common",
}

TYPE_TOKENS = [
    ("smartobject", None),  # not a type by itself
    ("边框", "frame"), ("frame", "frame"), ("头像框", "frame"),
    ("按钮", "btn"), ("btn", "btn"), ("选项", "btn"),
    ("页签", "tab"), ("tab", "tab"),
    ("图标", "icon"), ("icon", "icon"), ("徽章", "icon"), ("奖牌", "icon"),
    ("皇冠", "icon"), ("头像", "icon"), ("锁头", "icon"), ("秒表", "icon"),
    ("背景", "bg"), ("底衬", "bg"), ("底板", "bg"), ("衬底", "bg"),
    ("面板", "bg"), ("panel", "bg"),
    ("装饰", "deco"), ("光效", "deco"), ("halftone", "deco"),
    ("横线", "deco"), ("竖线", "deco"),
    ("mask", "mask"), ("蒙版", "mask"),
    ("列表项", "item"), ("item", "item"),
    ("img", "img"), ("图片", "img"),
]

PURPOSE_TOKENS = [
    ("navtab", "navtab"), ("navbar", "navbar"), ("bottom", "navbar"),
    ("topbar", "topbar"), ("顶部ui", "topbar"), ("底部ui", "navbar"),
    ("倒计时", "timer"), ("秒表", "timer"), ("timer", "timer"),
    ("标题", "title"), ("title", "title"),
    ("领奖台", "podium"), ("podium", "podium"),
    ("头像", "avatar"), ("avatar", "avatar"),
    ("奖牌", "medal"), ("徽章", "medal"), ("medal", "medal"),
    ("皇冠", "crown"), ("crown", "crown"),
    ("锁", "lock"), ("lock", "lock"),
    ("金币", "coin"), ("coin", "coin"),
    ("星星", "star"), ("五星", "star"), ("star", "star"),
    ("失败", "fail"), ("fail", "fail"),
    ("主页", "home"), ("home", "home"),
    ("商店", "shop"), ("shop", "shop"),
    ("排行", "rank"), ("ranking", "rank"),
    ("日榜", "daily"), ("daily", "daily"),
    ("总榜", "total"), ("total", "total"),
    ("名字", "name"), ("分数", "score"), ("排名", "place"),
    ("槽", "slot"), ("slot", "slot"),
    ("屏幕", "screen"), ("screen", "screen"),
    ("列表", "list"), ("list", "list"),
    ("横线", "hline"), ("竖线", "vline"), ("分割", "divider"),
    ("底", "base"),
]

STATE_TOKENS = [
    ("未选中", "off"), ("选中", "on"), ("_off", "off"), ("_on", "on"),
    ("off", "off"), ("on", "on"),
    ("自己", "self"), ("myself", "self"), ("self", "self"),
    ("普通", "normal"), ("normal", "normal"),
    ("金色", "gold"), ("黄金", "gold"), ("gold", "gold"),
    ("银色", "silver"), ("silver", "silver"),
    ("铜色", "bronze"), ("bronze", "bronze"),
    ("空", "empty"), ("empty", "empty"),
    ("满", "full"), ("full", "full"),
    ("按下", "pressed"), ("pressed", "pressed"),
    ("禁用", "disabled"), ("disabled", "disabled"),
    ("hover", "hover"),
]

SKIP_TOKENS = {"拷贝", "copy", "组", "图层", "形状", "矩形", "group", "layer"}

SPECIAL_LAYER_NAMES = {
    "no.1": ("icon", "medal", "gold"),
    "no.2": ("icon", "medal", "silver"),
    "no.3": ("icon", "medal", "bronze"),
    "bg": ("bg", "screen", ""),
    "bottom": ("bg", "navbar", ""),
    "panel_list": ("bg", "item", "normal"),
    "ui顶部bg": ("bg", "topbar", ""),
    "当前选项bg": ("bg", "navtab", "on"),
}

KIND_COLORS = {
    "image": (0, 170, 255),
    "nine_slice": (255, 160, 0),
    "dynamic_text": (80, 220, 80),
    "art_text": (220, 80, 200),
}


def enum_val(v):
    if v is None:
        return None
    if hasattr(v, "name"):
        return v.name
    return str(v)


def layer_kind(layer) -> str:
    k = getattr(layer, "kind", None)
    return k if isinstance(k, str) else (enum_val(k) or type(layer).__name__)


def is_group(layer) -> bool:
    try:
        return bool(layer.is_group())
    except Exception:
        return layer_kind(layer).lower() in {"group", "artboard"}


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def find_token(text: str, pairs):
    low = text.lower()
    for src, dst in pairs:
        if src.lower() in low:
            return dst
    return None


# ---------------------------------------------------------------------------
# Layer tree (visible-aware)
# ---------------------------------------------------------------------------

def collect_layer(layer, path, depth, index_in_parent, warnings):
    name = layer.name or ""
    kind = layer_kind(layer)
    try:
        lid = int(layer.layer_id) if getattr(layer, "layer_id", None) is not None else None
    except Exception:
        lid = None
        warnings.append(f"layer_id unreadable: {'/'.join(path + [name])}")
    try:
        bbox = [int(layer.left), int(layer.top), int(layer.right), int(layer.bottom)]
        width, height = int(layer.width), int(layer.height)
    except Exception as exc:
        bbox, width, height = None, 0, 0
        warnings.append(f"bbox failed {'/'.join(path + [name])}: {exc}")

    visible = bool(getattr(layer, "visible", True))
    kind_l = kind.lower()
    node = {
        "name": name,
        "path": "/".join(path + [name]) if path else name,
        "depth": depth,
        "index_in_parent": index_in_parent,
        "layer_id": lid,
        "kind": kind,
        "is_group": is_group(layer),
        "is_artboard": "artboard" in kind_l,
        "is_text": ("type" in kind_l or kind_l == "text"),
        "is_adjustment": "adjust" in kind_l or "gradientmap" in kind_l,
        "visible": visible,
        "opacity": getattr(layer, "opacity", None),
        "blend_mode": enum_val(getattr(layer, "blend_mode", None)),
        "clipping": bool(getattr(layer, "clipping", False)),
        "bbox": bbox,
        "width": width,
        "height": height,
        "effects": [],
        "text": None,
        "children": [],
        "_layer": layer,
    }
    try:
        fx = layer.effects
        if fx:
            for effect in fx:
                enabled = getattr(effect, "enabled", True)
                if enabled:
                    node["effects"].append(effect.__class__.__name__)
    except Exception:
        pass
    if node["is_text"]:
        try:
            text = getattr(layer, "text", None)
            node["text"] = str(text) if text is not None else None
        except Exception as exc:
            warnings.append(f"text unreadable {node['path']}: {exc}")
    if is_group(layer) and visible:
        # Hidden groups are ignored entirely: do not walk their children.
        for i, child in enumerate(layer):
            node["children"].append(collect_layer(child, path + [name], depth + 1, i, warnings))
    return node


def visible_children(node):
    return [c for c in (node.get("children") or []) if c.get("visible")]


def attach_clips(children):
    """Children are stored top-to-bottom (PSD order). Clipped layers sit above their base."""
    units = []
    pending = []
    for child in children:
        if child.get("clipping"):
            pending.append(child)
            continue
        units.append({"base": child, "clipped": pending})
        pending = []
    if pending:
        units.append({"base": None, "clipped": pending})
    return units


def has_visible_text(node):
    if not node.get("visible"):
        return False
    if node.get("is_text"):
        return True
    return any(has_visible_text(c) for c in visible_children(node))


def descendant_visible_ids(node):
    ids = []
    if not node.get("visible"):
        return ids
    if node.get("layer_id") is not None:
        ids.append(node["layer_id"])
    for child in visible_children(node):
        ids.extend(descendant_visible_ids(child))
    return ids


def looks_like_state_name(name: str) -> bool:
    low = (name or "").lower()
    return bool(re.search(r"(_on|_off|选中|未选中|myself|self|自己)$", low)) or any(
        tok in low for tok in ("_on", "_off", "daily_on", "daily_off", "total_on", "total_off")
    )


def is_large_on(node, art_w, art_h):
    w, h = node.get("width") or 0, node.get("height") or 0
    return w >= art_w * 0.6 or h >= art_h * 0.4 or (w * h >= art_w * art_h * 0.2)


def should_descend(node, art_w, art_h):
    if not node.get("is_group") or not node.get("visible"):
        return False
    if node.get("is_artboard"):
        return True
    kids = visible_children(node)
    if not kids:
        return False
    if has_visible_text(node):
        return True
    if any(looks_like_state_name(c.get("name") or "") for c in kids):
        return True
    group_kids = [c for c in kids if c.get("is_group")]
    if len(group_kids) >= 2:
        return True
    # Large background + other siblings (icon / deco) must stay separate resources.
    siblings = [c for c in kids if not c.get("clipping")]
    siblings.sort(key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
    if len(siblings) >= 2 and is_large_on(siblings[-1], art_w, art_h):
        largest = siblings[-1]
        others = siblings[:-1]
        if any((c.get("width") or 0) < (largest.get("width") or 0) * 0.6 for c in others):
            return True
    if is_large_on(node, art_w, art_h) and len(group_kids) >= 1 and len(kids) >= 2:
        return True
    return False


def classify_text(node) -> str:
    text = (node.get("text") or "").strip()
    name = node.get("name") or ""
    heavy = bool(set(node.get("effects") or []) & {"Stroke", "GradientOverlay", "FrameFX", "frameFX"})
    # psd-tools class names vary; also treat any stroke-like effect as heavy.
    heavy = heavy or any("stroke" in e.lower() or "frame" in e.lower() or "gradient" in e.lower()
                         for e in (node.get("effects") or []))
    if re.search(r"标题|title|美术字", name, re.I):
        return "art_text"
    if re.fullmatch(r"[\d.,\s]+", text) or re.search(r"\d+\s*[hHmM秒分点小时]|score|分数", text):
        return "dynamic_text"
    if re.search(r"[A-Za-z]+\d+", text) or re.search(r"(myself|player|name|id)", text, re.I):
        return "dynamic_text"
    if heavy and text and not re.search(r"\d", text):
        return "art_text"
    return "dynamic_text"


def infer_module(path: str, artboard_name: str) -> str:
    blob = f"{artboard_name}/{path}".lower()
    if any(s in blob for s in ("底部ui", "顶部ui", "navbar", "topbar", "底部/", "顶部/")):
        # shared chrome defaults to common unless clearly shop-only etc.
        if "商店" in blob or "shop" in blob:
            return "shop"
        return "common"
    for src, dst in MODULE_TOKENS.items():
        if src.lower() in blob:
            return dst
    return "common"


def list_item_index(path: str):
    m = re.search(r"/list\s*(\d+)(?:/|$)", path or "", re.I)
    return m.group(1) if m else None


def text_purpose_from_content(text: str, name: str):
    blob = f"{name} {text}".replace("\r", " ").replace("\n", " ")
    if re.search(r"\d+\s*[hH小时].*\d+\s*[mM分]|\d+h\s*\d+m", blob):
        return "timer"
    if re.search(r"^\s*(score|level|分数)\b", blob, re.I) or re.search(r"score\s*\d+", blob, re.I):
        return "score"
    # Place numbers are short (1-2 digits). "007" and other zero-padded ids are names.
    if re.fullmatch(r"\s*\d{1,2}\s*", (text or "").replace("\r", "").replace("\n", "")):
        return "place"
    if re.search(r"player\s*name|myself|名字", blob, re.I):
        return "name"
    if re.search(r"\b(daily|total|home|ranking)\b", blob, re.I):
        return re.search(r"\b(daily|total|home|ranking)\b", blob, re.I).group(1).lower()
    if text and re.search(r"[A-Za-z]", text) and not re.search(r"score|level", text, re.I):
        return "name"
    return None


def infer_type_purpose_state(node, module: str):
    name = node.get("name") or ""
    path = (node.get("path") or "").lower()
    key = name.strip().lower()
    typ = purpose = None
    state = ""
    if key in SPECIAL_LAYER_NAMES:
        typ, purpose, state = SPECIAL_LAYER_NAMES[key]
    if re.fullmatch(r"no\.?\s*1", key):
        typ, purpose, state = "icon", "medal", "gold"
    elif re.fullmatch(r"no\.?\s*2", key):
        typ, purpose, state = "icon", "medal", "silver"
    elif re.fullmatch(r"no\.?\s*3", key):
        typ, purpose, state = "icon", "medal", "bronze"
    typ = typ or find_token(name + " " + path, TYPE_TOKENS)
    purpose = purpose or find_token(name + " " + path, PURPOSE_TOKENS)
    state = state or find_token(name + " " + path, STATE_TOKENS) or ""
    item_n = list_item_index(node.get("path") or "")
    if node.get("is_text") or node.get("kind_export") in {"dynamic_text", "art_text"}:
        purpose = text_purpose_from_content(node.get("text") or "", name) or purpose
    # Nav chrome
    if "底部ui" in path or "navbar" in path:
        typ = typ or ("icon" if node.get("kind") and "smart" in (node.get("kind") or "").lower() else typ)
        if key in {"1", "2", "3", "3-2", "3-3"}:
            typ, purpose = "btn", "navtab"
        elif key == "bottom":
            typ, purpose = "bg", "navbar"
        elif "主页" in name:
            typ, purpose = "icon", "home"
        elif "排行" in name:
            typ, purpose = "icon", "rank"
        elif key.startswith("图层"):
            typ, purpose = typ or "icon", purpose or "nav"
    if "顶部ui" in path or "topbar" in path:
        if "横线" in name:
            typ, purpose = "deco", "hline"
        elif "bg" in key or "底" in name:
            typ, purpose = typ or "bg", purpose or "topbar"
    if re.search(r"秒表", name):
        typ, purpose = "icon", "timer"
    if re.search(r"底衬", name):
        typ, purpose = typ or "bg", purpose or "timer"
    if item_n:
        if key == "panel_list" or purpose in {"item", "base"}:
            typ, purpose = "bg", f"item{item_n}"
        elif typ == "icon" and purpose == "avatar":
            purpose = f"avatar_item{item_n}"
        elif typ == "icon" and purpose == "medal":
            purpose = f"medal_item{item_n}" if not state else purpose
        elif purpose in {"score", "name", "place"}:
            purpose = f"{purpose}_item{item_n}"
    # Self row: Myself / 自己 in the same item path.
    if item_n and re.search(r"myself|自己", path, re.I) and not state:
        state = "self"
    if node.get("is_text") or node.get("kind_export") in {"dynamic_text", "art_text"}:
        typ = typ or "img"
        purpose = purpose or slugify(re.sub(r"[\d.]+", " ", name)) or "label"
        if purpose in {"bg", "btn", "icon", "frame", "tab", "item", "deco"}:
            purpose = "label"
    if typ is None:
        if purpose in {"medal", "crown", "avatar", "lock", "coin", "star", "timer"}:
            typ = "icon"
        elif purpose in {"navbar", "topbar", "screen", "base", "item", "list", "title", "podium", "slot"}:
            typ = "bg"
        elif purpose in {"hline", "vline", "divider"}:
            typ = "deco"
        elif (node.get("width") or 0) >= 200 and (node.get("height") or 0) >= 80:
            typ = "bg"
        elif (node.get("width") or 0) <= 200 and (node.get("height") or 0) <= 200:
            typ = "icon"
        else:
            typ = "img"
    if not purpose:
        # Fallback purpose: English tokens in the layer name, else a stable id tag.
        latin = slugify(name)
        if latin and latin not in SKIP_TOKENS and not re.fullmatch(r"l?\d+", latin):
            purpose = latin
        else:
            purpose = f"l{node.get('layer_id') or 0}"
    return typ, purpose, state


def unique_name(parts, used):
    parts = [p for p in parts if p]
    base = "_".join(parts)
    base = re.sub(r"_+", "_", base).strip("_").lower()
    if not base:
        base = "slice"
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Nine-slice: longest uniform adjacent column/row run -> margins
# ---------------------------------------------------------------------------

def detect_nine_slice(image, min_run=8, tol=1.0, min_side=48):
    """Return {left,top,right,bottom} insets, or None if not a stretchable panel."""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32)
    h, w = arr.shape[:2]
    if w < min_side or h < min_side:
        return None

    def longest_run(axis):
        seq = arr if axis == 1 else arr.transpose(1, 0, 2)
        n = seq.shape[1]
        same = []
        for i in range(n - 1):
            delta = np.abs(seq[:, i] - seq[:, i + 1])
            same.append(float(delta.max()) <= tol * 4 and float(delta.mean()) <= tol)
        best = (0, 0, 0)
        i = 0
        while i < n - 1:
            if same[i]:
                j = i
                while j < n - 1 and same[j]:
                    j += 1
                if j - i > best[0]:
                    best = (j - i, i, j)
                i = j
            else:
                i += 1
        return best

    rx, ry = longest_run(1), longest_run(0)
    left = right = top = bottom = 0
    ok = False
    if rx[0] >= min_run:
        left, right = rx[1] + 1, w - rx[2] - 1
        ok = True
    if ry[0] >= min_run:
        top, bottom = ry[1] + 1, h - ry[2] - 1
        ok = True
    if not ok:
        return None
    # Keep a stretchable center; treat full-bleed "uniform" images as not nine-slice.
    if left + right >= w - 2 or top + bottom >= h - 2:
        return None
    # A 1px "corner" on an otherwise uniform fill is not a real cap/stroke.
    if (left + right) < min_run and (top + bottom) < min_run:
        return None
    if left < 0 or right < 0 or top < 0 or bottom < 0:
        return None
    # Corners must remain (a 1px "corner" with a huge stretch is fine; reject 0-size image).
    return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}


def try_composite(node, warnings):
    layer = node.get("_layer")
    if layer is None:
        return None
    try:
        image = layer.composite()
        if image is None:
            return None
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        return image
    except Exception as exc:
        warnings.append(f"composite failed {node.get('path')}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def list_item_tag(path: str) -> str:
    """If the layer sits under a repeated list item, return a stable itemN tag."""
    return ""


def build_entries(artboard, art_w, art_h, module_hint, warnings):
    used = set()
    entries = []
    tree_nodes = []
    item_counters = {}

    def item_suffix(node):
        # Number repeated instances under the same parent path by sibling order.
        parent = "/".join((node.get("path") or "").split("/")[:-1])
        key = parent
        item_counters[key] = item_counters.get(key, 0) + 1
        return item_counters[key]

    def emit(node, clipped, parent_name, z_base, siblings_for_index):
        if node is None or not node.get("visible"):
            return None
        if node.get("is_adjustment") and not clipped:
            return None
        if (node.get("width") or 0) <= 0 or (node.get("height") or 0) <= 0:
            warnings.append(f"skip empty layer {node.get('path')}")
            return None

        select_ids = descendant_visible_ids(node)
        for c in clipped:
            select_ids.extend(descendant_visible_ids(c))
        # unique, keep order
        seen = set()
        ids = []
        for i in select_ids:
            if i not in seen:
                seen.add(i)
                ids.append(i)
        if not ids:
            return None

        kind = "image"
        text_attrs = None
        if node.get("is_text") and not clipped:
            kind = classify_text(node)
            text_attrs = {
                "content": node.get("text") or "",
                "font": None,
                "size": None,
                "color": None,
                "alignment": None,
                "stroke": None,
                "opacity": (node.get("opacity") / 255.0) if isinstance(node.get("opacity"), (int, float)) else None,
                "effects": list(node.get("effects") or []),
            }

        module = infer_module(node.get("path") or "", module_hint)
        typ, purpose, state = infer_type_purpose_state({**node, "kind_export": kind}, module)
        if kind in {"dynamic_text", "art_text"} and typ not in {"img"}:
            typ = "img"
        name = unique_name([module, typ, purpose, state], used)

        nine = None
        image = None
        if kind not in {"dynamic_text"}:
            image = try_composite(node, warnings)
            if image is not None and kind != "art_text":
                nine = detect_nine_slice(image)
                if nine:
                    kind = "nine_slice"

        bbox = node.get("bbox") or [0, 0, 0, 0]
        entry = {
            "name": name,
            "layer_id": node.get("layer_id"),
            "select_ids": ids,
            "source_name": node.get("name") or "",
            "source_path": node.get("path") or "",
            "source_kind": node.get("kind"),
            "kind": kind,
            "logical_bounds": bbox,
            "nine_slice": nine,
            "text": text_attrs,
            "parent": parent_name,
            "opacity": node.get("opacity"),
            "blend_mode": node.get("blend_mode"),
            "notes": [],
        }
        if kind == "dynamic_text":
            entry["notes"].append("dynamic_text is not rasterized; Photoshop export writes attributes only.")
        if node.get("effects"):
            entry["notes"].append("has layer effects; psd-tools logical_bounds exclude effects.")
        if clipped:
            entry["notes"].append(f"includes {len(clipped)} visible clipped layer(s)")
        entries.append(entry)
        return {
            "name": name,
            "kind": kind,
            "layer_id": node.get("layer_id"),
            "children": [],
        }

    def walk(node, parent_name, z_base=0):
        children_out = []
        if should_descend(node, art_w, art_h):
            container = {
                "name": None,
                "kind": "group",
                "layer_id": node.get("layer_id"),
                "children": [],
            }
            # Name the container for hierarchy only (not a slice).
            module = infer_module(node.get("path") or "", module_hint)
            typ, purpose, state = infer_type_purpose_state(node, module)
            # groups use a name that won't collide with slices: prefix group_
            gname = unique_name(["group", module, typ, purpose, state], used)
            container["name"] = gname
            units = attach_clips(visible_children(node))
            for unit in units:
                base, clipped = unit["base"], [c for c in unit["clipped"] if c.get("visible")]
                if base is None:
                    warnings.append(f"orphaned clipping layers under {node.get('path')}")
                    continue
                if not base.get("visible"):
                    continue
                if should_descend(base, art_w, art_h) and not clipped:
                    child = walk(base, gname)
                    if child:
                        container["children"].append(child)
                else:
                    child = emit(base, clipped, gname, z_base, units)
                    if child:
                        container["children"].append(child)
            return container
        # export the group / layer as one unit
        return emit(node, [], parent_name, z_base, [])

    tree = walk(artboard, None)
    if tree:
        tree_nodes.append(tree)
    return entries, tree_nodes


def find_artboard(roots, needle):
    if needle is None:
        artboards = [n for n in roots if n.get("is_artboard")]
        if len(artboards) == 1:
            return artboards[0]
        if not artboards and len(roots) == 1:
            return roots[0]
        names = [(n.get("layer_id"), n.get("name")) for n in artboards]
        raise SystemExit(f"multiple artboards, pass --artboard: {names}")
    raw = str(needle)
    for n in roots:
        if not n.get("is_artboard") and n.get("depth") != 1:
            continue
        if raw == str(n.get("layer_id")) or raw == (n.get("name") or ""):
            return n
        if n.get("is_artboard"):
            found = find_artboard_in([n], raw)
            if found:
                return found
    # search all artboards recursively
    found = find_artboard_in(roots, raw)
    if found:
        return found
    raise SystemExit(f"artboard not found: {needle}")


def find_artboard_in(nodes, raw):
    for n in nodes:
        if n.get("is_artboard") and (raw == str(n.get("layer_id")) or raw == (n.get("name") or "")):
            return n
        got = find_artboard_in(n.get("children") or [], raw)
        if got:
            return got
    return None


def load_font(size):
    for path in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def artboard_image(artboard, warnings):
    image = try_composite(artboard, warnings)
    if image is not None:
        return image
    # Fallback: empty canvas
    w, h = artboard.get("width") or 1, artboard.get("height") or 1
    return Image.new("RGBA", (w, h), (24, 24, 24, 255))


def annotate_preview(base: Image.Image, entries, art_origin, out_path: Path, title: str):
    ox, oy = art_origin
    image = base.convert("RGBA")
    # Dim the artwork slightly so overlays read clearly.
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    canvas.paste(image, (0, 0))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(16)
    font_sm = load_font(13)
    for entry in entries:
        bbox = entry.get("logical_bounds") or [0, 0, 0, 0]
        l, t, r, b = bbox
        l, t, r, b = l - ox, t - oy, r - ox, b - oy
        color = KIND_COLORS.get(entry["kind"], (255, 255, 255))
        draw.rectangle([l, t, r - 1, b - 1], outline=color + (255,), width=2)
        nine = entry.get("nine_slice")
        if nine and entry["kind"] == "nine_slice":
            guide = (255, 220, 80, 220)
            draw.line([(l + nine["left"], t), (l + nine["left"], b - 1)], fill=guide, width=1)
            draw.line([(r - 1 - nine["right"], t), (r - 1 - nine["right"], b - 1)], fill=guide, width=1)
            draw.line([(l, t + nine["top"]), (r - 1, t + nine["top"])], fill=guide, width=1)
            draw.line([(l, b - 1 - nine["bottom"]), (r - 1, b - 1 - nine["bottom"])], fill=guide, width=1)
        label = f"{entry['name']}  [{entry['kind']}]"
        # Place the label above the box if there is room, else inside.
        ty = t - 16 if t >= 18 else t + 2
        tx = max(2, l)
        # background strip for readability
        try:
            tw = draw.textlength(label, font=font_sm)
        except Exception:
            tw = 8 * len(label)
        draw.rectangle([tx, ty, tx + tw + 6, ty + 16], fill=(0, 0, 0, 160))
        draw.text((tx + 3, ty + 1), label, fill=color + (255,), font=font_sm)
    out = Image.alpha_composite(canvas, overlay).convert("RGB")
    # Legend bar at the top
    legend = Image.new("RGB", (out.width, 36), (20, 20, 20))
    ld = ImageDraw.Draw(legend)
    ld.text((8, 8), title, fill=(255, 255, 255), font=font)
    x = min(out.width - 10, 8 + int(ld.textlength(title, font=font) + 24) if hasattr(ld, "textlength") else 400)
    for kind, color in KIND_COLORS.items():
        ld.rectangle([x, 10, x + 12, 22], fill=color)
        ld.text((x + 16, 8), kind, fill=(220, 220, 220), font=font_sm)
        x += 16 + int(ld.textlength(kind, font=font_sm) if hasattr(ld, "textlength") else 80) + 16
    final = Image.new("RGB", (out.width, out.height + 36), (20, 20, 20))
    final.paste(legend, (0, 0))
    final.paste(out, (0, 36))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, "PNG")
    return out_path


def strip_runtime(entries):
    clean = []
    for e in entries:
        item = {k: v for k, v in e.items() if not k.startswith("_")}
        clean.append(item)
    return clean


def cmd_infer(args):
    if PSDImage is None:
        raise SystemExit("psd-tools is required for infer (pip install psd-tools)")
    t0 = time.time()
    psd_path = Path(args.psd)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"opening {psd_path} ({psd_path.stat().st_size} bytes)", flush=True)
    open_kwargs = {}
    if ParseLimits is not None:
        # Large UI PSBs easily exceed the default 1_000_000 object cap (实测).
        open_kwargs["parse_limits"] = ParseLimits(
            max_objects=None, max_total_bytes=None, max_read_bytes=None
        )
    psd = PSDImage.open(str(psd_path), **open_kwargs)
    print(f"opened in {time.time()-t0:.1f}s: {psd.width}x{psd.height}", flush=True)
    warnings = []
    roots = []
    t1 = time.time()
    for i, layer in enumerate(psd):
        roots.append(collect_layer(layer, [], 1, i, warnings))
    print(f"tree walked in {time.time()-t1:.1f}s, top-level {len(roots)}", flush=True)

    artboard = find_artboard(roots, args.artboard)
    if not artboard.get("visible"):
        raise SystemExit(f"artboard is hidden: {artboard.get('name')}")
    bbox = artboard.get("bbox") or [0, 0, artboard["width"], artboard["height"]]
    art_w, art_h = artboard["width"], artboard["height"]
    print(f"artboard {artboard.get('name')} id={artboard.get('layer_id')} {art_w}x{art_h} bbox={bbox}", flush=True)

    t2 = time.time()
    entries, tree = build_entries(artboard, art_w, art_h, artboard.get("name") or "", warnings)
    print(f"inferred {len(entries)} entries in {time.time()-t2:.1f}s", flush=True)

    # Composite artboard for preview (visible layers only; FX may be missing).
    t3 = time.time()
    preview_base = artboard_image(artboard, warnings)
    print(f"artboard composite in {time.time()-t3:.1f}s size={preview_base.size}", flush=True)

    kinds = Counter(e["kind"] for e in entries)
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "status": "pending_review",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": {
            "file": str(psd_path),
            "doc_size": [int(psd.width), int(psd.height)],
            "color_mode": enum_val(getattr(psd, "color_mode", None)),
            "depth": getattr(psd, "depth", None),
            "artboard": {
                "id": artboard.get("layer_id"),
                "name": artboard.get("name"),
                "bounds": bbox,
                "size": [art_w, art_h],
            },
        },
        "naming_rule": "模块_类型_用途_状态，小写英文；禁止直接使用 PSD 图层名",
        "review_gate": "Edit this JSON to confirm. Nothing is exported until status is set to confirmed.",
        "entries": strip_runtime(entries),
        "tree": tree,
        "warnings": warnings,
        "stats": {
            "entry_count": len(entries),
            "by_kind": dict(kinds),
            "infer_sec": round(time.time() - t0, 2),
        },
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_path = out_dir / "preview_annotated.png"
    annotate_preview(
        preview_base,
        manifest["entries"],
        (bbox[0], bbox[1]),
        preview_path,
        f"{artboard.get('name')}  {len(entries)} slices  (review preview, FX may be missing)",
    )
    print(json.dumps({"manifest": str(manifest_path), "preview": str(preview_path), "stats": manifest["stats"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_preview(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    src = manifest["source"]["artboard"]
    ox, oy = src["bounds"][0], src["bounds"][1]
    if args.ref:
        base = Image.open(args.ref).convert("RGBA")
        # If the ref is the artboard render (no legend), use as-is.
    else:
        w, h = src["size"]
        base = Image.new("RGBA", (w, h), (24, 24, 24, 255))
    out = Path(args.out)
    # logical_bounds are in document space. Artboard-cropped images need the origin subtracted.
    origin = (0, 0) if args.ref_is_document else (ox, oy)
    annotate_preview(base, manifest["entries"], origin, out,
                     f"{src.get('name')}  {len(manifest['entries'])} slices")
    print(f"wrote {out}")
    return 0


def _rect_to_dict(rect):
    if rect is None:
        return None
    if isinstance(rect, dict):
        if "left" in rect:
            return {k: float(rect[k]) for k in ("left", "top", "right", "bottom")}
        return rect
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        l, t, r, b = rect
        return {"left": float(l), "top": float(t), "right": float(r), "bottom": float(b)}
    return None


def _center(bounds):
    if not bounds:
        return None
    return {
        "x": round((bounds["left"] + bounds["right"]) / 2.0, 2),
        "y": round((bounds["top"] + bounds["bottom"]) / 2.0, 2),
    }


def _shift(bounds, ox, oy):
    if not bounds:
        return None
    return {
        "left": bounds["left"] - ox,
        "top": bounds["top"] - oy,
        "right": bounds["right"] - ox,
        "bottom": bounds["bottom"] - oy,
    }


def extract_bounds_from_export_item(item):
    """Prefer Photoshop-reported bounds; fall back to manifest logical_bounds."""
    vis = item.get("visible_bounds") or item.get("bounds_with_effects") or item.get("bounds")
    logi = item.get("logical_bounds") or item.get("bounds_no_effects") or item.get("boundsNoEffects")
    return _rect_to_dict(vis), _rect_to_dict(logi)


def cmd_build_layout(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    export = json.loads(Path(args.export_log).read_text(encoding="utf-8"))
    slices_dir = Path(args.slices_dir)
    art = manifest["source"]["artboard"]
    ox, oy = art["bounds"][0], art["bounds"][1]
    by_name = {e["name"]: e for e in manifest["entries"]}
    export_items = {}
    for item in export.get("items") or []:
        export_items[item.get("name")] = item

    def refine_nine(entry, png_path):
        if not png_path.exists():
            return entry.get("nine_slice")
        if entry["kind"] in {"dynamic_text"}:
            return None
        try:
            image = Image.open(png_path)
        except Exception:
            return entry.get("nine_slice")
        detected = detect_nine_slice(image)
        return detected if detected is not None else entry.get("nine_slice")

    nodes = []
    for entry in manifest["entries"]:
        name = entry["name"]
        item = export_items.get(name, {})
        vis, logi = extract_bounds_from_export_item(item)
        if logi is None:
            logi = _rect_to_dict(entry.get("logical_bounds"))
        if vis is None:
            vis = logi
        # If the PNG is the same size as the logical box, effects did not expand
        # the pixels (or were clipped). Prefer the logical origin over a stale
        # `bounds` / DOM box that may be the full canvas or otherwise offset.
        png_size = item.get("out_size")
        if logi and png_size and len(png_size) == 2:
            lw = logi["right"] - logi["left"]
            lh = logi["bottom"] - logi["top"]
            if abs(lw - png_size[0]) <= 1 and abs(lh - png_size[1]) <= 1:
                vis = dict(logi)
        vis_ab = _shift(vis, ox, oy) if vis else None
        logi_ab = _shift(logi, ox, oy) if logi else None
        png_name = None if entry["kind"] == "dynamic_text" else f"{name}.png"
        png_path = slices_dir / png_name if png_name else None
        nine = refine_nine(entry, png_path) if png_path else None
        kind = entry["kind"]
        if nine and kind == "image":
            kind = "nine_slice"
        if not nine and kind == "nine_slice":
            # Keep the reviewed kind if insets were confirmed, else downgrade.
            if entry.get("nine_slice"):
                nine = entry["nine_slice"]
            else:
                kind = "image"
        text = item.get("text") or entry.get("text")
        node = {
            "id": name,
            "name": name,
            "kind": kind,
            "layer_id": entry.get("layer_id"),
            "select_ids": entry.get("select_ids"),
            "parent": entry.get("parent"),
            "source_name": entry.get("source_name"),
            "source_path": entry.get("source_path"),
            "visible_bounds": vis_ab,
            "logical_bounds": logi_ab,
            "anchor": _center(logi_ab),
            "pivot": {"x": 0.5, "y": 0.5},
            "nine_slice": nine,
            "image": png_name,
            "text": text,
            "opacity": entry.get("opacity"),
            "blend_mode": entry.get("blend_mode"),
        }
        nodes.append(node)

    layout = {
        "schema": SCHEMA_LAYOUT,
        "source": manifest["source"],
        "export": {
            "ok": export.get("ok"),
            "started": export.get("started"),
            "finished": export.get("finished"),
            "log": str(Path(args.export_log)),
        },
        "nodes": nodes,
        "tree": manifest.get("tree") or [],
        "notes": [
            "Coordinates are artboard-relative pixels (1x).",
            "visible_bounds include layer effects; logical_bounds do not.",
            "anchor is the center of logical_bounds; pivot is normalized (0.5, 0.5).",
            "Engine importers are out of scope for this schema.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(nodes)} nodes)")
    return 0


# ---------------------------------------------------------------------------
# Validation / ΔE
# ---------------------------------------------------------------------------

def srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    lin = srgb_to_linear(rgb)
    m = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = lin @ m.T
    w = np.array([0.95047, 1.0, 1.08883])
    x = xyz / w
    eps, k = 216 / 24389, 24389 / 27
    f = np.where(x > eps, np.cbrt(x), (k * x + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def ciede2000(lab1, lab2):
    """Vectorized CIEDE2000. lab1/lab2:[...,3]."""
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    kL = kC = kH = 1.0
    C1 = np.sqrt(a1 ** 2 + b1 ** 2)
    C2 = np.sqrt(a2 ** 2 + b2 ** 2)
    Cbar = (C1 + C2) / 2.0
    C7 = Cbar ** 7
    G = 0.5 * (1 - np.sqrt(C7 / (C7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.sqrt(a1p ** 2 + b1 ** 2), np.sqrt(a2p ** 2 + b2 ** 2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where((C1p * C2p) == 0, 0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))
    Lbar = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hbarp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2.0, hsum / 2.0)
    hbarp = np.where((C1p * C2p) == 0, hsum, hbarp)
    T = (1
         - 0.17 * np.cos(np.radians(hbarp - 30))
         + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6))
         - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25 ** 7))
    SL = 1 + (0.015 * (Lbar - 50) ** 2) / np.sqrt(20 + (Lbar - 50) ** 2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2 * dtheta)) * Rc
    dE = np.sqrt(
        (dLp / (kL * SL)) ** 2
        + (dCp / (kC * SC)) ** 2
        + (dHp / (kH * SH)) ** 2
        + RT * (dCp / (kC * SC)) * (dHp / (kH * SH))
    )
    return dE


def recomposite(layout, slices_dir, canvas_size):
    w, h = canvas_size
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    placed = []
    for node in layout["nodes"]:
        if node["kind"] == "dynamic_text":
            continue
        image_name = node.get("image")
        if not image_name:
            continue
        path = Path(slices_dir) / image_name
        if not path.exists():
            placed.append({"name": node["name"], "missing_file": True})
            continue
        im = Image.open(path).convert("RGBA")
        vb = node.get("visible_bounds") or node.get("logical_bounds")
        if not vb:
            placed.append({"name": node["name"], "missing_bounds": True})
            continue
        x, y = int(round(vb["left"])), int(round(vb["top"]))
        canvas.paste(im, (x, y), im)
        placed.append({"name": node["name"], "xy": [x, y], "size": list(im.size)})
    return canvas, placed


def cmd_validate(args):
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    slices_dir = Path(args.slices_dir)
    full = Image.open(args.full_render).convert("RGBA")
    art = layout["source"]["artboard"]
    w, h = art["size"]
    if full.size != (w, h):
        # Allow a render that still has a legend or slight mismatch; crop/resize is not done silently.
        print(f"warning: full render size {full.size} != artboard {w}x{h}", flush=True)

    issues = []
    names = [n["name"] for n in layout["nodes"]]
    dup = [k for k, v in Counter(names).items() if v > 1]
    if dup:
        issues.append({"type": "duplicate_names", "names": dup})

    empty = []
    missing = []
    for node in layout["nodes"]:
        if node["kind"] == "dynamic_text":
            if not (node.get("text") or {}).get("content") and not (node.get("text") or {}).get("font"):
                issues.append({"type": "missing_text_attrs", "name": node["name"]})
            continue
        image_name = node.get("image")
        path = slices_dir / image_name if image_name else None
        if path is None or not path.exists():
            missing.append(node["name"])
            continue
        im = Image.open(path)
        arr = np.asarray(im.convert("RGBA"))
        if arr.size == 0 or int(arr[..., 3].max()) == 0:
            empty.append(node["name"])
    if missing:
        issues.append({"type": "missing_slices", "names": missing})
    if empty:
        issues.append({"type": "empty_images", "names": empty})

    recomposed, placed = recomposite(layout, slices_dir, (w, h))
    # Align sizes for the pixel comparison.
    cw, ch = min(full.width, w), min(full.height, h)
    ref = np.asarray(full.convert("RGBA").crop((0, 0, cw, ch)))
    rec = np.asarray(recomposed.convert("RGBA").crop((0, 0, cw, ch)))

    # Mask dynamic text regions so typography the engine will redraw is not counted as a miss.
    text_mask = np.zeros((ch, cw), dtype=bool)
    for node in layout["nodes"]:
        if node["kind"] != "dynamic_text":
            continue
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        l, t, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
        l, t = max(0, l), max(0, t)
        r, bt = min(cw, r), min(ch, bt)
        if r > l and bt > t:
            text_mask[t:bt, l:r] = True

    def metrics(a, b, mask=None):
        rgb_a, rgb_b = a[..., :3], b[..., :3]
        if mask is not None:
            keep = ~mask
            if not keep.any():
                return {}
            rgb_a, rgb_b = rgb_a[keep], rgb_b[keep]
            alpha_diff = np.abs(a[..., 3].astype(np.int16) - b[..., 3].astype(np.int16))[keep]
        else:
            alpha_diff = np.abs(a[..., 3].astype(np.int16) - b[..., 3].astype(np.int16))
        diff = np.abs(rgb_a.astype(np.int16) - rgb_b.astype(np.int16))
        chan_max = diff.max(axis=-1)
        mae = float(diff.mean())
        mse = float((diff.astype(np.float64) ** 2).mean())
        psnr = 10 * np.log10(255 ** 2 / mse) if mse else 99.0
        lab_a = rgb_to_lab(rgb_a.reshape(-1, 3) if mask is not None else rgb_a)
        lab_b = rgb_to_lab(rgb_b.reshape(-1, 3) if mask is not None else rgb_b)
        if mask is None:
            dE = ciede2000(lab_a, lab_b)
        else:
            dE = ciede2000(lab_a, lab_b)
        return {
            "mae": round(mae, 3),
            "psnr_db": round(float(psnr), 2),
            "pct_px_diff_gt16": round(float((chan_max > 16).mean() * 100), 3),
            "pct_px_diff_gt48": round(float((chan_max > 48).mean() * 100), 3),
            "mean_delta_e": round(float(dE.mean()), 3),
            "p95_delta_e": round(float(np.percentile(dE, 95)), 3),
            "pct_delta_e_gt2": round(float((dE > 2).mean() * 100), 3),
            "pct_delta_e_gt5": round(float((dE > 5).mean() * 100), 3),
            "mean_alpha_diff": round(float(alpha_diff.mean()), 3),
        }

    m_all = metrics(ref, rec, None)
    m_masked = metrics(ref, rec, text_mask)

    # Visual: side-by-side + heatmap
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    recomposed.convert("PNG" if False else "RGBA").save(out_dir / "recomposite.png")
    d_rgb = np.abs(ref[..., :3].astype(np.int16) - rec[..., :3].astype(np.int16)).max(axis=2)
    heat = np.zeros((ch, cw, 3), dtype=np.uint8)
    v = np.clip(d_rgb * 3, 0, 255).astype(np.uint8)
    heat[..., 0] = v
    heat[..., 1] = (v * 0.3).astype(np.uint8)
    g = (ref[..., :3].mean(axis=2) * 0.25).astype(np.uint8)
    heat = np.maximum(heat, np.stack([g, g, g], axis=2))
    scale = 0.5
    rw, rh = int(cw * scale), int(ch * scale)
    canvas = Image.new("RGB", (rw * 3 + 40, rh + 50), (30, 30, 30))
    dr = ImageDraw.Draw(canvas)
    font = load_font(16)
    for i, (im, title) in enumerate([
        (Image.fromarray(ref[..., :3]), "Photoshop full render"),
        (Image.fromarray(rec[..., :3]), "Recomposite from slices"),
        (Image.fromarray(heat), "|diff| x3"),
    ]):
        canvas.paste(im.resize((rw, rh), getattr(Image, 'Resampling', Image).LANCZOS), (i * (rw + 20), 40))
        dr.text((i * (rw + 20) + 8, 10), title, fill=(255, 255, 255), font=font)
    canvas.save(out_dir / "validate_side_by_side.jpg", quality=88)

    report = {
        "artboard": art,
        "full_render": str(args.full_render),
        "full_render_size": list(full.size),
        "canvas": [w, h],
        "node_count": len(layout["nodes"]),
        "by_kind": dict(Counter(n["kind"] for n in layout["nodes"])),
        "diff_all": m_all,
        "diff_text_masked": m_masked,
        "issues": issues,
        "placed": len(placed),
        "ok": not issues,
    }
    (out_dir / "validate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def build_parser():
    p = argparse.ArgumentParser(description="Agent Photoshop slicing helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("infer", help="Infer slicing manifest + annotated preview from a PSD/PSB")
    s.add_argument("--psd", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--artboard", default=None, help="Artboard name or layer_id (required if several)")
    s.set_defaults(func=cmd_infer)

    s = sub.add_parser("preview", help="Redraw annotated preview from a (edited) manifest")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--ref", default=None, help="Optional artboard PNG to draw on")
    s.add_argument("--ref-is-document", action="store_true", help="Ref is full-document sized, not artboard-cropped")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("build-layout", help="Build engine-agnostic layout JSON from export results")
    s.add_argument("--manifest", required=True)
    s.add_argument("--export-log", required=True)
    s.add_argument("--slices-dir", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_build_layout)

    s = sub.add_parser("validate", help="Recomposite and compare against a full Photoshop render")
    s.add_argument("--layout", required=True)
    s.add_argument("--slices-dir", required=True)
    s.add_argument("--full-render", required=True)
    s.add_argument("--out-dir", required=True)
    s.set_defaults(func=cmd_validate)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
