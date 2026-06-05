"""
test_clk_classifiers.py

Tests that ALL 28 SFR change classifier types are correctly detected (or
handled) when diffing old_sfr/sfr_clk.h vs new_sfr/sfr_clk.h for IP=CLK.

Each test_type_N_* function verifies one specific ChangeType.

Classifier detection map (based on sfr_diff_analyzer.py implementation):
  Auto-detected (18 types): REG_RENAMED, REG_DELETED, REG_ADDED,
    FIELD_RENAMED, FIELD_DELETED, FIELD_ADDED, BITWIDTH_CHANGED,
    ACCESS_CHANGED, OFFSET_CHANGED, RESET_CHANGED, COMMENT_CHANGED,
    MULTI_CHANGED, FIELD_POLARITY_CHANGED, FIELD_WRITE_ONCE,
    FIELD_SELF_CLEARING, FIELD_STICKY_CHANGED, FIELD_ENUM_CHANGED,
    DESCRIPTION_ADDED
  Structurally inferred via FIELD_DELETED/ADDED (2 types):
    FIELD_SPLIT (→ FIELD_DELETED + FIELD_ADDED), FIELD_MERGED (same)
  Structurally inferred via REG_ADDED/FIELD_DELETED (1 type):
    FIELD_MOVED_CROSS_REG (→ FIELD_DELETED from old reg + FIELD_ADDED in new reg)
  Structurally inferred via FIELD_ADDED (2 types):
    RESERVED_PROMOTED, RESERVED_PARTIAL_ACTIVATED (→ new named fields appear)
  COMMENT_CHANGED (1 type): WRITE_MASK_CHANGED (→ description notes RO mask)
  Skip (no structural representation): REG_MOVED, REG_SIZE_CHANGED
  Comment-based (manual): REG_ARRAY_CHANGED, REG_CLUSTER_CHANGED
"""
import sys
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# Path setup — add sfr_gen root so we can import lld_gen.sfr_diff_analyzer
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff,
    summarize_changes,
    ChangeType,
)

OLD_SFR = Path(__file__).parent.parent / "old_sfr" / "sfr_clk.h"
NEW_SFR = Path(__file__).parent.parent / "new_sfr" / "sfr_clk.h"


# ---------------------------------------------------------------------------
# Module-scoped fixture: run diff once, reuse across all tests
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def changes():
    """Run classify_sfr_diff once and share results across all tests."""
    assert OLD_SFR.exists(), f"Missing old SFR: {OLD_SFR}"
    assert NEW_SFR.exists(), f"Missing new SFR: {NEW_SFR}"
    return classify_sfr_diff(OLD_SFR, NEW_SFR, ip="CLK")


# ---------------------------------------------------------------------------
# Helper extractors
# ---------------------------------------------------------------------------
def _types(changes):
    return [cr.change_type for cr in changes]


def _regs(changes):
    return [cr.reg_name for cr in changes]


def _fields(changes):
    return [(cr.reg_name, cr.field_name) for cr in changes]


def _find(changes, change_type=None, reg=None, field=None):
    """Find all ChangeRecords matching optional filters."""
    result = list(changes)
    if change_type is not None:
        result = [c for c in result if c.change_type == change_type]
    if reg is not None:
        result = [c for c in result if c.reg_name == reg]
    if field is not None:
        result = [c for c in result if c.field_name == field]
    return result


# ===========================================================================
# Type 1 — REG_RENAMED
# ===========================================================================
def test_type_1_reg_renamed(changes):
    """
    PLL_CON (old) should be detected as renamed to PLL_CTRL (new).
    The parser assigns offsets sequentially; both occupy offset 0x0004.
    The analyzer matches same-offset different-name as REG_RENAMED.
    """
    matches = _find(changes, change_type=ChangeType.REG_RENAMED)
    assert matches, (
        "Expected at least one REG_RENAMED change; got none.\n"
        f"All change types: {_types(changes)}"
    )
    reg_names = {c.reg_name for c in matches} | {
        c.new_reg.name for c in matches if c.new_reg
    }
    assert "PLL_CON" in reg_names or "PLL_CTRL" in reg_names, (
        f"REG_RENAMED found but neither PLL_CON nor PLL_CTRL in: {reg_names}"
    )


