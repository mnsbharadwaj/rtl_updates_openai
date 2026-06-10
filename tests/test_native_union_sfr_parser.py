"""
test_native_union_sfr_parser.py — pytest suite for NativeUnionSfrParser

Tests:
    TestNativeUnionSfrParser      (10 tests) — parsing the new native typedef union format
    TestNativeVsVolatileDispatch  (3 tests)  — SfrParser auto-detects correct sub-parser
    TestNativeVsVolatileDiff      (4 tests)  — classify_sfr_diff works across native union files
    TestStructFieldName           (2 tests)  — struct_field_name populated from IP struct

Run:
    pytest tests/test_native_union_sfr_parser.py -v
    Expected: 19 passed
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.sfr_diff_analyzer import (
    NativeUnionSfrParser,
    VolatileUnionSfrParser,
    SfrParser,
    SfrIR,
    RegisterIR,
    FieldIR,
    ChangeType,
    classify_sfr_diff,
)

# ---------------------------------------------------------------------------
# Paths to fixture SFR headers
# ---------------------------------------------------------------------------
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pcie_sfr"
V1 = FIXTURE_DIR / "sfr_pcielink_v1.h"
V2 = FIXTURE_DIR / "sfr_pcielink_v2.h"


# ---------------------------------------------------------------------------
# Minimal native union SFR text (self-contained, no fixture files)
# ---------------------------------------------------------------------------
_MINIMAL_NATIVE_SFR = """\
typedef unsigned int uint32;

typedef union _SFR_TST_CTRL_U
{
    uint32 nvalue _value(0x00000003);
    struct
    {
        uint32 en        :  1; // 0-0   [RW]   Enable the controller
        uint32 mode      :  2; // 1-2   [RW]   Operating mode: 0=idle 1=run 2=test
        uint32 RSVDN0    : 29; // 3-31  [RO]   reserved
    } stNative;
} SFR_TST_CTRL, *pSFR_TST_CTRL;

typedef union _SFR_TST_STATUS_U
{
    uint32 nvalue _value(0x00000000);
    struct
    {
        uint32 busy      :  1; // 0-0   [RO]   Controller busy
        uint32 err_flag  :  1; // 1-1   [W1C]  Error detected. Write 1 to clear
        uint32 RSVDN0    : 30; // 2-31  [RO]   reserved
    } stNative;
} SFR_TST_STATUS, *pSFR_TST_STATUS;

