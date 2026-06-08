"""
sfr_diff_analyzer.py — SFR Header Parser + 12-Type Change Classifier

Parses two sfr.h files into an Intermediate Representation (IR) and
classifies every change into one of 12 precisely defined types in
strict priority order.

SFR comment format (single source of truth):
    /* FIELDNAME [MSB:LSB] ACCESS — description */
    #define PERIPH_REG_FIELDNAME_MASK   0x000000XXU
    #define PERIPH_REG_FIELDNAME_SHIFT  XU

Change types (priority order — first match wins):
    1.  REG_RENAMED       same byte offset, different name token
    2.  REG_DELETED       OFFSET define gone from new sfr.h
    3.  REG_ADDED         new OFFSET define appears
    4.  FIELD_RENAMED     same mask+shift, different name token
    5.  FIELD_DELETED     MASK define gone from new sfr.h
    6.  FIELD_ADDED       new MASK define appears
    7.  BITWIDTH_CHANGED  popcount(old_mask) ≠ popcount(new_mask)
    8.  ACCESS_CHANGED    ACCESS token changed in comment
    9.  OFFSET_CHANGED    field shift (lsb) changed
    10. RESET_CHANGED     reset value changed
    11. COMMENT_CHANGED   description text changed
    12. MULTI_CHANGED     two or more of the above in same field
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Change type constants
# ---------------------------------------------------------------------------
class ChangeType:
    # ── Register-level (Types 1–3, 15, 19–21) ─────────────────────────────
    REG_RENAMED           = "REG_RENAMED"
    REG_DELETED           = "REG_DELETED"
    REG_ADDED             = "REG_ADDED"
    REG_MOVED             = "REG_MOVED"            # same name, offset changed
    REG_SIZE_CHANGED      = "REG_SIZE_CHANGED"     # 8/16/32/64-bit width change
    REG_ARRAY_CHANGED     = "REG_ARRAY_CHANGED"    # scalar ↔ array (e.g. CH_CON[8])
    REG_CLUSTER_CHANGED   = "REG_CLUSTER_CHANGED"  # IP restructuring into groups

    # ── Field existence (Types 4–6, 13–14, 24) ────────────────────────────
    FIELD_RENAMED            = "FIELD_RENAMED"
    FIELD_DELETED            = "FIELD_DELETED"
    FIELD_ADDED              = "FIELD_ADDED"
    FIELD_SPLIT              = "FIELD_SPLIT"           # 1 field → 2+ (same bits)
    FIELD_MERGED             = "FIELD_MERGED"          # 2+ fields → 1 (same bits)
    FIELD_MOVED_CROSS_REG    = "FIELD_MOVED_CROSS_REG" # field jumps to other register

    # ── Field attributes (Types 7–12, 16–18) ──────────────────────────────
    BITWIDTH_CHANGED      = "BITWIDTH_CHANGED"
    ACCESS_CHANGED        = "ACCESS_CHANGED"
    OFFSET_CHANGED        = "OFFSET_CHANGED"
    RESET_CHANGED         = "RESET_CHANGED"
    COMMENT_CHANGED       = "COMMENT_CHANGED"
    MULTI_CHANGED         = "MULTI_CHANGED"
    RESERVED_PROMOTED     = "RESERVED_PROMOTED"    # full RSVD range → named field
    FIELD_POLARITY_CHANGED = "FIELD_POLARITY_CHANGED"  # access+reset+desc all flip
    DESCRIPTION_ADDED     = "DESCRIPTION_ADDED"    # empty desc → non-empty

    # ── Field semantics / behavior (Types 22–27) ───────────────────────────
    FIELD_ENUM_CHANGED    = "FIELD_ENUM_CHANGED"   # value mapping changed
    FIELD_WRITE_ONCE      = "FIELD_WRITE_ONCE"     # gains lock / RWL access
    WRITE_MASK_CHANGED    = "WRITE_MASK_CHANGED"   # partial sub-field goes RO
    FIELD_SELF_CLEARING   = "FIELD_SELF_CLEARING"  # SC / auto-reset / pulse
    FIELD_STICKY_CHANGED  = "FIELD_STICKY_CHANGED" # RO → W1C sticky
    RESERVED_PARTIAL_ACTIVATED = "RESERVED_PARTIAL_ACTIVATED"  # some RSVD bits → active

    # ── Special ────────────────────────────────────────────────────────────
    UNCHANGED             = "UNCHANGED"
    MANUAL_REVIEW         = "MANUAL_REVIEW"  # cannot auto-patch safely

    # LLM routing sets
    LLM_REQUIRED = {
        COMMENT_CHANGED, MULTI_CHANGED, FIELD_POLARITY_CHANGED,
        FIELD_ENUM_CHANGED, FIELD_WRITE_ONCE, FIELD_SELF_CLEARING,
        FIELD_STICKY_CHANGED, DESCRIPTION_ADDED,
        FIELD_SPLIT, FIELD_MERGED,
    }
    LLM_IF_DESC  = {REG_ADDED, FIELD_ADDED, RESERVED_PROMOTED, RESERVED_PARTIAL_ACTIVATED}

    # Change types that must NOT be auto-patched — always flag for manual review
    MANUAL_REVIEW_REQUIRED = {
        REG_DELETED, REG_ADDED,
        REG_ARRAY_CHANGED, REG_CLUSTER_CHANGED,
        WRITE_MASK_CHANGED,
    }

    @classmethod
    def needs_llm(cls, change_type: str, has_desc: bool = False) -> bool:
        if change_type in cls.LLM_REQUIRED:
            return True
        if change_type in cls.LLM_IF_DESC and has_desc:
            return True
        return False

    @classmethod
    def is_manual_review(cls, change_type: str) -> bool:
        """Return True if this change type cannot be auto-patched."""
        return change_type in cls.MANUAL_REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Intermediate Representation (IR)
# ---------------------------------------------------------------------------
@dataclass
class FieldIR:
    """IR for a single register field."""
    name:       str
    reg_name:   str
    mask:       int
    shift:      int        # lsb
    msb:        int
    lsb:        int
    access:     str        # RO / RW / WO / W1C / W1S
    reset:      int
    desc:       str
    ip:         str = ""   # peripheral / IP name

    @property
    def width(self) -> int:
        return self.msb - self.lsb + 1

    @property
    def popcount(self) -> int:
        return bin(self.mask).count("1")

    @property
    def has_desc(self) -> bool:
        return bool(self.desc.strip())

    @property
    def return_type(self) -> str:
        """C return type based on bit width."""
        w = self.width
        if w <= 8:   return "uint8_t"
        if w <= 16:  return "uint16_t"
        if w <= 32:  return "uint32_t"
        return "uint64_t"

    @property
    def sha16(self) -> str:
        raw = (
            f"{self.reg_name}|{self.name}|{self.mask}|{self.shift}"
            f"|{self.access}|{self.reset}|{self.desc}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class RegisterIR:
    """IR for a hardware register."""
    name:        str
    offset:      int        # byte offset
    desc:        str
    fields:      Dict[str, FieldIR] = field(default_factory=dict)
    ip:          str = ""

    @property
    def word_index(self) -> int:
        return self.offset // 4

    @property
    def sha16(self) -> str:
        raw = (
            f"{self.name}|{self.offset}|{self.desc}|"
            + "|".join(f.sha16 for f in sorted(self.fields.values(), key=lambda x: x.name))
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class SfrIR:
    """Complete SFR Intermediate Representation for one header file."""
    ip:        str
    source:    str
    registers: Dict[str, RegisterIR] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Change record
# ---------------------------------------------------------------------------
@dataclass
class ChangeRecord:
    change_type: str
    reg_name:    str
    field_name:  Optional[str] = None
    old_field:   Optional[FieldIR] = None
    new_field:   Optional[FieldIR] = None
    old_reg:     Optional[RegisterIR] = None
    new_reg:     Optional[RegisterIR] = None
    needs_llm:   bool = False
    details:     List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict (for --classify-out)."""
        def _field_dict(f: Optional[FieldIR]) -> Optional[dict]:
            if f is None:
                return None
            return {
                "name": f.name, "reg_name": f.reg_name,
                "mask": f"0x{f.mask:08X}U", "shift": f.shift,
                "msb": f.msb, "lsb": f.lsb,
                "access": f.access, "reset": f"0x{f.reset:X}",
                "desc": f.desc, "sha16": f.sha16,
                "return_type": f.return_type,
            }
        def _reg_dict(r: Optional[RegisterIR]) -> Optional[dict]:
            if r is None:
                return None
            return {"name": r.name, "offset": r.offset, "desc": r.desc, "sha16": r.sha16}

        return {
            "change_type": self.change_type,
            "reg_name":    self.reg_name,
            "field_name":  self.field_name,
            "old_field":   _field_dict(self.old_field),
            "new_field":   _field_dict(self.new_field),
            "old_reg":     _reg_dict(self.old_reg),
            "new_reg":     _reg_dict(self.new_reg),
            "needs_llm":   self.needs_llm,
            "details":     self.details,
        }