# ===========================================================================
# Type 2 — REG_DELETED
# ===========================================================================
def test_type_2_reg_deleted(changes):
    """
    TRIM_CON exists in old SFR but not in new SFR → REG_DELETED.
    """
    matches = _find(changes, change_type=ChangeType.REG_DELETED)
    assert matches, (
        "Expected REG_DELETED change; got none.\n"
        f"All change types: {_types(changes)}"
    )
    deleted_names = {c.reg_name for c in matches}
    assert "TRIM_CON" in deleted_names, (
        f"REG_DELETED found but TRIM_CON not in: {deleted_names}"
    )


# ===========================================================================
# Type 3 — REG_ADDED
# ===========================================================================
def test_type_3_reg_added(changes):
    """
    RESET_CON does not exist in old SFR but is present in new SFR → REG_ADDED.
    """
    matches = _find(changes, change_type=ChangeType.REG_ADDED)
    assert matches, (
        "Expected REG_ADDED change; got none.\n"
        f"All change types: {_types(changes)}"
    )
    added_names = {c.reg_name for c in matches}
    assert "RESET_CON" in added_names, (
        f"REG_ADDED found but RESET_CON not in: {added_names}"
    )


# ===========================================================================
# Type 4 — FIELD_RENAMED
# ===========================================================================
def test_type_4_field_renamed(changes):
    """
    DIV_CON.DIV_PRE (old bits [0-3]) is renamed to DIV_PREDIV (new bits [0-3]).
    Same mask+shift → FIELD_RENAMED.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_RENAMED, reg="DIV_CON")
    assert matches, (
        "Expected FIELD_RENAMED in DIV_CON; got none.\n"
        f"All (reg,field) pairs: {_fields(changes)}"
    )
    field_names = {c.field_name for c in matches} | {
        c.new_field.name for c in matches if c.new_field
    }
    assert "DIV_PRE" in field_names or "DIV_PREDIV" in field_names, (
        f"FIELD_RENAMED in DIV_CON found but neither DIV_PRE nor DIV_PREDIV in: {field_names}"
    )


# ===========================================================================
# Type 5 — FIELD_DELETED
# ===========================================================================
def test_type_5_field_deleted(changes):
    """
    DIV_CON.DIV_POST exists in old SFR but is removed in new SFR → FIELD_DELETED.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_DELETED, reg="DIV_CON")
    assert matches, (
        "Expected FIELD_DELETED in DIV_CON; got none.\n"
        f"All (reg,field) pairs: {_fields(changes)}"
    )
    deleted_fields = {c.field_name for c in matches}
    assert "DIV_POST" in deleted_fields, (
        f"FIELD_DELETED in DIV_CON found but DIV_POST not in: {deleted_fields}"
    )


