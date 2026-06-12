"""
lld_patcher.py — Surgical LLD Header Patcher (Struct-Based Architecture)

Generates and patches LLD functions that use a typed struct pointer parameter
instead of raw `volatile uint32_t *base`.

Function signature convention:
    uint8_t  lld_pmu_status_con_thresh_get(struct lld_pmu *lld)
    void     lld_pmu_status_con_thresh_set(struct lld_pmu *lld, uint8_t val)

Bitfield access via SFR aggregate struct:
    lld->pSFR->stSTATUS_CON.stNative.THRESH

SFR Aggregate Struct (auto-generated in lld header):
    typedef volatile struct _SFR_PMU_S {
        SFR_PMU_PMU_CTRL    stPMU_CTRL;
        SFR_PMU_STATUS_CON  stSTATUS_CON;
        ...
    } SFR_PMU, *pSFR_PMU;

    struct lld_pmu { pSFR_PMU pSFR; };

Key rules:
    1. Function NAMES are FROZEN — never change on REG_RENAMED
       Only the struct member path inside the body is updated.
    2. REG_DELETED → DEPRECATE (add comment, keep functions, list in PR)
    3. FIELD_RENAMED / BITWIDTH / OFFSET → smart body patch only
       (replace stNative.OLD_FIELD with stNative.NEW_FIELD)
    4. ACCESS_CHANGED → add/remove setter as needed
    5. COMMENT_CHANGED → update doxygen /** @brief ... */ only
    6. Existing base[] functions are auto-migrated to struct style on first run
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lld_gen.sfr_diff_analyzer import (
    ChangeRecord, ChangeType, FieldIR, RegisterIR, SfrIR,
)
from lld_gen.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SRC_SHA anchor patterns
# ---------------------------------------------------------------------------
_SHA_BLOCK_BEGIN = re.compile(
    r"/\*\s*[═=]{10,}\s*\n"
    r"\s*\*\s*REGISTER:\s*(\w+)"
    r"[^\n]*\n"
    r"(?:[^\n]*\n)*?"
    r"\s*\*\s*SRC_SHA:\s*([0-9a-f]+)"
    r"[^\n]*\n"
    r"\s*\*\s*[═=]{10,}\s*\*/",
    re.DOTALL
)

_STATIC_INLINE_RE = re.compile(
    r"(/\*\*.*?\*/\s*)?"
    r"(?:static\s+)?(?:inline\s+)?\S+(?:\s+\S+)?\s+(\w+)\s*\([^)]*\)"
    r"\s*\{",
    re.DOTALL
)

_RETTYPE_RE = re.compile(r"\b(uint8_t|uint16_t|uint32_t|uint64_t)\b")

# Markers for the generated struct block
_STRUCT_BEGIN_MARKER = "/* === BEGIN LLD_{ip}_STRUCTS ==="
_STRUCT_END_MARKER   = "/* === END LLD_{ip}_STRUCTS === */"


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------
def _fn_name(ip: str, reg: str, field: str, verb: str) -> str:
    """Build LLD function name: lld_ip_reg_field_verb (all lowercase).
    NOTE: Function names are FROZEN — reg name changes do NOT rename functions."""
    return f"lld_{ip.lower()}_{reg.lower()}_{field.lower()}_{verb.lower()}"


def _struct_member(reg_name: str) -> str:
    """Convert register name to aggregate struct member: STATUS_CON -> stSTATUS_CON"""
    return f"st{reg_name}"


def _field_path(ip: str, reg_name: str, field_name: str) -> str:
    """Full path to a bitfield via struct: lld->pSFR->stREG.stNative.FIELD"""
    return f"lld->pSFR->{_struct_member(reg_name)}.stNative.{field_name}"


def _word_idx(reg_offset: int) -> int:
    return reg_offset // 4


# ---------------------------------------------------------------------------
# AST-based C function extractor
# ---------------------------------------------------------------------------
def extract_function(text: str, fn_name: str) -> Optional[str]:
    """
    Extract a complete function body from C source text using brace counting.
    Returns full function text including preceding doxygen comment, or None.
    """
    pattern = re.compile(
        r"(/\*\*(?:(?!\b(?:static\s+)?(?:inline\s+)?\b\w+\s+\w+\s*\().)*?\*/\s*)?"
        r"(?:static\s+)?(?:inline\s+)?\S+(?:\s+\S+)?\s+"
        + re.escape(fn_name)
        + r"\s*\([^)]*\)\s*\{",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return None

    start     = m.start()
    brace_pos = m.end() - 1

    depth  = 0
    i      = brace_pos
    in_str = False
    prev   = ""
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == '"' and prev != "\\": in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        prev = ch
        i   += 1
    return None


def extract_all_functions_for_field(text: str, ip: str, reg: str, field: str) -> str:
    """Extract all static inline functions for lld_ip_reg_field_* pattern."""
    prefix = f"lld_{ip.lower()}_{reg.lower()}_{field.lower()}_"
    parts  = []
    for m in _STATIC_INLINE_RE.finditer(text):
        fn_name = m.group(2)
        if fn_name.startswith(prefix):
            fn_text = extract_function(text, fn_name)
            if fn_text:
                parts.append(fn_text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Struct-based template code generators
# ---------------------------------------------------------------------------
def _getter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "get")
    rt   = field.return_type
    desc = field.desc or f"Get {field.name} field"
    path = _field_path(ip, reg, field.name)
    return (
        f"/** @brief {desc} */\n"
        f"static inline {rt} {fn}(struct lld_{ip.lower()} *lld)\n"
        f"{{\n"
        f"    return ({rt})({path});\n"
        f"}}"
    )


def _setter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set")
    rt   = field.return_type
    desc = field.desc or f"Set {field.name} field"
    path = _field_path(ip, reg, field.name)
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(struct lld_{ip.lower()} *lld, {rt} val)\n"
        f"{{\n"
        f"    {path} = val;\n"
        f"}}"
    )


def _wo_setter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set")
    rt   = field.return_type
    desc = field.desc or f"Write {field.name} (WO)"
    path = _field_path(ip, reg, field.name)
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(struct lld_{ip.lower()} *lld, {rt} val)\n"
        f"{{\n"
        f"    {path} = val;\n"
        f"}}"
    )


def _w1c_clear(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "clear")
    desc = field.desc or f"Clear {field.name} (W1C)"
    path = _field_path(ip, reg, field.name)
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(struct lld_{ip.lower()} *lld)\n"
        f"{{\n"
        f"    {path} = 1U; /* W1C: write 1 to clear */\n"
        f"}}"
    )


def _w1s_set1(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set1")
    desc = field.desc or f"Set1 {field.name} (W1S)"
    path = _field_path(ip, reg, field.name)
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(struct lld_{ip.lower()} *lld)\n"
        f"{{\n"
        f"    {path} = 1U; /* W1S: write 1 to set */\n"
        f"}}"
    )


def _irq_helpers(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    """Four mandatory IRQ helpers for interrupt-capable fields."""
    lp   = f"struct lld_{ip.lower()} *lld"
    p    = f"lld_{ip.lower()}_{reg.lower()}_{field.name.lower()}_irq"
    path = _field_path(ip, reg, field.name)
    return (
        f"static inline void {p}_enable({lp})  {{ {path} = 1U; }}\n"
        f"static inline void {p}_disable({lp}) {{ {path} = 0U; }}\n"
        f"static inline uint32_t {p}_status({lp}) {{ return (uint32_t)({path}); }}\n"
        f"static inline void {p}_clear({lp})   {{ {path} = 1U; }} /* W1C */"
    )


def _is_irq_field(field: FieldIR) -> bool:
    return "irq" in field.name.lower() or "interrupt" in (field.desc or "").lower()


def generate_field_functions(
    ip: str, reg_name: str, field: FieldIR, reg_offset: int
) -> str:
    """Generate complete struct-based function set for a field based on access type."""
    parts: List[str] = []
    access = field.access.upper()

    if access in {"RO", "RW", "W1C", "W1S"}:
        parts.append(_getter(ip, reg_name, field, reg_offset))
    if access == "RW":
        parts.append(_setter(ip, reg_name, field, reg_offset))
    elif access == "WO":
        parts.append(_wo_setter(ip, reg_name, field, reg_offset))
    elif access == "W1C":
        parts.append(_w1c_clear(ip, reg_name, field, reg_offset))
    elif access == "W1S":
        parts.append(_w1s_set1(ip, reg_name, field, reg_offset))

    if _is_irq_field(field):
        parts.append(_irq_helpers(ip, reg_name, field, reg_offset))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# SFR aggregate struct + LLD driver struct generator
# ---------------------------------------------------------------------------
def generate_lld_structs(ip: str, new_ir: SfrIR) -> str:
    """
    Generate the SFR aggregate typedef and lld driver struct for the top of lld_*.h.

    Example output:
        /* === BEGIN LLD_PMU_STRUCTS === */
        typedef volatile struct _SFR_PMU_S {
            SFR_PMU_PMU_CTRL   stPMU_CTRL;   /* offset 0x0000 */
            ...
        } SFR_PMU, *pSFR_PMU;

        struct lld_pmu { pSFR_PMU pSFR; };
        /* === END LLD_PMU_STRUCTS === */
    """
    ip_up = ip.upper()
    ip_lo = ip.lower()

    # Sort registers by offset
    regs = sorted(new_ir.registers.values(), key=lambda r: r.offset)

    lines = [
        f"/* === BEGIN LLD_{ip_up}_STRUCTS === */",
        f"/* SFR Aggregate Struct (auto-generated from new SFR — DO NOT EDIT) */",
        f"typedef volatile struct _SFR_{ip_up}_S",
        f"{{",
    ]
    for reg in regs:
        type_name   = f"SFR_{ip_up}_{reg.name}"
        member_name = _struct_member(reg.name)
        lines.append(
            f"    {type_name:<36} {member_name};  /* offset 0x{reg.offset:04X} */"
        )
    lines += [
        f"}} SFR_{ip_up}, *pSFR_{ip_up};",
        f"",
        f"/* LLD Driver Struct */",
        f"struct lld_{ip_lo} {{",
        f"    pSFR_{ip_up} pSFR;  /* pointer to hardware register block */",
        f"}};",
        f"/* === END LLD_{ip_up}_STRUCTS === */",
    ]
    return "\n".join(lines)


def _remove_struct_section(content: str, ip: str) -> str:
    """Remove existing LLD_IP_STRUCTS section from content completely if present."""
    ip_up = ip.upper()
    begin = f"/* === BEGIN LLD_{ip_up}_STRUCTS ==="
    end   = f"/* === END LLD_{ip_up}_STRUCTS === */"

    b_pos = content.find(begin)
    e_pos = content.find(end)

    if b_pos != -1 and e_pos != -1:
        # Strip the section completely (including leading/trailing whitespace around it)
        before = content[:b_pos]
        after = content[e_pos + len(end):]
        # Clean up double newlines that might be left behind
        return before.rstrip() + "\n\n" + after.lstrip()
    return content


# ---------------------------------------------------------------------------
# Block migration (base[] → struct-based)
# ---------------------------------------------------------------------------
def _is_struct_based(block: str) -> bool:
    """Return True if block already uses struct lld_* pointer style."""
    return "lld->pSFR->" in block or "struct lld_" in block


def _migrate_block_to_struct(
    ip: str, block: str, reg_name: str, reg_ir: RegisterIR
) -> str:
    """
    Migrate an old base[]-style register block to struct-based.
    Regenerates each field's functions from the FieldIR data.
    """
    result = block
    for fname in sorted(reg_ir.fields):
        fld = reg_ir.fields[fname]
        # Remove the old function(s) for this field
        result = _remove_field_functions_raw(result, ip, reg_name, fname)
        # Regenerate with new struct-based template
        new_fns = generate_field_functions(ip, reg_name, fld, reg_ir.offset)
        if new_fns.strip():
            result = result.rstrip() + "\n\n" + new_fns + "\n"
    return result


def _remove_field_functions_raw(block: str, ip: str, reg: str, field: str) -> str:
    """Remove all static inline functions for lld_ip_reg_field_* from block."""
    prefix = f"lld_{ip.lower()}_{reg.lower()}_{field.lower()}_"

    to_remove: List[Tuple[int, int]] = []
    for m in _STATIC_INLINE_RE.finditer(block):
        fn_name = m.group(2)
        if not fn_name.startswith(prefix):
            continue
        fn_start = m.start()
        before   = block[:fn_start]
        doc_m    = re.search(r"/\*\*[^*]*(?:\*(?!/)[^*]*)*\*/\s*$", before, re.DOTALL)
        if doc_m:
            fn_start = fn_start - (len(before) - doc_m.start())
        try:
            brace_pos = block.index("{", m.start())
        except ValueError:
            continue
        depth = 0
        i     = brace_pos
        while i < len(block):
            ch = block[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    to_remove.append((fn_start, i + 1))
                    break
            i += 1

    result = block
    for start, end in sorted(to_remove, reverse=True):
        tail_end = end
        while tail_end < len(result) and result[tail_end] in "\n\r":
            tail_end += 1
        result = result[:start] + result[tail_end:]
    return result


# ---------------------------------------------------------------------------
# Smart body patchers
# ---------------------------------------------------------------------------
def _patch_struct_member_ref(block: str, old_reg: str, new_reg: str) -> str:
    """
    Replace ->stOLD_REG. with ->stNEW_REG. in function bodies.
    Used for REG_RENAMED — function names are NOT changed.
    """
    old_tok = f"->st{old_reg}."
    new_tok = f"->st{new_reg}."
    return block.replace(old_tok, new_tok)


def _patch_field_name_in_body(
    block: str, reg_name: str, old_field: str, new_field: str
) -> str:
    """
    Replace stNative.OLD_FIELD with stNative.NEW_FIELD inside function bodies.
    Used for FIELD_RENAMED — function names are NOT changed.
    """
    pattern = re.compile(
        r'(->st' + re.escape(reg_name) + r'\.stNative\.)' + re.escape(old_field) + r'\b'
    )
    return pattern.sub(r'\g<1>' + new_field, block)


def _patch_doxygen_desc(block: str, old_desc: str, new_desc: str) -> str:
    """Replace @brief description text in doxygen comments."""
    if old_desc and new_desc and old_desc != new_desc:
        # Try exact match first
        block = block.replace(f"@brief {old_desc}", f"@brief {new_desc}")
    return block


def _deprecate_block(block: str, reg_name: str) -> Tuple[str, List[str]]:
    """
    Mark a deleted register's block as deprecated.
    Returns (annotated_block, list_of_function_names).
    """
    fn_names = re.findall(r"(?:static\s+)?(?:inline\s+)?\S+(?:\s+\S+)?\s+(lld_\w+)\s*\(", block)
    dep_header = (
        f"\n/* ⚠ DEPRECATED: SFR register {reg_name} was DELETED in the new SFR version.\n"
        f" * The functions below are no longer backed by hardware registers.\n"
        f" * They are kept here to avoid compilation errors in IP emulation files.\n"
        f" * Review the callers and MANUALLY DELETE these functions in a follow-up PR.\n"
        f" * See PR_DESCRIPTION.md for the full list. */\n"
    )
    return dep_header + block, fn_names


# ---------------------------------------------------------------------------
# SRC_SHA helpers
# ---------------------------------------------------------------------------
def _sha16(reg: RegisterIR) -> str:
    return reg.sha16


def _reg_block_header(ip: str, reg: RegisterIR) -> str:
    sha  = _sha16(reg)
    line = "═" * 63
    return (
        f"/* {line}\n"
        f" * REGISTER: {reg.name:<30} offset=0x{reg.offset:04X}\n"
        f" * {reg.desc or ''}\n"
        f" * SRC_SHA: {sha}\n"
        f" * {line} */\n"
    )


def generate_register_block(ip: str, reg: RegisterIR) -> str:
    """Generate complete struct-based LLD block for a register."""
    lines = [_reg_block_header(ip, reg)]
    for fname in sorted(reg.fields):
        fld = reg.fields[fname]
        lines.append(generate_field_functions(ip, reg.name, fld, reg.offset))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unit test generator (struct-based)
# ---------------------------------------------------------------------------
def generate_test_for_field(ip: str, reg_name: str, field: FieldIR, reg_offset: int) -> str:
    """
    Generate struct-based C test functions for one field.

    Uses:
        SFR_{IP} sfr = {{0}};
        struct lld_{ip} lld = {{ .pSFR = &sfr }};
    """
    fn_base    = f"lld_{ip.lower()}_{reg_name.lower()}_{field.name.lower()}"
    ip_up      = ip.upper()
    ip_lo      = ip.lower()
    sm         = _struct_member(reg_name)                       # stSTATUS_CON
    fpath      = f"sfr.{sm}.stNative.{field.name}"             # sfr.stSTATUS_CON.stNative.THRESH
    access     = field.access.upper()
    tests: List[str] = []

    # Common struct init
    sinit = (
        f"    SFR_{ip_up} sfr = {{{{0}}}};\n"
        f"    struct lld_{ip_lo} lld = {{ .pSFR = &sfr }};"
    )

    if access in {"RO", "RW", "W1C", "W1S"}:
        tests.append(
            f"static void test_{fn_base}_get(void) {{\n"
            f"{sinit}\n"
            f"    {fpath} = 1U;\n"
            f"    assert({fn_base}_get(&lld) == 1U);\n"
            f"}}"
        )

    if access == "RW":
        tests.append(
            f"static void test_{fn_base}_set(void) {{\n"
            f"{sinit}\n"
            f"    {fn_base}_set(&lld, 1U);\n"
            f"    assert({fpath} == 1U);\n"
            f"}}"
        )

    if access == "WO":
        tests.append(
            f"static void test_{fn_base}_set(void) {{\n"
            f"{sinit}\n"
            f"    {fn_base}_set(&lld, 1U);\n"
            f"    assert({fpath} != 0U);\n"
            f"}}"
        )

    if access == "W1C":
        tests.append(
            f"static void test_{fn_base}_clear(void) {{\n"
            f"{sinit}\n"
            f"    {fn_base}_clear(&lld);\n"
            f"    assert({fpath} == 1U); /* W1C: must write 1 */\n"
            f"}}"
        )

    if access == "W1S":
        tests.append(
            f"static void test_{fn_base}_set1(void) {{\n"
            f"{sinit}\n"
            f"    {fn_base}_set1(&lld);\n"
            f"    assert({fpath} == 1U);\n"
            f"}}"
        )

    return "\n\n".join(tests)



# ---------------------------------------------------------------------------
# Extra module-level helpers (used by new type handlers)
# ---------------------------------------------------------------------------

def _find_sha_header_end(block: str) -> int:
    """
    Return the character index just after the closing '*/' of the SRC_SHA
    block header comment. Returns 0 if no header found.

    Used by REG_SIZE_CHANGED to preserve the header while regenerating
    all function bodies.
    """
    # Find the closing */ of the first block comment that contains SRC_SHA
    if "SRC_SHA:" not in block:
        return 0
    end = block.find("*/")
    return end + 2 if end != -1 else 0


def _insert_comment_into_setter(block: str, fn_name: str, comment: str) -> str:
    """
    Insert `comment` as the first statement inside the setter function `fn_name`.
    Used by FIELD_WRITE_ONCE to add a write-once warning comment.

    Finds the opening brace of fn_name, then inserts comment on the next line.
    """
    # Locate the setter function
    fn_start = block.find(fn_name)
    if fn_start == -1:
        return block

    # Find the opening brace after fn_name
    brace_pos = block.find("{", fn_start)
    if brace_pos == -1:
        return block

    # Insert comment immediately after the opening brace
    return block[:brace_pos + 1] + "\n" + comment + block[brace_pos + 1:]


# ---------------------------------------------------------------------------
# LLD Patcher
# ---------------------------------------------------------------------------
class LLDPatcher:
    """
    Surgically patches lld.h for a list of ChangeRecord objects.

    New architecture (struct-based):
      1. Regenerate / replace the LLD_IP_STRUCTS section from new_ir
      2. Auto-migrate any base[]-style blocks to struct-based
      3. For each change record → _apply()
      4. Collect deprecated function names (deleted registers)
      5. Write patched file + generate unit tests
    """

    def __init__(
        self,
        ip:              str,
        llm_client:      Optional[LLMClient] = None,
        no_llm:          bool = False,
        verbose_callback = None,
    ):
        """
        verbose_callback — optional callable invoked after each _apply() with:
            (reg_name:str, field_name:str, change_type:str,
             before:str, after:str, used_llm:bool, lld_file:str)
        Use it in BatchRunner to print before/after diffs in verbose mode.
        """
        self.ip              = ip.upper()
        self._llm            = llm_client
        self._no_llm         = no_llm
        self._verbose_cb     = verbose_callback   # (reg, field, ct, before, after, llm, file)
        self._test_stubs: List[str] = []
        self._deprecated_fns: List[str] = []
        self._added_fns: List[str] = []
        self._patched_fns: List[str] = []
        self._removed_fns: List[str] = []
        self._current_lld_file: str = ""          # set by patch() for callback context
        self._llm_used_this_apply: bool = False   # reset before each _apply call

    # ── File split ──────────────────────────────────────────────────────────
    def _split_blocks(
        self, text: str
    ) -> List[Tuple[Optional[str], Optional[str], str]]:
        segments: List[Tuple[Optional[str], Optional[str], str]] = []
        pos = 0
        for m in _SHA_BLOCK_BEGIN.finditer(text):
            if m.start() > pos:
                segments.append((None, None, text[pos:m.start()]))
            reg_name    = m.group(1)
            sha         = m.group(2)
            block_start = m.start()
            next_m      = _SHA_BLOCK_BEGIN.search(text, m.end())
            block_end   = next_m.start() if next_m else len(text)
            segments.append((reg_name, sha, text[block_start:block_end]))
            pos = block_end
        if pos < len(text):
            segments.append((None, None, text[pos:]))
        return segments

    # ── SHA helpers ─────────────────────────────────────────────────────────
    def _update_sha_anchor(self, block: str, new_sha: str) -> str:
        return re.sub(r"SRC_SHA:\s*[0-9a-f]+", f"SRC_SHA: {new_sha}", block)

    # ── Remove field functions ───────────────────────────────────────────────
    def _remove_field_functions(
        self, block: str, ip: str, reg: str, field: str
    ) -> str:
        return _remove_field_functions_raw(block, ip, reg, field)

    # ── ACCESS_CHANGED helpers ───────────────────────────────────────────────
    def _regen_access_template(self, block: str, cr: ChangeRecord) -> str:
        """
        For ACCESS_CHANGED:
          - If now RO: remove setter(s) from block
          - If now RW: add setter if missing
          - If access type changes category (e.g. RW→W1C): regenerate full set
        """
        new_f   = cr.new_field
        old_f   = cr.old_field
        new_reg = cr.new_reg
        if new_f is None or new_reg is None:
            return block

        old_acc = (old_f.access if old_f else "RW").upper()
        new_acc = new_f.access.upper()
        fn_pfx  = f"lld_{self.ip.lower()}_{cr.reg_name.lower()}_{new_f.name.lower()}"

        # Simple RW → RO: remove setter only
        if old_acc == "RW" and new_acc == "RO":
            block = _remove_field_functions_raw(
                block, self.ip, cr.reg_name, new_f.name + "_set_SENTINEL"
            )
            # Remove any function named ..._set
            setter_fn = f"{fn_pfx}_set"
            block     = _remove_field_functions_raw(block, self.ip, cr.reg_name,
                         cr.field_name or "")
            # Re-add only the getter
            new_fns = _getter(self.ip, cr.reg_name, new_f, new_reg.offset)
            block   = block.rstrip() + "\n\n" + new_fns + "\n"
        # Simple RO → RW: add setter
        elif old_acc == "RO" and new_acc == "RW":
            new_fns = _setter(self.ip, cr.reg_name, new_f, new_reg.offset)
            block   = block.rstrip() + "\n\n" + new_fns + "\n"
        else:
            # Category change: regenerate full set
            block = _remove_field_functions_raw(block, self.ip, cr.reg_name,
                                                cr.field_name or "")
            new_fns = generate_field_functions(
                self.ip, cr.reg_name, new_f, new_reg.offset
            )
            block = block.rstrip() + "\n\n" + new_fns + "\n"

        if cr.new_reg:
            block = self._update_sha_anchor(block, cr.new_reg.sha16)
        self._test_stubs.extend(self._make_test(cr))
        return block

    # ── Test stub helpers ────────────────────────────────────────────────────
    def _make_test(self, cr: ChangeRecord) -> List[str]:
        fld = cr.new_field
        reg = cr.new_reg
        if fld is None or reg is None:
            return []
        test = generate_test_for_field(self.ip, cr.reg_name, fld, reg.offset)
        return [test] if test else []

    # ── IRQ helper tests ─────────────────────────────────────────────────────
    @staticmethod
    def _irq_test_stubs(ip: str, reg_name: str, field: FieldIR, reg_offset: int) -> List[str]:
        ip_up   = ip.upper()
        ip_lo   = ip.lower()
        fn_base = f"lld_{ip_lo}_{reg_name.lower()}_{field.name.lower()}_irq"
        sm      = _struct_member(reg_name)
        fpath   = f"sfr.{sm}.stNative.{field.name}"
        sinit   = (
            f"    SFR_{ip_up} sfr = {{{{0}}}};\n"
            f"    struct lld_{ip_lo} lld = {{ .pSFR = &sfr }};"
        )
        return [
            f"static void test_{fn_base}_enable(void) {{\n{sinit}\n"
            f"    {fn_base}_enable(&lld);\n"
            f"    assert({fpath} == 1U);\n}}",
            f"static void test_{fn_base}_disable(void) {{\n{sinit}\n"
            f"    {fpath} = 1U;\n"
            f"    {fn_base}_disable(&lld);\n"
            f"    assert({fpath} == 0U);\n}}",
            f"static void test_{fn_base}_status(void) {{\n{sinit}\n"
            f"    {fpath} = 1U;\n"
            f"    assert({fn_base}_status(&lld) != 0U);\n}}",
            f"static void test_{fn_base}_clear(void) {{\n{sinit}\n"
            f"    {fn_base}_clear(&lld);\n"
            f"    assert({fpath} == 1U);\n}}",
        ]

    # ── LLM body-only helper (signature-locked) ──────────────────────────────
    def _extract_fn_for_llm(self, block: str, field_name: str) -> str:
        """
        Extract only the affected functions from a block, capped at 4000 chars.
        Keeps context window within Qwen2.5-7B limits.
        """
        extracted = extract_all_functions_for_field(
            block, self.ip, self._reg_name_from_block(block), field_name
        )
        if len(extracted) > 4000:
            extracted = extracted[:4000] + "\n/* ... truncated for context window ... */"
        return extracted

    def _reg_name_from_block(self, block: str) -> str:
        """Extract register name from SRC_SHA block header comment."""
        m = re.search(r"REGISTER:\s*(\w+)", block)
        return m.group(1) if m else ""

    def _llm_body_only(self, block: str, cr: ChangeRecord) -> str:
        """
        Signature-locked LLM prompt: LLM returns body code ONLY.
        The caller's function signature (name, params, return type) is NEVER
        sent to or modified by the LLM.

        Prompt strategy:
          - Extract only the affected function(s) (≤4000 chars)
          - Tell LLM: 'You may only modify the /** @brief */ comment
                       and/or function body. Signature is frozen.'
          - Graft returned body onto the original locked signature
        """
        new_f   = cr.new_field
        old_f   = cr.old_field
        new_reg = cr.new_reg
        if new_f is None or new_reg is None:
            return block

        if self._no_llm or self._llm is None:
            return self._template_fallback(block, cr)

        old_code = self._extract_fn_for_llm(block, (old_f or new_f).name)

        try:
            new_code = self._llm.generate(
                ip=self.ip, reg_name=cr.reg_name, reg_offset=new_reg.offset,
                reg_desc=new_reg.desc or "",
                field_name=new_f.name, msb=new_f.msb, lsb=new_f.lsb,
                access=new_f.access, desc=new_f.desc,
                mask=new_f.mask, shift=new_f.shift,
                return_type=new_f.return_type,
                change_type=cr.change_type,
                old_code=old_code,
                functions_needed="body_only_signature_locked",
            )
            self._llm_used_this_apply = True   # ← signal to _apply wrapper
            field_key = old_f.name if old_f else new_f.name
            block = _remove_field_functions_raw(block, self.ip, cr.reg_name, field_key)
            self._test_stubs.extend(self._make_test(cr))
            if cr.new_reg:
                new_code_with_sha = self._update_sha_anchor(
                    block.rstrip() + "\n\n" + new_code + "\n", cr.new_reg.sha16
                )
                return new_code_with_sha
            return block.rstrip() + "\n\n" + new_code + "\n"
        except RuntimeError as exc:
            logger.error("[LLM-ERROR] %s; falling back to template", exc)
            self._test_stubs.extend(self._make_test(cr))
            return self._template_fallback(block, cr)

    def _template_fallback(self, block: str, cr: ChangeRecord) -> str:
        """Non-LLM fallback: apply deterministic patches for semantic changes."""
        old_f = cr.old_field
        new_f = cr.new_field
        ct    = cr.change_type

        if ct in {ChangeType.COMMENT_CHANGED, ChangeType.DESCRIPTION_ADDED,
                  ChangeType.FIELD_ENUM_CHANGED}:
            if old_f and new_f:
                block = _patch_doxygen_desc(block, old_f.desc or "", new_f.desc or "")

        elif ct == ChangeType.MULTI_CHANGED:
            if old_f and new_f:
                if old_f.name != new_f.name:
                    block = _patch_field_name_in_body(block, cr.reg_name, old_f.name, new_f.name)
                block = _patch_doxygen_desc(block, old_f.desc or "", new_f.desc or "")
                if old_f.access != new_f.access:
                    block = self._regen_access_template(block, cr)

        elif ct == ChangeType.FIELD_POLARITY_CHANGED:
            if old_f and new_f:
                block = _patch_doxygen_desc(block, old_f.desc or "", new_f.desc or "")
                if old_f.access != new_f.access:
                    block = self._regen_access_template(block, cr)

        elif ct == ChangeType.FIELD_WRITE_ONCE:
            # Insert write-once warning comment into setter body
            if new_f:
                fn_set = _fn_name(self.ip, cr.reg_name, new_f.name, "set")
                warning = "    /* WARNING: Write-Once field — verify written only once */"
                block = _insert_comment_into_setter(block, fn_set, warning)

        elif ct == ChangeType.FIELD_SELF_CLEARING:
            # Rename _set() to _trigger(), remove _get()
            if old_f and new_f:
                block = _remove_field_functions_raw(block, self.ip, cr.reg_name, old_f.name)
                trigger_fn = (
                    f"/** @brief {new_f.desc or 'Self-clearing trigger (auto-resets after write)'} */\n"
                    f"static inline void lld_{self.ip.lower()}_{cr.reg_name.lower()}"
                    f"_{new_f.name.lower()}_trigger(struct lld_{self.ip.lower()} *lld) {{\n"
                    f"    lld->pSFR->{_struct_member(cr.reg_name)}.stNative.{new_f.name} = 1U;"
                    f" /* SC: self-clearing */\n}}"
                )
                block = block.rstrip() + "\n\n" + trigger_fn + "\n"

        elif ct == ChangeType.FIELD_STICKY_CHANGED:
            # RO -> W1C: keep getter, add _clear()
            if new_f:
                clear_fn = (
                    f"/** @brief Clear (W1C) {new_f.desc or new_f.name} — write 1 to clear */\n"
                    f"static inline void lld_{self.ip.lower()}_{cr.reg_name.lower()}"
                    f"_{new_f.name.lower()}_clear(struct lld_{self.ip.lower()} *lld) {{\n"
                    f"    lld->pSFR->{_struct_member(cr.reg_name)}.stNative.{new_f.name} = 1U;"
                    f" /* W1C: write 1 to clear */\n}}"
                )
                block = block.rstrip() + "\n\n" + clear_fn + "\n"

        if cr.new_reg:
            block = self._update_sha_anchor(block, cr.new_reg.sha16)
        self._test_stubs.extend(self._make_test(cr))
        return block

    # ── Apply one change record ───────────────────────────────────────────────
    def _apply(self, block: str, sha: Optional[str], cr: ChangeRecord) -> str:
        """
        Apply one ChangeRecord to a register block string.
        Handles all 28 change types. Function names are FROZEN.
        """
        ct = cr.change_type

        # Track added / patched / removed functions based on change type
        from lld_gen.change_summary import lld_function_names as _lfns
        if ct == ChangeType.REG_SIZE_CHANGED:
            new_reg = cr.new_reg
            if new_reg:
                for f in new_reg.fields.values():
                    self._patched_fns.extend(_lfns(self.ip, new_reg.name, f.name, f.access))
        elif ct == ChangeType.FIELD_ADDED:
            new_f = cr.new_field
            if new_f:
                self._added_fns.extend(_lfns(self.ip, cr.reg_name, new_f.name, new_f.access))
        elif ct == ChangeType.FIELD_DELETED:
            old_f = cr.old_field
            if old_f:
                self._removed_fns.extend(_lfns(self.ip, cr.reg_name, cr.field_name or "", old_f.access))
        elif ct == ChangeType.FIELD_SPLIT:
            old_f = cr.old_field
            if old_f:
                self._removed_fns.extend(_lfns(self.ip, cr.reg_name, old_f.name, old_f.access))
            new_reg = cr.new_reg
            if new_reg:
                for fname, fobj in new_reg.fields.items():
                    old_reg = cr.old_reg
                    if old_reg and fname in old_reg.fields:
                        continue
                    self._added_fns.extend(_lfns(self.ip, cr.reg_name, fname, fobj.access))
        elif ct == ChangeType.FIELD_MERGED:
            old_reg = cr.old_reg
            if old_reg:
                for fname, fobj in old_reg.fields.items():
                    if cr.new_reg and fname not in cr.new_reg.fields:
                        self._removed_fns.extend(_lfns(self.ip, cr.reg_name, fname, fobj.access))
            new_f = cr.new_field
            if new_f:
                self._added_fns.extend(_lfns(self.ip, cr.reg_name, new_f.name, new_f.access))
        elif ct == ChangeType.FIELD_MOVED_CROSS_REG:
            old_f = cr.old_field
            if old_f:
                self._removed_fns.extend(_lfns(self.ip, cr.reg_name, old_f.name, old_f.access))
        elif ct in {ChangeType.RESERVED_PROMOTED, ChangeType.RESERVED_PARTIAL_ACTIVATED}:
            new_f = cr.new_field
            if new_f:
                self._added_fns.extend(_lfns(self.ip, cr.reg_name, new_f.name, new_f.access))
        elif ct in {
            ChangeType.BITWIDTH_CHANGED, ChangeType.ACCESS_CHANGED, ChangeType.OFFSET_CHANGED,
            ChangeType.RESET_CHANGED, ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED,
            ChangeType.FIELD_POLARITY_CHANGED, ChangeType.DESCRIPTION_ADDED, ChangeType.FIELD_ENUM_CHANGED,
            ChangeType.FIELD_WRITE_ONCE, ChangeType.FIELD_SELF_CLEARING, ChangeType.FIELD_STICKY_CHANGED
        }:
            ref_f = cr.new_field or cr.old_field
            if ref_f:
                self._patched_fns.extend(_lfns(self.ip, cr.reg_name, ref_f.name, ref_f.access))

        # ── Types that must not be auto-patched ──────────────────────────────
        if ChangeType.is_manual_review(ct):
            # Store for PR manual-review section — do not touch block
            label = f"{ct}: {cr.reg_name}" + (f".{cr.field_name}" if cr.field_name else "")
            if label not in getattr(self, "_manual_review", []):
                if not hasattr(self, "_manual_review"):
                    self._manual_review = []
                self._manual_review.append(label)
            return block

        # ── Register-level changes ───────────────────────────────────────────
        if ct == ChangeType.REG_RENAMED:
            old_name = cr.old_reg.name if cr.old_reg else cr.reg_name
            new_name = cr.new_reg.name if cr.new_reg else cr.reg_name
            block = _patch_struct_member_ref(block, old_name, new_name)
            block = block.replace(f"REGISTER: {old_name}", f"REGISTER: {new_name}")
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.REG_MOVED:
            # Struct-based: byte offset is encoded in struct layout, not in fn bodies.
            # Only update SHA anchor.
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.REG_SIZE_CHANGED:
            # Container width changed (8/16/32/64-bit).
            # Regenerate entire register block from new_ir.
            new_reg = cr.new_reg
            if new_reg:
                new_block_fns = "".join(
                    generate_field_functions(self.ip, new_reg.name, f, new_reg.offset)
                    for f in new_reg.fields.values()
                )
                # Preserve the SHA header, replace function bodies
                header_end = _find_sha_header_end(block)
                sha_header = block[:header_end] if header_end else ""
                block = sha_header + "\n" + new_block_fns
                block = self._update_sha_anchor(block, new_reg.sha16)
            return block

        # ── Field existence changes ──────────────────────────────────────────
        if ct == ChangeType.FIELD_RENAMED:
            old_fname = cr.old_field.name if cr.old_field else (cr.field_name or "")
            new_fname = cr.new_field.name if cr.new_field else (cr.field_name or "")
            block = _patch_field_name_in_body(block, cr.reg_name, old_fname, new_fname)
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.FIELD_DELETED:
            return _remove_field_functions_raw(
                block, self.ip, cr.reg_name, cr.field_name or ""
            )

        if ct == ChangeType.FIELD_ADDED:
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f is None or new_reg is None:
                return block
            new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
            self._test_stubs.extend(self._make_test(cr))
            block = block.rstrip() + "\n\n" + new_fns + "\n"
            if cr.needs_llm and not self._no_llm and self._llm is not None:
                block = self._llm_body_only(block, cr)
            return block

        if ct == ChangeType.FIELD_SPLIT:
            # 1 old field → 2+ new fields
            # Remove old merged function, generate split functions.
            old_f = cr.old_field
            if old_f:
                block = _remove_field_functions_raw(block, self.ip, cr.reg_name, old_f.name)
                self._deprecated_fns.append(
                    _fn_name(self.ip, cr.reg_name, old_f.name, "get")
                )
            # cr.details contains list of new field names (populated by analyzer)
            new_reg = cr.new_reg
            if new_reg:
                for fname, fobj in new_reg.fields.items():
                    # Only generate for split sub-fields (those not in old_reg)
                    old_reg = cr.old_reg
                    if old_reg and fname in old_reg.fields:
                        continue  # unchanged field, skip
                    new_fns = generate_field_functions(self.ip, cr.reg_name, fobj, new_reg.offset)
                    block = block.rstrip() + "\n\n" + new_fns + "\n"
                    self._test_stubs.extend(self._make_test(
                        ChangeRecord(change_type=ChangeType.FIELD_ADDED,
                                     reg_name=cr.reg_name, field_name=fname,
                                     old_field=None, new_field=fobj,
                                     old_reg=old_reg, new_reg=new_reg)
                    ))
            if cr.needs_llm and not self._no_llm and self._llm is not None:
                block = self._llm_body_only(block, cr)
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.FIELD_MERGED:
            # 2+ old fields → 1 new field
            old_reg = cr.old_reg
            if old_reg:
                for fname in old_reg.fields:
                    if cr.new_reg and fname not in cr.new_reg.fields:
                        block = _remove_field_functions_raw(block, self.ip, cr.reg_name, fname)
                        self._deprecated_fns.append(
                            _fn_name(self.ip, cr.reg_name, fname, "get")
                        )
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f and new_reg:
                new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
                block = block.rstrip() + "\n\n" + new_fns + "\n"
                self._test_stubs.extend(self._make_test(cr))
                if cr.needs_llm and not self._no_llm and self._llm is not None:
                    block = self._llm_body_only(block, cr)
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.FIELD_MOVED_CROSS_REG:
            # Field jumped to another register.
            # Remove from current (old) register block. New register block
            # will receive FIELD_ADDED when its block is processed.
            old_f = cr.old_field
            if old_f:
                block = _remove_field_functions_raw(block, self.ip, cr.reg_name, old_f.name)
                self._deprecated_fns.append(
                    _fn_name(self.ip, cr.reg_name, old_f.name, "get")
                )
            return block

        if ct in {ChangeType.RESERVED_PROMOTED, ChangeType.RESERVED_PARTIAL_ACTIVATED}:
            # RSVD bits → named field: same as FIELD_ADDED
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f and new_reg:
                new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
                self._test_stubs.extend(self._make_test(cr))
                block = block.rstrip() + "\n\n" + new_fns + "\n"
                if cr.needs_llm and not self._no_llm and self._llm is not None:
                    block = self._llm_body_only(block, cr)
                block = self._update_sha_anchor(block, new_reg.sha16)
            return block

        # ── Field attribute changes ──────────────────────────────────────────
        if ct == ChangeType.BITWIDTH_CHANGED:
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f and new_reg:
                fn_get = _fn_name(self.ip, cr.reg_name, new_f.name, "get")
                lines  = block.splitlines(keepends=True)
                result = []
                for ln in lines:
                    if fn_get in ln:
                        ln = _RETTYPE_RE.sub(new_f.return_type, ln, count=1)
                    result.append(ln)
                block = "".join(result)
                self._test_stubs.extend(self._make_test(cr))
                block = self._update_sha_anchor(block, new_reg.sha16)
            return block

        if ct == ChangeType.ACCESS_CHANGED:
            return self._regen_access_template(block, cr)

        if ct == ChangeType.OFFSET_CHANGED:
            # Struct-based: bit position is in the hardware struct layout.
            # No function body changes needed — only update SHA.
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct == ChangeType.RESET_CHANGED:
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        if ct in {ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED}:
            return self._llm_body_only(block, cr)

        # ── Field semantic / behavior changes (LLM preferred, template fallback)
        if ct in {
            ChangeType.FIELD_POLARITY_CHANGED,
            ChangeType.DESCRIPTION_ADDED,
            ChangeType.FIELD_ENUM_CHANGED,
            ChangeType.FIELD_WRITE_ONCE,
            ChangeType.FIELD_SELF_CLEARING,
            ChangeType.FIELD_STICKY_CHANGED,
        }:
            return self._llm_body_only(block, cr)

        if ct == ChangeType.WRITE_MASK_CHANGED:
            # Cannot auto-patch — partial sub-field RO requires structural split.
            # Flag for manual review, leave block unchanged.
            label = f"WRITE_MASK_CHANGED: {cr.reg_name}.{cr.field_name or ''}"
            if not hasattr(self, "_manual_review"):
                self._manual_review = []
            self._manual_review.append(label)
            return block

        # Unknown type — pass through unchanged
        return block

    # ── Public patch API ─────────────────────────────────────────────────────
    def patch(
        self,
        lld_path: str | Path,
        changes:  List[ChangeRecord],
        new_ir:   Optional[SfrIR] = None,
        out_path: Optional[str | Path] = None,
    ) -> str:
        """
        Patch lld.h with the given changes. Returns the updated content.

        Steps:
          1. Backup original
          2. Update / insert LLD_IP_STRUCTS section from new_ir
          3. Auto-migrate any base[]-style blocks to struct-based
          4. Apply change records per block
          5. Append new REG_ADDED blocks
          6. Mark REG_DELETED blocks as deprecated (do NOT remove)
          7. Brace-balance check
          8. Write output
        """
        lld_path = Path(lld_path)
        out_path = Path(out_path) if out_path else lld_path

        original = lld_path.read_text(encoding="utf-8")
        backup   = lld_path.with_suffix(".h.bak")
        backup.write_text(original, encoding="utf-8")

        self._changes             = changes
        self._test_stubs          = []
        self._deprecated_fns      = []
        self._added_fns           = []
        self._patched_fns         = []
        self._removed_fns         = []
        self._current_lld_file    = str(lld_path)  # for callback context
        self._llm_used_this_apply = False

        # ── Step 1: Remove aggregate struct section if present ─────────────
        content = _remove_struct_section(original, self.ip)

        # ── Step 2: Build per-change lookup ────────────────────────────────
        changes_by_reg: Dict[str, List[ChangeRecord]] = {}
        reg_deletes:    set              = set()
        reg_adds:       List[ChangeRecord] = []

        for cr in changes:
            if cr.change_type == ChangeType.REG_DELETED:
                reg_deletes.add(cr.reg_name)
            elif cr.change_type == ChangeType.REG_ADDED:
                reg_adds.append(cr)
            else:
                changes_by_reg.setdefault(cr.reg_name, []).append(cr)

        # ── Step 3: Split + process blocks ─────────────────────────────────
        segments  = self._split_blocks(content)
        out_parts: List[str] = []
        processed_regs = set()

        for (reg_name, sha, block) in segments:
            if reg_name is None:
                out_parts.append(block)
                continue

            processed_regs.add(reg_name)

            # Auto-migrate base[] → struct-based before applying changes
            if not _is_struct_based(block) and new_ir and reg_name in new_ir.registers:
                logger.info("[MIGRATE] %s -- base[] -> struct-based", reg_name)
                block = _migrate_block_to_struct(
                    self.ip, block, reg_name, new_ir.registers[reg_name]
                )
            elif not _is_struct_based(block) and reg_name in changes_by_reg:
                # Use old reg info for migration if not in new_ir
                first_cr = changes_by_reg[reg_name][0]
                if first_cr.old_reg:
                    logger.info("[MIGRATE] %s -- base[] -> struct-based (old IR)", reg_name)
                    block = _migrate_block_to_struct(
                        self.ip, block, reg_name, first_cr.old_reg
                    )

            # REG_DELETED: deprecate, don't drop
            if reg_name in reg_deletes:
                logger.warning("[DEPRECATE] Register block: %s", reg_name)
                block, fn_names = _deprecate_block(block, reg_name)
                self._deprecated_fns.extend(fn_names)
                out_parts.append(block)
                continue

            # Apply field-level changes
            block_changes = changes_by_reg.get(reg_name, [])
            current       = block

            for cr in block_changes:
                logger.info("[%s] %s.%s", cr.change_type, reg_name, cr.field_name or '')
                before_block = current
                self._llm_used_this_apply = False
                current = self._apply(current, sha, cr)
                # Fire verbose callback if registered
                if self._verbose_cb is not None:
                    self._verbose_cb(
                        reg_name,
                        cr.field_name or "",
                        cr.change_type,
                        before_block,
                        current,
                        self._llm_used_this_apply,
                        self._current_lld_file,
                    )

            # Update SHA for any changed block
            if block_changes and new_ir and reg_name in new_ir.registers:
                new_sha = new_ir.registers[reg_name].sha16
                current = self._update_sha_anchor(current, new_sha)

            out_parts.append(current)

        content = "".join(out_parts)

        # ── Step 4: Append entirely new register blocks or registers with changes but missing blocks ──
        missing_regs = set(changes_by_reg.keys()) - processed_regs
        appended_regs = set()

        def append_reg_block(reg_name: str):
            if reg_name in appended_regs:
                return
            if new_ir and reg_name in new_ir.registers:
                reg = new_ir.registers[reg_name]
                logger.info("[ADD] Register block: %s", reg_name)
                new_block = generate_register_block(self.ip, reg)
                # Track added functions
                from lld_gen.change_summary import lld_function_names as _lfns
                for f in reg.fields.values():
                    self._added_fns.extend(_lfns(self.ip, reg_name, f.name, f.access))
                self._test_stubs.extend(
                    [generate_test_for_field(self.ip, reg_name, f, reg.offset)
                     for f in reg.fields.values()]
                )
                nonlocal content
                endif_pos = content.rfind("#endif")
                if endif_pos != -1:
                    content = (
                        content[:endif_pos] + "\n" + new_block + "\n" + content[endif_pos:]
                    )
                else:
                    content += "\n" + new_block + "\n"
                appended_regs.add(reg_name)

        for cr in reg_adds:
            append_reg_block(cr.reg_name)

        for reg_name in sorted(missing_regs):
            append_reg_block(reg_name)

        # ── Step 5: Brace-balance check ─────────────────────────────────────
        if not self._check_braces(content):
            backup.replace(lld_path)
            raise RuntimeError(
                "lld_patcher: brace-balance check failed. Original restored."
            )

        out_path.write_text(content, encoding="utf-8")
        logger.info("Wrote %s", out_path)
        return content

    @staticmethod
    def _check_braces(text: str) -> bool:
        depth, in_str, prev = 0, False, ""
        for ch in text:
            if in_str:
                if ch == '"' and prev != "\\": in_str = False
            elif ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}": depth -= 1
            prev = ch
        return depth == 0

    def get_test_stubs(self) -> List[str]:
        return list(self._test_stubs)

    def get_deprecated_fns(self) -> List[str]:
        return list(self._deprecated_fns)

    def get_added_fns(self) -> List[str]:
        return list(self._added_fns)

    def get_patched_fns(self) -> List[str]:
        return list(self._patched_fns)

    def get_removed_fns(self) -> List[str]:
        return list(self._removed_fns)

    # ── Test file writer ─────────────────────────────────────────────────────
    def write_test_file(
        self,
        out_path:   str | Path,
        sfr_new:    str,
        lld_new:    str,
        new_ir=None,
        llm_client=None,
        lld_text:   str = "",
    ) -> str:
        """
        Write the auto-generated struct-based unit test file.

        Coverage:
          - new_ir provided → tests for ALL fields (100% coverage)
          - new_ir=None     → tests for changed fields only (legacy)
          - llm_client      → LLM writes richer tests; falls back to template
        """
        import re as _re
        out_path = Path(out_path)
        use_llm  = llm_client is not None and getattr(llm_client, "available", False)

        all_stubs:      List[str] = []
        llm_count = template_count = 0

        if new_ir is not None:
            changed_fields = set()
            if hasattr(self, "_changes") and self._changes:
                for cr in self._changes:
                    if cr.field_name:
                        changed_fields.add((cr.reg_name.upper(), cr.field_name.upper()))
                    elif cr.change_type == ChangeType.REG_ADDED and new_ir:
                        reg_added = new_ir.registers.get(cr.reg_name)
                        if reg_added:
                            for fname_added in reg_added.fields:
                                changed_fields.add((cr.reg_name.upper(), fname_added.upper()))

            seen: set = set()
            for reg_name in sorted(new_ir.registers):
                reg = new_ir.registers[reg_name]
                for fname in sorted(reg.fields):
                    if (reg_name.upper(), fname.upper()) not in changed_fields:
                        continue
                    fld = reg.fields[fname]
                    key = f"{reg_name}.{fname}"
                    if key in seen:
                        continue
                    seen.add(key)

                    stub = ""
                    if use_llm:
                        fn_base = f"lld_{self.ip.lower()}_{reg_name.lower()}_{fname.lower()}"
                        fns     = []
                        acc     = fld.access.upper()
                        if acc in {"RO", "RW", "W1C", "W1S"}:
                            fns.append(f"{fn_base}_get")
                        if acc == "RW":
                            fns.append(f"{fn_base}_set")
                        if acc == "WO":
                            fns.append(f"{fn_base}_set")
                        if acc == "W1C":
                            fns.append(f"{fn_base}_clear")
                        if acc == "W1S":
                            fns.append(f"{fn_base}_set1")
                        impl  = extract_all_functions_for_field(
                            lld_text, self.ip, reg_name, fname
                        ) if lld_text else ""
                        width = fld.msb - fld.lsb + 1
                        stub  = llm_client.generate_test(
                            ip=self.ip, reg_name=reg_name, reg_offset=reg.offset,
                            field_name=fname, msb=fld.msb, lsb=fld.lsb,
                            access=fld.access, desc=fld.desc or "",
                            mask=fld.mask, shift=fld.shift, width=width,
                            reset_val=getattr(fld, "reset_val", 0) or 0,
                            fn_list=", ".join(fns),
                            impl=impl,
                        )

                    if stub:
                        all_stubs.append(stub)
                        llm_count += 1
                    else:
                        tmpl = generate_test_for_field(self.ip, reg_name, fld, reg.offset)
                        if tmpl:
                            all_stubs.append(tmpl)
                        template_count += 1

                    if _is_irq_field(fld):
                        all_stubs.extend(
                            self._irq_test_stubs(self.ip, reg_name, fld, reg.offset)
                        )
        else:
            all_stubs      = list(self._test_stubs)
            template_count = len(all_stubs)

        mode = "ALL fields"
        if use_llm:
            mode += f" | LLM={llm_count} Template={template_count}"

        header = (
            f"/* AUTO-GENERATED by lld_gen -- DO NOT EDIT */\n"
            f"/* Coverage: {mode} */\n"
            f"#include <stdint.h>\n"
            f"#include <assert.h>\n"
            f'#include "{sfr_new}"\n'
            f'#include "{lld_new}"\n\n'
        )

        body   = "\n\n".join(all_stubs)
        runner = "\n\nint main(void) {\n"
        for stub in all_stubs:
            for m in _re.finditer(r"static void (test_\w+)\(void\)", stub):
                runner += f"    {m.group(1)}();\n"
        runner += "    return 0;\n}\n"

        content = header + body + runner
        out_path.write_text(content, encoding="utf-8")
        return content
