"""Optional parity check against the pinned upstream TypeScript parser.

The upstream parser is bundled with esbuild into
``tools/upstream_parity/upstream-parser.cjs``.  Rebuild it with::

    cd tools/upstream_parity
    npm install
    npx esbuild ../../third_party/Map-View/src/parser/index.ts \\
        --bundle --platform=node --format=cjs --outfile=upstream-parser.cjs

The test is skipped when Node.js or the bundle is not available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from mapcore.parser import parse_map_file
from mapcore.tests.helpers import fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
PARITY_DIR = REPO_ROOT / "tools" / "upstream_parity"
COMPARE_SCRIPT = PARITY_DIR / "compare.js"
BUNDLE = PARITY_DIR / "upstream-parser.cjs"

CASES = (
    "keil_ac5.map",
    "keil_ac6.map",
    "eide_arm_link_map.txt",
    "gnu_ld_esp32.map",
)


def upstream_stats(node: str, path: Path) -> dict:
    process = subprocess.run(
        [node, str(COMPARE_SCRIPT), str(path)],
        cwd=str(PARITY_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode != 0:
        raise AssertionError(f"upstream parser failed: {process.stderr}")
    return json.loads(process.stdout)


@unittest.skipUnless(
    shutil.which("node") and COMPARE_SCRIPT.is_file() and BUNDLE.is_file(),
    "upstream TypeScript parser bundle is not available",
)
class UpstreamParityTests(unittest.TestCase):
    def test_fixture_statistics_match_upstream(self) -> None:
        node = shutil.which("node")
        assert node is not None
        for name in CASES:
            with self.subTest(fixture=name):
                path = fixture(name)
                upstream = upstream_stats(node, path)
                data = parse_map_file(path).data

                self.assertEqual(data.compiler_version, upstream["compilerVersion"])
                self.assertEqual(len(data.load_regions), upstream["loadRegions"])
                self.assertEqual(data.execution_region_count(), upstream["executionRegions"])
                self.assertEqual(data.section_count(), upstream["sections"])
                self.assertEqual(len(data.component_sizes), upstream["components"])
                self.assertEqual(len(data.library_members), upstream["libraryMembers"])
                self.assertEqual(len(data.library_sizes), upstream["librarySizes"])
                self.assertEqual(len(data.memory_regions), upstream["memoryRegions"])

                totals = data.grand_totals
                upstream_totals = upstream["grandTotals"]
                self.assertEqual(totals.total_ro, upstream_totals["totalRO"])
                self.assertEqual(totals.total_rw, upstream_totals["totalRW"])
                self.assertEqual(totals.total_rom, upstream_totals["totalROM"])
                if data.format == "gnu-ld":
                    self.assertEqual(totals.code, upstream_totals["code"])
                    self.assertEqual(totals.ro_data, upstream_totals["roData"])
                    self.assertEqual(totals.rw_data, upstream_totals["rwData"])
                    self.assertEqual(totals.zi_data, upstream_totals["ziData"])

                sections = [
                    section
                    for load in data.load_regions
                    for region in load.execution_regions
                    for section in region.sections
                ]
                section_types: dict[str, int] = {}
                for section in sections:
                    section_types[section.section_type] = (
                        section_types.get(section.section_type, 0) + 1
                    )
                for section_type, expected in upstream["sectionTypes"].items():
                    self.assertEqual(section_types.get(section_type, 0), expected)

                self.assertEqual(
                    [section.function_name for section in sections if section.function_name],
                    upstream["functionNames"],
                )


if __name__ == "__main__":
    unittest.main()