# ---------------------------------------------------------------------------
# SFR header parser
# ---------------------------------------------------------------------------

# ── Regex patterns for #define MASK/SHIFT format ───────────────────────────
_DEFINE_RE  = re.compile(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+U?|0x[0-9A-Fa-f]+|[0-9]+U?)")
_OFFSET_RE  = re.compile(r"#define\s+(\w+)_OFFSET\s+(0x[0-9A-Fa-f]+U?|[0-9]+)")
_FIELD_CMT  = re.compile(
    r"/\*\s*(\w+)\s+\[(\d+):(\d+)\]\s+(\w+)\s*(?:[\u2014\u2013]|-{1,3})\s*(.*?)\s*\*/"
)
_MASK_RE    = re.compile(r"#define\s+(\w+)_MASK\s+(0x[0-9A-Fa-f]+U?)")
_SHIFT_RE   = re.compile(r"#define\s+(\w+)_SHIFT\s+(\d+)U?")
_RESET_CMT  = re.compile(r"reset\s*[=:]\s*(0x[0-9A-Fa-f]+|\d+)", re.I)

# ── Regex patterns for typedef volatile union bitfield format ───────────────
# typedef volatile union _SFR_PMU_PMU_CON_U
_UNION_TYPEDEF_RE = re.compile(
    r"typedef\s+volatile\s+union\s+(\w+)"
)
# } SFR_PMU_PMU_CON, *pSFR_PMU_PMU_CON;
_UNION_END_RE = re.compile(
    r"^\s*}\s*(\w+)\s*,\s*\*(\w+)\s*;"
)
# volatile UINT32 nValue _VALUE_(0x10000000);
_UNION_RESET_RE = re.compile(
    r"_VALUE_\s*\(\s*(0x[0-9A-Fa-f]+|\d+)\s*\)"
)
# volatile UINT32 FIELD_NAME : 3;  // lsb-msb [ACCESS] description
_UNION_FIELD_RE = re.compile(
    r"volatile\s+\w+\s+(\w+)\s*:\s*(\d+)\s*;"
    r"(?:\s*//\s*(.*))?"
)
# Comment tail: 0-0 [RW1S] description   OR   // 2-2 [RW] desc
_FIELD_COMMENT_RE = re.compile(
    r"(\d+)\s*[-–]\s*(\d+)\s*"
    r"\[\s*([A-Za-z0-9]+)\s*\]"
    r"\s*(.*)"
)
# Access alias map  (union files sometimes use RW1S, RW1C etc.)
_ACCESS_MAP = {
    "RW":   "RW",  "RO":   "RO",  "WO":   "WO",
    "W1C":  "W1C", "W1S":  "W1S",
    "RW1C": "W1C", "RW1S": "W1S",  # Samsung aliases
    "RW1":  "W1C", "WC":   "W1C",
    # ── Behavioral access modifiers ────────────────────────────────────────
    "RWL":  "RWL",  # Write-once lock (one-time programmable / OTP)
    "SC":   "SC",   # Self-clearing (pulse / auto-reset to 0 after write)
    "SELFCLR": "SC", "SELF_CLR": "SC", "SELF-CLR": "SC",
}


