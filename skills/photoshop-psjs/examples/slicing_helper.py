#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent slicing helper: infer a manifest, annotate a preview, build layout JSON, validate.

Requires: psd-tools, Pillow, numpy

Subcommands:
  infer       Read a PSD/PSB with psd-tools and write manifest JSON + annotated preview.
              Optional --artboard <name or id>; optional --rules naming JSON.
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


# ---------------------------------------------------------------------------
# Naming rules (optional --rules JSON; generic defaults otherwise)
# ---------------------------------------------------------------------------

DEFAULT_RULES = {
    "default_module": "main",
    "chrome_module": "common",
    "module_keywords": [],
    "type_keywords": [
        ["button", "btn"], ["btn", "btn"],
        ["tab", "tab"],
        ["icon", "icon"],
        ["frame", "frame"],
        ["background", "bg"], ["panel", "bg"], ["bg", "bg"],
        ["deco", "deco"],
        ["mask", "mask"],
        ["item", "item"],
        ["img", "img"],
    ],
    "purpose_keywords": [
        ["navbar", "navbar"], ["topbar", "topbar"],
        ["timer", "timer"], ["title", "title"],
        ["avatar", "avatar"],
        ["screen", "screen"], ["list", "list"],
        ["slot", "slot"], ["divider", "divider"],
    ],
    "state_keywords": [
        ["_off", "off"], ["_on", "on"],
        ["off", "off"], ["on", "on"],
        ["normal", "normal"],
        ["empty", "empty"], ["full", "full"],
        ["pressed", "pressed"], ["disabled", "disabled"],
        ["hover", "hover"], ["selected", "on"],
    ],
    "skip_tokens": ["copy", "group", "layer"],
    "special_layer_names": {},
    "fixed_labels": [],
    "chrome_path_keywords": ["navbar", "topbar", "header", "footer"],
    "role_path_groups": {
        "tab_label": ["tab", "segment"],
        "nav_label": ["navbar", "footer"],
        "title_label": ["topbar", "header"],
    },
    "list_item_pattern": r"/list\s*(\d+)(?:/|$)",
    "self_row_keywords": ["self"],
    "art_text_name_keywords": ["title"],
    "value_name_keywords": ["player name"],
}


def _pairs(seq):
    out = []
    if isinstance(seq, dict):
        seq = seq.items()
    for item in seq or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    return out


def merge_rules(base, overlay):
    out = json.loads(json.dumps(base))
    for key, val in (overlay or {}).items():
        if val is None:
            continue
        if key in {"module_keywords", "type_keywords", "purpose_keywords", "state_keywords"}:
            seen = set()
            merged = []
            for pair in _pairs(val) + _pairs(out.get(key)):
                if pair in seen:
                    continue
                seen.add(pair)
                merged.append(list(pair))
            out[key] = merged
        elif key in {
            "skip_tokens", "fixed_labels", "chrome_path_keywords",
            "self_row_keywords", "art_text_name_keywords", "value_name_keywords",
        }:
            seen = set()
            merged = []
            for item in list(val) + list(out.get(key) or []):
                mark = str(item).casefold()
                if mark in seen:
                    continue
                seen.add(mark)
                merged.append(item)
            out[key] = merged
        elif key == "special_layer_names":
            merged = dict(out.get(key) or {})
            merged.update(val)
            out[key] = merged
        elif key == "path_specials":
            out[key] = list(val) + list(out.get(key) or [])
        elif key == "role_path_groups":
            merged = dict(out.get(key) or {})
            for role, paths in (val or {}).items():
                old = [str(p) for p in (merged.get(role) or [])]
                new = [str(p) for p in (paths or [])]
                merged[role] = new + [p for p in old if p not in new]
            out[key] = merged
        else:
            out[key] = val
    return out


