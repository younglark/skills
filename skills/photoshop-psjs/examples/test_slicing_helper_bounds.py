#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small tests for inverted / empty preview bounds. Run from this directory:

    python test_slicing_helper_bounds.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


class TestNormalizeBox(unittest.TestCase):
    def test_swaps_inverted_edges(self):
        self.assertEqual(h.normalize_box(10, 20, 4, 8), (4.0, 8.0, 10.0, 20.0))

    def test_empty_box_returns_none_and_warns(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertIsNone(h.normalize_box(5, 5, 5, 5, name="empty"))
        self.assertIn("skipping empty or inverted bounds", buf.getvalue())
        self.assertIn("empty", buf.getvalue())

    def test_almost_empty_after_clamp_is_skipped(self):
        # Completely outside the image collapses to a point and is skipped.
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(h.normalize_box(-20, -20, -10, -10, width=8, height=8))

    def test_clamps_to_image(self):
        box = h.normalize_box(-4, -4, 50, 50, width=10, height=10)
        self.assertEqual(box, (0.0, 0.0, 9.0, 9.0))

    def test_non_numeric_is_skipped(self):
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(h.normalize_box("x", 0, 1, 1))


class TestAnnotatePreviewBounds(unittest.TestCase):
    def test_inverted_entry_does_not_crash(self):
        base = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
        entries = [
            {
                "name": "valid_part",
                "kind": "image",
                "logical_bounds": [8, 8, 24, 24],
            },
            {
                "name": "inverted_part",
                "kind": "image",
                "logical_bounds": [40, 40, 20, 10],  # swapped; should clamp/swap, not raise
            },
            {
                "name": "empty_part",
                "kind": "nine_slice",
                "logical_bounds": [5, 5, 5, 5],
                "nine_slice": {"left": 1, "top": 1, "right": 1, "bottom": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "preview.png"
            buf = io.StringIO()
            with redirect_stderr(buf):
                path = h.annotate_preview(base, entries, (0, 0), out, "test preview")
            self.assertTrue(path.exists())
            with Image.open(path) as im:
                # legend bar is 36px tall
                self.assertEqual(im.size, (64, 100))
            err = buf.getvalue()
            self.assertIn("empty_part", err)

    def test_missing_state_annotation_skips_empty_box(self):
        image = Image.new("RGB", (32, 32), (0, 0, 0))
        markers = [
            {
                "label": "missing: highlight",
                "bounds": {"left": 20, "top": 20, "right": 4, "bottom": 8},
            },
            {
                "label": "missing: base",
                "bounds": {"left": 1, "top": 1, "right": 1, "bottom": 1},
            },
        ]
        with redirect_stderr(io.StringIO()):
            out = h.annotate_missing_state(image, markers)
        self.assertEqual(out.size, (32, 32))


if __name__ == "__main__":
    unittest.main()