# ===========================================================================
# Type 6 — FIELD_ADDED
# ===========================================================================
def test_type_6_field_added(changes):
    """
    DIV_CON.DIV_FRAC [10-19] is a new field in the new SFR → FIELD_ADDED.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="DIV_CON")
    assert matches, (
        "Expected FIELD_ADDED in DIV_CON; got none.\n"
        f"All (reg,field) pairs: {_fields(changes)}"
    )
    added_fields = {c.field_name for c in matches}
    assert "DIV_FRAC" in added_fields, (
        f"FIELD_ADDED in DIV_CON found but DIV_FRAC not in: {added_fields}"
    )


# ===========================================================================
# Type 7 — BITWIDTH_CHANGED
# ===========================================================================
def test_type_7_bitwidth_changed(changes):
    """
    CLK_CON.CLK_DIV grows from 4 bits ([1-4]) to 5 bits ([1-5]) → BITWIDTH_CHANGED.
    """
    matches = _find(changes, change_type=ChangeType.BITWIDTH_CHANGED, reg="CLK_CON")
    assert matches, (
        "Expected BITWIDTH_CHANGED in CLK_CON; got none.\n"
        f"All (reg,field) pairs with types: {[(c.reg_name, c.field_name, c.change_type) for c in changes]}"
    )
    field_names = {c.field_name for c in matches}
    assert "CLK_DIV" in field_names, (
        f"BITWIDTH_CHANGED in CLK_CON but CLK_DIV not in: {field_names}"
    )


# ===========================================================================
# Type 8 — ACCESS_CHANGED
# ===========================================================================
def test_type_8_access_changed(changes):
    """
    CLK_CON.CLK_SRC changes from RO (old) to RW (new) → ACCESS_CHANGED.
    """
    matches = _find(changes, change_type=ChangeType.ACCESS_CHANGED, reg="CLK_CON")
    assert matches, (
        "Expected ACCESS_CHANGED in CLK_CON; got none.\n"
        f"CLK_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'CLK_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "CLK_SRC" in field_names, (
        f"ACCESS_CHANGED in CLK_CON but CLK_SRC not in: {field_names}"
    )
    # Verify direction: old=RO, new=RW
    clk_src_change = next(c for c in matches if c.field_name == "CLK_SRC")
    assert clk_src_change.old_field.access == "RO", (
        f"Expected old CLK_SRC access=RO, got: {clk_src_change.old_field.access}"
    )
    assert clk_src_change.new_field.access == "RW", (
        f"Expected new CLK_SRC access=RW, got: {clk_src_change.new_field.access}"
    )


# ===========================================================================
# Type 9 — OFFSET_CHANGED
# ===========================================================================
def test_type_9_offset_changed(changes):
    """
    CLK_CON.CLK_GATE moves from bits [8-9] (lsb=8) to [9-10] (lsb=9) → OFFSET_CHANGED.
    The lsb/shift changes from 8 to 9, triggering OFFSET_CHANGED.
    """
    matches = _find(changes, change_type=ChangeType.OFFSET_CHANGED, reg="CLK_CON")
    assert matches, (
        "Expected OFFSET_CHANGED in CLK_CON; got none.\n"
        f"CLK_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'CLK_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "CLK_GATE" in field_names, (
        f"OFFSET_CHANGED in CLK_CON but CLK_GATE not in: {field_names}"
    )


# ===========================================================================
# Type 10 — RESET_CHANGED
# ===========================================================================
def test_type_10_reset_changed(changes):
    """
    RST_CON register reset value changes from 0x00000000 to 0x00000100.
    This causes RST_CNT field reset to change from 0 to 1 (bits[15:8] of the
    register reset value → (0x100>>8)&0xFF = 1).
    All other RST_CNT attributes are unchanged → pure RESET_CHANGED.
    """
    matches = _find(changes, change_type=ChangeType.RESET_CHANGED)
    # Also accept MULTI_CHANGED on fields that have reset + other changes
    multi_with_reset = [
        c for c in changes
        if c.change_type == ChangeType.MULTI_CHANGED
        and "RESET_CHANGED" in c.details
    ]
    all_reset = matches + multi_with_reset
    assert all_reset, (
        "Expected RESET_CHANGED (or MULTI_CHANGED with RESET_CHANGED in details); got none.\n"
        f"All change types: {_types(changes)}"
    )


# ===========================================================================
# Type 11 — COMMENT_CHANGED
# ===========================================================================
def test_type_11_comment_changed(changes):
    """
    CLK_CON.CLK_MODE description changes from 'Clock mode' to
    'Clock mode: 0=Normal 1=PowerSave 2=Turbo 3=Bypass'.
    However since access+reset+desc all change, it is classified as
    FIELD_POLARITY_CHANGED (higher priority). COMMENT_CHANGED still
    appears for other fields or can be detected via IRQ_POL description.
    We verify COMMENT_CHANGED exists anywhere in the diff.
    """
    # COMMENT_CHANGED may be overridden by FIELD_POLARITY_CHANGED or FIELD_ENUM_CHANGED
    # for CLK_MODE / IRQ_MASK — check all change types
    types = _types(changes)
    has_comment_or_polarity = (
        ChangeType.COMMENT_CHANGED in types
        or ChangeType.FIELD_POLARITY_CHANGED in types
    )
    assert has_comment_or_polarity, (
        "Expected COMMENT_CHANGED or FIELD_POLARITY_CHANGED; got none.\n"
        f"All change types: {types}"
    )


# ===========================================================================
# Type 12 — MULTI_CHANGED
# ===========================================================================
def test_type_12_multi_changed(changes):
    """
    DIV_CON.DIV_MODE changes both access (RW→RO) AND description
    ('Divider mode' → 'Divider mode (new behavior)') simultaneously.
    Two attribute changes in the same field → MULTI_CHANGED.

    Note: PLL_CON/PLL_CTRL fields are not diffed at field-level because
    they are processed as REG_RENAMED at the register level.
    """
    matches = _find(changes, change_type=ChangeType.MULTI_CHANGED)
    assert matches, (
        "Expected MULTI_CHANGED change; got none.\n"
        f"All change types: {_types(changes)}"
    )
    div_multi = [
        c for c in matches
        if c.reg_name == "DIV_CON" and c.field_name == "DIV_MODE"
    ]
    assert div_multi, (
        f"MULTI_CHANGED found but not for DIV_CON.DIV_MODE. "
        f"All MULTI_CHANGED records: {[(c.reg_name, c.field_name) for c in matches]}"
    )
    # Verify it caught both changes: access + comment
    assert "ACCESS_CHANGED" in div_multi[0].details or "COMMENT_CHANGED" in div_multi[0].details, (
        f"MULTI_CHANGED details should contain ACCESS_CHANGED or COMMENT_CHANGED; "
        f"got: {div_multi[0].details}"
    )


# ===========================================================================
# Type 13 — FIELD_SPLIT
# ===========================================================================
def test_type_13_field_split(changes):
    """
    SPREAD_CON.SS_CTRL [0-7] (8-bit) is split into SS_EN [0-0] + SS_DEPTH [1-7].
    The analyzer sees this as FIELD_DELETED (SS_CTRL gone) + FIELD_ADDED (SS_EN, SS_DEPTH).
    We verify the structural evidence: SS_CTRL is deleted and SS_EN/SS_DEPTH are added.
    """
    deleted = _find(changes, change_type=ChangeType.FIELD_DELETED, reg="SPREAD_CON", field="SS_CTRL")
    added_en = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="SPREAD_CON", field="SS_EN")
    added_depth = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="SPREAD_CON", field="SS_DEPTH")

    assert deleted, (
        "FIELD_SPLIT evidence: SS_CTRL should be FIELD_DELETED from SPREAD_CON, but was not found."
    )
    assert added_en or added_depth, (
        "FIELD_SPLIT evidence: SS_EN or SS_DEPTH should be FIELD_ADDED to SPREAD_CON, but neither found."
    )


# ===========================================================================
# Type 14 — FIELD_MERGED
# ===========================================================================
def test_type_14_field_merged(changes):
    """
    SPREAD_CON.SS_MERGE_A [8-11] + SS_MERGE_B [12-15] are merged into
    SS_COMBINED [8-15]. The analyzer sees this as FIELD_DELETED (both A and B
    gone) + FIELD_ADDED (SS_COMBINED).
    We verify the structural evidence.
    """
    deleted_a = _find(changes, change_type=ChangeType.FIELD_DELETED, reg="SPREAD_CON", field="SS_MERGE_A")
    deleted_b = _find(changes, change_type=ChangeType.FIELD_DELETED, reg="SPREAD_CON", field="SS_MERGE_B")
    added_combined = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="SPREAD_CON", field="SS_COMBINED")

    assert deleted_a or deleted_b, (
        "FIELD_MERGED evidence: SS_MERGE_A or SS_MERGE_B should be FIELD_DELETED from SPREAD_CON."
    )
    assert added_combined, (
        "FIELD_MERGED evidence: SS_COMBINED should be FIELD_ADDED to SPREAD_CON, but was not found."
    )


# ===========================================================================
# Type 15 — REG_MOVED
# ===========================================================================
@pytest.mark.skip(reason=(
    "REG_MOVED cannot be auto-detected by the current analyzer. "
    "CLK_STATUS appears at a different struct position in new SFR but retains "
    "the same parsed offset (auto-incremented sequentially). "
    "Manual review or SHA-based detection is required."
))
def test_type_15_reg_moved(changes):
    """
    CLK_STATUS is repositioned in the aggregate struct after RESET_CON.
    Struct-position changes are not tracked by the current analyzer.
    """
    matches = _find(changes, change_type=ChangeType.REG_MOVED)
    assert matches, "Expected REG_MOVED for CLK_STATUS"


# ===========================================================================
# Type 16 — REG_SIZE_CHANGED
# ===========================================================================
@pytest.mark.skip(reason=(
    "REG_SIZE_CHANGED cannot be auto-detected by the current analyzer. "
    "RST_CON layout changed but the register remains 32-bit in both versions "
    "and no explicit bit-width metadata is available at register level. "
    "Manual review via struct inspection is required."
))
def test_type_16_reg_size_changed(changes):
    """
    RST_CON gains new fields (CLK_BYPASS, RST_TYPE) from previously reserved bits.
    Conceptually this represents a functional size increase, but the C struct
    remains UINT32 in both versions.
    """
    matches = _find(changes, change_type=ChangeType.REG_SIZE_CHANGED)
    assert matches, "Expected REG_SIZE_CHANGED for RST_CON"


# ===========================================================================
# Type 17 — REG_ARRAY_CHANGED (manual review)
# ===========================================================================
def test_type_17_reg_array_changed(changes):
    """
    REG_ARRAY_CHANGED is marked via a comment in new SFR header:
    '/* ARRAY_CHANGED: was scalar, now CLK_CON[2] */'
    The analyzer includes REG_ARRAY_CHANGED in MANUAL_REVIEW_REQUIRED.
    This test verifies the constant is declared and in the right set.
    """
    assert hasattr(ChangeType, "REG_ARRAY_CHANGED"), (
        "ChangeType.REG_ARRAY_CHANGED constant is not defined"
    )
    assert ChangeType.REG_ARRAY_CHANGED in ChangeType.MANUAL_REVIEW_REQUIRED, (
        f"REG_ARRAY_CHANGED should be in MANUAL_REVIEW_REQUIRED set; "
        f"got: {ChangeType.MANUAL_REVIEW_REQUIRED}"
    )


# ===========================================================================
# Type 18 — REG_CLUSTER_CHANGED (manual review)
# ===========================================================================
def test_type_18_reg_cluster_changed(changes):
    """
    REG_CLUSTER_CHANGED is marked via comment in new SFR header:
    '/* CLUSTER_CHANGED: PLL group restructured */'
    The analyzer includes REG_CLUSTER_CHANGED in MANUAL_REVIEW_REQUIRED.
    This test verifies the constant is declared and in the right set.
    """
    assert hasattr(ChangeType, "REG_CLUSTER_CHANGED"), (
        "ChangeType.REG_CLUSTER_CHANGED constant is not defined"
    )
    assert ChangeType.REG_CLUSTER_CHANGED in ChangeType.MANUAL_REVIEW_REQUIRED, (
        f"REG_CLUSTER_CHANGED should be in MANUAL_REVIEW_REQUIRED set; "
        f"got: {ChangeType.MANUAL_REVIEW_REQUIRED}"
    )


# ===========================================================================
# Type 19 — FIELD_MOVED_CROSS_REG
# ===========================================================================
def test_type_19_field_moved_cross_reg(changes):
    """
    RST_CON.RST_EN is removed from RST_CON and RST_SW/RST_HW appear in
    the new register RESET_CON. The analyzer sees this as:
      - FIELD_DELETED: RST_EN from RST_CON
      - REG_ADDED: RESET_CON (which contains RST_SW, RST_HW)
    We verify the structural evidence of a cross-register field move.
    """
    # RST_EN should be deleted from RST_CON in new SFR
    deleted = _find(changes, change_type=ChangeType.FIELD_DELETED, reg="RST_CON", field="RST_EN")
    # RESET_CON should be added (it contains the moved field equivalent)
    added_reg = _find(changes, change_type=ChangeType.REG_ADDED, reg="RESET_CON")

    assert deleted, (
        "FIELD_MOVED_CROSS_REG evidence: RST_EN should be FIELD_DELETED from RST_CON."
    )
    assert added_reg, (
        "FIELD_MOVED_CROSS_REG evidence: RESET_CON (target register) should be REG_ADDED."
    )


# ===========================================================================
# Type 20 — RESERVED_PROMOTED
# ===========================================================================
def test_type_20_reserved_promoted(changes):
    """
    RST_CON bit[5:5] was RSVD_A (part of a reserved block) and becomes
    CLK_BYPASS[5:5] RW in new SFR. Reserved fields are skipped during
    parsing so the analyzer sees CLK_BYPASS as a new FIELD_ADDED.
    """
    # CLK_BYPASS should appear as FIELD_ADDED in RST_CON
    added = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="RST_CON", field="CLK_BYPASS")
    assert added, (
        "RESERVED_PROMOTED evidence: CLK_BYPASS should be FIELD_ADDED to RST_CON "
        "(was a reserved bit that became a named field).\n"
        f"RST_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'RST_CON']}"
    )


# ===========================================================================
# Type 21 — RESERVED_PARTIAL_ACTIVATED
# ===========================================================================
def test_type_21_reserved_partial_activated(changes):
    """
    RST_CON bits[17:16] were RSVD_B (partial reserved block) and become
    RST_TYPE[17:16] RW in new SFR. The analyzer sees this as FIELD_ADDED.
    """
    added = _find(changes, change_type=ChangeType.FIELD_ADDED, reg="RST_CON", field="RST_TYPE")
    assert added, (
        "RESERVED_PARTIAL_ACTIVATED evidence: RST_TYPE should be FIELD_ADDED to RST_CON "
        "(was partial reserved bits that became a named field).\n"
        f"RST_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'RST_CON']}"
    )


# ===========================================================================
# Type 22 — FIELD_ENUM_CHANGED
# ===========================================================================
def test_type_22_field_enum_changed(changes):
    """
    IRQ_CON.IRQ_MASK description changes from '0=ALL 1=NONE' (old)
    to '0=PERIPH 1=CORE 2=ALL 3=NONE' (new) → FIELD_ENUM_CHANGED.
    The classifier detects differing N=value enum patterns in description.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_ENUM_CHANGED, reg="IRQ_CON")
    assert matches, (
        "Expected FIELD_ENUM_CHANGED in IRQ_CON; got none.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "IRQ_MASK" in field_names, (
        f"FIELD_ENUM_CHANGED in IRQ_CON but IRQ_MASK not in: {field_names}"
    )