class NamingRules:
    def __init__(self, data):
        d = data or {}
        self.module_keywords = _pairs(d.get("module_keywords"))
        self.type_keywords = _pairs(d.get("type_keywords"))
        self.purpose_keywords = _pairs(d.get("purpose_keywords"))
        self.state_keywords = _pairs(d.get("state_keywords"))
        self.skip_tokens = {str(x).lower() for x in (d.get("skip_tokens") or [])}
        special = d.get("special_layer_names") or {}
        self.special_layer_names = {}
        for key, val in special.items():
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                state = val[2] if len(val) > 2 else ""
                self.special_layer_names[str(key).strip().lower()] = (
                    val[0] or "", val[1] or "", state or "",
                )
        self.fixed_labels = {str(x).casefold() for x in (d.get("fixed_labels") or [])}
        self.chrome_path_keywords = [str(x).lower() for x in (d.get("chrome_path_keywords") or [])]
        self.chrome_module = d.get("chrome_module") or "common"
        groups = d.get("role_path_groups") or {}
        self.role_path_groups = {
            str(role): [str(p).lower() for p in (paths or [])]
            for role, paths in groups.items()
        }
        self.list_item_pattern = d.get("list_item_pattern") or r"/list\s*(\d+)(?:/|$)"
        self.self_row_keywords = [str(x).lower() for x in (d.get("self_row_keywords") or [])]
        self.art_text_name_keywords = [str(x).lower() for x in (d.get("art_text_name_keywords") or [])]
        self.default_module = d.get("default_module") or "main"
        self.value_name_keywords = [str(x).lower() for x in (d.get("value_name_keywords") or [])]
        self.path_specials = []
        for item in d.get("path_specials") or []:
            parts = item.get("parts") or [item.get("type"), item.get("purpose"), item.get("state")]
            self.path_specials.append({
                "path_includes": [str(x).lower() for x in (item.get("path_includes") or [])],
                "name": str(item.get("name") or "").strip().lower(),
                "name_prefix": str(item.get("name_prefix") or "").strip().lower(),
                "type": (parts[0] if len(parts) > 0 else "") or "",
                "purpose": (parts[1] if len(parts) > 1 else "") or "",
                "state": (parts[2] if len(parts) > 2 else "") or "",
            })

    @classmethod
    def load(cls, path=None):
        data = json.loads(json.dumps(DEFAULT_RULES))
        if path:
            overlay = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise SystemExit(f"rules file must be a JSON object: {path}")
            data = merge_rules(data, overlay)
        return cls(data)


def looks_like_state_name(name: str, rules: NamingRules | None = None) -> bool:
    low = (name or "").lower()
    if re.search(r"(_on|_off)$", low) or "_on" in low or "_off" in low:
        return True
    rules = rules or NamingRules(DEFAULT_RULES)
    return any(src.lower() in low for src, _dst in rules.state_keywords)


def is_large_on(node, art_w, art_h):
    w, h = node.get("width") or 0, node.get("height") or 0
    return w >= art_w * 0.6 or h >= art_h * 0.4 or (w * h >= art_w * art_h * 0.2)


def should_descend(node, art_w, art_h, rules: NamingRules | None = None):
    if not node.get("is_group") or not node.get("visible"):
        return False
    if node.get("is_artboard"):
        return True
    kids = visible_children(node)
    if not kids:
        return False
    if has_visible_text(node):
        return True
    if any(looks_like_state_name(c.get("name") or "", rules) for c in kids):
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


def normalize_text(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text or "").strip()


def has_heavy_effects(node) -> bool:
    """Typographic / decorative layer effects that should be baked as art_text."""
    keys = ("stroke", "frame", "gradient", "shadow", "glow", "bevel")
    return any(any(k in (e or "").lower() for k in keys) for e in (node.get("effects") or []))


def is_fixed_label(text: str, rules: NamingRules) -> bool:
    return normalize_text(text).casefold() in rules.fixed_labels


def is_value_text(text: str, rules: NamingRules) -> bool:
    """Numbers, names, scores, and timers — rendered by the engine, not baked."""
    t = normalize_text(text)
    if not t:
        return False
    if is_fixed_label(t, rules):
        return False
    if re.search(r"\d+\s*[hH小时].*\d+\s*[mM分]|\d+h\s*\d+m", t):
        return True
    if re.search(r"^(score|level)\b", t, re.I) or re.search(r"score\s*\d+", t, re.I):
        return True
    # Place / rank numbers are 1-2 digits only. Three or more digits, or a
    # leading-zero string, are treated as names / ids.
    if re.fullmatch(r"\d{1,2}", t):
        return True
    if re.fullmatch(r"0\d+", t) or re.fullmatch(r"\d{3,}", t):
        return True
    blob = t.casefold()
    if any(k and k in blob for k in rules.value_name_keywords):
        return True
    if re.search(r"[A-Za-z]+\d+", t):
        return True
    if re.search(r"[A-Za-z].*\s+[A-Za-z]", t):
        return True
    if re.fullmatch(r"[\d.,\s]+", t):
        return True
    return False


