"""Generate a large synthetic GNU ld MAP file for benchmarks and stress tests.

The output follows the subset of the GNU ld map grammar that the parser reads:
Memory Configuration + Linker script and memory map with output sections and
one-line input-section entries.  It is intentionally synthetic and contains no
real firmware data.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

OBJECT_SECTIONS = (".text", ".rodata", ".data", ".bss")


def _distribute(total: int, buckets: int) -> list[int]:
    base = total // buckets
    remainder = total % buckets
    return [base + (1 if index < remainder else 0) for index in range(buckets)]


def generate_gnu_map(
    sections: int,
    objects: int,
    output: Path,
    *,
    seed: int = 1,
) -> Path:
    if sections <= 0:
        raise ValueError("sections must be positive")
    objects = max(1, min(objects, sections))
    rng = random.Random(seed)

    output.parent.mkdir(parents=True, exist_ok=True)
    rom_base = 0x08000000
    ram_base = 0x20000000
    rom_cursor = rom_base
    ram_cursor = ram_base
    totals = {".text": 0, ".rodata": 0, ".data": 0, ".bss": 0}

    lines: list[str] = []
    lines.append("Archive member included to satisfy reference by file (symbol)\n\n")
    lines.append("Memory Configuration\n\n")
    lines.append("Name             Origin             Length             Attributes\n")
    lines.append("FLASH            0x08000000         0x00100000         rx\n")
    lines.append("RAM              0x20000000         0x00020000         xrw\n")
    lines.append("*default*        0x00000000         0xffffffffffffffff\n\n")
    lines.append("Linker script and memory map\n\n")
    lines.append("LOAD build/synthetic.elf\n")

    index = 0
    counts = _distribute(sections, len(OBJECT_SECTIONS))
    for section_name, count in zip(OBJECT_SECTIONS, counts):
        is_ram = section_name in (".data", ".bss")
        base = ram_cursor if is_ram else rom_cursor
        stem = {
            ".text": "func",
            ".rodata": "const",
            ".data": "var",
            ".bss": "buf",
        }[section_name]
        entries: list[str] = []
        section_total = 0
        for offset in range(count):
            size = rng.choice((16, 24, 32, 48, 64, 96, 128, 192, 256, 384))
            object_index = offset % objects
            name = f"{section_name}.{stem}_{index + offset:08d}"
            entries.append(
                f" {name:<34s} 0x{base + section_total:08x} "
                f"0x{size:08x} build/module_{object_index:05d}.o\n"
            )
            section_total += size

        lines.append(f"{section_name:<16s} 0x{base:08x} 0x{section_total:08x}\n")
        lines.extend(entries)
        totals[section_name] += section_total
        index += count
        if is_ram:
            ram_cursor += section_total + 0x10
        else:
            rom_cursor += section_total + 0x10

    lines.append(
        f"\nTOTAL ROM 0x{totals['.text'] + totals['.rodata']:x} "
        f"RAM 0x{totals['.data'] + totals['.bss']:x}\n"
    )
    output.write_text("".join(lines), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sections", type=int, default=50000)
    parser.add_argument("--objects", type=int, default=2000)
    parser.add_argument("-o", "--output", default="build/synthetic.map")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    path = generate_gnu_map(
        args.sections, args.objects, Path(args.output), seed=args.seed
    )
    size = path.stat().st_size
    print(f"Wrote {path} ({size / 1024 / 1024:.1f} MiB, {args.sections} sections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