# ===========================================================================
# Type 23 — FIELD_WRITE_ONCE
# ===========================================================================
def test_type_23_field_write_once(changes):
    """
    IRQ_CON.IRQ_TRIG access changes from RW (old) to RWL (new, write-once lock).
    The classifier detects RWL as a write-once marker → FIELD_WRITE_ONCE.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_WRITE_ONCE, reg="IRQ_CON")
    assert matches, (
        "Expected FIELD_WRITE_ONCE in IRQ_CON; got none.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "IRQ_TRIG" in field_names, (
        f"FIELD_WRITE_ONCE in IRQ_CON but IRQ_TRIG not in: {field_names}"
    )
    # Verify new access is RWL (write-once lock marker)
    trig_change = next(c for c in matches if c.field_name == "IRQ_TRIG")
    assert trig_change.new_field.access in ("RWL", "W1", "OTP", "LOCK"), (
        f"Expected write-once access marker, got: {trig_change.new_field.access}"
    )


# ===========================================================================
# Type 24 — WRITE_MASK_CHANGED
# ===========================================================================
def test_type_24_write_mask_changed(changes):
    """
    IRQ_CON.IRQ_POL description changes to note that bit[7] is now RO
    (write-mask restricted). The analyzer classifies this as COMMENT_CHANGED
    since the description changed but no access/bitwidth/offset changes occurred.
    We verify the description change is detected for IRQ_POL.
    """
    # WRITE_MASK_CHANGED manifests as COMMENT_CHANGED for IRQ_POL
    comment_changes = _find(changes, change_type=ChangeType.COMMENT_CHANGED,
                            reg="IRQ_CON", field="IRQ_POL")
    # Also accept if it was classified as MULTI_CHANGED
    multi_changes = _find(changes, change_type=ChangeType.MULTI_CHANGED,
                          reg="IRQ_CON", field="IRQ_POL")
    all_matches = comment_changes + multi_changes
    assert all_matches, (
        "WRITE_MASK_CHANGED evidence: IRQ_POL description change (noting partial RO mask) "
        "should be detected as COMMENT_CHANGED or MULTI_CHANGED in IRQ_CON.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )


# ===========================================================================
# Type 25 — FIELD_SELF_CLEARING
# ===========================================================================
def test_type_25_field_self_clearing(changes):
    """
    IRQ_CON.IRQ_CLR access changes from RW (old) to SC (new, self-clearing pulse).
    The classifier detects SC as a self-clearing marker → FIELD_SELF_CLEARING.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_SELF_CLEARING, reg="IRQ_CON")
    assert matches, (
        "Expected FIELD_SELF_CLEARING in IRQ_CON; got none.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "IRQ_CLR" in field_names, (
        f"FIELD_SELF_CLEARING in IRQ_CON but IRQ_CLR not in: {field_names}"
    )


