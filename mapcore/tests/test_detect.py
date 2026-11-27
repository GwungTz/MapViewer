"""Format detection and encoding tests."""

from __future__ import annotations

import unittest

from mapcore.errors import EmptyMapData, UnsupportedMapFormat
from mapcore.parser import parse_map_text
from mapcore.parser.detect import detect_format, is_gnu_ld_map
from mapcore.parser.textio import decode_map_bytes
from mapcore.tests.helpers import fixture


class DetectFormatTests(unittest.TestCase):
    def test_keil_ac5(self) -> None:
        text = fixture("keil_ac5.map").read_text(encoding="utf-8")
        self.assertEqual(detect_format(text), "keil-ac5")

    def test_keil_ac6(self) -> None:
        text = fixture("keil_ac6.map").read_text(encoding="utf-8")
        self.assertEqual(detect_format(text), "keil-ac6")

    def test_gnu_ld(self) -> None:
        text = fixture("gnu_ld_esp32.map").read_text(encoding="utf-8")
        self.assertTrue(is_gnu_ld_map(text))
        self.assertEqual(detect_format(text), "gnu-ld")

    def test_unknown_format_reports_markers(self) -> None:
        text = fixture("unknown_iar.map").read_text(encoding="utf-8")
        with self.assertRaises(UnsupportedMapFormat) as context:
            detect_format(text)
        self.assertIn("IAR ELF Linker", " ".join(context.exception.detected))

    def test_unknown_format_does_not_silently_parse(self) -> None:
        with self.assertRaises(UnsupportedMapFormat):
            parse_map_text("hello world\nthis is not a map file\n")

    def test_keil_header_without_data_raises_empty(self) -> None:
        with self.assertRaises(EmptyMapData):
            parse_map_text("Component: ARM Compiler 5.06 update 7 (build 960) Tool: armlink [4d3601]\n")


class EncodingTests(unittest.TestCase):
    def test_utf8_bom(self) -> None:
        raw = "\ufeffComponent: ARM Compiler 6.19\n".encode("utf-8")
        decoded = decode_map_bytes(raw)
        self.assertEqual(decoded.encoding, "utf-8-sig")
        self.assertTrue(decoded.text.startswith("Component:"))
        self.assertFalse(decoded.text.startswith("\ufeff"))

    def test_utf16_bom(self) -> None:
        raw = "Component: ARM Compiler 6.19\n".encode("utf-16")
        decoded = decode_map_bytes(raw)
        self.assertEqual(decoded.encoding, "utf-16")
        self.assertIn("Compiler", decoded.text)
        self.assertTrue(decoded.warnings)

    def test_gb18030_fallback(self) -> None:
        raw = "Component: ARM Compiler 5.06\n中文目标文件.o\n".encode("gb18030")
        decoded = decode_map_bytes(raw)
        self.assertEqual(decoded.encoding, "gb18030")
        self.assertIn("中文目标文件.o", decoded.text)

    def test_empty_bytes(self) -> None:
        from mapcore.errors import MapViewError

        with self.assertRaises(MapViewError):
            decode_map_bytes(b"")


if __name__ == "__main__":
    unittest.main()