// Base address: 0x4000
typedef struct _SFR_TST_WRP_RW_S
{
    SFR_TST_CTRL   stCTRL;
    SFR_TST_STATUS stSTATUS;
} SFR_TST_WRP_RW, *pSFR_TST_WRP_RW;
"""

_MINIMAL_VOLATILE_SFR = """\
typedef volatile union _SFR_TST_CTRL_U
{
    volatile unsigned int nValue _VALUE_(0x00000003);
    struct
    {
        volatile unsigned int en     : 1; // 0-0 [RW] Enable
        volatile unsigned int RSVD   : 31; // reserved
    } stNative;
} SFR_TST_CTRL, *pSFR_TST_CTRL;
"""


# ============================================================================
# 1. TestNativeUnionSfrParser  (10 tests)
# ============================================================================
class TestNativeUnionSfrParser:

    def _parse(self, text: str, ip: str = "TST") -> SfrIR:
        return NativeUnionSfrParser(ip=ip).parse_text(text, source="<test>")

    # N01 — register names extracted from typedef end line
    def test_register_names_extracted(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        assert "CTRL" in ir.registers
        assert "STATUS" in ir.registers

    # N02 — base address parsed from comment and offset assigned
    def test_base_address_assigned_to_first_register(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        # Base address 0x4000, CTRL is first member → offset = 0x4000
        assert ir.registers["CTRL"].offset == 0x4000

    # N03 — second register gets base + 4
    def test_second_register_offset_is_base_plus_4(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        assert ir.registers["STATUS"].offset == 0x4004

    # N04 — field names parsed
    def test_field_names_parsed(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        fields = ir.registers["CTRL"].fields
        assert "en" in fields
        assert "mode" in fields

    # N05 — reserved fields NOT included
    def test_reserved_fields_excluded(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        for reg in ir.registers.values():
            for name in reg.fields:
                assert not name.upper().startswith("RSVD"), \
                    f"Reserved field {name} should not be in IR"

    # N06 — bit ranges parsed from inline comment
    def test_bit_range_from_comment(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        mode = ir.registers["CTRL"].fields["mode"]
        assert mode.lsb == 1
        assert mode.msb == 2
        assert mode.width == 2

    # N07 — access type parsed from inline comment
    def test_access_type_parsed(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        assert ir.registers["CTRL"].fields["en"].access == "RW"
        assert ir.registers["STATUS"].fields["busy"].access == "RO"
        assert ir.registers["STATUS"].fields["err_flag"].access == "W1C"

    # N08 — reset value parsed from _value(...)
    def test_reset_value_parsed(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        # CTRL nvalue=0x3: en=1, mode=1 (bits 1-2 → 0b01 → 1)
        en = ir.registers["CTRL"].fields["en"]
        assert en.reset == 1
        mode = ir.registers["CTRL"].fields["mode"]
        assert mode.reset == 1  # bits 1-2 of 0x3 → 0b01 = 1

    # N09 — mask computed correctly
    def test_mask_computed_correctly(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        mode = ir.registers["CTRL"].fields["mode"]
        # width=2, shift=1 → mask = 0b110 = 0x6
        assert mode.mask == 0x6

    # N10 — IP field from filename
    def test_ip_from_filename(self):
        ir = NativeUnionSfrParser().parse_file(V1)
        assert ir.ip == "PCIELINK"

    # N11 — registers from fixture file parse correctly
    def test_fixture_v1_registers(self):
        ir = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        assert "CTRL_LT0" in ir.registers
        assert "CTRL_LT1" in ir.registers
        assert "STATUS" in ir.registers
        assert "ERR_INJECT" in ir.registers

    # N12 — struct_field_name populated from IP struct member name
    def test_struct_field_name_from_ip_struct(self):
        ir = self._parse(_MINIMAL_NATIVE_SFR)
        ctrl = ir.registers["CTRL"]
        # The IP struct uses "stCTRL" as member name
        assert ctrl.struct_field_name == "stCTRL"


# ============================================================================
# 2. TestNativeVsVolatileDispatch  (3 tests)
# ============================================================================
class TestNativeVsVolatileDispatch:

    # D01 — SfrParser dispatches to NativeUnionSfrParser for native format
    def test_sfr_parser_detects_native(self):
        ir = SfrParser(ip="TST").parse_text(_MINIMAL_NATIVE_SFR, source="<test>")
        assert "CTRL" in ir.registers
        assert "STATUS" in ir.registers

    # D02 — SfrParser dispatches to VolatileUnionSfrParser for volatile format
    def test_sfr_parser_detects_volatile(self):
        ir = SfrParser(ip="TST").parse_text(_MINIMAL_VOLATILE_SFR, source="<test>")
        assert "CTRL" in ir.registers

    # D03 — NativeUnionSfrParser does NOT accidentally parse volatile format
    def test_native_parser_ignores_volatile(self):
        ir = NativeUnionSfrParser(ip="TST").parse_text(_MINIMAL_VOLATILE_SFR, source="<test>")
        # volatile keyword should prevent _NATIVE_UNION_TYPEDEF_RE from matching
        # (it checks "volatile" not in ln)
        # The volatile text will match the guard check → no registers parsed
        assert len(ir.registers) == 0


# ============================================================================
# 3. TestNativeVsVolatileDiff  (4 tests — uses fixture files)
# ============================================================================
class TestNativeVsVolatileDiff:

    @pytest.mark.skipif(not V1.exists() or not V2.exists(),
                        reason="Fixture SFR files not found")
    def test_bitwidth_changed_detected(self):
        # lt_speed: 2-bit → 3-bit in CTRL_LT0.
        # The classifier may emit BITWIDTH_CHANGED or MULTI_CHANGED (bitwidth + comment)
        changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
        bw = [
            c for c in changes
            if c.field_name == "lt_speed" and (
                c.change_type == ChangeType.BITWIDTH_CHANGED
                or (c.change_type == ChangeType.MULTI_CHANGED
                    and ChangeType.BITWIDTH_CHANGED in c.details)
            )
        ]
        assert bw, (
            f"Expected BITWIDTH_CHANGED (or MULTI_CHANGED with BITWIDTH_CHANGED detail) for "
            f"lt_speed; got: {[(c.change_type, c.details) for c in changes if c.field_name == 'lt_speed']}"
        )

    @pytest.mark.skipif(not V1.exists() or not V2.exists(),
                        reason="Fixture SFR files not found")
    def test_field_added_detected(self):
        # preset_hint added to CTRL_LT0 in v2
        changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
        added = [c for c in changes
                 if c.change_type == ChangeType.FIELD_ADDED and c.field_name == "preset_hint"]
        assert added, "Expected FIELD_ADDED for preset_hint"

    @pytest.mark.skipif(not V1.exists() or not V2.exists(),
                        reason="Fixture SFR files not found")
    def test_field_renamed_detected(self):
        # err_type → err_code in ERR_INJECT
        changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
        renamed = [c for c in changes if c.change_type == ChangeType.FIELD_RENAMED]
        names = {c.field_name for c in renamed}
        # Either old or new name can be reported depending on analyzer direction
        assert "err_type" in names or "err_code" in names, \
            f"Expected FIELD_RENAMED for err_type/err_code, got: {names}"

    @pytest.mark.skipif(not V1.exists() or not V2.exists(),
                        reason="Fixture SFR files not found")
    def test_status_register_unchanged(self):
        # STATUS register is identical in v1 and v2
        changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
        status_changes = [c for c in changes if c.reg_name == "STATUS"
                          and c.change_type != ChangeType.UNCHANGED]
        assert len(status_changes) == 0, \
            f"STATUS should be unchanged, got: {[(c.change_type, c.field_name) for c in status_changes]}"


# ============================================================================
# 4. TestStructFieldName  (2 tests)
# ============================================================================
class TestStructFieldName:

    def test_struct_field_name_populated_from_fixture(self):
        ir = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        ctrl = ir.registers["CTRL_LT0"]
        # IP struct member: SFR_PCIELINK_CTRL_LT0 stCTRL_LT0;
        assert ctrl.struct_field_name == "stCTRL_LT0"

    def test_struct_field_name_default_is_empty_for_volatile(self):
        # VolatileUnionSfrParser doesn't parse IP struct, so struct_field_name stays ""
        ir = VolatileUnionSfrParser(ip="TST").parse_text(_MINIMAL_VOLATILE_SFR, source="<test>")
        if ir.registers:
            reg = next(iter(ir.registers.values()))
            assert reg.struct_field_name == ""
