"""GNU ld / GCC / ESP-IDF / ArtInChip MAP parser.

Behavioural port of upstream ``src/parser/esp32Parser.ts``.  The parser keeps
the same phase machine (memory configuration -> linker script and memory map),
the same output-section / entry / *fill* recognition and the same object-name
normalisation.  Component sizes are derived from the sections, exactly like the
upstream implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    ComponentSize,
    ExecutionRegion,
    GrandTotals,
    LoadRegion,
    MapFileData,
    MemoryRegion,
    MemorySection,
    ParseWarning,
)

MEMORY_REGION_RE = re.compile(r"^(\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s*(\S*)\s*$", re.I)
OUTPUT_SECTION_RE = re.compile(
    r"^(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)(?:\s+load address\s+0x[\da-f]+)?\s*$"
)
ENTRY_RE = re.compile(r"^\s+(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(.+)$")
ENTRY_CONTINUED_RE = re.compile(r"^\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s+(.+)$")
SYMBOL_RE = re.compile(r"^\s+(0x[\da-f]+)\s+(\S+)$")
FILL_RE = re.compile(r"^\s+\*fill\*\s+(0x[\da-f]+)\s+(0x[\da-f]+)")
SUBSECTION_ONLY_RE = re.compile(r"^\s+(\.\S+)\s*$")
SECTION_START_RE = re.compile(r"^\.\S+")
INLINE_SECTION_RE = re.compile(r"^(\.\S+)\s+(0x[\da-f]+)\s+(0x[\da-f]+)")
SECTION_NAME_AND_ADDR_RE = re.compile(r"^\s+(0x[\da-f]+)\s+(0x[\da-f]+)\s*$")
UNMATCHED_ENTRY_RE = re.compile(
    r"^\s+(?:0x[\da-fA-F]+|\.\S+)\s+0x[\da-fA-F]+\s+0x[\da-fA-F]+\s+\S+\.(?:o|obj)\b"
)


@dataclass(slots=True)
class SectionEntry:
    section_name: str
    address: int
    size: int
    object_file: str
    function_name: str | None = None


@dataclass(slots=True)
class OutputSection:
    name: str
    address: int
    total_size: int
    entries: list[SectionEntry] = field(default_factory=list)


def is_linker_symbol(name: str) -> bool:
    return bool(
        re.match(r"^__(.+_(start|end|size|base|limit)|[A-Z_]+_)$", name)
        or re.match(r"^_(end|start|etext|edata|sbss|ebss|sdata)$", name)
    )


def parse_gnu_ld_map(content: str) -> MapFileData:
    lines = content.splitlines()
    memory_regions: list[MemoryRegion] = []
    output_sections: list[OutputSection] = []
    warnings: list[ParseWarning] = []

    phase = "scan"
    current_section: OutputSection | None = None
    last_entry: SectionEntry | None = None
    pending_sub_section_name: str | None = None

    index = 0
    line_count = len(lines)
    while index < line_count:
        line = lines[index]

        if line.startswith("Memory Configuration"):
            phase = "memconfig"
            index += 3  # blank line + "Name Origin Length Attributes" header
            continue
        if line.startswith("Linker script and memory map"):
            phase = "linkermap"
            index += 1
            continue

        if phase == "memconfig":
            if line.strip() == "":
                phase = "scan"
                index += 1
                continue
            match = MEMORY_REGION_RE.match(line)
            if match and match.group(1) != "*default*":
                memory_regions.append(
                    MemoryRegion(
                        name=match.group(1),
                        origin=int(match.group(2), 16),
                        length=int(match.group(3), 16),
                        attrs=match.group(4),
                    )
                )
            index += 1
            continue

        if phase == "linkermap":
            # Skip LOAD lines and linker script assignments.
            if line.startswith("LOAD ") or " = " in line:
                index += 1
                continue

            # Output section header (dot at column 0, address + size).
            section_match = OUTPUT_SECTION_RE.match(line)
            if section_match:
                if current_section is not None and current_section.entries:
                    output_sections.append(current_section)
                current_section = OutputSection(
                    name=section_match.group(1),
                    address=int(section_match.group(2), 16),
                    total_size=int(section_match.group(3), 16),
                    entries=[],
                )
                last_entry = None
                index += 1
                continue

            # Output section name on its own line, address/size on the next.
            if current_section is None and re.match(r"^(\.\S+)\s*$", line):
                next_line = lines[index + 1] if index + 1 < line_count else ""
                cont_match = SECTION_NAME_AND_ADDR_RE.match(next_line)
                if cont_match:
                    current_section = OutputSection(
                        name=line.strip(),
                        address=int(cont_match.group(1), 16),
                        total_size=int(cont_match.group(2), 16),
                        entries=[],
                    )
                    last_entry = None
                    index += 2
                    continue

            if current_section is None:
                index += 1
                continue

            fill_match = FILL_RE.match(line)
            if fill_match:
                size = int(fill_match.group(2), 16)
                if size > 0:
                    last_entry = SectionEntry(
                        section_name="*fill*",
                        address=int(fill_match.group(1), 16),
                        size=size,
                        object_file="*fill*",
                    )
                    current_section.entries.append(last_entry)
                pending_sub_section_name = None
                index += 1
                continue

            entry_match = ENTRY_RE.match(line)
            if entry_match:
                size = int(entry_match.group(3), 16)
                if size > 0:
                    last_entry = SectionEntry(
                        section_name=entry_match.group(1),
                        address=int(entry_match.group(2), 16),
                        size=size,
                        object_file=entry_match.group(4).strip(),
                    )
                    current_section.entries.append(last_entry)
                pending_sub_section_name = None
                index += 1
                continue

            sub_only = SUBSECTION_ONLY_RE.match(line)
            if sub_only:
                pending_sub_section_name = sub_only.group(1)
                index += 1
                continue

            continued = ENTRY_CONTINUED_RE.match(line)
            if continued:
                size = int(continued.group(2), 16)
                object_file = continued.group(3).strip()
                if size > 0 and not object_file.startswith("0x") and "(size before" not in object_file:
                    last_entry = SectionEntry(
                        section_name=pending_sub_section_name or "",
                        address=int(continued.group(1), 16),
                        size=size,
                        object_file=object_file,
                    )
                    current_section.entries.append(last_entry)
                pending_sub_section_name = None
                index += 1
                continue

            symbol_match = SYMBOL_RE.match(line)
            if symbol_match and last_entry is not None and not symbol_match.group(2).startswith("."):
                symbol = symbol_match.group(2)
                if not last_entry.function_name and not is_linker_symbol(symbol):
                    last_entry.function_name = symbol

            # A new dot-column line that is not a full section header: try the
            # inline form, otherwise close the current section.
            if SECTION_START_RE.match(line) and not line[:1].isspace():
                if current_section is not None and current_section.entries:
                    output_sections.append(current_section)
                inline = INLINE_SECTION_RE.match(line)
                if inline:
                    current_section = OutputSection(
                        name=inline.group(1),
                        address=int(inline.group(2), 16),
                        total_size=int(inline.group(3), 16),
                        entries=[],
                    )
                else:
                    current_section = None
                last_entry = None

            if UNMATCHED_ENTRY_RE.match(line):
                warnings.append(
                    ParseWarning(
                        index + 1,
                        "Unrecognised linker map entry; it was skipped. "
                        "Check for uppercase hexadecimal or an unsupported layout.",
                    )
                )

        index += 1

    if current_section is not None and current_section.entries:
        output_sections.append(current_section)

    data = build_map_file_data(output_sections, memory_regions, warnings)
    return data


def classify_section_type(section_name: str, parent_section: str) -> tuple[str, str]:
    combined = parent_section + " " + section_name
    if re.search(r"\.bss|\.noinit|zidata", combined, re.I):
        return "Zero", "RW"
    if re.search(r"\.data", combined, re.I) and not re.search(r"\.rodata", combined, re.I):
        return "Data", "RW"
    if re.search(r"\.rodata|drom", combined, re.I):
        return "Data", "RO"
    if re.search(r"\.text|\.literal|iram|\.vectors", combined, re.I):
        return "Code", "RO"
    if section_name == "*fill*":
        return "PAD", "RO"
    if re.search(r"iram|flash\.text", parent_section, re.I):
        return "Code", "RO"
    return "Data", "RO"


def extract_object_name(obj_path: str) -> str:
    normalized = obj_path.replace("\\", "/")
    lib_match = re.search(r"([^/]+\.a\(.+?\))", normalized)
    if lib_match:
        return lib_match.group(1)
    obj_match = re.search(r"([^/]+\.obj)$", normalized)
    if obj_match:
        return obj_match.group(1)
    o_match = re.search(r"([^/]+\.o)$", normalized)
    if o_match:
        return o_match.group(1)
    return obj_path


def extract_library_name(obj_path: str) -> str | None:
    normalized = obj_path.replace("\\", "/")
    match = re.search(r"([^/]+\.a)\(", normalized)
    return match.group(1) if match else None


def get_function_from_section(section_name: str) -> str | None:
    match = re.match(r"\.(?:text|literal|iram\d*)\.(.+)", section_name)
    return match.group(1) if match else None


def build_map_file_data(
    output_sections: list[OutputSection],
    memory_regions: list[MemoryRegion],
    warnings: list[ParseWarning] | None = None,
) -> MapFileData:
    warnings = warnings if warnings is not None else []

    runtime_sections = []
    for section in output_sections:
        name = section.name.lower()
        if name.startswith(".debug"):
            continue
        if name.startswith(".comment"):
            continue
        if name.startswith(".xtensa"):
            continue
        if name.startswith(".xt."):
            continue
        if name == ".noload":
            continue
        if "dummy" in name:
            continue
        if name == ".ext_ram.dummy":
            continue
        runtime_sections.append(section)

    load_region = LoadRegion(
        name="ESP32_FLASH",
        base_addr=0,
        size=0,
        max_size=0,
        execution_regions=[],
    )

    component_sizes: dict[str, dict[str, object]] = {}

    for section in runtime_sections:
        if section.total_size == 0:
            continue

        exec_region = ExecutionRegion(
            name=section.name,
            exec_base=section.address,
            load_base=section.address,
            size=section.total_size,
            max_size=section.total_size,
            sections=[],
        )

        for entry in section.entries:
            section_type, attr = classify_section_type(entry.section_name, section.name)
            function_name = entry.function_name or get_function_from_section(entry.section_name)
            object_name = extract_object_name(entry.object_file)

            exec_region.sections.append(
                MemorySection(
                    exec_addr=entry.address,
                    load_addr=None,
                    size=entry.size,
                    section_type=section_type,
                    attr=attr,
                    section_name=entry.section_name,
                    object_name=object_name,
                    function_name=function_name,
                )
            )

            if object_name == "*fill*":
                continue
            if object_name not in component_sizes:
                component_sizes[object_name] = {
                    "code": 0,
                    "ro_data": 0,
                    "rw_data": 0,
                    "zi_data": 0,
                    "library": extract_library_name(entry.object_file),
                }
            comp = component_sizes[object_name]
            if section_type == "Code":
                comp["code"] = int(comp["code"]) + entry.size  # type: ignore[arg-type]
            elif section_type == "Zero":
                comp["zi_data"] = int(comp["zi_data"]) + entry.size  # type: ignore[arg-type]
            elif attr == "RW":
                comp["rw_data"] = int(comp["rw_data"]) + entry.size  # type: ignore[arg-type]
            else:
                comp["ro_data"] = int(comp["ro_data"]) + entry.size  # type: ignore[arg-type]

        if exec_region.sections:
            load_region.execution_regions.append(exec_region)

    load_region.size = sum(region.size for region in load_region.execution_regions)

    components: list[ComponentSize] = []
    library_members: list[ComponentSize] = []
    for object_name, sizes in component_sizes.items():
        comp = ComponentSize(
            object_name=object_name,
            code=int(sizes["code"]),  # type: ignore[arg-type]
            code_inc_data=int(sizes["code"]),  # type: ignore[arg-type]
            ro_data=int(sizes["ro_data"]),  # type: ignore[arg-type]
            rw_data=int(sizes["rw_data"]),  # type: ignore[arg-type]
            zi_data=int(sizes["zi_data"]),  # type: ignore[arg-type]
            debug=0,
            library=sizes["library"],  # type: ignore[arg-type]
        )
        if sizes["library"]:
            library_members.append(comp)
        else:
            components.append(comp)

    total_code = total_ro = total_rw = total_zi = 0
    for comp in [*components, *library_members]:
        total_code += comp.code
        total_ro += comp.ro_data
        total_rw += comp.rw_data
        total_zi += comp.zi_data

    total_rom = total_code + total_ro + total_rw
    total_rw_all = total_rw + total_zi

    return MapFileData(
        compiler="GNU ld (ESP-IDF)",
        compiler_version="AC6",  # upstream reuses AC6 function-name extraction rules
        load_regions=[load_region],
        component_sizes=components,
        library_members=library_members,
        library_sizes=[],
        grand_totals=GrandTotals(
            code=total_code,
            ro_data=total_ro,
            rw_data=total_rw,
            zi_data=total_zi,
            total_ro=total_code + total_ro,
            total_rw=total_rw_all,
            total_rom=total_rom,
        ),
        memory_regions=memory_regions,
        warnings=warnings,
        format="gnu-ld",
    )