def classify_text(node, rules: NamingRules) -> str:
    text = node.get("text") or ""
    name = (node.get("name") or "").lower()
    if any(k and k in name for k in rules.art_text_name_keywords):
        return "art_text"
    if is_value_text(text, rules):
        return "dynamic_text"
    if is_fixed_label(text, rules):
        return "art_text" if has_heavy_effects(node) else "dynamic_text"
    if has_heavy_effects(node) and normalize_text(text) and not re.search(r"\d", normalize_text(text)):
        return "art_text"
    return "dynamic_text"


def text_role(path: str, rules: NamingRules) -> str | None:
    blob = (path or "").lower()
    for role, tokens in rules.role_path_groups.items():
        if any(tok and tok in blob for tok in tokens):
            return role
    return None


def apply_pair_consistency(entries, rules: NamingRules):
    """The same text role in an on/off or tab pair must share one kind.
    If any member is art_text, all become art_text so no visible label is dropped.
    """
    groups = {}
    for entry in entries:
        if entry.get("kind") not in {"dynamic_text", "art_text"}:
            continue
        content = normalize_text((entry.get("text") or {}).get("content") or "")
        if not content or is_value_text(content, rules):
            continue
        path = entry.get("source_path") or ""
        role = text_role(path, rules) or ("label:" + content.casefold())
        groups.setdefault(role, []).append(entry)
    for members in groups.values():
        if any(m.get("kind") == "art_text" for m in members):
            for member in members:
                if member.get("kind") != "art_text":
                    member["kind"] = "art_text"
                    member.setdefault("notes", []).append(
                        "kind raised to art_text to match on/off or tab-label pair"
                    )


def _path_relative_to_artboard(path: str, artboard_name: str) -> str:
    path_l = (path or "").lower()
    art_l = (artboard_name or "").lower()
    if art_l and (path_l == art_l or path_l.startswith(art_l + "/")):
        return path_l[len(art_l):].lstrip("/")
    return path_l


def infer_module(path: str, artboard_name: str, rules: NamingRules) -> str:
    art_l = (artboard_name or "").lower()
    rel = _path_relative_to_artboard(path, artboard_name)
    # Shared chrome is decided from the layer path after stripping the artboard
    # prefix, so a board named after the feature does not relabel its navbar.
    if any(s and s in rel for s in rules.chrome_path_keywords):
        for src, dst in rules.module_keywords:
            if src.lower() in rel and dst != rules.chrome_module:
                return dst
        return rules.chrome_module
    blob = f"{art_l}/{rel}"
    for src, dst in rules.module_keywords:
        if src.lower() in blob:
            return dst
    slug = slugify(artboard_name)
    return slug or rules.default_module


def list_item_index(path: str, rules: NamingRules):
    try:
        m = re.search(rules.list_item_pattern, path or "", re.I)
    except re.error:
        m = None
    return m.group(1) if m else None


def text_purpose_from_content(text: str, name: str, rules: NamingRules):
    blob = f"{name} {text}".replace("\r", " ").replace("\n", " ")
    if re.search(r"\d+\s*[hH小时].*\d+\s*[mM分]|\d+h\s*\d+m", blob):
        return "timer"
    if re.search(r"^\s*(score|level)\b", blob, re.I) or re.search(r"score\s*\d+", blob, re.I):
        return "score"
    compact = (text or "").replace("\r", "").replace("\n", "").strip()
    if re.fullmatch(r"\d{1,2}", compact):
        return "place"
    if re.fullmatch(r"0\d+", compact) or re.fullmatch(r"\d{3,}", compact):
        return "name"
    low = blob.casefold()
    if any(k and k in low for k in rules.value_name_keywords):
        return "name"
    if is_fixed_label(text or "", rules) or is_fixed_label(name, rules):
        return slugify(normalize_text(text) or name)
    if text and re.search(r"[A-Za-z]", text) and not re.search(r"score|level", text, re.I):
        return "name"
    return None