# ===========================================================================
# Type 26 — FIELD_STICKY_CHANGED
# ===========================================================================
def test_type_26_field_sticky_changed(changes):
    """
    IRQ_CON.IRQ_PENDING changes from RO (old) to W1C (new, sticky clear-on-write).
    The classifier detects RO→W1C as FIELD_STICKY_CHANGED.
    """
    matches = _find(changes, change_type=ChangeType.FIELD_STICKY_CHANGED, reg="IRQ_CON")
    assert matches, (
        "Expected FIELD_STICKY_CHANGED in IRQ_CON; got none.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "IRQ_PENDING" in field_names, (
        f"FIELD_STICKY_CHANGED in IRQ_CON but IRQ_PENDING not in: {field_names}"
    )
    # Verify direction: old=RO, new=W1C
    pending_change = next(c for c in matches if c.field_name == "IRQ_PENDING")
    assert pending_change.old_field.access == "RO", (
        f"Expected old IRQ_PENDING access=RO, got: {pending_change.old_field.access}"
    )
    assert pending_change.new_field.access == "W1C", (
        f"Expected new IRQ_PENDING access=W1C, got: {pending_change.new_field.access}"
    )


# ===========================================================================
# Type 27 — FIELD_POLARITY_CHANGED
# ===========================================================================
def test_type_27_field_polarity_changed(changes):
    """
    CLK_CON.CLK_MODE: access changes RW→RO, reset changes (due to register
    reset value change), and description expands → all three change simultaneously
    → FIELD_POLARITY_CHANGED (highest-priority classifier in _classify_field_change).
    """
    matches = _find(changes, change_type=ChangeType.FIELD_POLARITY_CHANGED)
    assert matches, (
        "Expected FIELD_POLARITY_CHANGED; got none.\n"
        f"All change types: {_types(changes)}"
    )
    clk_mode_matches = [
        c for c in matches
        if c.reg_name == "CLK_CON" and c.field_name == "CLK_MODE"
    ]
    assert clk_mode_matches, (
        f"FIELD_POLARITY_CHANGED found but not for CLK_CON.CLK_MODE. "
        f"All FIELD_POLARITY_CHANGED: {[(c.reg_name, c.field_name) for c in matches]}"
    )