def _parse_int(s: str) -> int:
    """Parse hex or decimal integer from define value string."""
    s = s.rstrip("U").rstrip("u")
    return int(s, 16) if s.startswith("0x") or s.startswith("0X") else int(s)


def _ip_from_filename(path: str | Path) -> str:
    """
    Auto-detect IP name from SFR filename convention.

    Examples:
        sfr_pmu.h      -> PMU
        sfr_uart.h     -> UART
        SFR_DMA.h      -> DMA
        pmu_sfr.h      -> PMU
        sfr_pmu_old.h  -> PMU   (strips _old / _new version suffixes)
        sfr_pmu_new.h  -> PMU
        sfr_pmu_v2.h   -> PMU
    """
    stem = Path(path).stem.upper()          # e.g. SFR_PMU or SFR_PMU_OLD
    # Strip leading SFR_ token
    for prefix in ("SFR_",):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    # Strip trailing SFR token
    for suffix in ("_SFR",):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    # Strip common version suffixes: _OLD, _NEW, _V1, _V2, _BACKUP, _BAK
    _VERSION_SUFFIXES = (
        "_OLD", "_NEW", "_V1", "_V2", "_V3", "_V4",
        "_BACKUP", "_BAK", "_PREV", "_UPDATED", "_ORIG",
    )
    for vs in _VERSION_SUFFIXES:
        if stem.endswith(vs):
            stem = stem[: -len(vs)]
            break
    return stem or "IP"


def _detect_sfr_format(text: str) -> str:
    """
    Return 'union' if the file uses typedef volatile union bitfields,
    otherwise return 'define' for the #define MASK/SHIFT format.
    """
    if _UNION_TYPEDEF_RE.search(text):
        return "union"
    return "define"



