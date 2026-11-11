#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for parent-group layer effects vs inner-leaf export targets.

    python test_slicing_helper_group_effects.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _node(**kw):
    base = {
        "name": kw.get("name", "layer"),
        "path": kw.get("path", kw.get("name", "layer")),
        "layer_id": kw.get("layer_id"),
        "is_group": kw.get("is_group", False),
        "is_artboard": kw.get("is_artboard", False),
        "is_adjustment": kw.get("is_adjustment", False),
        "visible": kw.get("visible", True),
        "effects": kw.get("effects", []),
        "bbox": kw.get("bbox", [0, 0, 10, 10]),
        "children": kw.get("children", []),
    }
    base.update({k: v for k, v in kw.items() if k not in base})
    return base


class TestEffectGroupHelpers(unittest.TestCase):
    def setUp(self):
        # artboard > wrapper (no fx) > item group (DropShadow) > inner leaf (no fx)
        self.leaf = _node(name="artwork", layer_id=20, bbox=[2, 2, 8, 8])
        self.item = _node(
            name="<item>",
            layer_id=10,
            is_group=True,
            effects=["DropShadow"],
            bbox=[1, 1, 9, 9],
            children=[self.leaf],
        )
        self.wrapper = _node(
            name="wrapper",
            layer_id=5,
            is_group=True,
            effects=[],
            bbox=[0, 0, 20, 20],
            children=[self.item],
        )
        self.artboard = _node(
            name="<artboard>",
            layer_id=1,
            is_group=True,
            is_artboard=True,
            effects=["DropShadow"],  # must be ignored
            bbox=[0, 0, 100, 100],
            children=[self.wrapper],
        )

    def test_is_effect_group_skips_artboard_and_bare_group(self):
        self.assertTrue(h.is_effect_group(self.item))
        self.assertFalse(h.is_effect_group(self.wrapper))
        self.assertFalse(h.is_effect_group(self.artboard))
        self.assertFalse(h.is_effect_group(self.leaf))

    def test_nearest_group_walks_past_wrapper_and_skips_artboard(self):
        found = h.nearest_effect_group([self.artboard, self.wrapper, self.item])
        self.assertIs(found, self.item)
        self.assertIsNone(h.nearest_effect_group([self.artboard, self.wrapper]))

    def test_leaf_map_points_at_the_group_not_the_leaf(self):
        mapping = h.leaf_to_effect_group_map(self.artboard)
        self.assertEqual(set(mapping), {20})
        self.assertEqual(mapping[20]["layer_id"], 10)

    def test_missing_crop_does_not_change_suggested_select_ids(self):
        rec = h.effect_group_record(self.item, crop_rel=None)
        self.assertIsNone(rec["crop"])
        self.assertEqual(rec["select_ids"], [10])
        self.assertEqual(rec["suggested_select_ids"], [10])
        self.assertEqual(rec["child_leaf_ids"], [20])
        self.assertNotEqual(rec["suggested_select_ids"], rec["child_leaf_ids"])
        self.assertIn("not a reason to fall back", rec["notes"])

    def test_iter_effect_groups_only_yields_the_item_group(self):
        ids = [g["layer_id"] for g, _ in h.iter_effect_groups(self.artboard, [0, 0, 100, 100])]
        self.assertEqual(ids, [10])


if __name__ == "__main__":
    unittest.main()
