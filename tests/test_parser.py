"""
Tests for the Excel IPXACT parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from parser.excel_parser import parse_excel
from models import Field, Register, RegisterMap

SAMPLE = Path(__file__).parent / "sample_regs.xlsx"


@pytest.fixture(scope="module", autouse=True)
def ensure_sample():
    if not SAMPLE.exists():
        from .create_sample_excel import create_sample
        create_sample(SAMPLE)


@pytest.fixture(scope="module")
def maps():
    # Pass base_addr=0 so any non-zero value must have come from the sheet
    return parse_excel(SAMPLE, base_addr=0, skip_debug=True)


# ── Sheet detection ──────────────────────────────────────────────────────────
class TestSheetDetection:
    def test_two_peripherals(self, maps):
        assert len(maps) == 2

    def test_peripheral_names(self, maps):
        names = {m.peripheral for m in maps}
        assert names == {"CXL_CTRL", "CXL_LINK"}

    def test_base_addr_from_sheet_ctrl(self, maps):
        """CXL_CTRL base address must come from the Excel sheet (0x40000000)."""
        ctrl = next(m for m in maps if m.peripheral == "CXL_CTRL")
        assert ctrl.base_addr == 0x40000000

    def test_base_addr_from_sheet_link(self, maps):
        """CXL_LINK base address must come from the Excel sheet (0x40001000)."""
        link = next(m for m in maps if m.peripheral == "CXL_LINK")
        assert link.base_addr == 0x40001000

    def test_cli_base_addr_is_fallback_only(self):
        """If the sheet has Base Address, --base CLI value is ignored."""
        # Parse with a different CLI base — sheet value should still win
        maps_alt = parse_excel(SAMPLE, base_addr=0xDEAD0000, skip_debug=True)
        ctrl = next(m for m in maps_alt if m.peripheral == "CXL_CTRL")
        assert ctrl.base_addr == 0x40000000  # sheet wins

    def test_fallback_base_used_when_sheet_has_no_base_column(self, tmp_path):
        """A sheet WITHOUT a Base Address column uses the CLI fallback."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "NO_BASE"
        # Headers without Base Address
        ws.append(["Register Name", "Register Offset", "Field Name",
                   "MSB", "LSB", "Access", "Reset Value", "Description"])
        ws.append(["CTRL", "0x0", "EN", 0, 0, "RW", "0x0", ""])
        path = tmp_path / "no_base.xlsx"
        wb.save(str(path))
        result = parse_excel(path, base_addr=0xCAFE0000)
        assert result[0].base_addr == 0xCAFE0000


# ── CXL_CTRL sheet ──────────────────────────────────────────────────────────
class TestCXLCtrl:
    @pytest.fixture
    def ctrl(self, maps):
        return next(m for m in maps if m.peripheral == "CXL_CTRL")

    def test_debug_registers_excluded(self, ctrl):
        names = {r.name for r in ctrl.registers}
        assert "DEBUG_REG" not in names

    def test_register_count(self, ctrl):
        # CTRL_REG, STATUS_REG, INT_ENABLE_REG, INT_STATUS_REG, TIMEOUT_REG
        assert len(ctrl.registers) == 5

    def test_ctrl_reg_offset(self, ctrl):
        reg = ctrl.get_register("CTRL_REG")
        assert reg is not None
        assert reg.offset == 0x0000

    def test_ctrl_reg_fields(self, ctrl):
        reg = ctrl.get_register("CTRL_REG")
        field_names = {f.name for f in reg.fields}
        assert "EN" in field_names
        assert "MODE" in field_names
        assert "RESET" in field_names

    def test_field_access_types(self, ctrl):
        reg = ctrl.get_register("CTRL_REG")
        fmap = {f.name: f for f in reg.fields}
        assert fmap["EN"].access   == "RW"
        assert fmap["RESET"].access == "WO"

    def test_rsvd_field_present(self, ctrl):
        reg = ctrl.get_register("CTRL_REG")
        names = {f.name for f in reg.fields}
        assert "RSVD" in names

    def test_w1c_field(self, ctrl):
        reg = ctrl.get_register("INT_STATUS_REG")
        fmap = {f.name: f for f in reg.fields}
        assert fmap["LINK_ERR"].access == "W1C"
        assert fmap["LINK_ERR"].is_write_one_clear()

    def test_reset_value_parsed(self, ctrl):
        reg = ctrl.get_register("TIMEOUT_REG")
        fmap = {f.name: f for f in reg.fields}
        assert fmap["TIMEOUT_VAL"].reset_value == 0x64

    def test_description_present(self, ctrl):
        reg = ctrl.get_register("CTRL_REG")
        fmap = {f.name: f for f in reg.fields}
        assert fmap["EN"].has_description
        assert not fmap["RSVD"].has_description


# ── CXL_LINK sheet ───────────────────────────────────────────────────────────
class TestCXLLink:
    @pytest.fixture
    def link(self, maps):
        return next(m for m in maps if m.peripheral == "CXL_LINK")

    def test_debug_lane_excluded(self, link):
        names = {r.name for r in link.registers}
        assert "DEBUG_LANE" not in names

    def test_eq_ctrl_no_description_fields(self, link):
        reg = link.get_register("EQ_CTRL")
        fmap = {f.name: f for f in reg.fields}
        assert not fmap["TX_PRESET"].has_description
        assert not fmap["RX_PRESET"].has_description

    def test_err_log_w1c(self, link):
        reg = link.get_register("ERR_LOG")
        fmap = {f.name: f for f in reg.fields}
        assert fmap["DECODE_ERR"].is_write_one_clear()


# ── Mask / shift calculation ─────────────────────────────────────────────────
class TestFieldMath:
    def test_single_bit_mask(self, maps):
        ctrl = next(m for m in maps if m.peripheral == "CXL_CTRL")
        reg = ctrl.get_register("CTRL_REG")
        en = next(f for f in reg.fields if f.name == "EN")
        assert en.mask  == 0x00000001
        assert en.shift == 0
        assert en.width == 1

    def test_multi_bit_mask(self, maps):
        ctrl = next(m for m in maps if m.peripheral == "CXL_CTRL")
        reg = ctrl.get_register("CTRL_REG")
        mode = next(f for f in reg.fields if f.name == "MODE")
        # bits [2:1] → mask = 0b110 = 0x6
        assert mode.mask  == 0x00000006
        assert mode.shift == 1
        assert mode.width == 2

    def test_can_read_ro(self, maps):
        ctrl = next(m for m in maps if m.peripheral == "CXL_CTRL")
        reg = ctrl.get_register("STATUS_REG")
        link_up = next(f for f in reg.fields if f.name == "LINK_UP")
        assert link_up.can_read()
        assert not link_up.can_write()

    def test_can_write_wo(self, maps):
        ctrl = next(m for m in maps if m.peripheral == "CXL_CTRL")
        reg = ctrl.get_register("CTRL_REG")
        reset_f = next(f for f in reg.fields if f.name == "RESET")
        assert reset_f.can_write()
        assert not reset_f.can_read()