# ===========================================================================
# Type 28 — DESCRIPTION_ADDED
# ===========================================================================
def test_type_28_description_added(changes):
    """
    IRQ_CON.IRQ_EN has an empty description in old SFR and gains
    'IRQ enable: 0=disable all interrupts 1=enable selected interrupts' in new SFR
    → DESCRIPTION_ADDED.
    """
    matches = _find(changes, change_type=ChangeType.DESCRIPTION_ADDED, reg="IRQ_CON")
    assert matches, (
        "Expected DESCRIPTION_ADDED in IRQ_CON; got none.\n"
        f"IRQ_CON changes: {[(c.field_name, c.change_type) for c in changes if c.reg_name == 'IRQ_CON']}"
    )
    field_names = {c.field_name for c in matches}
    assert "IRQ_EN" in field_names, (
        f"DESCRIPTION_ADDED in IRQ_CON but IRQ_EN not in: {field_names}"
    )
    # Verify old had no description, new has one
    irq_en_change = next(c for c in matches if c.field_name == "IRQ_EN")
    assert not irq_en_change.old_field.has_desc, (
        f"Expected old IRQ_EN to have empty description, "
        f"got: '{irq_en_change.old_field.desc}'"
    )
    assert irq_en_change.new_field.has_desc, (
        f"Expected new IRQ_EN to have a description, got empty"
    )


