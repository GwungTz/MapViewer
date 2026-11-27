"""GNU ld / ESP-IDF parser regression tests."""

from __future__ import annotations

import unittest

from mapcore.parser import parse_map_file, parse_map_text
from mapcore.errors import MapParseError
from mapcore.parser.gnu_ld_parser import (
    classify_section_type,
    extract_library_name,
    extract_object_name,
    get_function_from_section,
    parse_gnu_ld_map,
)
from mapcore.tests.helpers import fixture


class GnuLdFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parsed = parse_map_file(fixture("gnu_ld_esp32.map"))
        cls.data = cls.parsed.data

    def test_detection(self) -> None:
        self.assertEqual(self.data.format, "gnu-ld")
        self.assertEqual(self.data.compiler_version, "AC6")
        self.assertEqual(self.data.compiler, "GNU ld (ESP-IDF)")

    def test_memory_regions(self) -> None:
        regions = self.data.memory_regions
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].name, "iram0_0_seg")
        self.assertEqual(regions[0].origin, 0x40080000)
        self.assertEqual(regions[0].length, 0x40000)
        self.assertEqual(regions[0].attrs, "xrw")
        self.assertEqual(regions[1].name, "drom0_0_seg")
        self.assertEqual(regions[1].length, 0xC00000)

    def test_output_sections(self) -> None:
        regions = self.data.load_regions[0].execution_regions
        self.assertEqual([region.name for region in regions], [".text", ".rodata", ".data", ".bss", ".eh_frame"])
        # Debug sections are filtered out by upstream rules.
        self.assertNotIn(".debug_info", [region.name for region in regions])
        self.assertEqual(self.data.section_count(), 12)

    def test_entries_and_merging_names(self) -> None:
        text = self.data.load_regions[0].execution_regions[0]
        names = [(section.section_name, section.function_name, section.size) for section in text.sections]
        self.assertIn((".literal.app_main", "app_main", 16), names)
        self.assertIn((".text.app_main", "app_main", 64), names)
        self.assertIn((".text.esp_log_write", "esp_log_write", 32), names)
        self.assertIn(("*fill*", None, 8), names)

    def test_fill_classification(self) -> None:
        regions = {region.name: region for region in self.data.load_regions[0].execution_regions}
        text_fill = [s for s in regions[".text"].sections if s.section_name == "*fill*"][0]
        bss_fill = [s for s in regions[".bss"].sections if s.section_name == "*fill*"][0]
        eh_fill = [s for s in regions[".eh_frame"].sections if s.section_name == "*fill*"][0]
        self.assertEqual((text_fill.section_type, text_fill.attr), ("Code", "RO"))
        self.assertEqual((bss_fill.section_type, bss_fill.attr), ("Zero", "RW"))
        self.assertEqual((eh_fill.section_type, eh_fill.attr), ("PAD", "RO"))

    def test_object_names_for_library_members(self) -> None:
        sections = [
            section
            for region in self.data.load_regions[0].execution_regions
            for section in region.sections
        ]
        log_names = {section.object_name for section in sections if "log" in section.object_name}
        self.assertEqual(log_names, {"liblog.a(log.c.obj)"})

    def test_components(self) -> None:
        self.assertEqual(len(self.data.component_sizes), 1)
        self.assertEqual(len(self.data.library_members), 1)
        main = self.data.component_sizes[0]
        self.assertEqual(main.object_name, "main.o")
        self.assertEqual((main.code, main.ro_data, main.rw_data, main.zi_data), (80, 32, 8, 48))
        log = self.data.library_members[0]
        self.assertEqual(log.object_name, "liblog.a(log.c.obj)")
        self.assertEqual(log.library, "liblog.a")
        self.assertEqual((log.code, log.ro_data, log.rw_data, log.zi_data), (32, 8, 32, 0))

    def test_grand_totals(self) -> None:
        totals = self.data.grand_totals
        self.assertEqual((totals.code, totals.ro_data, totals.rw_data, totals.zi_data), (112, 40, 40, 48))
        self.assertEqual(totals.total_ro, 152)
        self.assertEqual(totals.total_rw, 88)
        self.assertEqual(totals.total_rom, 192)


