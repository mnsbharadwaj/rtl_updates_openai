"""
test_demo_patch.py — pytest suite validating the demo patch end-to-end

Tests all 12 SFR change types in sfr_old.h → sfr_new.h → lld.h patch.

Run from sfr_gen/:
    pytest tests/demo/test_demo_patch.py -v
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT     = Path(__file__).parent.parent.parent   # sfr_gen/
DEMO_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, ChangeType, SfrParser,
)
from lld_gen.lld_patcher import LLDPatcher

OLD_SFR = DEMO_DIR / "sfr_old.h"
NEW_SFR = DEMO_DIR / "sfr_new.h"
LLD_SRC = DEMO_DIR / "lld.h"
IP      = "DMA"


# ── Shared fixtures ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def changes():
    return classify_sfr_diff(OLD_SFR, NEW_SFR, ip=IP)


@pytest.fixture(scope="module")
def new_ir():
    return SfrParser(ip=IP).parse_file(NEW_SFR)


@pytest.fixture(scope="module")
def patched_content(tmp_path_factory, changes, new_ir):
    """Run the patcher once for the whole module and return patched content."""
    tmp = tmp_path_factory.mktemp("demo")
    lld = tmp / "lld.h"
    shutil.copy2(LLD_SRC, lld)
    patcher = LLDPatcher(ip=IP, no_llm=True)
    content = patcher.patch(lld, changes, new_ir=new_ir)
    return content


# ── Classification tests (12 change types detected) ─────────────────────────
class TestClassification:

    def _has(self, changes, ct: str, **kw) -> bool:
        for c in changes:
            if c.change_type != ct:
                continue
            if "reg" in kw and c.reg_name != kw["reg"]:
                continue
            if "field" in kw and c.field_name != kw["field"]:
                continue
            return True
        return False

    def test_type1_reg_renamed(self, changes):
        assert self._has(changes, ChangeType.REG_RENAMED, reg="CHAN")

    def test_type2_reg_deleted(self, changes):
        assert self._has(changes, ChangeType.REG_DELETED, reg="DEBUG")

    def test_type3_reg_added(self, changes):
        assert self._has(changes, ChangeType.REG_ADDED, reg="IRQ")

    def test_type4_field_renamed(self, changes):
        assert self._has(changes, ChangeType.FIELD_RENAMED, reg="CTRL", field="EN")

    def test_type5_field_deleted(self, changes):
        assert self._has(changes, ChangeType.FIELD_DELETED, reg="STATUS", field="DONE")

    def test_type6_field_added(self, changes):
        assert self._has(changes, ChangeType.FIELD_ADDED, reg="STATUS", field="BUSY")

    def test_type7_bitwidth_changed(self, changes):
        assert self._has(changes, ChangeType.BITWIDTH_CHANGED, reg="CTRL", field="BURST")

    def test_type8_access_changed(self, changes):
        assert self._has(changes, ChangeType.ACCESS_CHANGED, reg="CTRL", field="MODE")

    def test_type9_offset_changed(self, changes):
        assert self._has(changes, ChangeType.OFFSET_CHANGED, reg="STATUS", field="THRESH")

    def test_type10_reset_changed(self, changes):
        assert self._has(changes, ChangeType.RESET_CHANGED, reg="STATUS", field="LEVEL")

    def test_type11_comment_changed(self, changes):
        assert self._has(changes, ChangeType.COMMENT_CHANGED, reg="CTRL", field="TIMEOUT")

    def test_type12_multi_changed(self, changes):
        assert self._has(changes, ChangeType.MULTI_CHANGED, reg="CTRL", field="PRIORITY")

    def test_all_12_types_present(self, changes):
        expected = {
            ChangeType.REG_RENAMED, ChangeType.REG_DELETED, ChangeType.REG_ADDED,
            ChangeType.FIELD_RENAMED, ChangeType.FIELD_DELETED, ChangeType.FIELD_ADDED,
            ChangeType.BITWIDTH_CHANGED, ChangeType.ACCESS_CHANGED, ChangeType.OFFSET_CHANGED,
            ChangeType.RESET_CHANGED, ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED,
        }
        found = {c.change_type for c in changes}
        missing = expected - found
        assert not missing, f"Missing change types: {missing}"

    def test_llm_required_for_comment_changed(self, changes):
        cc = [c for c in changes if c.change_type == ChangeType.COMMENT_CHANGED]
        assert cc and cc[0].needs_llm

    def test_llm_required_for_multi_changed(self, changes):
        mc = [c for c in changes if c.change_type == ChangeType.MULTI_CHANGED]
        assert mc and mc[0].needs_llm

    def test_fifo_unchanged(self, changes):
        fifo_changes = [c for c in changes if c.reg_name == "FIFO"]
        assert not fifo_changes, f"FIFO should be unchanged but got: {fifo_changes}"

    def test_error_field_unchanged(self, changes):
        error_changes = [c for c in changes
                         if c.reg_name == "STATUS" and c.field_name == "ERROR"]
        assert not error_changes


# ── Patch output tests ───────────────────────────────────────────────────────
class TestPatchOutput:

    # Type 1 — REG_RENAMED
    def test_reg_renamed_new_name_present(self, patched_content):
        assert "DMA_CHANNEL_SRC_ADDR_get" in patched_content

    def test_reg_renamed_old_name_gone(self, patched_content):
        assert "DMA_CHAN_SRC_ADDR_get" not in patched_content

    # Type 2 — REG_DELETED
    def test_reg_deleted_block_gone(self, patched_content):
        assert "DMA_DEBUG_DBG_EN_get" not in patched_content
        assert "DMA_DEBUG_DBG_SEL_get" not in patched_content

    # Type 3 — REG_ADDED
    def test_reg_added_irq_en_present(self, patched_content):
        assert "DMA_IRQ_IRQ_EN_get" in patched_content

    def test_reg_added_irq_status_present(self, patched_content):
        assert "DMA_IRQ_IRQ_STATUS_get" in patched_content

    # Type 4 — FIELD_RENAMED
    def test_field_renamed_enable_present(self, patched_content):
        assert "DMA_CTRL_ENABLE_get" in patched_content

    def test_field_renamed_en_gone(self, patched_content):
        assert "DMA_CTRL_EN_get" not in patched_content

    # Type 5 — FIELD_DELETED
    def test_field_deleted_done_gone(self, patched_content):
        assert "DMA_STATUS_DONE_get" not in patched_content
        assert "DMA_STATUS_DONE_clear" not in patched_content

    # Type 6 — FIELD_ADDED
    def test_field_added_busy_present(self, patched_content):
        assert "DMA_STATUS_BUSY_get" in patched_content

    # Type 7 — BITWIDTH_CHANGED (BURST mask 0xE → 0x1E)
    def test_bitwidth_burst_new_mask(self, patched_content):
        assert "0x0000001EU" in patched_content

    def test_bitwidth_burst_old_mask_gone(self, patched_content):
        # Old mask 0x0000000EU should no longer appear in CTRL block
        # (it may still appear in STATUS DONE old remnant check — scoped check)
        import re
        # Only check within CTRL block context
        ctrl_start = patched_content.find("REGISTER: CTRL")
        ctrl_end   = patched_content.find("REGISTER: STATUS")
        ctrl_block = patched_content[ctrl_start:ctrl_end] if ctrl_start != -1 else ""
        assert "0x0000000EU" not in ctrl_block, \
            "Old BURST mask 0x0000000EU still in CTRL block"

    # Type 8 — ACCESS_CHANGED (MODE: RW→RO, setter gone)
    def test_access_changed_mode_getter_present(self, patched_content):
        assert "DMA_CTRL_MODE_get" in patched_content

    def test_access_changed_mode_setter_gone(self, patched_content):
        import re
        setters = re.findall(r'\bDMA_CTRL_MODE_set\s*\(', patched_content)
        assert not setters, f"MODE_set should not exist after ACCESS_CHANGED: {setters}"

    # Type 9 — OFFSET_CHANGED (THRESH shift 8→9)
    def test_offset_changed_thresh_new_shift(self, patched_content):
        assert ">> 9U" in patched_content

    def test_offset_changed_thresh_new_mask(self, patched_content):
        assert "0x0000FE00U" in patched_content

    # Type 10 — RESET_CHANGED (SHA updated; LEVEL function still present)
    def test_reset_changed_level_still_present(self, patched_content):
        assert "DMA_STATUS_LEVEL_get" in patched_content

    # Type 11 — COMMENT_CHANGED (TIMEOUT functions present in template-fallback mode)
    def test_comment_changed_timeout_present(self, patched_content):
        assert "DMA_CTRL_TIMEOUT_get" in patched_content

    # Type 12 — MULTI_CHANGED (PRIORITY: RW→RO, setter gone)
    def test_multi_changed_priority_getter_present(self, patched_content):
        assert "DMA_CTRL_PRIORITY_get" in patched_content

    def test_multi_changed_priority_setter_gone(self, patched_content):
        import re
        setters = re.findall(r'\bDMA_CTRL_PRIORITY_set\s*\(', patched_content)
        assert not setters, f"PRIORITY_set should not exist after MULTI_CHANGED→RO: {setters}"

    # Integrity check — unchanged FIFO block
    def test_fifo_block_preserved(self, patched_content):
        assert "DMA_FIFO_DEPTH_get" in patched_content
        assert "DMA_FIFO_FLUSH_set" in patched_content

    # Structural integrity
    def test_brace_balance(self, patched_content):
        assert LLDPatcher._check_braces(patched_content)

    def test_header_guard_present(self, patched_content):
        assert "#ifndef DMA_LLD_H" in patched_content
        assert "#define DMA_LLD_H" in patched_content
        assert "#endif" in patched_content