class UnionSfrParser:
    """
    Parses Samsung-style typedef volatile union bitfield SFR headers.

    Format example (sfr_pmu.h)::

        typedef volatile union _SFR_PMU_PMU_CON_U
        {
            volatile UINT32 nValue _VALUE_(0x10000000);
            struct
            {
                volatile UINT32 PMU_DMA_CON        : 1; // 0-0  [RW1S] Enable DMA
                volatile UINT32 PMU_DMA_PASS       : 1; // 4-4  [RO]   DMA result
                volatile UINT32 RSVD               : 30; // reserved
            } stNative;
        } SFR_PMU_PMU_CON, *pSFR_PMU_PMU_CON;

    Extraction rules:
        - IP name    : auto-detected from filename  (sfr_pmu.h → PMU)
        - Reg name   : typedef end name strip SFR_ + IP prefix
                       SFR_PMU_PMU_CON → PMU_CON
        - Reset value: _VALUE_(0xXXXXXXXX)
        - Reg offset : order of typedef in file × 4  (or extracted if present)
        - Field name : struct member name
        - Bit width  : :N  from struct member
        - Shift/mask : accumulated bit position as fields are walked in order
        - Access/desc: parsed from trailing // lsb-msb [ACCESS] description comment
    """

    def __init__(self, ip: str = ""):
        self.ip = ip.upper()

    def parse_file(self, path: str | Path) -> SfrIR:
        if not self.ip:
            self.ip = _ip_from_filename(path)
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        return self.parse_text(text, source=str(path))

    def parse_text(self, text: str, source: str = "<string>") -> SfrIR:
        ir = SfrIR(ip=self.ip, source=source)
        lines = text.splitlines()

        in_union   = False
        in_struct  = False
        brace_depth = 0
        current_reg: Optional[RegisterIR] = None
        bit_pos     = 0          # running bit accumulator for shift/mask
        reset_val   = 0
        reg_offset  = 0          # auto-incremented per register (×4 bytes)

        i = 0
        while i < len(lines):
            ln = lines[i]

            # ── Detect start of a union typedef ────────────────────────────
            m = _UNION_TYPEDEF_RE.search(ln)
            if m and not in_union:
                in_union    = True
                in_struct   = False
                brace_depth = 0
                bit_pos     = 0
                reset_val   = 0
                # Placeholder reg — name filled when we hit the closing typedef
                current_reg = RegisterIR(
                    name="__PENDING__",
                    offset=reg_offset,
                    desc="",
                    ip=self.ip,
                )
                i += 1
                continue

            if not in_union:
                i += 1
                continue

            # ── Track braces ────────────────────────────────────────────────
            brace_depth += ln.count("{") - ln.count("}")

            # ── Reset value from _VALUE_(...) ──────────────────────────────
            mv = _UNION_RESET_RE.search(ln)
            if mv:
                try:
                    reset_val = _parse_int(mv.group(1))
                except ValueError:
                    pass

            # ── Detect entry into inner struct ─────────────────────────────
            # Handles both:  struct {   and   struct\n    {
            stripped = ln.strip()
            if stripped.startswith("struct"):
                in_struct = True
                i += 1
                continue
            # Also handle bare '{' when in_struct starts after struct keyword
            if stripped == "{" and not in_struct and brace_depth == 2:
                in_struct = True
                i += 1
                continue

            # ── Parse bitfield members inside struct ───────────────────────
            if in_struct and current_reg is not None:
                mf = _UNION_FIELD_RE.search(ln)
                if mf:
                    fname     = mf.group(1)
                    width     = int(mf.group(2))
                    comment   = (mf.group(3) or "").strip()

                    # Parse comment: "0-0 [RW1S] Enable DMA transfer"
                    mc = _FIELD_COMMENT_RE.search(comment)
                    if mc:
                        lsb_c  = int(mc.group(1))
                        msb_c  = int(mc.group(2))
                        access_raw = mc.group(3).upper()
                        desc   = mc.group(4).strip()
                        access = _ACCESS_MAP.get(access_raw, "RW")
                        # Use comment-derived lsb as authoritative shift
                        shift  = lsb_c
                        msb    = msb_c
                        lsb    = lsb_c
                    else:
                        # No structured comment — derive from bit accumulator
                        shift  = bit_pos
                        lsb    = bit_pos
                        msb    = bit_pos + width - 1
                        access = "RW"
                        desc   = comment

                    mask = ((1 << width) - 1) << shift

                    # Skip reserved/padding fields
                    is_rsvd = fname.upper().startswith("RSVD") or \
                              fname.upper().startswith("RESERVED") or \
                              fname.upper() == "RSVDB" or \
                              fname.upper().startswith("RSVD")

                    if not is_rsvd:
                        f_ir = FieldIR(
                            name=fname, reg_name=current_reg.name,
                            mask=mask, shift=shift,
                            msb=msb, lsb=lsb,
                            access=access, reset=(reset_val >> shift) & ((1 << width) - 1),
                            desc=desc, ip=self.ip,
                        )
                        current_reg.fields[fname] = f_ir

                    bit_pos += width

            # ── Detect closing typedef line: } SFR_PMU_PMU_CON, *pSFR...;
            me = _UNION_END_RE.search(ln)
            if me and in_union:
                typedef_name = me.group(1)   # e.g. SFR_PMU_PMU_CON
                reg_name     = self._extract_reg_name(typedef_name)
                if current_reg is not None and reg_name:
                    current_reg.name = reg_name
                    # Fix reg_name in all fields
                    for f in current_reg.fields.values():
                        f.reg_name = reg_name
                    ir.registers[reg_name] = current_reg
                    reg_offset += 4   # next register at next word

                in_union   = False
                in_struct  = False
                current_reg = None

            i += 1

        return ir

    def _extract_reg_name(self, typedef_name: str) -> str:
        """
        SFR_PMU_PMU_CON  → PMU_CON   (strip SFR_ then IP prefix)
        SFR_PMU_SWT_CON  → SWT_CON
        """
        name = typedef_name.upper()
        # Strip SFR_ prefix
        if name.startswith("SFR_"):
            name = name[4:]
        # Strip IP prefix (e.g. PMU_)
        prefix = self.ip + "_"
        if name.startswith(prefix):
            name = name[len(prefix):]
        return name



