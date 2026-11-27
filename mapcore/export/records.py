"""Wire-format records for the versioned report schema (schema_version = 1).

Node records are deliberately flat.  Internal nodes only carry ``id``/``name``/
``size``/``category``/``children_chunk``; leaf records carry the detail fields.
The same physical section is written to the region view (kind ``section``) and
optionally to the module view (a second node ID that shares ``element_id``), so
both views can be joined by element identity without duplicating detail in a
single tree.
"""

from __future__ import annotations

from typing import Any

from ..transform.tree_builder import ModuleView, RegionView
from ..transform.categories import dominant_category


def node_record(
    *,
    node_id: str,
    parent_id: str | None,
    view: str,
    kind: str,
    name: str,
    size: int,
    category: str | None = None,
    address: int | None = None,
    object_file: str | None = None,
    child_count: int = 0,
    children_chunk: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": node_id,
        "parent_id": parent_id,
        "view": view,
        "kind": kind,
        "name": name,
        "size": int(size),
        "category": category,
        "address": address,
        "object_file": object_file,
        "child_count": int(child_count),
        "children_chunk": children_chunk,
    }
    record.update(extra)
    return record


def _root_category(sizes: dict[str, int]) -> str:
    return dominant_category(sizes)


def build_region_leaf_groups(
    view: RegionView,
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Return per-object leaf record groups and the region search records."""

    counter = 0
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    search_records: list[dict[str, Any]] = []

    for region in view.regions:
        for obj in region.objects:
            leaf_records: list[dict[str, Any]] = []
            for leaf in obj.leaves:
                counter += 1
                node_id = f"rsec{counter:08d}"
                leaf_records.append(
                    node_record(
                        node_id=node_id,
                        parent_id=obj.node_id,
                        view="region",
                        kind="section",
                        name=leaf.name,
                        size=leaf.size,
                        category=leaf.category,
                        address=leaf.address,
                        object_file=leaf.object_name,
                        child_count=0,
                        children_chunk=None,
                        element_id=leaf.element_id,
                    )
                )
                search_records.append(
                    {
                        "id": node_id,
                        "name": leaf.name,
                        "object_file": leaf.object_name,
                        "view": "region",
                        "kind": "section",
                        "category": leaf.category,
                        "address": leaf.address,
                        "size": leaf.size,
                        "path_ids": [
                            view.root_id,
                            region.node_id,
                            obj.node_id,
                            node_id,
                        ],
                    }
                )
            groups.append((obj.node_id, leaf_records))

    return groups, search_records


def build_region_object_groups(
    view: RegionView, leaf_chunk_assignments: dict[str, str]
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Object records grouped per execution region (for very large maps)."""

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for region in view.regions:
        records: list[dict[str, Any]] = []
        for obj in region.objects:
            records.append(
                node_record(
                    node_id=obj.node_id,
                    parent_id=region.node_id,
                    view="region",
                    kind="object",
                    name=obj.name,
                    size=obj.size,
                    category=obj.category,
                    address=obj.address,
                    object_file=obj.object_name,
                    child_count=len(obj.leaves),
                    children_chunk=leaf_chunk_assignments.get(obj.node_id),
                )
            )
        groups.append((region.node_id, records))
    return groups


def build_region_root_records(
    view: RegionView,
    chunk_assignments: dict[str, str],
    region_chunk_assignments: dict[str, str] | None = None,
    *,
    include_objects: bool = True,
) -> list[dict[str, Any]]:
    category_sizes: dict[str, int] = {}
    for region in view.regions:
        category_sizes[region.category] = (
            category_sizes.get(region.category, 0) + region.size
        )

    records: list[dict[str, Any]] = [
        node_record(
            node_id=view.root_id,
            parent_id=None,
            view="region",
            kind="root",
            name="All",
            size=view.total,
            category=_root_category(category_sizes),
            address=None,
            object_file=None,
            child_count=len(view.regions),
            children_chunk=None,
        )
    ]

    for region in view.regions:
        records.append(
            node_record(
                node_id=region.node_id,
                parent_id=view.root_id,
                view="region",
                kind="region",
                name=region.name,
                size=region.size,
                category=region.category,
                address=region.exec_base,
                object_file=None,
                child_count=len(region.objects),
                children_chunk=(
                    (region_chunk_assignments or {}).get(region.node_id)
                ),
            )
        )

    if include_objects:
        for region in view.regions:
            for obj in region.objects:
                records.append(
                    node_record(
                        node_id=obj.node_id,
                        parent_id=region.node_id,
                        view="region",
                        kind="object",
                        name=obj.name,
                        size=obj.size,
                        category=obj.category,
                        address=obj.address,
                        object_file=obj.object_name,
                        child_count=len(obj.leaves),
                        children_chunk=chunk_assignments.get(obj.node_id),
                    )
                )

    return records


def build_module_leaf_groups(
    view: ModuleView,
) -> list[tuple[str, list[dict[str, Any]]]]:
    counter = 0
    groups: list[tuple[str, list[dict[str, Any]]]] = []

    for category in view.categories:
        for module in category.modules:
            leaf_records: list[dict[str, Any]] = []
            for leaf in module.leaves:
                counter += 1
                leaf_records.append(
                    node_record(
                        node_id=f"msec{counter:08d}",
                        parent_id=module.node_id,
                        view="module",
                        kind="section",
                        name=leaf.name,
                        size=leaf.size,
                        category=leaf.category,
                        address=leaf.address,
                        object_file=leaf.object_name,
                        child_count=0,
                        children_chunk=None,
                        element_id=leaf.element_id,
                    )
                )
            groups.append((module.node_id, leaf_records))

    return groups


def build_module_root_records(
    view: ModuleView, chunk_assignments: dict[str, str]
) -> list[dict[str, Any]]:
    category_sizes: dict[str, int] = {}
    for category in view.categories:
        category_sizes[category.category] = (
            category_sizes.get(category.category, 0) + category.size
        )

    records: list[dict[str, Any]] = [
        node_record(
            node_id=view.root_id,
            parent_id=None,
            view="module",
            kind="root",
            name="All",
            size=view.total,
            category=_root_category(category_sizes),
            address=None,
            object_file=None,
            child_count=len(view.categories),
            children_chunk=None,
        )
    ]

    for category in view.categories:
        records.append(
            node_record(
                node_id=category.node_id,
                parent_id=view.root_id,
                view="module",
                kind="category",
                name=category.name,
                size=category.size,
                category=category.category,
                address=None,
                object_file=None,
                child_count=len(category.modules),
                children_chunk=None,
            )
        )

    for category in view.categories:
        for module in category.modules:
            records.append(
                node_record(
                    node_id=module.node_id,
                    parent_id=category.node_id,
                    view="module",
                    kind="module",
                    name=module.name,
                    size=module.size,
                    category=module.category,
                    address=None,
                    object_file=module.name,
                    child_count=len(module.parts),
                    children_chunk=chunk_assignments.get(module.node_id),
                    parts=dict(module.parts),
                )
            )

    return records


def build_module_search_records(view: ModuleView) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for category in view.categories:
        for module in category.modules:
            records.append(
                {
                    "id": module.node_id,
                    "name": module.name,
                    "object_file": module.name,
                    "view": "module",
                    "kind": "module",
                    "category": module.category,
                    "address": None,
                    "size": module.size,
                    "path_ids": [view.root_id, category.node_id, module.node_id],
                }
            )
    return records
