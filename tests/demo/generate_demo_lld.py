"""
generate_demo_lld.py — Build tests/demo/lld.h from sfr_old.h

Run from sfr_gen/ directory:
    python tests/demo/generate_demo_lld.py

This generates lld.h with correct SRC_SHA anchors so the patcher
can surgically update it when sfr_new.h is applied.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).parent.parent.parent   # sfr_gen/
sys.path.insert(0, str(ROOT))

from lld_gen.sfr_diff_analyzer import SfrParser
from lld_gen.lld_patcher import generate_register_block

DEMO_DIR = Path(__file__).parent
OLD_SFR  = DEMO_DIR / "sfr_old.h"
LLD_OUT  = DEMO_DIR / "lld.h"
IP       = "DMA"


def main() -> None:
    print(f"[gen] Parsing {OLD_SFR.name} …")
    ir = SfrParser(ip=IP).parse_file(OLD_SFR)

    regs = list(ir.registers.values())
    print(f"[gen] Registers found: {[r.name for r in regs]}")
    for r in regs:
        print(f"      {r.name}  offset=0x{r.offset:04X}  fields={list(r.fields)}")

    guard = f"{IP}_LLD_H"
    lines = [
        "/*",
        " * lld.h — DMA Controller Low-Level Driver",
        " * AUTO-GENERATED from sfr_old.h by generate_demo_lld.py",
        " * DO NOT EDIT — regenerate with generate_demo_lld.py",
        " *",
        " * This file is the BASELINE lld.h for the patcher demo.",
        " * Apply sfr_new.h changes with:",
        " *   python -m lld_gen.main_sfr run \\",
        " *       --old tests/demo/sfr_old.h \\",
        " *       --new tests/demo/sfr_new.h \\",
        " *       --lld tests/demo/lld.h     \\",
        " *       --ip DMA --no-llm --no-git",
        " */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        f'#include "sfr_old.h"',
        "",
        "/* Macro shims — allows tests to build without hardware */",
        "/* Replace with real MMIO read/write in production       */",
        "",
    ]

    for reg in sorted(regs, key=lambda r: r.offset):
        lines.append(generate_register_block(IP, reg))
        lines.append("")

    lines.append(f"#endif /* {guard} */")
    lines.append("")

    content = "\n".join(lines)
    LLD_OUT.write_text(content, encoding="utf-8")
    print(f"[gen] Written: {LLD_OUT}")


if __name__ == "__main__":
    main()
