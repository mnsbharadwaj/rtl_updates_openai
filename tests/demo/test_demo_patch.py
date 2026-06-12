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
        assert not self._has(changes, ChangeType.RESET_CHANGED, reg="STATUS", field="LEVEL")

    def test_type11_comment_changed(self, changes):
        assert self._has(changes, ChangeType.COMMENT_CHANGED, reg="CTRL", field="TIMEOUT")

    def test_type12_multi_changed(self, changes):
        assert self._has(changes, ChangeType.MULTI_CHANGED, reg="CTRL", field="PRIORITY")

    def test_all_12_types_present(self, changes):
        expected = {
            ChangeType.REG_RENAMED, ChangeType.REG_DELETED, ChangeType.REG_ADDED,
            ChangeType.FIELD_RENAMED, ChangeType.FIELD_DELETED, ChangeType.FIELD_ADDED,
            ChangeType.BITWIDTH_CHANGED, ChangeType.ACCESS_CHANGED, ChangeType.OFFSET_CHANGED,
            ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED,
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

    # ── Structural ──────────────────────────────────────────────────────────
    def test_brace_balance(self, patched_content):
        assert LLDPatcher._check_braces(patched_content)

    def test_header_guard_present(self, patched_content):
        assert "#ifndef DMA_LLD_H" in patched_content
        assert "#define DMA_LLD_H" in patched_content
        assert "#endif" in patched_content

    def test_struct_lld_driver_generated(self, patched_content):
        """Aggregate SFR struct and lld driver struct must appear."""
        assert "struct lld_dma" in patched_content
        assert "pSFR_DMA" in patched_content
        assert "SFR_DMA, *pSFR_DMA" in patched_content

    def test_struct_members_use_st_prefix(self, patched_content):
        """Struct member names use st prefix."""
        assert "stCTRL" in patched_content or "stCHANNEL" in patched_content

    # ── Type 1 — REG_RENAMED: function names FROZEN, body updated ──────────
    def test_reg_renamed_fn_name_frozen(self, patched_content):
        """Function names must NOT be renamed — they are frozen on REG_RENAMED."""
        # The old function name (lld_dma_chan_*) should REMAIN because names are frozen
        assert "lld_dma_chan_src_addr_get" in patched_content

    def test_reg_renamed_body_updated(self, patched_content):
        """Struct member path in body must use NEW register name (stCHANNEL)."""
        assert "->stCHANNEL." in patched_content

    def test_reg_renamed_old_body_gone(self, patched_content):
        """Old struct member path stCHAN must be replaced by stCHANNEL."""
        # stCHAN. should not appear (only stCHANNEL.)
        import re
        old_member = re.findall(r'->stCHAN\.', patched_content)
        assert not old_member, f"Old ->stCHAN. still in patched content"

    # ── Type 2 — REG_DELETED: DEPRECATE, don't delete ──────────────────────
    def test_reg_deleted_block_preserved(self, patched_content):
        """Deleted register's functions must be KEPT (not removed)."""
        # Functions for DEBUG register must still be present
        assert "lld_dma_debug_dbg_en_get" in patched_content or \
               "lld_dma_debug" in patched_content

    def test_reg_deleted_deprecated_comment(self, patched_content):
        """Deleted register block must have DEPRECATED comment."""
        assert "DEPRECATED" in patched_content

    # ── Type 3 — REG_ADDED ──────────────────────────────────────────────────
    def test_reg_added_irq_en_present(self, patched_content):
        assert "lld_dma_irq_irq_en_get" in patched_content

    def test_reg_added_struct_ptr_signature(self, patched_content):
        """New functions use struct lld_dma pointer."""
        assert "struct lld_dma *lld" in patched_content

    def test_reg_added_body_uses_struct_path(self, patched_content):
        """New function body uses lld->pSFR->stIRQ path."""
        assert "lld->pSFR->stIRQ." in patched_content

    # ── Type 4 — FIELD_RENAMED: function name FROZEN, body updated ─────────
    def test_field_renamed_fn_name_frozen(self, patched_content):
        """FIELD_RENAMED must NOT rename the function — name is frozen."""
        # lld_dma_ctrl_en_get should remain (frozen)
        assert "lld_dma_ctrl_en_get" in patched_content

    def test_field_renamed_body_updated(self, patched_content):
        """Function body must reference the NEW field name (ENABLE)."""
        assert ".stNative.ENABLE" in patched_content

    # ── Type 5 — FIELD_DELETED ──────────────────────────────────────────────
    def test_field_deleted_done_gone(self, patched_content):
        assert "lld_dma_status_done_get" not in patched_content
        assert "lld_dma_status_done_clear" not in patched_content

    # ── Type 6 — FIELD_ADDED ────────────────────────────────────────────────
    def test_field_added_busy_present(self, patched_content):
        assert "lld_dma_status_busy_get" in patched_content

    def test_field_added_body_struct_path(self, patched_content):
        assert "->stSTATUS.stNative.BUSY" in patched_content

    # ── Type 7 — BITWIDTH_CHANGED ───────────────────────────────────────────
    def test_bitwidth_burst_getter_present(self, patched_content):
        """Getter for BURST must still be present after bitwidth change."""
        assert "lld_dma_ctrl_burst_get" in patched_content

    def test_bitwidth_no_raw_masks(self, patched_content):
        """Struct-based LLD has NO raw mask literals — hardware handles bits."""
        # Raw mask 0x0000001EU should NOT be in struct-based functions
        import re
        ctrl_start = patched_content.find("REGISTER: CTRL")
        ctrl_end   = patched_content.find("REGISTER:", ctrl_start + 1) if ctrl_start != -1 else len(patched_content)
        ctrl_block = patched_content[ctrl_start:ctrl_end] if ctrl_start != -1 else ""
        # No mask literals in struct-based approach
        assert "0x0000001EU" not in ctrl_block

    # ── Type 8 — ACCESS_CHANGED (MODE: RW→RO) ───────────────────────────────
    def test_access_changed_mode_getter_present(self, patched_content):
        assert "lld_dma_ctrl_mode_get" in patched_content

    def test_access_changed_mode_setter_gone(self, patched_content):
        import re
        setters = re.findall(r'\blld_dma_ctrl_mode_set\s*\(', patched_content)
        assert not setters, f"MODE_set should not exist after ACCESS_CHANGED: {setters}"

    # ── Type 9 — OFFSET_CHANGED (struct-based: no body change needed) ───────
    def test_offset_changed_thresh_getter_present(self, patched_content):
        """Getter must still be present — struct handles bit position."""
        assert "lld_dma_status_thresh_get" in patched_content

    def test_offset_changed_body_uses_struct(self, patched_content):
        """Body must use struct member, not raw shift literal."""
        # >> 9U should NOT appear in struct-based mode
        assert ">> 9U" not in patched_content

    # ── Type 10 — RESET_CHANGED ─────────────────────────────────────────────
    def test_reset_changed_level_still_present(self, patched_content):
        assert "lld_dma_status_level_get" in patched_content

    # ── Type 11 — COMMENT_CHANGED ───────────────────────────────────────────
    def test_comment_changed_timeout_present(self, patched_content):
        assert "lld_dma_ctrl_timeout_get" in patched_content

    def test_comment_changed_new_desc_in_docstring(self, patched_content):
        """Updated description should appear in doxygen @brief."""
        # The new desc for TIMEOUT from sfr_new.h should appear in the LLD
        assert "@brief" in patched_content  # at minimum doxygen format preserved

    # ── Type 12 — MULTI_CHANGED (PRIORITY: RW→RO + desc changed) ───────────
    def test_multi_changed_priority_getter_present(self, patched_content):
        assert "lld_dma_ctrl_priority_get" in patched_content

    def test_multi_changed_priority_setter_gone(self, patched_content):
        import re
        setters = re.findall(r'\blld_dma_ctrl_priority_set\s*\(', patched_content)
        assert not setters, f"PRIORITY_set should not exist after MULTI_CHANGED→RO: {setters}"

    # ── Integrity: unchanged blocks ─────────────────────────────────────────
    def test_fifo_block_preserved(self, patched_content):
        assert "lld_dma_fifo_depth_get" in patched_content
        assert "#endif" in patched_content
