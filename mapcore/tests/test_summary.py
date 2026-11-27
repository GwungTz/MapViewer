"""Memory summary and grand-total normalisation tests."""

from __future__ import annotations

import unittest

from mapcore.parser import parse_map_file
from mapcore.tests.helpers import fixture
from mapcore.transform.summary import (
    MemoryOverrides,
    build_memory_summary,
    component_totals,
    normalize_grand_totals,
)


class KeilSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("keil_ac5.map")).data

    def test_keil_summary(self) -> None:
        summary = build_memory_summary(self.data)
        self.assertEqual(summary.rom.used, 414)
        self.assertEqual(summary.rom.total, 0x000F8000)
        self.assertEqual(summary.rom.percent, 0.0408)
        self.assertEqual(summary.ram.used, 80)
        self.assertEqual(summary.ram.total, 0x00020000)
        self.assertEqual(summary.ram.percent, 0.061)

    def test_override_in_kib(self) -> None:
        summary = build_memory_summary(self.data, MemoryOverrides(rom_kb=512, ram_kb=128))
        self.assertEqual(summary.rom.total, 512 * 1024)
        self.assertEqual(summary.ram.total, 128 * 1024)
        self.assertEqual(summary.rom.percent, 0.079)

    def test_zero_override_uses_map(self) -> None:
        summary = build_memory_summary(self.data, MemoryOverrides(rom_kb=0, ram_kb=0))
        self.assertEqual(summary.rom.total, 0x000F8000)
        self.assertEqual(summary.ram.total, 0x00020000)

    def test_normalized_totals(self) -> None:
        totals = normalize_grand_totals(self.data)
        self.assertEqual((totals.code, totals.ro_data, totals.rw_data, totals.zi_data), (294, 96, 24, 56))
        self.assertEqual(totals.total_rom, 414)

    def test_component_totals_match_modules(self) -> None:
        derived = component_totals(self.data)
        self.assertEqual(derived.total_rom, 414)
        self.assertEqual(derived.total_rw, 80)


class EideSummaryTests(unittest.TestCase):
    def test_zero_grand_totals_are_derived(self) -> None:
        data = parse_map_file(fixture("eide_arm_link_map.txt")).data
        # The upstream parser leaves the per-category buckets at zero.
        self.assertEqual(
            (data.grand_totals.code, data.grand_totals.ro_data, data.grand_totals.rw_data, data.grand_totals.zi_data),
            (222, 128, 16, 48),
        )
        totals = normalize_grand_totals(data)
        self.assertEqual(totals.total_ro, 350)
        self.assertEqual(totals.total_rw, 64)
        self.assertEqual(totals.total_rom, 366)


class GnuSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("gnu_ld_esp32.map")).data

    def test_gnu_memory_regions(self) -> None:
        summary = build_memory_summary(self.data)
        self.assertEqual(summary.rom.used, 192)
        self.assertEqual(summary.rom.total, 0xC00000)
        self.assertEqual(summary.ram.used, 88)
        self.assertEqual(summary.ram.total, 0x40000)
        self.assertGreater(summary.rom.percent, 0)

    def test_override(self) -> None:
        summary = build_memory_summary(self.data, MemoryOverrides(rom_kb=1024, ram_kb=64))
        self.assertEqual(summary.rom.total, 1024 * 1024)
        self.assertEqual(summary.ram.total, 64 * 1024)


if __name__ == "__main__":
    unittest.main()