class SfrParser:
    """
    Unified SFR header parser — auto-detects file format.

    Supports two formats:
        1. ``#define`` MASK/SHIFT format  (legacy / synthesised headers)
        2. ``typedef volatile union`` bitfield format  (Samsung sfr_pmu.h style)

    The IP name is **auto-detected from the filename** when not supplied:
        sfr_pmu.h  → ip = PMU
        sfr_uart.h → ip = UART

    ``--ip`` is therefore **optional** on the CLI.
    """

    def __init__(self, ip: str = ""):
        self.ip = ip.upper()

    def parse_file(self, path: str | Path) -> SfrIR:
        if not self.ip:
            self.ip = _ip_from_filename(path)
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        fmt  = _detect_sfr_format(text)
        if fmt == "union":
            return UnionSfrParser(ip=self.ip).parse_text(text, source=str(path))
        return self.parse_text(text, source=str(path))


    def parse_text(self, text: str, source: str = "<string>") -> SfrIR:
        ir = SfrIR(ip=self.ip, source=source)
        lines = text.splitlines()

        # Step 1: Collect all defines
        defines: Dict[str, int] = {}
        for ln in lines:
            m = _DEFINE_RE.search(ln)
            if m:
                try:
                    defines[m.group(1)] = _parse_int(m.group(2))
                except ValueError:
                    pass

        # Step 2: Find OFFSET defines → registers
        # OFFSET define name format: {PREFIX}_{REGNAME}_OFFSET
        # We strip the peripheral prefix if ip is known.
        for ln in lines:
            m = _OFFSET_RE.search(ln)
            if not m:
                continue
            full_name = m.group(1)   # e.g. DMA_CTRL_OFFSET → prefix=DMA, reg=CTRL
            offset = _parse_int(m.group(2))
            # Extract reg name: last token before _OFFSET after stripping IP prefix
            parts = full_name.split("_")
            # Remove trailing OFFSET (already consumed by regex)
            # The reg name could be multi-word: take everything after ip prefix
            if self.ip and parts and parts[0] == self.ip:
                reg_name = "_".join(parts[1:])
            else:
                reg_name = "_".join(parts)
            if not reg_name:
                continue
            ir.registers[reg_name] = RegisterIR(
                name=reg_name, offset=offset, desc="", ip=self.ip
            )

        # Step 3+4: Walk lines, parse field comments + MASK/SHIFT defines
        prev_field_meta: Optional[Tuple[str, int, int, str, str]] = None  # (name,msb,lsb,access,desc)

        for ln in lines:
            # Detect field comment
            mc = _FIELD_CMT.search(ln)
            if mc:
                fname  = mc.group(1)
                msb    = int(mc.group(2))
                lsb    = int(mc.group(3))
                access = mc.group(4).upper()
                desc   = mc.group(5).strip()
                prev_field_meta = (fname, msb, lsb, access, desc)
                continue

            # Detect MASK define
            mm = _MASK_RE.search(ln)
            if mm:
                define_name = mm.group(1)   # e.g. DMA_CTRL_EN_MASK → need to split
                mask_val    = _parse_int(mm.group(2))

                # Find which register this belongs to
                reg, field_name = self._resolve_reg_field(define_name, ir)
                if reg is None or field_name is None:
                    prev_field_meta = None
                    continue

                # Get shift from the SHIFT define
                # define_name is already without _MASK (regex group(1) captures before _MASK)
                shift_key = define_name + "_SHIFT"  # e.g. DMA_CTRL_BURST_SHIFT
                shift_val = defines.get(shift_key, None)

                # Compute msb/lsb from mask if no comment
                if prev_field_meta and prev_field_meta[0] == field_name:
                    _, msb, lsb, access, desc = prev_field_meta
                    if shift_val is None:
                        shift_val = lsb
                else:
                    # Derive lsb from mask (position of lowest set bit)
                    if mask_val == 0:
                        lsb = 0
                    else:
                        lsb = (mask_val & -mask_val).bit_length() - 1
                    if shift_val is None:
                        shift_val = lsb
                    # Derive msb from mask
                    msb = mask_val.bit_length() - 1
                    access = "RW"
                    desc   = ""

                # Reset value
                reset_key = define_name + "_RESET"  # e.g. DMA_STATUS_LEVEL_RESET
                reset_val = defines.get(reset_key, 0)

                f_ir = FieldIR(
                    name=field_name, reg_name=reg.name,
                    mask=mask_val, shift=shift_val if shift_val is not None else lsb,
                    msb=msb, lsb=lsb,
                    access=access, reset=reset_val,
                    desc=desc, ip=self.ip,
                )
                reg.fields[field_name] = f_ir
                prev_field_meta = None
                continue

        return ir

    def _resolve_reg_field(
        self, define_name: str, ir: SfrIR
    ) -> Tuple[Optional[RegisterIR], Optional[str]]:
        """
        Given a define like DMA_CTRL_EN_MASK, find the RegisterIR (CTRL)
        and field name (EN) by matching known register names.
        """
        parts = define_name.split("_")
        # Strip ip prefix
        if self.ip and parts and parts[0] == self.ip:
            parts = parts[1:]

        # Try progressively longer register name prefixes
        for i in range(1, len(parts)):
            reg_candidate = "_".join(parts[:i])
            if reg_candidate in ir.registers:
                field_candidate = "_".join(parts[i:])
                return ir.registers[reg_candidate], field_candidate or None

        return None, None


