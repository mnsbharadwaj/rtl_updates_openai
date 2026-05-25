"""
Creates a sample IPXACT Excel workbook for testing.

Peripheral sheets:
  1. CXL_CTRL  — CXL Controller registers  (mixed RW/RO/WO/W1C, with/without descriptions)
  2. CXL_LINK  — CXL Link registers         (RO status, W1C error flags, debug regs)

Run: python tests/create_sample_excel.py
Produces: tests/sample_regs.xlsx  and  tests/sample_regs_v2.xlsx (modified version for update tests)
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

HEADERS = [
    "Register Name", "Register Offset", "Base Address", "Field Name",
    "MSB", "LSB", "Access", "Reset Value", "Description", "Debug",
]

# Base addresses for each peripheral
_CXL_CTRL_BASE = "0x40000000"
_CXL_LINK_BASE = "0x40001000"

# Each row: (reg_name, reg_offset, base_addr, field_name, msb, lsb, access, reset, description, debug)
# base_addr is only filled in the very FIRST field row of the sheet;
# all other rows leave it as "" so the carry-forward reads it once.
CXL_CTRL_ROWS: List[Tuple] = [
    # CTRL_REG — enable/disable/mode control
    ("CTRL_REG", "0x0000", _CXL_CTRL_BASE, "EN",       0,  0, "RW",  "0x0",
     "Enables the CXL controller. Write 1 to activate link training state machine.", False),
    ("",         "",       "",              "MODE",     2,  1, "RW",  "0x0",
     "Operating mode: 0=CXL1, 1=CXL2, 2=CXL3, 3=reserved.", False),
    ("",         "",       "",              "RESET",    3,  3, "WO",  "0x0",
     "Write 1 to issue a soft reset to the controller.", False),
    ("",         "",       "",              "RSVD",    31,  4, "RO",  "0x0",
     "", False),

    # STATUS_REG — read-only status
    ("STATUS_REG", "0x0004", "", "LINK_UP",   0,  0, "RO",  "0x0",
     "Indicates CXL link is up and trained.", False),
    ("",           "",       "", "SPEED",     2,  1, "RO",  "0x0",
     "Current link speed: 0=Gen1, 1=Gen2, 2=Gen3.", False),
    ("",           "",       "", "WIDTH",     5,  3, "RO",  "0x0",
     "Negotiated link width (x1=0, x2=1, x4=2, x8=3, x16=4).", False),
    ("",           "",       "", "RSVD",     31,  6, "RO",  "0x0", "", False),

    # INT_ENABLE_REG — interrupt enables
    ("INT_ENABLE_REG", "0x0008", "", "LINK_ERR_EN",  0,  0, "RW",  "0x0",
     "Enable interrupt on link error detection.", False),
    ("",              "",       "", "TIMEOUT_EN",   1,  1, "RW",  "0x0",
     "Enable interrupt on training timeout.", False),
    ("",              "",       "", "RSVD",        31,  2, "RO",  "0x0", "", False),

    # INT_STATUS_REG — W1C interrupt status
    ("INT_STATUS_REG", "0x000C", "", "LINK_ERR",  0,  0, "W1C", "0x0",
     "Link error occurred. Write 1 to clear.", False),
    ("",              "",       "", "TIMEOUT",   1,  1, "W1C", "0x0",
     "Training timeout. Write 1 to clear.", False),
    ("",              "",       "", "RSVD",     31,  2, "RO",  "0x0", "", False),

    # TIMEOUT_REG — configurable timeout
    ("TIMEOUT_REG", "0x0010", "", "TIMEOUT_VAL",  15,  0, "RW",  "0x0064",
     "Training timeout value in microseconds (default 100us).", False),
    ("",            "",       "", "RSVD",         31, 16, "RO",  "0x0", "", False),

    # DEBUG_REG — should be skipped
    ("DEBUG_REG", "0x0FF0", "", "DBG_BUS",  7,  0, "RW",  "0x0",
     "Debug bus selector — internal use only.", True),
    ("",          "",       "", "DBG_EN",   8,  8, "RW",  "0x0",
     "Enables debug mux output.", True),
]

CXL_LINK_ROWS: List[Tuple] = [
    # LTSSM_STATE
    ("LTSSM_STATE", "0x0000", _CXL_LINK_BASE, "STATE",   5,  0, "RO",  "0x0",
     "Current LTSSM state (see CXL spec Table 8-1).", False),
    ("",            "",       "",              "SUBSTATE", 9,  6, "RO",  "0x0",
     "LTSSM sub-state within current state.", False),
    ("",            "",       "",              "RSVD",    31, 10, "RO",  "0x0", "", False),

    # LANE_STATUS
    ("LANE_STATUS", "0x0004", "", "RX_LOCK", 15,  0, "RO",  "0x0",
     "Bitmask of lanes with CDR lock (bit N = lane N).", False),
    ("",            "",       "", "RSVD",    31, 16, "RO",  "0x0", "", False),

    # EQ_CTRL — no description on TX/RX_PRESET → getter/setter only
    ("EQ_CTRL", "0x0008", "", "TX_PRESET",  3,  0, "RW",  "0x8", "", False),
    ("",        "",       "", "RX_PRESET",  7,  4, "RW",  "0x8", "", False),
    ("",        "",       "", "EQ_EN",      8,  8, "RW",  "0x1",
     "Enable PCIe equalization algorithm.", False),

    # ERR_LOG — W1C
    ("ERR_LOG", "0x000C", "", "DECODE_ERR",  0,  0, "W1C", "0x0",
     "TLP decode error detected on the receive path.", False),
    ("",        "",       "", "FRAMING_ERR", 1,  1, "W1C", "0x0",
     "DLLP framing error. Write 1 to clear.", False),
    ("",        "",       "", "RSVD",        31,  2, "RO",  "0x0", "", False),

    # DEBUG_LANE — should be skipped
    ("DEBUG_LANE", "0x0FF4", "", "PRBS_SEL",  3,  0, "RW",  "0x0",
     "PRBS pattern selector for lane debug.", True),
]


def _style_headers(ws) -> None:
    fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")


def _write_sheet(wb: openpyxl.Workbook, title: str, rows: List[Tuple]) -> None:
    ws = wb.create_sheet(title=title)
    ws.append(HEADERS)
    _style_headers(ws)

    # Each row tuple: (reg_name, reg_offset, base_addr, field_name, msb, lsb,
    #                  access, reset, description, debug)
    # base_addr is "" for all rows except the first of each sheet.
    for row in rows:
        ws.append(list(row))

    # Column widths: RegName, RegOffset, BaseAddr, FieldName, MSB, LSB,
    #                Access, Reset, Description, Debug
    widths = [18, 14, 14, 18, 6, 6, 8, 12, 55, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def create_sample(path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove default empty sheet
    _write_sheet(wb, "CXL_CTRL", CXL_CTRL_ROWS)
    _write_sheet(wb, "CXL_LINK", CXL_LINK_ROWS)
    wb.save(str(path))
    print(f"Created: {path}")


# ── V2 variant: simulate IPXACT update ──────────────────────────────────────
# Changes from v1:
#   CXL_CTRL / CTRL_REG / MODE description updated
#   CXL_CTRL / TIMEOUT_REG / TIMEOUT_VAL reset value changed 0x64 → 0xC8
#   CXL_LINK / EQ_CTRL / EQ_EN description added (was empty)
CXL_CTRL_ROWS_V2 = list(CXL_CTRL_ROWS)
# MODE: index 1 — update description (tuple has base_addr at index 2)
CXL_CTRL_ROWS_V2[1] = (
    "", "", "", "MODE", 2, 1, "RW", "0x0",
    "Operating mode: 0=CXL1.1, 1=CXL2.0, 2=CXL3.0, 3=CXL3.1 (updated in rev B).", False
)
# TIMEOUT_VAL: find by searching
_tv_idx = next(
    i for i, r in enumerate(CXL_CTRL_ROWS)
    if r[0] == "TIMEOUT_REG" and r[3] == "TIMEOUT_VAL"
)
CXL_CTRL_ROWS_V2[_tv_idx] = (
    "TIMEOUT_REG", "0x0010", "", "TIMEOUT_VAL", 15, 0, "RW", "0x00C8",
    "Training timeout value in microseconds (default changed to 200us in rev B).", False
)

# EQ_EN: search by field_name (index 3); reg_name may be blank due to carry-forward
_eq_en_idx = next(
    i for i, r in enumerate(CXL_LINK_ROWS)
    if r[3] == "EQ_EN"
)
CXL_LINK_ROWS_V2 = list(CXL_LINK_ROWS)
CXL_LINK_ROWS_V2[_eq_en_idx] = (
    "", "", "", "EQ_EN", 8, 8, "RW", "0x1",
    "Enable PCIe Gen3/4 equalization algorithm. Must be set before LTSSM Polling.", False
)


def create_sample_v2(path: Path) -> None:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "CXL_CTRL", CXL_CTRL_ROWS_V2)
    _write_sheet(wb, "CXL_LINK", CXL_LINK_ROWS_V2)
    wb.save(str(path))
    print(f"Created: {path}")


if __name__ == "__main__":
    out = Path(__file__).parent
    create_sample(out / "sample_regs.xlsx")
    create_sample_v2(out / "sample_regs_v2.xlsx")
