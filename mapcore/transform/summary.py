"""Memory summary and grand-total normalisation.

The ROM/RAM heuristic is a port of upstream ``buildMemorySummary``.  In
addition, :func:`normalize_grand_totals` fills the Code / RO / RW / ZI buckets
from the component table when a linker did not print them in a machine readable
form (upstream leaves them at zero for regular Keil output).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..parser.models import GrandTotals, MapFileData, MemoryRegion


@dataclass(slots=True)
class MemoryUsage:
    used: int
    total: int
    percent: float

    def to_dict(self) -> dict[str, int | float]:
        return {"used": self.used, "total": self.total, "percent": self.percent}


@dataclass(slots=True)
class MemorySummary:
    rom: MemoryUsage
    ram: MemoryUsage

    def to_dict(self) -> dict[str, dict[str, int | float]]:
        return {"rom": self.rom.to_dict(), "ram": self.ram.to_dict()}


@dataclass(slots=True)
class MemoryOverrides:
    """User supplied capacities, in KiB (same unit as the upstream dialog)."""

    rom_kb: int | None = None
    ram_kb: int | None = None


def component_totals(data: MapFileData) -> GrandTotals:
    code = ro = rw = zi = 0
    for comp in data.all_components():
        code += comp.code
        ro += comp.ro_data
        rw += comp.rw_data
        zi += comp.zi_data
    return GrandTotals(
        code=code,
        ro_data=ro,
        rw_data=rw,
        zi_data=zi,
        total_ro=code + ro,
        total_rw=rw + zi,
        total_rom=code + ro + rw,
    )


def normalize_grand_totals(data: MapFileData) -> GrandTotals:
    """Return grand totals with missing per-category values derived.

    Parsed "Total RO/RW/ROM Size" values win when present, because they are the
    linker's own statement.  Individual Code/RO/RW/ZI buckets are derived from
    the (de-duplicated) component table when the linker did not provide them.
    """

    parsed = data.grand_totals
    derived = component_totals(data)

    code = parsed.code or derived.code
    ro_data = parsed.ro_data or derived.ro_data
    rw_data = parsed.rw_data or derived.rw_data
    zi_data = parsed.zi_data or derived.zi_data

    total_ro = parsed.total_ro or (code + ro_data)
    total_rw = parsed.total_rw or (rw_data + zi_data)
    total_rom = parsed.total_rom or (total_ro + rw_data)

    return GrandTotals(
        code=code,
        ro_data=ro_data,
        rw_data=rw_data,
        zi_data=zi_data,
        total_ro=total_ro,
        total_rw=total_rw,
        total_rom=total_rom,
    )


def _percent(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(used / total * 100.0, 4)


def build_memory_summary(
    data: MapFileData, overrides: MemoryOverrides | None = None
) -> MemorySummary:
    totals = normalize_grand_totals(data)
    rom_override = (overrides.rom_kb or 0) * 1024 if overrides else 0
    ram_override = (overrides.ram_kb or 0) * 1024 if overrides else 0

    if data.memory_regions:
        execution_regions = (
            data.load_regions[0].execution_regions if data.load_regions else []
        )
        regions = data.memory_regions

        def find_region(addr: int) -> MemoryRegion | None:
            for region in regions:
                if region.length > 0 and region.origin <= addr < region.origin + region.length:
                    return region
            return None

        text_section = next(
            (
                region
                for region in execution_regions
                if region.name == ".text"
                or "flash.text" in region.name
                or "iram0.text" in region.name
            ),
            None,
        )
        data_section = next(
            (
                region
                for region in execution_regions
                if region.name == ".data" or "dram0.data" in region.name
            ),
            None,
        )
        bss_section = next(
            (
                region
                for region in execution_regions
                if region.name == ".bss" or "dram0.bss" in region.name
            ),
            None,
        )

        rom_region = find_region(text_section.exec_base) if text_section else None
        ram_region = find_region(data_section.exec_base) if data_section else None
        if ram_region is None and bss_section is not None:
            ram_region = find_region(bss_section.exec_base)

        rom_total = rom_override if rom_override > 0 else (rom_region.length if rom_region else 0)
        ram_total = ram_override if ram_override > 0 else (ram_region.length if ram_region else 0)
        rom_used = totals.total_rom
        ram_used = totals.total_rw
        return MemorySummary(
            rom=MemoryUsage(rom_used, rom_total, _percent(rom_used, rom_total)),
            ram=MemoryUsage(ram_used, ram_total, _percent(ram_used, ram_total)),
        )

    execution_regions = data.load_regions[0].execution_regions if data.load_regions else []

    rom_region = next(
        (
            region
            for region in execution_regions
            if "IROM" in region.name
            or (0x08000000 <= region.exec_base < 0x20000000)
        ),
        None,
    )
    ram_region = next(
        (
            region
            for region in execution_regions
            if "IRAM" in region.name or region.exec_base >= 0x20000000
        ),
        None,
    )

    rom_total = rom_override if rom_override > 0 else (rom_region.max_size if rom_region else 0)
    ram_total = ram_override if ram_override > 0 else (ram_region.max_size if ram_region else 0)
    rom_used = totals.total_rom
    ram_used = totals.total_rw

    return MemorySummary(
        rom=MemoryUsage(rom_used, rom_total, _percent(rom_used, rom_total)),
        ram=MemoryUsage(ram_used, ram_total, _percent(ram_used, ram_total)),
    )