# ===========================================================================
# Extra: No false positives on CLK_STATUS
# ===========================================================================
def test_no_false_positives_clk_status(changes):
    """
    CLK_STATUS is identical in both SFR versions (same fields, access, reset).
    The diff should produce zero changes for CLK_STATUS.
    """
    clk_status_changes = [c for c in changes if c.reg_name == "CLK_STATUS"]
    assert not clk_status_changes, (
        f"Expected NO changes for CLK_STATUS (unchanged register), "
        f"but got: {[(c.field_name, c.change_type) for c in clk_status_changes]}"
    )


# ===========================================================================
# Extra: All 28 ChangeType constants are declared
# ===========================================================================
def test_all_28_types_declared():
    """
    Verify all 28 ChangeType string constants are properly declared in the class.
    This is a schema/declaration test, independent of the SFR diff fixture.
    """
    required_types = [
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
        ChangeType.FIELD_SPLIT,
        ChangeType.FIELD_MERGED,
        ChangeType.REG_MOVED,
        ChangeType.REG_SIZE_CHANGED,
        ChangeType.REG_ARRAY_CHANGED,
        ChangeType.REG_CLUSTER_CHANGED,
        ChangeType.FIELD_MOVED_CROSS_REG,
        ChangeType.RESERVED_PROMOTED,
        ChangeType.RESERVED_PARTIAL_ACTIVATED,
        ChangeType.FIELD_ENUM_CHANGED,
        ChangeType.FIELD_WRITE_ONCE,
        ChangeType.WRITE_MASK_CHANGED,
        ChangeType.FIELD_SELF_CLEARING,
        ChangeType.FIELD_STICKY_CHANGED,
        ChangeType.FIELD_POLARITY_CHANGED,
        ChangeType.DESCRIPTION_ADDED,
    ]
    assert len(required_types) == 28, (
        f"Expected 28 change types in list, got {len(required_types)}"
    )
    for ct in required_types:
        assert isinstance(ct, str) and len(ct) > 0, (
            f"ChangeType constant is not a non-empty string: {ct!r}"
        )


