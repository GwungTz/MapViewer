"""Keil MDK / ARMCC / AC6 / EIDE parser regression tests."""

from __future__ import annotations

import unittest

from mapcore.parser import parse_map_file, parse_map_text
from mapcore.parser.keil_parser import parse_keil_map
from mapcore.tests.helpers import fixture


class KeilAc5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parsed = parse_map_file(fixture("keil_ac5.map"))
        cls.data = cls.parsed.data

    def test_detects_ac5(self) -> None:
        self.assertEqual(self.data.format, "keil-ac5")
        self.assertEqual(self.data.compiler_version, "AC5")
        self.assertIn("ARM Compiler 5.06", self.data.compiler)

    def test_region_structure(self) -> None:
        self.assertEqual(len(self.data.load_regions), 1)
        load = self.data.load_regions[0]
        self.assertEqual(load.name, "LR_IROM1")
        self.assertEqual(load.base_addr, 0x08008000)
        self.assertEqual(len(load.execution_regions), 2)
        self.assertEqual([region.name for region in load.execution_regions], ["ER_IROM1", "RW_IRAM1"])
        self.assertEqual(self.data.section_count(), 13)

    def test_pad_section(self) -> None:
        sections = [
            section
            for region in self.data.load_regions[0].execution_regions
            for section in region.sections
        ]
        pads = [section for section in sections if section.section_type == "PAD"]
        self.assertEqual(len(pads), 1)
        self.assertEqual(pads[0].size, 2)
        self.assertEqual(pads[0].attr, "RO")
        self.assertEqual(pads[0].object_name, "[PAD]")
        self.assertIsNone(pads[0].load_addr)

    def test_function_name_extraction_ac5(self) -> None:
        sections = self.data.load_regions[0].execution_regions[0].sections
        names = {section.section_name: section.function_name for section in sections}
        self.assertEqual(names["i.main"], "main")
        self.assertEqual(names["i.SystemInit"], "SystemInit")
        self.assertEqual(names["i.printf"], "printf")
        self.assertIsNone(names[".text"])
        self.assertIsNone(names["RESET"])

    def test_standard_keil_table_has_no_load_address(self) -> None:
        for region in self.data.load_regions[0].execution_regions:
            for section in region.sections:
                self.assertIsNone(section.load_addr)

    def test_component_tables(self) -> None:
        self.assertEqual(len(self.data.component_sizes), 4)
        self.assertEqual(len(self.data.library_members), 1)
        self.assertEqual(len(self.data.library_sizes), 1)
        member = self.data.library_members[0]
        self.assertEqual(member.object_name, "printf.o")
        self.assertEqual(member.library, "libc.a")
        self.assertEqual(member.member, "printf.o")
        self.assertEqual(
            (member.code, member.ro_data, member.rw_data, member.zi_data),
            (32, 16, 8, 0),
        )

    def test_grand_totals_from_numeric_line(self) -> None:
        totals = self.data.grand_totals
        self.assertEqual((totals.code, totals.ro_data, totals.rw_data, totals.zi_data), (294, 96, 24, 56))
        self.assertEqual(totals.total_ro, 390)
        self.assertEqual(totals.total_rw, 80)
        self.assertEqual(totals.total_rom, 414)

    def test_no_spurious_warnings(self) -> None:
        self.assertEqual(self.data.warnings, [])

    def test_encoding(self) -> None:
        self.assertEqual(self.parsed.encoding, "utf-8")
        self.assertEqual(self.parsed.size_bytes, len(self.parsed.raw))


class KeilAc6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("keil_ac6.map")).data

    def test_detects_ac6(self) -> None:
        self.assertEqual(self.data.format, "keil-ac6")
        self.assertEqual(self.data.compiler_version, "AC6")

    def test_function_extraction_and_outlined(self) -> None:
        sections = self.data.load_regions[0].execution_regions[0].sections
        by_name = {section.section_name: section for section in sections}
        self.assertEqual(by_name[".text.helper"].function_name, "helper")
        self.assertIsNone(by_name[".text"].function_name)
        outlined = by_name[".text.OUTLINED_FUNCTION_1"]
        self.assertIsNone(outlined.function_name)
        self.assertTrue(outlined.is_outlined_function)
        self.assertIsNone(by_name[".ARM.exidx"].function_name)

    def test_totals(self) -> None:
        totals = self.data.grand_totals
        self.assertEqual((totals.code, totals.ro_data, totals.rw_data, totals.zi_data), (112, 32, 0, 0))
        self.assertEqual(totals.total_ro, 144)
        self.assertEqual(totals.total_rom, 144)


class EideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("eide_arm_link_map.txt")).data

    def test_eide_execution_region_without_exec_base(self) -> None:
        regions = self.data.load_regions[0].execution_regions
        self.assertEqual(len(regions), 2)
        for region in regions:
            self.assertEqual(region.exec_base, region.load_base)
        self.assertEqual(regions[0].exec_base, 0x08008000)

    def test_eide_pad_and_totals(self) -> None:
        self.assertEqual(self.data.section_count(), 6)
        totals = self.data.grand_totals
        self.assertEqual((totals.code, totals.ro_data, totals.rw_data, totals.zi_data), (222, 128, 16, 48))
        self.assertEqual(totals.total_ro, 350)
        self.assertEqual(totals.total_rw, 64)
        self.assertEqual(totals.total_rom, 366)


class KeilEdgeCaseTests(unittest.TestCase):
    def test_load_address_variant(self) -> None:
        content = """Component: ARM Compiler 5.06 update 7 (build 960) Tool: armlink [4d3601]

Memory Map of the image

  Load Region LR (Base: 0x08000000, Size: 0x00000020, Max: 0x00010000, ABSOLUTE)

    Execution Region ER (Exec base: 0x20000000, Load base: 0x08000000, Size: 0x00000020, Max: 0x00010000, ABSOLUTE)

    Base Addr    Size         Type   Attr      Idx    E Section Name        Object

    0x20000000   0x08000010   0x00000010   Data   RW            1    .data               main.o
"""
        data = parse_keil_map(content)
        section = data.load_regions[0].execution_regions[0].sections[0]
        self.assertEqual(section.exec_addr, 0x20000000)
        self.assertEqual(section.load_addr, 0x08000010)
        self.assertEqual(section.size, 0x10)
        self.assertEqual(section.attr, "RW")

    def test_compressed_and_dash_load_address(self) -> None:
        content = """Component: ARM Compiler 5.06 update 7 (build 960) Tool: armlink [4d3601]

Memory Map of the image

  Load Region LR (Base: 0x08000000, Size: 0x00000020, Max: 0x00010000, ABSOLUTE)

    Execution Region ER (Exec base: 0x20000000, Load base: 0x08000000, Size: 0x00000020, Max: 0x00010000, ABSOLUTE)

    Base Addr    Size         Type   Attr      Idx    E Section Name        Object

    0x20000000   COMPRESSED   0x00000010   Data   RW   1    .data    main.o
    0x20000010   -            0x00000008   Data   RW   2    .data    other.o
"""
        data = parse_keil_map(content)
        sections = data.load_regions[0].execution_regions[0].sections
        self.assertIsNone(sections[0].load_addr)
        self.assertIsNone(sections[1].load_addr)

    def test_section_beyond_region_max_warns(self) -> None:
        content = """Component: ARM Compiler 5.06 update 7 (build 960) Tool: armlink [4d3601]

Memory Map of the image

  Load Region LR (Base: 0x08000000, Size: 0x00000020, Max: 0x00010000, ABSOLUTE)

    Execution Region ER (Exec base: 0x08000000, Load base: 0x08000000, Size: 0x00000020, Max: 0x00000010, ABSOLUTE)

    Base Addr    Size         Type   Attr      Idx    E Section Name        Object

    0x08000000   0x00000020   Code   RO            1    .text.foo           main.o
"""
        data = parse_keil_map(content)
        self.assertTrue(data.warnings)
        self.assertIn("extends beyond", data.warnings[0].message)

    def test_malformed_memory_line_warns_with_line_number(self) -> None:
        content = """Component: ARM Compiler 5.06 update 7 (build 960) Tool: armlink [4d3601]

Memory Map of the image

  Load Region LR (Base: 0x08000000, Size: 0x00000010, Max: 0x00010000, ABSOLUTE)

    Execution Region ER (Exec base: 0x08000000, Load base: 0x08000000, Size: 0x00000010, Max: 0x00010000, ABSOLUTE)

    0x08000000 garbage that is not a section line
"""
        data = parse_keil_map(content)
        self.assertEqual(len(data.warnings), 1)
        self.assertGreater(data.warnings[0].line_no, 0)
        self.assertIn("Unrecognised", data.warnings[0].message)


class FormatDispatchTests(unittest.TestCase):
    def test_keil_detection_from_header_only(self) -> None:
        content = "Component: Arm Compiler for Embedded 6.19 Tool: armlink\n\nnothing else\n"
        with self.assertRaises(Exception) as context:
            parse_map_text(content)
        self.assertEqual(type(context.exception).__name__, "EmptyMapData")

    def test_empty_content(self) -> None:
        with self.assertRaises(Exception) as context:
            parse_map_text("")
        self.assertEqual(type(context.exception).__name__, "UnsupportedMapFormat")


if __name__ == "__main__":
    unittest.main()
