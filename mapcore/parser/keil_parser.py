"""Keil MDK (ARMCC / AC5 / AC6, including EIDE ArmLink) MAP parser.

This is a line-for-line behavioural port of upstream ``src/parser/index.ts``.
Deliberate deviations are listed in ``docs/UPSTREAM_PARITY.md``:

* the numeric line following ``Grand Totals`` is parsed as well (upstream only
  parsed numbers that appear on the same line as the header, which leaves the
  Code / RO / RW / ZI legend at zero for regular Keil output);
* malformed memory-map data lines produce warnings instead of being silently
  dropped.
"""

from __future__ import annotations

import re

from .models import (
    ComponentSize,
    ExecutionRegion,
    GrandTotals,
    LoadRegion,
    MapFileData,
    MemorySection,
    ParseWarning,
)

LOAD_REGION_RE = re.compile(
    r"Load Region (\S+) \(Base: (0x[\da-f]+), Size: (0x[\da-f]+), Max: (0x[\da-f]+)", re.I
)
EXEC_REGION_RE = re.compile(
    r"Execution Region (\S+) \(Exec base: (0x[\da-f]+), Load base: (0x[\da-f]+), "
    r"Size: (0x[\da-f]+), Max: (0x[\da-f]+)",
    re.I,
)
EIDE_EXEC_REGION_RE = re.compile(
    r"Execution Region (\S+) \(Base: (0x[\da-f]+), Size: (0x[\da-f]+), Max: (0x[\da-f]+)", re.I
)
MEMORY_LINE_RE = re.compile(
    r"^\s*(0x[\da-f]+)\s+(0x[\da-f]+|-|COMPRESSED)\s+(0x[\da-f]+)\s+"
    r"(Code|Data|Zero|PAD)\s+(RO|RW)\s+(\d+)\s+(\*?)\s*(.+?)\s{2,}(\S+)\s*$",
    re.I,
)
EIDE_MEMORY_LINE_RE = re.compile(
    r"^\s*(0x[\da-f]+)\s+(0x[\da-f]+)\s+(Code|Data|Zero|PAD)\s+(RO|RW)\s+"
    r"(\d+)\s+(\*?)\s*(.+?)\s{2,}(\S+)\s*$",
    re.I,
)
PAD_LINE_RE = re.compile(r"^\s*(0x[\da-f]+)\s+(0x[\da-f]+|-|COMPRESSED)\s+(0x[\da-f]+)\s+PAD\s*$", re.I)
EIDE_PAD_LINE_RE = re.compile(r"^\s*(0x[\da-f]+)\s+(0x[\da-f]+)\s+PAD\s*$", re.I)
COMPONENT_LINE_RE = re.compile(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s*$")
TOTALS_LINE_RE = re.compile(r"Total RO\s+Size.*?(\d+)\s*\(")
TOTALS_RW_RE = re.compile(r"Total RW\s+Size.*?(\d+)\s*\(")
TOTALS_ROM_RE = re.compile(r"Total ROM\s+Size.*?(\d+)\s*\(")
GRAND_TOTALS_SAME_LINE_RE = re.compile(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")
GRAND_TOTALS_NUMERIC_LINE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
LOOKS_LIKE_DATA_RE = re.compile(r"^\s*0x[\da-f]+", re.I)

# Parser states (mirrors upstream `ParserState`).
_INITIAL = 0
_MEMORY_MAP = 4
_COMPONENT_SIZES = 5

# Component sub states.
_OBJECT_NAMES = 0
_LIBRARY_MEMBERS = 1
_LIBRARY_NAMES = 2
_GRAND_TOTALS = 3


def detect_compiler_version(first_line: str) -> str:
    if "Arm Compiler for Embedded" in first_line or re.search(r"Compiler.*6\.\d", first_line):
        return "AC6"
    return "AC5"


def extract_function_name(section_name: str, version: str) -> str | None:
    if version == "AC5":
        match = re.match(r"^i\.(.+)$", section_name)
        return match.group(1) if match else None
    match = re.match(r"^\.text\.(.+)$", section_name)
    if match and not match.group(1).startswith("OUTLINED_FUNCTION_"):
        return match.group(1)
    return None


def _is_pad_load_addr(value: str) -> bool:
    return value in ("-", "COMPRESSED")


def parse_keil_map(content: str) -> MapFileData:
    lines = content.splitlines()
    first_line = lines[0] if lines else ""
    compiler_version = detect_compiler_version(first_line)

    result = MapFileData(
        compiler=first_line,
        compiler_version=compiler_version,
        load_regions=[],
        component_sizes=[],
        library_members=[],
        library_sizes=[],
        grand_totals=GrandTotals(),
    )
    warnings: list[ParseWarning] = result.warnings

    state = _INITIAL
    component_sub_state = _OBJECT_NAMES
    current_load_region: LoadRegion | None = None
    current_exec_region: ExecutionRegion | None = None
    warned_regions: set[str] = set()

    def append_section(section: MemorySection, line_no: int) -> None:
        if current_exec_region is None:
            warnings.append(
                ParseWarning(
                    line_no,
                    f"Section '{section.section_name}' found outside an execution region and was ignored",
                )
            )
            return
        current_exec_region.sections.append(section)
        limit = current_exec_region.exec_base + current_exec_region.max_size
        end = section.exec_addr + section.size
        if (
            current_exec_region.max_size > 0
            and end > limit
            and current_exec_region.name not in warned_regions
        ):
            warned_regions.add(current_exec_region.name)
            warnings.append(
                ParseWarning(
                    line_no,
                    f"Section '{section.section_name}' extends beyond execution region "
                    f"'{current_exec_region.name}' (ends at 0x{end:08X}, limit 0x{limit:08X})",
                )
            )

    for line_no, line in enumerate(lines, start=1):
        if "Section Cross References" in line:
            state = _INITIAL
            continue
        if "Removing Unused input sections" in line:
            state = _INITIAL
            continue
        if "Image Symbol Table" in line:
            state = _INITIAL
            continue
        if "Memory Map of the image" in line:
            state = _MEMORY_MAP
            continue
        if "Image component sizes" in line:
            state = _COMPONENT_SIZES
            component_sub_state = _OBJECT_NAMES
            continue

        if state == _MEMORY_MAP:
            load_match = LOAD_REGION_RE.search(line)
            if load_match:
                current_load_region = LoadRegion(
                    name=load_match.group(1),
                    base_addr=int(load_match.group(2), 16),
                    size=int(load_match.group(3), 16),
                    max_size=int(load_match.group(4), 16),
                    execution_regions=[],
                )
                result.load_regions.append(current_load_region)
                current_exec_region = None
                continue

            exec_match = EXEC_REGION_RE.search(line)
            eide_exec_match = None if exec_match else EIDE_EXEC_REGION_RE.search(line)
            if exec_match or eide_exec_match:
                match = exec_match or eide_exec_match
                assert match is not None
                name = match.group(1)
                exec_base = int(match.group(2), 16)
                if exec_match:
                    load_base = int(match.group(3), 16)
                    size = int(match.group(4), 16)
                    max_size = int(match.group(5), 16)
                else:
                    load_base = exec_base
                    size = int(match.group(3), 16)
                    max_size = int(match.group(4), 16)
                current_exec_region = ExecutionRegion(
                    name=name,
                    exec_base=exec_base,
                    load_base=load_base,
                    size=size,
                    max_size=max_size,
                    sections=[],
                )
                if current_load_region is not None:
                    current_load_region.execution_regions.append(current_exec_region)
                else:
                    warnings.append(
                        ParseWarning(line_no, f"Execution region '{name}' appears before any load region")
                    )
                continue

            data_match = MEMORY_LINE_RE.search(line)
            eide_data_match = None if data_match else EIDE_MEMORY_LINE_RE.search(line)
            if data_match or eide_data_match:
                match = data_match or eide_data_match
                assert match is not None
                if data_match:
                    section_name = match.group(8).strip()
                    load_addr_raw = match.group(2)
                    section = MemorySection(
                        exec_addr=int(match.group(1), 16),
                        load_addr=None if _is_pad_load_addr(load_addr_raw) else int(load_addr_raw, 16),
                        size=int(match.group(3), 16),
                        section_type=match.group(4),
                        attr=match.group(5),
                        section_name=section_name,
                        object_name=match.group(9),
                        function_name=extract_function_name(section_name, compiler_version),
                        is_outlined_function=bool(re.match(r"^\.text\.OUTLINED_FUNCTION_\d+$", section_name)),
                    )
                else:
                    section_name = match.group(7).strip()
                    section = MemorySection(
                        exec_addr=int(match.group(1), 16),
                        load_addr=None,
                        size=int(match.group(2), 16),
                        section_type=match.group(3),
                        attr=match.group(4),
                        section_name=section_name,
                        object_name=match.group(8),
                        function_name=extract_function_name(section_name, compiler_version),
                        is_outlined_function=bool(re.match(r"^\.text\.OUTLINED_FUNCTION_\d+$", section_name)),
                    )
                append_section(section, line_no)
                continue

            pad_match = PAD_LINE_RE.search(line)
            eide_pad_match = None if pad_match else EIDE_PAD_LINE_RE.search(line)
            if pad_match or eide_pad_match:
                match = pad_match or eide_pad_match
                assert match is not None
                if pad_match:
                    load_addr_raw = match.group(2)
                    pad = MemorySection(
                        exec_addr=int(match.group(1), 16),
                        load_addr=None if _is_pad_load_addr(load_addr_raw) else int(load_addr_raw, 16),
                        size=int(match.group(3), 16),
                        section_type="PAD",
                        attr="RO",
                        section_name="PAD",
                        object_name="[PAD]",
                    )
                else:
                    pad = MemorySection(
                        exec_addr=int(match.group(1), 16),
                        load_addr=None,
                        size=int(match.group(2), 16),
                        section_type="PAD",
                        attr="RO",
                        section_name="PAD",
                        object_name="[PAD]",
                    )
                if current_exec_region is not None:
                    append_section(pad, line_no)
                continue

            if LOOKS_LIKE_DATA_RE.match(line):
                warnings.append(
                    ParseWarning(line_no, "Unrecognised memory map data line; it was skipped")
                )
            continue

        if state == _COMPONENT_SIZES:
            if "Library Member Name" in line:
                component_sub_state = _LIBRARY_MEMBERS
                continue
            if "Library Name" in line and "Library Member" not in line:
                component_sub_state = _LIBRARY_NAMES
                continue

            ro_match = TOTALS_LINE_RE.search(line)
            if ro_match:
                result.grand_totals.total_ro = int(ro_match.group(1))
                continue
            rw_match = TOTALS_RW_RE.search(line)
            if rw_match:
                result.grand_totals.total_rw = int(rw_match.group(1))
                continue
            rom_match = TOTALS_ROM_RE.search(line)
            if rom_match:
                result.grand_totals.total_rom = int(rom_match.group(1))
                continue

            if "Grand Totals" in line:
                same_line = GRAND_TOTALS_SAME_LINE_RE.search(line)
                if same_line:
                    result.grand_totals.code = int(same_line.group(1))
                    result.grand_totals.ro_data = int(same_line.group(3))
                    result.grand_totals.rw_data = int(same_line.group(4))
                    result.grand_totals.zi_data = int(same_line.group(5))
                component_sub_state = _GRAND_TOTALS
                continue

            if (
                line.strip() == ""
                or "---" in line
                or "Code (inc. data)" in line
                or "Object Totals" in line
                or "(incl." in line
                or "Library Totals" in line
                or "ELF Image Totals" in line
                or "ROM Totals" in line
            ):
                continue

            if component_sub_state == _GRAND_TOTALS:
                numeric = GRAND_TOTALS_NUMERIC_LINE_RE.match(line)
                if numeric:
                    result.grand_totals.code = int(numeric.group(1))
                    result.grand_totals.ro_data = int(numeric.group(3))
                    result.grand_totals.rw_data = int(numeric.group(4))
                    result.grand_totals.zi_data = int(numeric.group(5))
                    continue

            comp_match = COMPONENT_LINE_RE.match(line)
            if comp_match:
                raw_name = comp_match.group(7).strip()
                lib_match = re.match(r"^(.+?)\((.+?)\)$", raw_name)
                component = ComponentSize(
                    object_name=lib_match.group(2) if lib_match else raw_name,
                    code=int(comp_match.group(1)),
                    code_inc_data=int(comp_match.group(2)),
                    ro_data=int(comp_match.group(3)),
                    rw_data=int(comp_match.group(4)),
                    zi_data=int(comp_match.group(5)),
                    debug=int(comp_match.group(6)),
                    library=lib_match.group(1) if lib_match else None,
                    member=lib_match.group(2) if lib_match else None,
                )
                if component_sub_state == _OBJECT_NAMES:
                    result.component_sizes.append(component)
                elif component_sub_state == _LIBRARY_MEMBERS:
                    result.library_members.append(component)
                elif component_sub_state == _LIBRARY_NAMES:
                    result.library_sizes.append(component)
            continue

    # Upstream fill-in rules (only applied when the linker did not print the
    # dedicated "Total ... Size" lines).
    totals = result.grand_totals
    if totals.total_ro == 0 and totals.code > 0:
        totals.total_ro = totals.code + totals.ro_data
    if totals.total_rw == 0 and (totals.rw_data > 0 or totals.zi_data > 0):
        totals.total_rw = totals.rw_data + totals.zi_data
    if totals.total_rom == 0 and totals.total_ro > 0:
        totals.total_rom = totals.total_ro + totals.rw_data

    return result
