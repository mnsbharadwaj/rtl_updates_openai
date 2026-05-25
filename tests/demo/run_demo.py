"""
run_demo.py -- End-to-End Demonstration of all 12 SFR Change Types

Run from sfr_gen/ directory:
    python tests/demo/run_demo.py

What this does:
  1. Runs 'diff' step to classify all changes between sfr_old.h and sfr_new.h
  2. Makes a working copy of lld.h so the original is preserved
  3. Runs 'patch' step to apply all changes (--no-llm mode)
  4. Prints a side-by-side change summary
  5. Run:  pytest tests/demo/test_demo_patch.py -v
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent.parent   # sfr_gen/
DEMO_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from lld_gen.sfr_diff_analyzer import classify_sfr_diff, summarize_changes, ChangeType
from lld_gen.sfr_diff_analyzer import SfrParser
from lld_gen.lld_patcher import LLDPatcher

OLD_SFR = DEMO_DIR / "sfr_old.h"
NEW_SFR = DEMO_DIR / "sfr_new.h"
LLD_SRC = DEMO_DIR / "lld.h"
LLD_WRK = DEMO_DIR / "lld_patched.h"   # working copy -- original preserved
TEST_OUT = DEMO_DIR / "test_lld_generated.c"
IP       = "DMA"

EXPECTED_TYPES = {
    ChangeType.REG_RENAMED,
    ChangeType.REG_DELETED,
    ChangeType.REG_ADDED,
    ChangeType.FIELD_RENAMED,
    ChangeType.FIELD_DELETED,
    ChangeType.FIELD_ADDED,
    ChangeType.BITWIDTH_CHANGED,
    ChangeType.ACCESS_CHANGED,
    ChangeType.OFFSET_CHANGED,
    ChangeType.RESET_CHANGED,
    ChangeType.COMMENT_CHANGED,
    ChangeType.MULTI_CHANGED,
}


def _header(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def main() -> int:

    _header("Step 1 -- Classify SFR changes")
    changes = classify_sfr_diff(OLD_SFR, NEW_SFR, ip=IP)
    print(summarize_changes(changes))
    print()

    found_types = {c.change_type for c in changes}
    print("Change type coverage:")
    all_ok = True
    for ct in sorted(EXPECTED_TYPES):
        present = ct in found_types
        mark = "[OK]" if present else "[!!]"
        print(f"  {mark} {ct}")
        if not present:
            all_ok = False

    if not all_ok:
        print("\n[WARN] Some expected change types were not detected!")
        print("  Found types:", sorted(found_types))
    else:
        print("\n  All 12 change types detected [OK]")

    # Detailed change table
    print("\nDetailed change list:")
    print(f"  {'Register':<15} {'Field':<15} {'Type':<22} {'LLM?':<6} {'Details'}")
    print(f"  {'-'*14} {'-'*14} {'-'*21} {'-'*5} {'-'*30}")
    for c in changes:
        llm  = "Yes" if c.needs_llm else "No"
        det  = ", ".join(c.details[:1]) if c.details else ""
        print(f"  {c.reg_name:<15} {(c.field_name or '--'):<15} {c.change_type:<22} {llm:<6} {det}")

    _header("Step 2 -- Patch lld.h (--no-llm mode)")

    # Fresh working copy each run
    shutil.copy2(LLD_SRC, LLD_WRK)
    print(f"  Copied {LLD_SRC.name} -> {LLD_WRK.name}")

    # Parse new IR for REG_ADDED blocks
    new_ir = SfrParser(ip=IP).parse_file(NEW_SFR)

    patcher = LLDPatcher(ip=IP, no_llm=True)
    patcher.patch(LLD_WRK, changes, new_ir=new_ir)

    # Write generated test file
    patcher.write_test_file(TEST_OUT, "sfr_new.h", "lld_patched.h")
    print(f"  Test file written: {TEST_OUT.name}")

    _header("Step 3 -- Verify patch results")

    patched = LLD_WRK.read_text(encoding="utf-8")
    checks = [
        # REG_RENAMED: DMA_CHAN -> DMA_CHANNEL
        ("REG_RENAMED   DMA_CHAN->DMA_CHANNEL",
            "DMA_CHANNEL_SRC_ADDR_get" in patched and
            "DMA_CHAN_SRC_ADDR_get" not in patched),
        # REG_DELETED: DMA_DEBUG gone
        ("REG_DELETED   DMA_DEBUG removed",
            "DMA_DEBUG_DBG_EN_get" not in patched),
        # REG_ADDED: DMA_IRQ present
        ("REG_ADDED     DMA_IRQ inserted",
            "DMA_IRQ_IRQ_EN_get" in patched),
        # FIELD_RENAMED: EN -> ENABLE
        ("FIELD_RENAMED EN->ENABLE",
            "DMA_CTRL_ENABLE_get" in patched and
            "DMA_CTRL_EN_get" not in patched),
        # FIELD_DELETED: DONE gone
        ("FIELD_DELETED DONE removed",
            "DMA_STATUS_DONE_get" not in patched),
        # FIELD_ADDED: BUSY present
        ("FIELD_ADDED   BUSY inserted",
            "DMA_STATUS_BUSY_get" in patched),
        # BITWIDTH_CHANGED: BURST mask updated
        ("BITWIDTH_CHGD BURST mask 0xE->0x1E",
            "0x0000001EU" in patched),
        # ACCESS_CHANGED: MODE setter removed (RO now)
        ("ACCESS_CHGD   MODE_set removed",
            "DMA_CTRL_MODE_set(" not in patched),
        # OFFSET_CHANGED: THRESH shift updated
        ("OFFSET_CHGD   THRESH >>9U present",
            ">> 9U" in patched),
        # RESET_CHANGED: LEVEL present (SHA updated internally)
        ("RESET_CHGD    LEVEL function present",
            "DMA_STATUS_LEVEL_get" in patched),
        # COMMENT_CHANGED: TIMEOUT present (template fallback)
        ("COMMENT_CHGD  TIMEOUT present",
            "DMA_CTRL_TIMEOUT_get" in patched),
        # MULTI_CHANGED: PRIORITY setter removed (RO now)
        ("MULTI_CHANGED PRIORITY_set removed",
            "DMA_CTRL_PRIORITY_set(" not in patched),
        # FIFO unchanged
        ("UNCHANGED     FIFO block preserved",
            "DMA_FIFO_DEPTH_get" in patched),
    ]

    all_pass = True
    for label, cond in checks:
        mark = "[PASS]" if cond else "[FAIL]"
        print(f"  {mark} {label}")
        if not cond:
            all_pass = False

    _header("Summary")
    if all_pass:
        print("  All patch checks PASSED")
        print(f"\n  Patched file : {LLD_WRK}")
        print(f"  Test file    : {TEST_OUT}")
        print("\n  To run pytest on the demo:")
        print("    pytest tests/demo/test_demo_patch.py -v")
    else:
        print("  Some checks FAILED -- review output above")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