def infer_type_purpose_state(node, module: str, rules: NamingRules):
    name = node.get("name") or ""
    path = (node.get("path") or "").lower()
    key = name.strip().lower()
    typ = purpose = None
    state = ""
    if key in rules.special_layer_names:
        typ, purpose, state = rules.special_layer_names[key]
    for spec in rules.path_specials:
        if spec["path_includes"] and not any(tok and tok in path for tok in spec["path_includes"]):
            continue
        if spec["name"] and key != spec["name"]:
            continue
        if spec["name_prefix"] and not key.startswith(spec["name_prefix"]):
            continue
        if not spec["name"] and not spec["name_prefix"]:
            continue
        typ = typ or spec["type"]
        purpose = purpose or spec["purpose"]
        state = state or spec["state"]
    typ = typ or find_token(name + " " + path, rules.type_keywords)
    purpose = purpose or find_token(name + " " + path, rules.purpose_keywords)
    state = state or find_token(name + " " + path, rules.state_keywords) or ""
    item_n = list_item_index(node.get("path") or "", rules)
    if node.get("is_text") or node.get("kind_export") in {"dynamic_text", "art_text"}:
        purpose = text_purpose_from_content(node.get("text") or "", name, rules) or purpose
    if item_n:
        if typ in {None, "bg", "item"} and (purpose in {None, "item", "base", "list"} or typ == "bg"):
            typ = typ or "bg"
            purpose = f"item{item_n}"
        elif typ == "icon" and purpose:
            if not state:
                purpose = f"{purpose}_item{item_n}"
        elif purpose in {"score", "name", "place"}:
            purpose = f"{purpose}_item{item_n}"
    if item_n and not state and any(k and k in path for k in rules.self_row_keywords):
        state = "self"
    if node.get("is_text") or node.get("kind_export") in {"dynamic_text", "art_text"}:
        typ = typ or "img"
        purpose = purpose or slugify(re.sub(r"[\d.]+", " ", name)) or "label"
        if purpose in {"bg", "btn", "icon", "frame", "tab", "item", "deco"}:
            purpose = "label"
    if typ is None:
        if purpose in {"navbar", "topbar", "screen", "base", "item", "list", "title", "slot"}:
            typ = "bg"
        elif purpose in {"hline", "vline", "divider"}:
            typ = "deco"
        elif purpose and purpose.split("_")[0] in {"avatar", "medal", "icon"}:
            typ = "icon"
        elif (node.get("width") or 0) >= 200 and (node.get("height") or 0) >= 80:
            typ = "bg"
        elif (node.get("width") or 0) <= 200 and (node.get("height") or 0) <= 200:
            typ = "icon"
        else:
            typ = "img"
    if not purpose:
        latin = slugify(name)
        if latin and latin not in rules.skip_tokens and not re.fullmatch(r"l?\d+", latin):
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


def build_entries(artboard, art_w, art_h, module_hint, warnings, rules=None):
    rules = rules or NamingRules(DEFAULT_RULES)
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
            kind = classify_text(node, rules)
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

        module = infer_module(node.get("path") or "", module_hint, rules)
        typ, purpose, state = infer_type_purpose_state({**node, "kind_export": kind}, module, rules)
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
        if should_descend(node, art_w, art_h, rules):
            container = {
                "name": None,
                "kind": "group",
                "layer_id": node.get("layer_id"),
                "children": [],
            }
            # Name the container for hierarchy only (not a slice).
            module = infer_module(node.get("path") or "", module_hint, rules)
            typ, purpose, state = infer_type_purpose_state(node, module, rules)
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
                if should_descend(base, art_w, art_h, rules) and not clipped:
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
    apply_pair_consistency(entries, rules)
    by_name = {e["name"]: e for e in entries}

    def sync_tree_kinds(node):
        if not node:
            return
        entry = by_name.get(node.get("name"))
        if entry:
            node["kind"] = entry["kind"]
        for child in node.get("children") or []:
            sync_tree_kinds(child)

    if tree:
        sync_tree_kinds(tree)
        tree_nodes.append(tree)
    return entries, tree_nodes


