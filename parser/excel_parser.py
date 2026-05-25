"""
Parses an IP-XACT register description stored as an Excel workbook.

Sheet layout  (one sheet = one peripheral):
  - Sheet name  → peripheral name  (e.g. "CXL_CTRL")
  - Row 1       → column headers   (case-insensitive, any order)
  - Rows 2+     → one field per row; Register Name / Offset cells may be
                  left blank if the field belongs to the same register as
                  the previous row (carry-forward logic).

Recognised column aliases (case-insensitive):
  register name, register offset, field name, msb, lsb, access,
  reset value, description, debug, base address
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl

from models import Field, Register, RegisterMap

# ---------------------------------------------------------------------------
# Column alias table
# ---------------------------------------------------------------------------
_ALIASES: Dict[str, List[str]] = {
    "register_name":   ["register name", "reg name", "regname", "register"],
    "register_offset": ["register offset", "reg offset", "offset",
                        "reg addr", "address", "reg address"],
    "field_name":      ["field name", "fieldname", "field", "bit field",
                        "bitfield", "bit name"],
    "msb":             ["msb", "bit msb", "high bit", "bit high",
                        "end bit", "bit end", "bit[hi]"],
    "lsb":             ["lsb", "bit lsb", "low bit", "bit low",
                        "start bit", "bit start", "bit[lo]"],
    "access":          ["access", "access type", "access mode", "type",
                        "read/write"],
    "reset_value":     ["reset value", "reset", "default", "default value",
                        "reset val", "por value"],
    "description":     ["description", "desc", "comment", "function",
                        "remarks", "detail"],
    "debug":           ["debug", "is debug", "debug reg", "skip",
                        "debug register"],
    "base_address":    ["base address", "base addr", "base",
                        "peripheral base", "periph base"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _map_columns(headers: List[str]) -> Dict[str, int]:
    """Map column header → canonical key (returns only recognised columns)."""
    mapping: Dict[str, int] = {}
    for idx, h in enumerate(headers):
        norm = _norm(h) if h else ""
        for canonical, aliases in _ALIASES.items():
            if norm in aliases:
                mapping[canonical] = idx
                break
    return mapping


def _parse_int(value) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("", "n/a", "-", "none"):
        return None
    try:
        return int(s, 0)        # handles 0x… and decimal
    except ValueError:
        return None


def _parse_bool(value) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in ("true", "yes", "1", "y", "x", "✓", "debug")


def _parse_access(value) -> str:
    if value is None:
        return "RW"
    s = str(value).strip().upper()
    return s or "RW"


# ---------------------------------------------------------------------------
# Single-sheet parser
# ---------------------------------------------------------------------------
def _parse_sheet(ws, default_base: int) -> RegisterMap:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("empty sheet")

    headers = [str(c).strip() if c is not None else "" for c in rows[0]]
    col = _map_columns(headers)

    required = {"register_name", "register_offset", "field_name", "msb", "lsb"}
    missing = required - col.keys()
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. Found: {headers}"
        )

    registers: Dict[str, Register] = {}
    reg_order: List[str] = []

    # carry-forward state
    last_reg_name: Optional[str] = None
    last_reg_offset: Optional[int] = None
    last_reg_desc: str = ""
    last_reg_debug: bool = False
    effective_base: int = default_base

    def get(row, key):
        idx = col.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    for row in rows[1:]:
        # ── register-level cells (carry forward when blank) ──
        rn_raw = get(row, "register_name")
        if rn_raw is not None and str(rn_raw).strip():
            last_reg_name = str(rn_raw).strip().upper()
            last_reg_offset = _parse_int(get(row, "register_offset"))
            last_reg_debug = _parse_bool(get(row, "debug"))
            last_reg_desc = str(get(row, "description") or "").strip()
            b = _parse_int(get(row, "base_address"))
            if b is not None:
                effective_base = b

        if last_reg_name is None or last_reg_offset is None:
            continue

        # ── field-level cells ──────────────────────────────────────────
        fn_raw = get(row, "field_name")
        if fn_raw is None or not str(fn_raw).strip():
            continue

        field_name = str(fn_raw).strip().upper()
        msb = _parse_int(get(row, "msb"))
        lsb = _parse_int(get(row, "lsb"))
        if msb is None or lsb is None:
            continue

        fld = Field(
            name=field_name,
            msb=msb,
            lsb=lsb,
            access=_parse_access(get(row, "access")),
            reset_value=_parse_int(get(row, "reset_value")) or 0,
            description=str(get(row, "description") or "").strip(),
            is_debug=_parse_bool(get(row, "debug")),
        )

        if last_reg_name not in registers:
            reg = Register(
                name=last_reg_name,
                offset=last_reg_offset,
                description=last_reg_desc,
                is_debug=last_reg_debug,
            )
            registers[last_reg_name] = reg
            reg_order.append(last_reg_name)
        registers[last_reg_name].fields.append(fld)

    return RegisterMap(
        peripheral=ws.title.strip(),
        base_addr=effective_base,
        registers=[registers[n] for n in reg_order],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_excel(
    path: str | Path,
    base_addr: int = 0,
    skip_debug: bool = True,
) -> List[RegisterMap]:
    """
    Parse an Excel workbook where each sheet represents one peripheral.

    Args:
        path:       Path to the .xlsx file.
        base_addr:  Fallback base address if not specified in the sheet.
        skip_debug: If True, omit registers/fields flagged as Debug.

    Returns:
        List of RegisterMap (one per valid sheet).
    """
    path = Path(path)
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    maps: List[RegisterMap] = []

    for ws in wb.worksheets:
        try:
            rm = _parse_sheet(ws, base_addr)
            rm.source_file = str(path)
            if skip_debug:
                rm.registers = [r for r in rm.registers if not r.is_debug]
                for r in rm.registers:
                    r.fields = [f for f in r.fields if not f.is_debug]
            # drop registers that became empty after field filtering
            rm.registers = [r for r in rm.registers if r.fields]
            maps.append(rm)
        except ValueError as exc:
            print(f"[WARN] Skipping sheet '{ws.title}': {exc}")

    wb.close()
    return maps
