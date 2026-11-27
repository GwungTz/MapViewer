"""Report package round-trip: shards, manifest, integrity, atomic output."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mapcore.cancel import CancelToken
from mapcore.errors import ChunkIntegrityError, GenerationCancelled
from mapcore.export.jsonl_writer import iter_jsonl_gz, read_json_gz
from mapcore.export.checksum import sha256_file
from mapcore.export.report_writer import generate_report
from mapcore.export.verify import verify_report
from mapcore.tests.helpers import fixture


def load_chunk_records(report: Path, index: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page in index["chunks"][key]["pages"]:
        for _line_no, record in iter_jsonl_gz(report / page["path"]):
            records.append(record)
    return records


def load_all_chunks(report: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    index = read_json_gz(report / "data" / "tree-index.json.gz")
    chunks = {key: load_chunk_records(report, index, key) for key in index["chunks"]}
    return index, chunks


class GeneratedReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="mapview-test-")
        cls.report = Path(cls.temp.name) / "firmware.mapview"
        cls.result = generate_report(fixture("keil_ac5.map"), cls.report, verify="full")
        cls.manifest = json.loads((cls.report / "manifest.json").read_text(encoding="utf-8"))
        cls.index, cls.chunks = load_all_chunks(cls.report)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_manifest_schema(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["compression"], "gzip")
        self.assertEqual(manifest["encoding"], "utf-8")
        self.assertEqual(manifest["source"]["file_name"], "keil_ac5.map")
        self.assertEqual(manifest["source"]["format"], "keil-ac5")
        self.assertEqual(manifest["views"]["region"]["total_bytes"], 472)
        self.assertEqual(manifest["views"]["module"]["total_bytes"], 470)
        self.assertEqual(manifest["views"]["region"]["node_count"], 23)
        self.assertEqual(manifest["views"]["module"]["node_count"], 18)
        self.assertEqual(manifest["grand_totals"]["total_rom"], 414)

    def test_verify_levels(self) -> None:
        result = verify_report(self.report, level="full")
        self.assertEqual(result["records"], 41)
        self.assertEqual(result["search_records"], 16)
        self.assertEqual(verify_report(self.report, level="basic")["chunks"], 4)

    def test_region_root_chunk_is_first_screen(self) -> None:
        records = self.chunks["rroot"]
        kinds = {record["kind"] for record in records}
        self.assertEqual(kinds, {"root", "region", "object"})
        self.assertEqual(len(records), 11)
        root = records[0]
        self.assertEqual(root["id"], "rroot")
        self.assertIsNone(root["parent_id"])
        self.assertEqual(root["size"], 472)
        objects = [record for record in records if record["kind"] == "object"]
        self.assertEqual(len(objects), 8)
        for record in objects:
            self.assertTrue(record["children_chunk"])

    def test_module_root_chunk_is_first_screen(self) -> None:
        records = self.chunks["mroot"]
        kinds = {record["kind"] for record in records}
        self.assertEqual(kinds, {"root", "category", "module"})
        self.assertEqual(len(records), 7)
        modules = [record for record in records if record["kind"] == "module"]
        self.assertEqual(len(modules), 4)
        for module in modules:
            self.assertEqual(sum(module["parts"].values()), module["size"])
            self.assertGreater(module["child_count"], 0)

    def test_no_duplicate_ids_and_parents_resolve(self) -> None:
        records = [record for chunk in self.chunks.values() for record in chunk]
        ids = [record["id"] for record in records]
        self.assertEqual(len(ids), len(set(ids)))
        known = set(ids)
        for record in records:
            if record["parent_id"] is not None:
                self.assertIn(record["parent_id"], known)

    def test_size_aggregates(self) -> None:
        records = {record["id"]: record for chunk in self.chunks.values() for record in chunk}
        child_sums: dict[str, int] = {}
        for record in records.values():
            if record["parent_id"]:
                child_sums[record["parent_id"]] = (
                    child_sums.get(record["parent_id"], 0) + record["size"]
                )
        for record in records.values():
            if record["kind"] in ("root", "region", "object", "category"):
                self.assertEqual(
                    child_sums.get(record["id"], 0),
                    record["size"],
                    f"size mismatch for {record['id']}",
                )

    def test_children_chunks_are_indexed(self) -> None:
        for record in (r for chunk in self.chunks.values() for r in chunk):
            chunk = record["children_chunk"]
            if chunk is not None:
                self.assertIn(chunk, self.index["chunks"])

    def test_search_index_paths_resolve(self) -> None:
        records = [
            record
            for _line_no, record in iter_jsonl_gz(self.report / "data" / "search-index.jsonl.gz")
        ]
        self.assertEqual(len(records), 16)
        known = {record["id"] for chunk in self.chunks.values() for record in chunk}
        for record in records:
            path = record["path_ids"]
            self.assertEqual(path[-1], record["id"])
            self.assertTrue(set(path) <= known)
        self.assertTrue(any(record["view"] == "module" for record in records))
        self.assertTrue(any(record["name"] == "main" for record in records))

    def test_chunk_records_are_valid_jsonl(self) -> None:
        for entry in self.index["chunks"].values():
            for page in entry["pages"]:
                count = 0
                for _line_no, record in iter_jsonl_gz(self.report / page["path"]):
                    self.assertIsInstance(record, dict)
                    count += 1
                self.assertEqual(count, page["records"])

    def test_tree_index_metadata_matches_manifest(self) -> None:
        self.assertEqual(self.manifest["tree_index"], "data/tree-index.json.gz")
        self.assertEqual(self.manifest["search_index"], "data/search-index.jsonl.gz")
        self.assertEqual(
            sha256_file(self.report / self.manifest["tree_index"]),
            self.manifest["tree_index_sha256"],
        )
        self.assertEqual(
            sha256_file(self.report / self.manifest["search_index"]),
            self.manifest["search_index_sha256"],
        )


class IntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mapview-corrupt-")
        self.report = Path(self.temp.name) / "report.mapview"
        generate_report(fixture("keil_ac5.map"), self.report, verify="basic")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_corrupt_child_chunk_is_reported(self) -> None:
        target = self.report / "data" / "region" / "chunks" / "rchunk00001.jsonl.gz"
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0xFF
        target.write_bytes(bytes(data))
        with self.assertRaises(ChunkIntegrityError) as context:
            verify_report(self.report, level="basic")
        self.assertIn("rchunk00001", str(context.exception))

    def test_missing_shard_is_reported(self) -> None:
        target = self.report / "data" / "module" / "root.jsonl.gz"
        target.unlink()
        with self.assertRaises(ChunkIntegrityError) as context:
            verify_report(self.report, level="basic")
        self.assertIn("module/root.jsonl.gz", str(context.exception))

    def test_corrupt_manifest(self) -> None:
        (self.report / "manifest.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ChunkIntegrityError):
            verify_report(self.report, level="basic")


class RegenerationTests(unittest.TestCase):
    def test_deterministic_shards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-determinism-") as temp:
            first = Path(temp) / "a.mapview"
            second = Path(temp) / "b.mapview"
            generate_report(fixture("keil_ac5.map"), first, verify="none")
            generate_report(fixture("keil_ac5.map"), second, verify="none")

            def files(root: Path):
                return {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file() and path.name != "manifest.json"
                }

            self.assertEqual(files(first), files(second))

    def test_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-replace-") as temp:
            report = Path(temp) / "report.mapview"
            generate_report(fixture("keil_ac5.map"), report, verify="basic")
            first_manifest = (report / "manifest.json").read_bytes()
            generate_report(fixture("keil_ac6.map"), report, verify="basic")
            second_manifest = (report / "manifest.json").read_bytes()
            self.assertNotEqual(first_manifest, second_manifest)
            self.assertEqual(verify_report(report, level="basic")["level"], "basic")
            leftovers = [
                path.name
                for path in Path(temp).iterdir()
                if path.name.startswith(".") or ".bak-" in path.name
            ]
            self.assertEqual(leftovers, [])

    def test_cancel_does_not_create_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-cancel-") as temp:
            report = Path(temp) / "report.mapview"
            token = CancelToken()
            token.cancel()
            with self.assertRaises(GenerationCancelled):
                generate_report(fixture("keil_ac5.map"), report, cancel=token)
            self.assertFalse(report.exists())
            self.assertEqual([p.name for p in Path(temp).iterdir()], [])

    def test_cancel_preserves_existing_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-cancel2-") as temp:
            report = Path(temp) / "report.mapview"
            generate_report(fixture("keil_ac5.map"), report, verify="none")
            original = (report / "manifest.json").read_bytes()
            token = CancelToken()
            token.cancel()
            with self.assertRaises(GenerationCancelled):
                generate_report(fixture("keil_ac6.map"), report, cancel=token)
            self.assertEqual((report / "manifest.json").read_bytes(), original)


class SpilledRootTests(unittest.TestCase):
    def test_objects_moved_out_of_root_chunk(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mapview-spill-") as temp:
            report = Path(temp) / "report.mapview"
            generate_report(
                fixture("keil_ac5.map"),
                report,
                verify="full",
                max_root_objects=0,
            )
            manifest = json.loads((report / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["views"]["region"]["objects_embedded"])
            index, chunks = load_all_chunks(report)
            root_records = chunks["rroot"]
            self.assertEqual({record["kind"] for record in root_records}, {"root", "region"})
            regions = [record for record in root_records if record["kind"] == "region"]
            self.assertTrue(all(record["children_chunk"] for record in regions))
            objects = [
                record
                for key, records in chunks.items()
                if key.startswith("robjchunk")
                for record in records
            ]
            self.assertEqual(len(objects), 8)
            self.assertTrue(all(record["kind"] == "object" for record in objects))
            region_ids = {record["id"] for record in regions}
            self.assertTrue(all(record["parent_id"] in region_ids for record in objects))
            self.assertEqual(manifest["views"]["region"]["total_bytes"], 472)


class LargeShardPlanTests(unittest.TestCase):
    def test_shard_planning(self) -> None:
        from mapcore.export.chunk_builder import plan_child_shards

        groups = [(f"parent{i}", [{"id": f"n{i}-{j}"} for j in range(100)]) for i in range(10)]
        assignments, shards = plan_child_shards(groups, "testchunk", max_records=1000, max_groups=3)
        self.assertEqual(len(assignments), 10)
        self.assertEqual(len(shards), 4)
        total = sum(len(records) for _key, records in shards)
        self.assertEqual(total, 1000)
        for parent, key in assignments.items():
            self.assertIn(key, [chunk_key for chunk_key, _ in shards])

    def test_page_splitting(self) -> None:
        from mapcore.export.chunk_builder import write_pages

        with tempfile.TemporaryDirectory(prefix="mapview-pages-") as temp:
            root = Path(temp)
            records = [{"id": f"r{i}", "size": i} for i in range(25)]
            pages = write_pages(
                root,
                "data/chunks/test.jsonl.gz",
                records,
                max_records=10,
                max_bytes=1024 * 1024,
            )
            self.assertEqual([page.records for page in pages], [10, 10, 5])
            self.assertEqual(pages[0].path, "data/chunks/test.jsonl.gz")
            self.assertEqual(pages[1].path, "data/chunks/test.p002.jsonl.gz")
            self.assertEqual(pages[2].path, "data/chunks/test.p003.jsonl.gz")
            loaded = []
            for page in pages:
                loaded.extend(record for _line, record in iter_jsonl_gz(root / page.path))
            self.assertEqual(loaded, records)


if __name__ == "__main__":
    unittest.main()