def list_artboards(roots):
    found = []

    def walk(nodes):
        for node in nodes:
            if node.get("is_artboard"):
                found.append(node)
            elif node.get("children"):
                walk(node["children"])

    walk(roots)
    return found


def make_canvas_target(psd, roots):
    return {
        "name": "canvas",
        "path": "",
        "depth": 0,
        "index_in_parent": 0,
        "layer_id": None,
        "kind": "artboard",
        "is_group": True,
        "is_artboard": True,
        "is_text": False,
        "is_adjustment": False,
        "visible": True,
        "opacity": 255,
        "blend_mode": "NORMAL",
        "clipping": False,
        "bbox": [0, 0, int(psd.width), int(psd.height)],
        "width": int(psd.width),
        "height": int(psd.height),
        "effects": [],
        "text": None,
        "children": roots,
        "source_is_canvas": True,
    }


def select_targets(roots, needle, psd=None):
    """Return one or more infer targets.

    --artboard <name or id> selects a single artboard (or top-level layer).
    With no --artboard, every visible top-level artboard is processed; if the
    document has none, the whole canvas is treated as one target.
    """
    artboards = list_artboards(roots)
    if needle is None:
        visible = [a for a in artboards if a.get("visible")]
        if visible:
            return visible
        if psd is not None:
            return [make_canvas_target(psd, roots)]
        if roots:
            return [r for r in roots if r.get("visible")] or list(roots)
        raise SystemExit("no visible artboard or canvas target")
    raw = str(needle)
    for node in artboards + [r for r in roots if r not in artboards]:
        if raw == str(node.get("layer_id")) or raw == (node.get("name") or ""):
            return [node]
        found = find_artboard_in([node], raw)
        if found:
            return [found]
    found = find_artboard_in(roots, raw)
    if found:
        return [found]
    raise SystemExit(f"artboard not found: {needle}")


def target_slug(target, used):
    slug = slugify(target.get("name") or "") or (
        f"artboard_{target.get('layer_id')}" if target.get("layer_id") is not None else "canvas"
    )
    return unique_name([slug], used)




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

    rules = NamingRules.load(getattr(args, "rules", None))
    targets = select_targets(roots, args.artboard, psd=psd)
    hidden = [t for t in targets if not t.get("visible")]
    if hidden and args.artboard:
        raise SystemExit(f"artboard is hidden: {hidden[0].get('name')}")
    targets = [t for t in targets if t.get("visible")]
    if not targets:
        raise SystemExit("no visible artboard or canvas target")

    def infer_one(target, dest: Path, time_base):
        dest.mkdir(parents=True, exist_ok=True)
        bbox = target.get("bbox") or [0, 0, target["width"], target["height"]]
        art_w, art_h = target["width"], target["height"]
        print(
            f"target {target.get('name')} id={target.get('layer_id')} {art_w}x{art_h} bbox={bbox}",
            flush=True,
        )
        t2 = time.time()
        entries, tree = build_entries(
            target, art_w, art_h, target.get("name") or "", warnings, rules
        )
        print(f"inferred {len(entries)} entries in {time.time()-t2:.1f}s", flush=True)
        t3 = time.time()
        preview_base = artboard_image(target, warnings)
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
                    "id": target.get("layer_id"),
                    "name": target.get("name"),
                    "bounds": bbox,
                    "size": [art_w, art_h],
                },
            },
            "naming_rule": "模块_类型_用途_状态，小写英文；禁止直接使用 PSD 图层名",
            "naming_rules_file": str(args.rules) if getattr(args, "rules", None) else None,
            "review_gate": "Edit this JSON to confirm. Nothing is exported until status is set to confirmed.",
            "entries": strip_runtime(entries),
            "tree": tree,
            "warnings": warnings,
            "stats": {
                "entry_count": len(entries),
                "by_kind": dict(kinds),
                "infer_sec": round(time.time() - time_base, 2),
            },
        }
        manifest_path = dest / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        preview_path = dest / "preview_annotated.png"
        annotate_preview(
            preview_base,
            manifest["entries"],
            (bbox[0], bbox[1]),
            preview_path,
            f"{target.get('name')}  {len(entries)} slices  (review preview, FX may be missing)",
        )
        return {
            "manifest": str(manifest_path),
            "preview": str(preview_path),
            "stats": manifest["stats"],
            "artboard": manifest["source"]["artboard"],
        }

    slug_used = set()
    if len(targets) == 1:
        result = infer_one(targets[0], out_dir, t0)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    index = []
    for target in targets:
        slug = target_slug(target, slug_used)
        dest = out_dir / slug
        item = infer_one(target, dest, time.time())
        item["out_dir"] = str(dest)
        item["slug"] = slug
        index.append(item)
    index_path = out_dir / "index.json"
    payload = {"targets": index, "count": len(index)}
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index": str(index_path), **payload}, ensure_ascii=False, indent=2))
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
        # Prefer the post-trim inset origin computed in export_slices.psjs.
        # Only fall back to "PNG size == logical size → use logical origin"
        # when the exporter did not record trim insets (older logs).
        png_size = item.get("out_size")
        if (not item.get("visible_bounds_from_trim_insets")
                and logi and png_size and len(png_size) == 2):
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


