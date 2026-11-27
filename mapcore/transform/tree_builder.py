"""Region / Module tree construction.

Two layers live here:

* ``build_region_tree`` / ``build_module_tree`` - faithful ports of upstream
  ``src/transformer/treeBuilder.ts`` returning nested ``TreeNode`` objects.
  They are used by parity tests and by tooling that wants the whole tree.
* ``build_views`` - memory-efficient view descriptions used by the report
  exporter.  The exporter never materialises a second full tree: leaves carry
  stable IDs and chunk assignment happens while records are emitted.

Region view hierarchy: root -> execution region -> object file -> section.
Module view hierarchy: root -> category -> module -> code/RO/RW/ZI parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..parser.models import ComponentSize, MapFileData, MemorySection
from .categories import (
    categorize_components,
    classify_section,
    dominant_category,
)

_LIB_MEMBER_RE = re.compile(r"^(.+?)\((.+?)\)$")


# ---------------------------------------------------------------------------
# Upstream-compatible tree nodes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TreeNode:
    name: str
    children: list["TreeNode"] = field(default_factory=list)
    size: int | None = None
    section_type: str | None = None
    attr: str | None = None
    address: int | None = None
    object_file: str | None = None
    category: str | None = None


def _group_by(items: list[MemorySection], key) -> dict[str, list[MemorySection]]:
    grouped: dict[str, list[MemorySection]] = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


def _merge_same_name_entries(entries: list[TreeNode]) -> list[TreeNode]:
    merged: dict[str, TreeNode] = {}
    for entry in entries:
        existing = merged.get(entry.name)
        if existing is not None and existing.size is not None and entry.size is not None:
            previous_size = existing.size
            existing.size += entry.size
            # Keep the .text address over a .literal address, like upstream.
            if entry.address is not None and entry.size > previous_size:
                existing.address = entry.address
        else:
            merged[entry.name] = TreeNode(
                name=entry.name,
                size=entry.size,
                section_type=entry.section_type,
                attr=entry.attr,
                address=entry.address,
                object_file=entry.object_file,
                category=entry.category,
            )
    return list(merged.values())


def build_region_tree(data: MapFileData) -> TreeNode:
    root = TreeNode(name="root", children=[])

    for load_region in data.load_regions:
        for exec_region in load_region.execution_regions:
            region_node = TreeNode(name=exec_region.name, children=[])
            groups = _group_by(exec_region.sections, lambda section: section.object_name)

            for object_name, sections in groups.items():
                lib_match = _LIB_MEMBER_RE.match(object_name)
                display_name = (
                    f"[{lib_match.group(1)}] {lib_match.group(2)}"
                    if lib_match
                    else object_name
                )
                leaves = [
                    TreeNode(
                        name=section.function_name or section.section_name,
                        size=section.size,
                        section_type=section.section_type,
                        attr=section.attr,
                        address=section.exec_addr,
                        object_file=object_name,
                        category=classify_section(section),
                    )
                    for section in sections
                    if section.size > 0
                ]
                object_node = TreeNode(
                    name=display_name,
                    children=_merge_same_name_entries(leaves),
                )
                if object_node.children:
                    region_node.children.append(object_node)

            if region_node.children:
                root.children.append(region_node)

    return root


def build_module_tree(data: MapFileData, *, dedupe_library_members: bool = True) -> TreeNode:
    root = TreeNode(name="root", children=[])
    components = data.all_components() if dedupe_library_members else [
        *data.component_sizes,
        *data.library_members,
    ]

    for category, comps in categorize_components(components).items():
        category_node = TreeNode(name=category, children=[])
        for comp in comps:
            total_size = comp.code + comp.ro_data + comp.rw_data + comp.zi_data
            if total_size == 0:
                continue
            display_name = re.sub(r"\.o$", "", comp.object_name)
            parts: list[TreeNode] = []
            if comp.code > 0:
                parts.append(
                    TreeNode(
                        name=display_name,
                        size=comp.code,
                        category="code",
                        object_file=comp.object_name,
                    )
                )
            if comp.ro_data > 0:
                parts.append(
                    TreeNode(
                        name=display_name + " [RO]",
                        size=comp.ro_data,
                        category="rodata",
                        object_file=comp.object_name,
                    )
                )
            if comp.rw_data > 0:
                parts.append(
                    TreeNode(
                        name=display_name + " [RW]",
                        size=comp.rw_data,
                        category="rwdata",
                        object_file=comp.object_name,
                    )
                )
            if comp.zi_data > 0:
                parts.append(
                    TreeNode(
                        name=display_name + " [ZI]",
                        size=comp.zi_data,
                        category="zidata",
                        object_file=comp.object_name,
                    )
                )
            module_node = TreeNode(name=comp.object_name, children=parts)
            category_node.children.append(module_node)

        if category_node.children:
            root.children.append(category_node)

    return root


# ---------------------------------------------------------------------------
# Export-oriented views
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LeafEntry:
    element_id: str
    name: str
    size: int
    category: str
    address: int
    object_name: str
    section_name: str
    function_name: str | None
    section_type: str
    attr: str


@dataclass(slots=True)
class ObjectEntry:
    node_id: str
    name: str
    object_name: str
    size: int
    category: str
    address: int | None
    key: tuple[str | None, str]
    leaves: list[LeafEntry]


@dataclass(slots=True)
class RegionEntry:
    node_id: str
    name: str
    exec_base: int
    size: int
    max_size: int
    category: str
    objects: list[ObjectEntry]


@dataclass(slots=True)
class RegionView:
    root_id: str
    total: int
    regions: list[RegionEntry]
    leaf_count: int
    object_count: int
    section_count: int
    padding_bytes: int


@dataclass(slots=True)
class ModuleEntry:
    node_id: str
    name: str
    size: int
    category: str
    parts: dict[str, int]
    key: tuple[str | None, str]
    group_category: str
    leaves: list[LeafEntry]


@dataclass(slots=True)
class CategoryEntry:
    node_id: str
    name: str
    size: int
    category: str
    modules: list[ModuleEntry]


@dataclass(slots=True)
class ModuleView:
    root_id: str
    total: int
    categories: list[CategoryEntry]
    module_count: int
    leaf_count: int


def parse_member_name(name: str) -> tuple[str | None, str]:
    match = _LIB_MEMBER_RE.match(name)
    if match:
        return match.group(1), match.group(2)
    return None, name


def component_key(comp: ComponentSize) -> tuple[str | None, str]:
    if comp.library:
        library_match = _LIB_MEMBER_RE.match(comp.object_name)
        if library_match and library_match.group(1) == comp.library:
            return library_match.group(1), library_match.group(2)
        return comp.library, comp.object_name
    return parse_member_name(comp.object_name)


def _merged_leaves(sections: list[MemorySection]) -> list[tuple[str, int, str, int, MemorySection]]:
    """Merge same-named sections inside one object.

    Returns ``(display_name, size, category, address, representative_section)``
    tuples in first-seen order, matching upstream ``mergeSameNameEntries``.
    """

    merged: dict[str, tuple[str, int, str, int, MemorySection]] = {}
    for section in sections:
        if section.size <= 0:
            continue
        name = section.function_name or section.section_name
        category = classify_section(section)
        existing = merged.get(name)
        if existing is not None:
            previous_size = existing[1]
            new_size = previous_size + section.size
            address = existing[3]
            if section.size > previous_size:
                address = section.exec_addr
            merged[name] = (name, new_size, existing[2], address, existing[4])
        else:
            merged[name] = (name, section.size, category, section.exec_addr, section)
    return list(merged.values())


def build_region_view(data: MapFileData) -> RegionView:
    region_counter = 0
    object_counter = 0
    element_counter = 0
    regions: list[RegionEntry] = []

    for load_region in data.load_regions:
        for exec_region in load_region.execution_regions:
            grouped: dict[str, list[MemorySection]] = {}
            for section in exec_region.sections:
                grouped.setdefault(section.object_name, []).append(section)

            objects: list[ObjectEntry] = []
            for object_name, sections in grouped.items():
                merged = _merged_leaves(sections)
                if not merged:
                    continue

                object_counter += 1
                leaves: list[LeafEntry] = []
                category_sizes: dict[str, int] = {}
                minimum_address: int | None = None
                for display_name, size, category, address, representative in merged:
                    element_counter += 1
                    leaves.append(
                        LeafEntry(
                            element_id=f"e{element_counter:08d}",
                            name=display_name,
                            size=size,
                            category=category,
                            address=address,
                            object_name=object_name,
                            section_name=representative.section_name,
                            function_name=representative.function_name,
                            section_type=representative.section_type,
                            attr=representative.attr,
                        )
                    )
                    category_sizes[category] = category_sizes.get(category, 0) + size
                    if minimum_address is None or address < minimum_address:
                        minimum_address = address

                lib_match = _LIB_MEMBER_RE.match(object_name)
                display_object_name = (
                    f"[{lib_match.group(1)}] {lib_match.group(2)}"
                    if lib_match
                    else object_name
                )
                objects.append(
                    ObjectEntry(
                        node_id=f"robj{object_counter:07d}",
                        name=display_object_name,
                        object_name=object_name,
                        size=sum(leaf.size for leaf in leaves),
                        category=dominant_category(category_sizes),
                        address=minimum_address,
                        key=parse_member_name(object_name),
                        leaves=leaves,
                    )
                )

            if not objects:
                continue

            region_counter += 1
            region_sizes: dict[str, int] = {}
            for obj in objects:
                for leaf in obj.leaves:
                    region_sizes[leaf.category] = region_sizes.get(leaf.category, 0) + leaf.size

            regions.append(
                RegionEntry(
                    node_id=f"rgn{region_counter:05d}",
                    name=exec_region.name,
                    exec_base=exec_region.exec_base,
                    size=sum(obj.size for obj in objects),
                    max_size=exec_region.max_size,
                    category=dominant_category(region_sizes),
                    objects=objects,
                )
            )

    return RegionView(
        root_id="rroot",
        total=sum(region.size for region in regions),
        regions=regions,
        leaf_count=element_counter,
        object_count=object_counter,
        section_count=data.section_count(),
        padding_bytes=sum(
            leaf.size
            for region in regions
            for obj in region.objects
            for leaf in obj.leaves
            if leaf.category == "pad"
        ),
    )


def build_module_view(
    data: MapFileData,
    leaf_index: dict[tuple[str | None, str], list[LeafEntry]] | None = None,
) -> ModuleView:
    leaf_index = leaf_index or {}
    components = data.all_components()
    counter = 0
    category_counter = 0
    leaf_total = 0
    categories: list[CategoryEntry] = []

    for category_name, comps in categorize_components(components).items():
        category_counter += 1
        modules: list[ModuleEntry] = []
        for comp in comps:
            parts = {
                "code": comp.code,
                "rodata": comp.ro_data,
                "rwdata": comp.rw_data,
                "zidata": comp.zi_data,
            }
            parts = {key: value for key, value in parts.items() if value > 0}
            total_size = sum(parts.values())
            if total_size == 0:
                continue
            counter += 1
            key = component_key(comp)
            leaves = leaf_index.get(key, [])
            leaf_total += len(leaves)
            modules.append(
                ModuleEntry(
                    node_id=f"mmod{counter:07d}",
                    name=comp.object_name,
                    size=total_size,
                    category=dominant_category(parts),
                    parts=parts,
                    key=key,
                    group_category=category_name,
                    leaves=leaves,
                )
            )

        if not modules:
            continue

        category_sizes: dict[str, int] = {}
        for module in modules:
            category_sizes[module.category] = (
                category_sizes.get(module.category, 0) + module.size
            )
        categories.append(
            CategoryEntry(
                node_id=f"mcat{category_counter:03d}",
                name=category_name,
                size=sum(module.size for module in modules),
                category=dominant_category(category_sizes),
                modules=modules,
            )
        )

    return ModuleView(
        root_id="mroot",
        total=sum(category.size for category in categories),
        categories=categories,
        module_count=counter,
        leaf_count=leaf_total,
    )


def build_views(data: MapFileData) -> tuple[RegionView, ModuleView]:
    region_view = build_region_view(data)
    leaf_index: dict[tuple[str | None, str], list[LeafEntry]] = {}
    for region in region_view.regions:
        for obj in region.objects:
            leaf_index.setdefault(obj.key, []).extend(obj.leaves)
    module_view = build_module_view(data, leaf_index)
    return region_view, module_view
