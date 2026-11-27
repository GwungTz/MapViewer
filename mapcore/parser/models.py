"""Canonical data model, mirroring the upstream ``src/parser/types.ts``.

All addresses and sizes are integer bytes.  ``MapFileData`` is produced by the
parser layer only; the transform layer turns it into the two report views.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Section types / attributes as reported by the linkers.
SECTION_TYPES = ("Code", "Data", "Zero", "PAD")
SECTION_ATTRS = ("RO", "RW")
CATEGORIES = ("code", "rodata", "rwdata", "zidata", "pad")


@dataclass(slots=True)
class MemorySection:
    """One input section (or merged function) inside an execution region."""

    exec_addr: int
    load_addr: int | None
    size: int
    section_type: str
    attr: str
    section_name: str
    object_name: str
    function_name: str | None = None
    is_outlined_function: bool = False


@dataclass(slots=True)
class ExecutionRegion:
    name: str
    exec_base: int
    load_base: int
    size: int
    max_size: int
    sections: list[MemorySection] = field(default_factory=list)


@dataclass(slots=True)
class LoadRegion:
    name: str
    base_addr: int
    size: int
    max_size: int
    execution_regions: list[ExecutionRegion] = field(default_factory=list)


@dataclass(slots=True)
class ComponentSize:
    object_name: str
    code: int
    code_inc_data: int
    ro_data: int
    rw_data: int
    zi_data: int
    debug: int
    library: str | None = None
    member: str | None = None


@dataclass(slots=True)
class MemoryRegion:
    name: str
    origin: int
    length: int
    attrs: str


@dataclass(slots=True)
class GrandTotals:
    code: int = 0
    ro_data: int = 0
    rw_data: int = 0
    zi_data: int = 0
    total_ro: int = 0
    total_rw: int = 0
    total_rom: int = 0


@dataclass(slots=True)
class ParseWarning:
    line_no: int
    message: str


@dataclass(slots=True)
class MapFileData:
    compiler: str
    compiler_version: str
    load_regions: list[LoadRegion] = field(default_factory=list)
    component_sizes: list[ComponentSize] = field(default_factory=list)
    library_members: list[ComponentSize] = field(default_factory=list)
    library_sizes: list[ComponentSize] = field(default_factory=list)
    grand_totals: GrandTotals = field(default_factory=GrandTotals)
    memory_regions: list[MemoryRegion] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)
    format: str = "unknown"

    # -- convenience -----------------------------------------------------
    def all_components(self) -> list[ComponentSize]:
        """Object components plus library members, de-duplicated.

        Upstream concatenates ``componentSizes`` and ``libraryMembers``.  Keil
        maps list library members in both tables, which double counts them in
        the module view.  We keep upstream leaf semantics but remove exact
        duplicates so both views share the same total budget.
        """

        seen: set[tuple[str | None, str]] = set()
        merged: list[ComponentSize] = []
        for comp in [*self.component_sizes, *self.library_members]:
            key = (comp.library, comp.object_name)
            if key in seen:
                continue
            seen.add(key)
            merged.append(comp)
        return merged

    def section_count(self) -> int:
        return sum(
            len(region.sections)
            for load in self.load_regions
            for region in load.execution_regions
        )

    def execution_region_count(self) -> int:
        return sum(len(load.execution_regions) for load in self.load_regions)