def alpha_over(canvas: Image.Image, im: Image.Image, xy) -> Image.Image:
    """True src-over. Image.paste(..., mask=im) is not a correct Porter-Duff over."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    x, y = int(xy[0]), int(xy[1])
    cw, ch = canvas.size
    iw, ih = im.size
    sx = 0 if x >= 0 else -x
    sy = 0 if y >= 0 else -y
    dx, dy = max(x, 0), max(y, 0)
    tw, th = min(iw - sx, cw - dx), min(ih - sy, ch - dy)
    if tw <= 0 or th <= 0:
        return canvas
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(im.crop((sx, sy, sx + tw, sy + th)), (dx, dy))
    return Image.alpha_composite(canvas, layer)


def hex_to_rgba(s, alpha=255):
    s = (s or "#ffffff").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) < 6:
        return (255, 255, 255, int(alpha))
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (r, g, b, int(max(0, min(255, alpha))))


def iter_font_files(fonts_dir):
    if not fonts_dir:
        return
    root = Path(fonts_dir)
    folders = [root]
    if root.is_dir():
        parent = root.parent
        for extra in (parent / "fonts_install", parent / "fonts"):
            if extra.is_dir() and extra not in folders:
                folders.append(extra)
    for folder in folders:
        if not folder.is_dir():
            continue
        for pat in ("*.ttf", "*.otf", "*.TTC", "*.ttc", "*.TTF", "*.OTF"):
            yield from sorted(folder.glob(pat))


def load_font_named(name, size, fonts_dir=None):
    size = max(1, int(round(size or 16)))
    needle = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    ranked = []
    for path in iter_font_files(fonts_dir):
        stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
        if needle and (needle == stem or needle in stem or stem in needle):
            ranked.insert(0, path)
        else:
            ranked.append(path)
    for path in ranked:
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return load_font(size)


def draw_dynamic_text(canvas, node, fonts_dir, doc_size, art_origin):
    """Approximate a dynamic_text node with Pillow. Not a PS typography emulator."""
    text = node.get("text") or {}
    content = (text.get("content") or "").replace("\r", "\n")
    if not content or not str(content).strip():
        return canvas, {"name": node["name"], "skipped": "empty"}
    size = text.get("size") or 24
    font = load_font_named(text.get("font"), size, fonts_dir)
    opacity = text.get("opacity")
    alpha = int(round((opacity if opacity is not None else 1) * 255)) if (opacity is None or opacity <= 1) else int(round(opacity / 100.0 * 255))
    color = hex_to_rgba(text.get("color") or "#ffffff", alpha)
    stroke = text.get("stroke") or {}
    stroke_w = 0
    stroke_fill = None
    if stroke and stroke.get("enabled") is not False and stroke.get("size"):
        stroke_w = max(0, int(round(float(stroke.get("size") or 0))))
        sop = stroke.get("opacity")
        if sop is None:
            sa = 255
        elif sop <= 1:
            sa = int(round(sop * 255))
        else:
            sa = int(round(sop / 100.0 * 255))
        stroke_fill = hex_to_rgba(stroke.get("color") or "#000000", sa)
    align = str(text.get("alignment") or "left").lower()
    if "center" in align:
        anchor = "ms"
    elif "right" in align:
        anchor = "rs"
    else:
        anchor = "ls"
    ox, oy = art_origin
    dw, dh = doc_size if doc_size and all(doc_size) else (None, None)
    cp = text.get("click_point") or {}
    if cp and cp.get("horizontal") is not None and dw:
        ax = cp["horizontal"] / 100.0 * dw - ox
        ay = cp["vertical"] / 100.0 * dh - oy
    else:
        box = node.get("logical_bounds") or node.get("visible_bounds") or {}
        ax = (box.get("left", 0) + box.get("right", 0)) / 2.0
        ay = box.get("bottom", 0)
        if "center" not in align:
            ax = box.get("left", ax)
    lines = content.split("\n")
    leading = text.get("leading")
    if leading:
        line_h = float(leading)
    else:
        line_h = float(size) * 1.2
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        y = ay + i * line_h
        kwargs = {"font": font, "fill": color, "anchor": anchor}
        if stroke_w:
            kwargs["stroke_width"] = stroke_w
            kwargs["stroke_fill"] = stroke_fill
        try:
            draw.text((ax, y), line, **kwargs)
        except TypeError:
            draw.text((ax, y), line, font=font, fill=color)
    return Image.alpha_composite(canvas, layer), {
        "name": node["name"],
        "xy": [round(ax, 2), round(ay, 2)],
        "anchor": anchor,
        "rendered": True,
        "font": text.get("font"),
        "size": size,
    }


def recomposite(layout, slices_dir, canvas_size, render_text=False, fonts_dir=None):
    w, h = canvas_size
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    placed = []
    src = layout.get("source") or {}
    art = src.get("artboard") or {}
    doc_size = src.get("doc_size") or [None, None]
    art_origin = (art.get("bounds") or [0, 0])[0:2]
    for node in layout["nodes"]:
        if node["kind"] == "dynamic_text":
            if render_text:
                canvas, info = draw_dynamic_text(canvas, node, fonts_dir, doc_size, art_origin)
                placed.append(info)
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
        canvas = alpha_over(canvas, im, (x, y))
        placed.append({"name": node["name"], "xy": [x, y], "size": list(im.size)})
    return canvas, placed


def text_region_mask(layout, cw, ch):
    mask = np.zeros((ch, cw), dtype=bool)
    for node in layout["nodes"]:
        if node["kind"] != "dynamic_text":
            continue
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        l, top, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
        l, top = max(0, l), max(0, top)
        r, bt = min(cw, r), min(ch, bt)
        if r > l and bt > top:
            mask[top:bt, l:r] = True
    return mask


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


def per_element_diffs(layout, ref, rec, cw, ch):
    rows = []
    for node in layout["nodes"]:
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        l, top, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
        l, top = max(0, l), max(0, top)
        r, bt = min(cw, r), min(ch, bt)
        if r <= l or bt <= top:
            continue
        aa = ref[top:bt, l:r]
        bb = rec[top:bt, l:r]
        diff = np.abs(aa[..., :3].astype(np.int16) - bb[..., :3].astype(np.int16))
        chan_max = diff.max(axis=-1)
        mae = float(diff.mean())
        mse = float((diff.astype(np.float64) ** 2).mean())
        psnr = 10 * np.log10(255 ** 2 / mse) if mse else 99.0
        lab_a = rgb_to_lab(aa[..., :3].reshape(-1, 3))
        lab_b = rgb_to_lab(bb[..., :3].reshape(-1, 3))
        dE = ciede2000(lab_a, lab_b)
        rows.append({
            "name": node["name"],
            "kind": node["kind"],
            "bounds": [l, top, r, bt],
            "mae": round(mae, 3),
            "psnr_db": round(float(psnr), 2),
            "mean_delta_e": round(float(dE.mean()), 3),
            "p95_delta_e": round(float(np.percentile(dE, 95)), 3),
            "pct_delta_e_gt5": round(float((dE > 5).mean() * 100), 3),
            "pct_px_diff_gt16": round(float((chan_max > 16).mean() * 100), 3),
        })
    rows.sort(key=lambda row: (-row["mean_delta_e"], -row["mae"]))
    return rows[:10], rows


def write_side_by_side(ref, rec, path, titles):
    ch, cw = ref.shape[0], ref.shape[1]
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
    images = [
        (Image.fromarray(ref[..., :3]), titles[0]),
        (Image.fromarray(rec[..., :3]), titles[1]),
        (Image.fromarray(heat), titles[2]),
    ]
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    for i, (im, title) in enumerate(images):
        canvas.paste(im.resize((rw, rh), resampling), (i * (rw + 20), 40))
        dr.text((i * (rw + 20) + 8, 10), title, fill=(255, 255, 255), font=font)
    canvas.save(path, quality=88)
    return path


def cmd_validate(args):
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    slices_dir = Path(args.slices_dir)
    full = Image.open(args.full_render).convert("RGBA")
    art = layout["source"]["artboard"]
    w, h = art["size"]
    if full.size != (w, h):
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

    fonts_dir = getattr(args, "fonts_dir", None)
    rec_masked, placed_masked = recomposite(layout, slices_dir, (w, h), render_text=False)
    rec_text, placed_text = recomposite(
        layout, slices_dir, (w, h), render_text=True, fonts_dir=fonts_dir
    )

    cw, ch = min(full.width, w), min(full.height, h)
    ref = np.asarray(full.convert("RGBA").crop((0, 0, cw, ch)))
    rec_m = np.asarray(rec_masked.convert("RGBA").crop((0, 0, cw, ch)))
    rec_t = np.asarray(rec_text.convert("RGBA").crop((0, 0, cw, ch)))
    text_mask = text_region_mask(layout, cw, ch)

    m_text_all = metrics(ref, rec_t, None)
    m_text_masked_regions = metrics(ref, rec_t, text_mask)
    m_masked = metrics(ref, rec_m, text_mask)
    m_no_text_all = metrics(ref, rec_m, None)
    worst10, _all = per_element_diffs(layout, ref, rec_t, cw, ch)
    worst10_masked, _ = per_element_diffs(layout, ref, rec_m, cw, ch)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rec_text.save(out_dir / "recomposite.png")
    rec_masked.save(out_dir / "recomposite_text_masked.png")
    write_side_by_side(
        ref, rec_t, out_dir / "validate_side_by_side.jpg",
        ("Photoshop full render", "Recomposite + Pillow text", "|diff| x3"),
    )
    write_side_by_side(
        ref, rec_m, out_dir / "validate_side_by_side_masked.jpg",
        ("Photoshop full render", "Recomposite, dynamic text omitted", "|diff| x3"),
    )

    rendered = sum(1 for p in placed_text if p.get("rendered"))
    report = {
        "artboard": art,
        "full_render": str(args.full_render),
        "full_render_size": list(full.size),
        "canvas": [w, h],
        "node_count": len(layout["nodes"]),
        "by_kind": dict(Counter(n["kind"] for n in layout["nodes"])),
        "fonts_dir": fonts_dir,
        "dynamic_text_rendered": rendered,
        "diff_text_rendered": m_text_all,
        "diff_text_rendered_excluding_text_boxes": m_text_masked_regions,
        "diff_text_masked": m_masked,
        "diff_no_text_unmasked": m_no_text_all,
        "worst_10_elements_text_rendered": worst10,
        "worst_10_elements_text_masked": worst10_masked[:10],
        "notes": [
            "diff_text_rendered: slices + Pillow approximation of dynamic_text vs the PS render.",
            "diff_text_masked: slices only; dynamic_text boxes excluded from the metrics.",
            "Pillow cannot emulate Photoshop blend modes, layer effects on type, or hinting; residual error there is expected.",
            "visible_bounds use post-trim insets (before.left - after.left) so the PNG origin matches the tight opaque box.",
        ],
        "issues": issues,
        "placed": len(placed_text),
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
    s.add_argument(
        "--artboard",
        default=None,
        help="Artboard name or layer_id. If omitted, every visible top-level artboard is processed "
             "(or the whole canvas when the document has none).",
    )
    s.add_argument(
        "--rules",
        default=None,
        help="Optional naming-rules JSON (see naming_rules.example.json). Generic defaults are used when omitted.",
    )
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
    s.add_argument("--fonts-dir", default=None, help="Directory of TTF/OTF files used to approximate dynamic_text")
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
