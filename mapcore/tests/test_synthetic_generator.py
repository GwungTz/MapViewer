"""Synthetic MAP generator regression (used by benchmarks)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mapcore.parser import parse_map_file
from mapcore.transform.tree_builder import build_views
from tools.generate_synthetic_map import generate_gnu_map


class SyntheticGeneratorTests(unittest.TestCase):
    def test_generated_map_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-synth-") as temp:
            path = Path(temp) / "synthetic.map"
            generate_gnu_map(200, 20, path)
            parsed = parse_map_file(path)
            data = parsed.data
            self.assertEqual(data.format, "gnu-ld")
            self.assertEqual(data.execution_region_count(), 4)
            self.assertEqual(data.section_count(), 200)
            self.assertEqual(len(data.component_sizes), 20)
            region_view, module_view = build_views(data)
            self.assertEqual(region_view.total, module_view.total)
            self.assertEqual(region_view.padding_bytes, 0)
            self.assertEqual(region_view.leaf_count, 200)
            self.assertEqual(module_view.module_count, 20)

    def test_object_count_is_capped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-synth-") as temp:
            path = Path(temp) / "synthetic.map"
            generate_gnu_map(80, 1000, path)
            data = parse_map_file(path).data
            self.assertLessEqual(len(data.component_sizes), 80)


if __name__ == "__main__":
    unittest.main()