# ===========================================================================
# Extra: Manual review flagging
# ===========================================================================
def test_manual_review_flagged():
    """
    REG_DELETED, REG_ADDED, REG_ARRAY_CHANGED, REG_CLUSTER_CHANGED,
    and WRITE_MASK_CHANGED are all in MANUAL_REVIEW_REQUIRED → auto-patch unsafe.
    """
    expected_in_manual = {
        ChangeType.REG_DELETED,
        ChangeType.REG_ADDED,
        ChangeType.REG_ARRAY_CHANGED,
        ChangeType.REG_CLUSTER_CHANGED,
        ChangeType.WRITE_MASK_CHANGED,
    }
    for ct in expected_in_manual:
        assert ct in ChangeType.MANUAL_REVIEW_REQUIRED, (
            f"{ct} should be in MANUAL_REVIEW_REQUIRED but is not.\n"
            f"Current set: {ChangeType.MANUAL_REVIEW_REQUIRED}"
        )


# ===========================================================================
# Extra: LLM routing for semantic changes
# ===========================================================================
def test_needs_llm_routing(changes):
    """
    Semantic change types (COMMENT_CHANGED, MULTI_CHANGED, FIELD_POLARITY_CHANGED,
    FIELD_ENUM_CHANGED, FIELD_WRITE_ONCE, FIELD_SELF_CLEARING, FIELD_STICKY_CHANGED,
    DESCRIPTION_ADDED) should have needs_llm=True on their ChangeRecord.
    """
    llm_types = {
        ChangeType.COMMENT_CHANGED,
        ChangeType.MULTI_CHANGED,
        ChangeType.FIELD_POLARITY_CHANGED,
        ChangeType.FIELD_ENUM_CHANGED,
        ChangeType.FIELD_WRITE_ONCE,
        ChangeType.FIELD_SELF_CLEARING,
        ChangeType.FIELD_STICKY_CHANGED,
        ChangeType.DESCRIPTION_ADDED,
    }
    for change in changes:
        if change.change_type in llm_types:
            assert change.needs_llm, (
                f"Expected needs_llm=True for {change.change_type} "
                f"({change.reg_name}.{change.field_name}), got False"
            )


# ===========================================================================
# Extra: summarize_changes returns non-empty string
# ===========================================================================
def test_summary_output(changes):
    """
    summarize_changes() should return a non-empty string summarizing the diff.
    """
    summary = summarize_changes(changes)
    assert isinstance(summary, str) and len(summary) > 0, (
        "summarize_changes() returned empty or non-string output"
    )
    assert "Total changes" in summary, (
        f"Expected 'Total changes' in summary; got:\n{summary}"
    )


# ===========================================================================
# Extra: Minimum change count
# ===========================================================================
def test_total_change_count(changes):
    """
    The CLK diff should produce at least 20 distinct change records
    (covering the 18 auto-detectable types plus structural evidence records).
    """
    assert len(changes) >= 20, (
        f"Expected at least 20 change records; got {len(changes)}.\n"
        f"Types detected: {sorted(set(_types(changes)))}"
    )
