"""Transform layer: parser output -> Region / Module views and summaries."""

from __future__ import annotations

from .categories import (
    CATEGORY_LABELS,
    categorize_component,
    categorize_components,
    classify_section,
    dominant_category,
)
from .summary import (
    MemoryOverrides,
    MemorySummary,
    MemoryUsage,
    build_memory_summary,
    component_totals,
    normalize_grand_totals,
)
from .tree_builder import (
    CategoryEntry,
    LeafEntry,
    ModuleEntry,
    ModuleView,
    ObjectEntry,
    RegionEntry,
    RegionView,
    TreeNode,
    build_module_tree,
    build_region_tree,
    build_views,
)

__all__ = [
    "CATEGORY_LABELS",
    "CategoryEntry",
    "LeafEntry",
    "MemoryOverrides",
    "MemorySummary",
    "MemoryUsage",
    "ModuleEntry",
    "ModuleView",
    "ObjectEntry",
    "RegionEntry",
    "RegionView",
    "TreeNode",
    "build_memory_summary",
    "build_module_tree",
    "build_region_tree",
    "build_views",
    "categorize_component",
    "categorize_components",
    "classify_section",
    "component_totals",
    "dominant_category",
    "normalize_grand_totals",
]