class GnuHelperTests(unittest.TestCase):
    def test_object_name_normalisation(self) -> None:
        self.assertEqual(
            extract_object_name(r"esp-idf/heap/libheap.a(tlsf.c.obj)"), "libheap.a(tlsf.c.obj)"
        )
        self.assertEqual(
            extract_object_name(r"CMakeFiles/test.elf.dir/project_elf_src_esp32s3.c.obj"),
            "project_elf_src_esp32s3.c.obj",
        )
        self.assertEqual(
            extract_object_name(r"output/d13x/kernel/rt-thread/src/kservice.o"), "kservice.o"
        )
        self.assertEqual(extract_object_name("*fill*"), "*fill*")

    def test_library_name(self) -> None:
        self.assertEqual(
            extract_library_name(r"esp-idf/heap/libheap.a(tlsf.c.obj)"), "libheap.a"
        )
        self.assertIsNone(extract_library_name("build/main.o"))

    def test_function_from_section(self) -> None:
        self.assertEqual(get_function_from_section(".text.esp_log_write"), "esp_log_write")
        self.assertEqual(get_function_from_section(".literal.foo"), "foo")
        self.assertEqual(get_function_from_section(".iram0.foo"), "foo")
        self.assertEqual(get_function_from_section(".iram0.text.bar"), "text.bar")
        self.assertIsNone(get_function_from_section(".rodata.str1.1"))

    def test_classify_section_type(self) -> None:
        self.assertEqual(classify_section_type(".bss.foo", ".bss"), ("Zero", "RW"))
        self.assertEqual(classify_section_type(".data.foo", ".data"), ("Data", "RW"))
        self.assertEqual(classify_section_type(".rodata.str1.1", ".rodata"), ("Data", "RO"))
        self.assertEqual(classify_section_type(".text.foo", ".text"), ("Code", "RO"))
        self.assertEqual(classify_section_type("*fill*", ".eh_frame"), ("PAD", "RO"))


class GnuSplitHeaderTests(unittest.TestCase):
    def test_unsafe_64bit_address_is_rejected(self) -> None:
        content = """Archive member included to satisfy reference by file (symbol)

Memory Configuration

Name             Origin             Length             Attributes
FLASH            0x0020000000000000 0x00100000         rx

Linker script and memory map

.text           0x0020000000000000       0x100
 .text.foo      0x0020000000000000       0x10 build/main.o
"""
        with self.assertRaises(MapParseError):
            parse_map_text(content)

    def test_unmatched_uppercase_hex_warns(self) -> None:
        content = """Archive member included to satisfy reference by file (symbol)

Memory Configuration

Name             Origin             Length             Attributes
FLASH            0x08000000         0x00100000         rx

Linker script and memory map

.text           0x08000000       0x20
 .text.good     0x08000000       0x10 build/good.o
 .text.uppercase 0x080000A0       0x10 build/upper.o
"""
        data = parse_gnu_ld_map(content)
        self.assertEqual(data.section_count(), 1)
        self.assertTrue(data.warnings)
        self.assertIn("Unrecognised", data.warnings[0].message)

    def test_section_name_on_its_own_line(self) -> None:
        content = """Archive member included to satisfy reference by file (symbol)

Memory Configuration

Name             Origin             Length             Attributes
FLASH            0x08000000         0x00100000         rx

Linker script and memory map

.text
                0x08000000       0x20
 .text.foo
                0x08000000       0x10 build/main.o
                0x08000010                foo
"""
        data = parse_gnu_ld_map(content)
        regions = data.load_regions[0].execution_regions
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, ".text")
        self.assertEqual(regions[0].exec_base, 0x08000000)
        self.assertEqual(len(regions[0].sections), 1)
        self.assertEqual(regions[0].sections[0].function_name, "foo")


if __name__ == "__main__":
    unittest.main()
