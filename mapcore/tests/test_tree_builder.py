"""Transform tests: upstream tree ports, export views and totals."""

from __future__ import annotations

import unittest

from mapcore.parser import parse_map_file
from mapcore.tests.helpers import fixture
from mapcore.transform.tree_builder import (
    build_module_tree,
    build_region_tree,
    build_views,
)


class RegionTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("keil_ac5.map")).data
        cls.tree = build_region_tree(cls.data)

    def test_shape(self) -> None:
        self.assertEqual(self.tree.name, "root")
        self.assertEqual(len(self.tree.children), 2)
        self.assertEqual([node.name for node in self.tree.children], ["ER_IROM1", "RW_IRAM1"])
        irom = self.tree.children[0]
        self.assertEqual(
            [node.name for node in irom.children],
            ["startup_stm32f429xx.o", "[PAD]", "main.o", "system_stm32f4xx.o", "[libc.a] printf.o"],
        )

    def test_same_name_merge(self) -> None:
        main = next(node for node in self.tree.children[0].children if node.name == "main.o")
        self.assertEqual(len(main.children), 2)
        self.assertEqual(main.children[0].name, "main")
        self.assertEqual(main.children[0].size, 166)
        self.assertEqual(main.children[0].category, "code")
        # The first (larger) i.main address wins.
        self.assertEqual(main.children[0].address, 0x08008062)

    def test_pad_leaf(self) -> None:
        pad = next(node for node in self.tree.children[0].children if node.name == "[PAD]")
        self.assertEqual(pad.children[0].name, "PAD")
        self.assertEqual(pad.children[0].size, 2)
        self.assertEqual(pad.children[0].category, "pad")

    def test_leaf_sum(self) -> None:
        def total(node):
            if not node.children:
                return node.size or 0
            return sum(total(child) for child in node.children)

        self.assertEqual(total(self.tree), 472)


class ModuleTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("keil_ac5.map")).data
        cls.tree = build_module_tree(cls.data)

    def test_categories(self) -> None:
        self.assertEqual([node.name for node in self.tree.children], ["User Code", "Compiler Library"])

    def test_library_members_are_not_double_counted(self) -> None:
        user = self.tree.children[0]
        self.assertEqual([node.name for node in user.children], ["main.o", "startup_stm32f429xx.o", "system_stm32f4xx.o"])
        library = self.tree.children[1]
        self.assertEqual([node.name for node in library.children], ["printf.o"])

    def test_parts(self) -> None:
        main = self.tree.children[0].children[0]
        self.assertEqual(
            [(child.name, child.size, child.category) for child in main.children],
            [
                ("main", 166, "code"),
                ("main [RO]", 16, "rodata"),
                ("main [RW]", 16, "rwdata"),
                ("main [ZI]", 48, "zidata"),
            ],
        )

    def test_total_matches_grand_totals_without_padding(self) -> None:
        def total(node):
            if not node.children:
                return node.size or 0
            return sum(total(child) for child in node.children)

        self.assertEqual(total(self.tree), 470)


class ExportViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = parse_map_file(fixture("keil_ac5.map")).data
        cls.region_view, cls.module_view = build_views(cls.data)

    def test_region_view_totals(self) -> None:
        self.assertEqual(self.region_view.total, 472)
        self.assertEqual(self.region_view.padding_bytes, 2)
        self.assertEqual(self.region_view.leaf_count, 12)
        self.assertEqual(self.region_view.object_count, 8)
        self.assertEqual(sum(region.size for region in self.region_view.regions), 472)

    def test_module_view_totals(self) -> None:
        self.assertEqual(self.module_view.total, 470)
        self.assertEqual(self.module_view.module_count, 4)
        self.assertEqual(self.module_view.leaf_count, 11)
        self.assertEqual(
            sum(category.size for category in self.module_view.categories), 470
        )

    def test_views_share_element_ids(self) -> None:
        region_elements = {
            leaf.element_id
            for region in self.region_view.regions
            for obj in region.objects
            for leaf in obj.leaves
        }
        module_elements = {
            leaf.element_id
            for category in self.module_view.categories
            for module in category.modules
            for leaf in module.leaves
        }
        self.assertTrue(module_elements)
        self.assertTrue(module_elements <= region_elements)

    def test_padding_accounts_for_view_difference(self) -> None:
        self.assertEqual(self.region_view.total - self.module_view.total, self.region_view.padding_bytes)

    def test_library_module_key(self) -> None:
        library = next(
            category for category in self.module_view.categories if category.name == "Compiler Library"
        )
        module = library.modules[0]
        self.assertEqual(module.name, "printf.o")
        self.assertEqual(module.key, ("libc.a", "printf.o"))
        self.assertEqual(len(module.leaves), 3)

    def test_gnu_view_totals_and_fills(self) -> None:
        data = parse_map_file(fixture("gnu_ld_esp32.map")).data
        region_view, module_view = build_views(data)
        self.assertEqual(region_view.total, 272)
        self.assertEqual(region_view.padding_bytes, 8)
        self.assertEqual(module_view.total, 240)
        self.assertEqual(region_view.total - module_view.total, 32)

    def test_parts_are_complete(self) -> None:
        for category in self.module_view.categories:
            for module in category.modules:
                self.assertEqual(sum(module.parts.values()), module.size)


if __name__ == "__main__":
    unittest.main()