# ---------------------------------------------------------------------------
# Change classifier
# ---------------------------------------------------------------------------
class SfrDiffAnalyzer:
    """
    Compares two SfrIR objects and produces a list of ChangeRecord objects,
    classified in strict priority order (first match wins).
    """

    def __init__(self, ip: str = ""):
        self.ip = ip
        self._parser = SfrParser(ip=ip)

    def analyze_files(self, old_path: str | Path, new_path: str | Path) -> List[ChangeRecord]:
        old_ir = self._parser.parse_file(old_path)
        new_ir = self._parser.parse_file(new_path)
        return self.analyze(old_ir, new_ir)

    def analyze_texts(self, old_text: str, new_text: str) -> List[ChangeRecord]:
        old_ir = self._parser.parse_text(old_text, source="<old>")
        new_ir = self._parser.parse_text(new_text, source="<new>")
        return self.analyze(old_ir, new_ir)

    def analyze(self, old_ir: SfrIR, new_ir: SfrIR) -> List[ChangeRecord]:
        changes: List[ChangeRecord] = []
        old_regs = old_ir.registers
        new_regs = new_ir.registers

        # Build offset → name maps for rename detection
        old_by_offset: Dict[int, str] = {r.offset: n for n, r in old_regs.items()}
        new_by_offset: Dict[int, str] = {r.offset: n for n, r in new_regs.items()}

        processed_old: set = set()
        processed_new: set = set()

        # ── Priority 1: REG_RENAMED ─────────────────────────────────────────
        for offset, old_name in old_by_offset.items():
            if offset in new_by_offset:
                new_name = new_by_offset[offset]
                if old_name != new_name and old_name not in new_regs and new_name not in old_regs:
                    changes.append(ChangeRecord(
                        change_type=ChangeType.REG_RENAMED,
                        reg_name=old_name, field_name=None,
                        old_field=None, new_field=None,
                        old_reg=old_regs[old_name], new_reg=new_regs[new_name],
                        needs_llm=False,
                        details=[f"renamed {old_name} -> {new_name}"],
                    ))
                    processed_old.add(old_name)
                    processed_new.add(new_name)

        # ── Priority 2: REG_DELETED ─────────────────────────────────────────
        for name, reg in old_regs.items():
            if name not in new_regs and name not in processed_old:
                changes.append(ChangeRecord(
                    change_type=ChangeType.REG_DELETED,
                    reg_name=name, field_name=None,
                    old_field=None, new_field=None,
                    old_reg=reg, new_reg=None,
                    needs_llm=False,
                    details=[f"register {name} deleted"],
                ))
                processed_old.add(name)

        # ── Priority 3: REG_ADDED ───────────────────────────────────────────
        for name, reg in new_regs.items():
            if name not in old_regs and name not in processed_new:
                has_desc = any(f.has_desc for f in reg.fields.values())
                changes.append(ChangeRecord(
                    change_type=ChangeType.REG_ADDED,
                    reg_name=name, field_name=None,
                    old_field=None, new_field=None,
                    old_reg=None, new_reg=reg,
                    needs_llm=has_desc,
                    details=[f"register {name} added"],
                ))
                processed_new.add(name)

        # ── Common registers: diff fields ───────────────────────────────────
        for reg_name in set(old_regs) & set(new_regs):
            if reg_name in processed_old or reg_name in processed_new:
                continue
            old_reg = old_regs[reg_name]
            new_reg = new_regs[reg_name]
            changes.extend(self._diff_fields(old_reg, new_reg))

        return changes

    def _diff_fields(
        self, old_reg: RegisterIR, new_reg: RegisterIR
    ) -> List[ChangeRecord]:
        changes: List[ChangeRecord] = []
        old_fields = old_reg.fields
        new_fields = new_reg.fields

        # Build mask+shift → name for rename detection
        old_by_sig: Dict[Tuple[int, int], str] = {
            (f.mask, f.shift): n for n, f in old_fields.items()
        }
        new_by_sig: Dict[Tuple[int, int], str] = {
            (f.mask, f.shift): n for n, f in new_fields.items()
        }

        processed_old: set = set()
        processed_new: set = set()

        # Priority 4: FIELD_RENAMED
        for sig, old_name in old_by_sig.items():
            if sig in new_by_sig:
                new_name = new_by_sig[sig]
                if old_name != new_name and old_name not in new_fields and new_name not in old_fields:
                    changes.append(ChangeRecord(
                        change_type=ChangeType.FIELD_RENAMED,
                        reg_name=old_reg.name, field_name=old_name,
                        old_field=old_fields[old_name],
                        new_field=new_fields[new_name],
                        old_reg=old_reg, new_reg=new_reg,
                        needs_llm=False,
                        details=[f"field {old_name} -> {new_name}"],
                    ))
                    processed_old.add(old_name)
                    processed_new.add(new_name)

        # Priority 5: FIELD_DELETED
        for name, fld in old_fields.items():
            if name not in new_fields and name not in processed_old:
                changes.append(ChangeRecord(
                    change_type=ChangeType.FIELD_DELETED,
                    reg_name=old_reg.name, field_name=name,
                    old_field=fld, new_field=None,
                    old_reg=old_reg, new_reg=new_reg,
                    needs_llm=False,
                    details=[f"field {name} deleted from {old_reg.name}"],
                ))
                processed_old.add(name)

        # Priority 6: FIELD_ADDED
        for name, fld in new_fields.items():
            if name not in old_fields and name not in processed_new:
                changes.append(ChangeRecord(
                    change_type=ChangeType.FIELD_ADDED,
                    reg_name=new_reg.name, field_name=name,
                    old_field=None, new_field=fld,
                    old_reg=old_reg, new_reg=new_reg,
                    needs_llm=fld.has_desc,
                    details=[f"field {name} added to {new_reg.name}"],
                ))
                processed_new.add(name)

        # Priority 7–12: Compare common fields
        for name in set(old_fields) & set(new_fields):
            if name in processed_old or name in processed_new:
                continue
            old_f = old_fields[name]
            new_f = new_fields[name]
            if old_f.sha16 == new_f.sha16:
                continue  # unchanged
            changes.extend(self._classify_field_change(old_f, new_f, old_reg, new_reg))

        return changes

    def _classify_field_change(
        self,
        old_f: FieldIR, new_f: FieldIR,
        old_reg: RegisterIR, new_reg: RegisterIR,
    ) -> List[ChangeRecord]:
        """Classify a changed field using priority 7–28."""
        diffs: List[str] = []

        bitwidth_changed  = old_f.popcount    != new_f.popcount
        access_changed    = old_f.access      != new_f.access
        offset_changed    = old_f.shift       != new_f.shift
        reset_changed     = old_f.reset       != new_f.reset
        comment_changed   = old_f.desc.strip() != new_f.desc.strip()
        desc_added        = (not old_f.has_desc) and new_f.has_desc

        # ── Semantic / behavioral detection ───────────────────────────────
        # Type 17: FIELD_POLARITY_CHANGED — access + reset + comment all changed
        polarity_changed = access_changed and reset_changed and comment_changed

        # Type 23: FIELD_WRITE_ONCE — new access type contains lock indicator
        _write_once_markers = {"RWL", "W1", "OTP", "LOCK"}
        write_once_gained = (
            new_f.access.upper() in _write_once_markers
            or "write-once" in new_f.desc.lower()
            or "write once" in new_f.desc.lower()
            or "otp" in new_f.desc.lower()
        ) and not (
            old_f.access.upper() in _write_once_markers
            or "write-once" in old_f.desc.lower()
        )

        # Type 26: FIELD_SELF_CLEARING
        _sc_markers = {"SC", "SELFCLR", "SELF_CLR", "SELF-CLR"}
        self_clearing_gained = (
            new_f.access.upper() in _sc_markers
            or "self-clearing" in new_f.desc.lower()
            or "self clearing" in new_f.desc.lower()
            or "auto-reset" in new_f.desc.lower()
            or "pulse" in new_f.desc.lower()
        ) and new_f.access.upper() not in ("RW", "RO", "WO", "W1C", "W1S")

        # Type 27: FIELD_STICKY_CHANGED — RO → W1C transition
        sticky_changed = (
            old_f.access.upper() == "RO"
            and new_f.access.upper() == "W1C"
        )

        # Type 22: FIELD_ENUM_CHANGED — detect value mapping patterns
        _ENUM_RE = re.compile(r'\d+\s*=\s*\w+')
        old_enums = set(_ENUM_RE.findall(old_f.desc))
        new_enums = set(_ENUM_RE.findall(new_f.desc))
        enum_changed = bool(old_enums or new_enums) and (old_enums != new_enums)

        # ── Single-change classification ───────────────────────────────────
        # Priority: more-specific checks first

        if polarity_changed:
            return [ChangeRecord(
                change_type=ChangeType.FIELD_POLARITY_CHANGED,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=["access", "reset", "desc all changed (polarity flip)"],
            )]

        if write_once_gained:
            return [ChangeRecord(
                change_type=ChangeType.FIELD_WRITE_ONCE,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=[f"field gained write-once/lock: {new_f.access}"],
            )]

        if self_clearing_gained:
            return [ChangeRecord(
                change_type=ChangeType.FIELD_SELF_CLEARING,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=[f"field became self-clearing: {new_f.access}"],
            )]

        if sticky_changed:
            return [ChangeRecord(
                change_type=ChangeType.FIELD_STICKY_CHANGED,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=["RO → W1C sticky transition"],
            )]

        if desc_added and not (bitwidth_changed or access_changed or offset_changed or reset_changed):
            return [ChangeRecord(
                change_type=ChangeType.DESCRIPTION_ADDED,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=[f"description added: {new_f.desc[:60]}"],
            )]

        if enum_changed and comment_changed and not (bitwidth_changed or access_changed or offset_changed):
            return [ChangeRecord(
                change_type=ChangeType.FIELD_ENUM_CHANGED,
                reg_name=old_reg.name, field_name=old_f.name,
                old_field=old_f, new_field=new_f,
                old_reg=old_reg, new_reg=new_reg,
                needs_llm=True,
                details=[f"enum values changed: {old_enums} → {new_enums}"],
            )]

        # ── Standard priority 7-12 classification ─────────────────────────
        if bitwidth_changed: diffs.append("BITWIDTH_CHANGED")
        if access_changed:   diffs.append("ACCESS_CHANGED")
        if offset_changed:   diffs.append("OFFSET_CHANGED")
        if reset_changed:    diffs.append("RESET_CHANGED")
        if comment_changed:  diffs.append("COMMENT_CHANGED")

        # MULTI_CHANGED if two or more non-priority changes
        if len(diffs) > 1:
            change_type = ChangeType.MULTI_CHANGED
            needs_llm   = True
        elif diffs:
            change_type = diffs[0]
            needs_llm   = ChangeType.needs_llm(change_type, new_f.has_desc)
        else:
            # mask or msb changed without bitwidth change (same popcount, different position)
            change_type = ChangeType.BITWIDTH_CHANGED
            diffs       = ["mask_position_changed"]
            needs_llm   = False

        return [ChangeRecord(
            change_type=change_type,
            reg_name=old_reg.name, field_name=old_f.name,
            old_field=old_f, new_field=new_f,
            old_reg=old_reg, new_reg=new_reg,
            needs_llm=needs_llm,
            details=diffs,
        )]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def classify_sfr_diff(
    old_sfr: str | Path,
    new_sfr: str | Path,
    ip: str = "",
) -> List[ChangeRecord]:
    """
    Convenience function: parse two sfr.h files and return a classified
    list of ChangeRecord objects.

    Args:
        old_sfr: Path to previous sfr.h  (e.g. sfr_pmu_old.h)
        new_sfr: Path to updated sfr.h   (e.g. sfr_pmu_new.h)
        ip:      IP/peripheral name (e.g. "PMU").  **Optional** — auto-detected
                 from filename when omitted.  sfr_pmu.h → PMU, sfr_uart.h → UART.

    Returns:
        List[ChangeRecord] sorted by register name, then field name.
    """
    # Auto-detect IP from filename if not provided
    if not ip:
        ip = _ip_from_filename(old_sfr)

    analyzer = SfrDiffAnalyzer(ip=ip)
    changes  = analyzer.analyze_files(old_sfr, new_sfr)
    changes.sort(key=lambda c: (c.reg_name, c.field_name or ""))
    return changes



def changes_to_json(changes: List[ChangeRecord], indent: int = 2) -> str:
    return json.dumps([c.to_dict() for c in changes], indent=indent)


def summarize_changes(changes: List[ChangeRecord]) -> str:
    from collections import Counter
    counts = Counter(c.change_type for c in changes)
    llm_count = sum(1 for c in changes if c.needs_llm)
    lines = ["SFR Diff Summary:", f"  Total changes : {len(changes)}"]
    for ct, n in sorted(counts.items()):
        lines.append(f"  {ct:<20}: {n}")
    lines.append(f"  LLM calls needed: {llm_count}")
    return "\n".join(lines)
