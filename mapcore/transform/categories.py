"""Category rules shared by both views.

``classify_section`` and ``categorize_components`` are direct ports of upstream
``src/transformer/treeBuilder.ts``.  Category names are the stable wire values
``code`` / ``rodata`` / ``rwdata`` / ``zidata`` / ``pad``.
"""

from __future__ import annotations

import re

from ..parser.models import ComponentSize, MemorySection

CATEGORY_LABELS = {
    "code": "Code",
    "rodata": "RO-Data",
    "rwdata": "RW-Data",
    "zidata": "ZI-Data",
    "pad": "PAD",
}

# Stable order used for deterministic "dominant category" tie breaking.
CATEGORY_ORDER = ("code", "rodata", "rwdata", "zidata", "pad")


def classify_section(section: MemorySection) -> str:
    if section.section_type == "PAD":
        return "pad"
    if section.section_type == "Code":
        return "code"
    if section.section_type == "Zero":
        return "zidata"
    if section.attr == "RO":
        return "rodata"
    return "rwdata"


def categorize_component(comp: ComponentSize) -> str:
    if comp.library:
        if re.match(r"^lib(gcc|c|m|stdc\+\+|gloss|nosys)\.a$", comp.library):
            return "Compiler Library"
        if re.match(r"^lib(esp_|driver|hal|soc|freertos|xtensa|riscv)", comp.library):
            return "ESP-IDF System"
        if re.match(r"^lib(lwip|mbedtls|mqtt|nghttp|cjson|protobuf)", comp.library):
            return "Third Party"
        if re.match(r"^lib(lvgl|lv_|lfs)", comp.library):
            return "Third Party"
        if comp.library.startswith("lib") and ".a(" in comp.object_name:
            return "System Library"
        return "Compiler Library"
    if re.match(r"^(at32f|stm32|gd32)\d+", comp.object_name, re.I):
        return "Chip Library"
    if re.search(r"usb|hid|winusb|usbd_", comp.object_name, re.I):
        return "USB/Middleware"
    if re.match(r"^lv_|^lfs|lvgl", comp.object_name, re.I):
        return "Third Party"
    return "User Code"


def categorize_components(components: list[ComponentSize]) -> dict[str, list[ComponentSize]]:
    result: dict[str, list[ComponentSize]] = {}
    for comp in components:
        result.setdefault(categorize_component(comp), []).append(comp)
    return result


def dominant_category(sizes: dict[str, int]) -> str:
    """Return the category with the largest byte count (stable order)."""

    best = "code"
    best_size = -1
    for category in CATEGORY_ORDER:
        value = sizes.get(category, 0)
        if value > best_size:
            best = category
            best_size = value
    if best_size <= 0:
        return "code"
    return best
