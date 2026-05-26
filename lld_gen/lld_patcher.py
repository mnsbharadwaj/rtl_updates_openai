"""
lld_patcher.py — Surgical LLD Header Patcher

Locates SRC_SHA anchor blocks in lld.h and applies surgical patches
for each classified change. Unchanged blocks are copied verbatim.

SRC_SHA block header format (in lld.h):
    /* ═══════════════════════════════════════════════════════════════
     * REGISTER: CTRL                     offset=0x0000
     * DMA Control Register
     * SRC_SHA: a1b2c3d4e5f6g7h8
     * ═══════════════════════════════════════════════════════════════ */

Processing rules per change type:
    Template path (no LLM):
        REG_RENAMED      → atomic string replace of all function names in block
        FIELD_RENAMED    → string replace scoped to register block
        BITWIDTH_CHANGED → substitute uint8_t / uint16_t / uint32_t / uint64_t
        ACCESS_CHANGED   → regenerate function set from template
        OFFSET_CHANGED   → update base[N] word-offset literal
        RESET_CHANGED    → update SRC_SHA anchor comment only
        REG_DELETED      → remove entire register block
        FIELD_DELETED    → remove all _get/_set/_clear functions for field

    LLM path (Qwen2.5-Coder-7B):
        COMMENT_CHANGED  → LLM rewrites docstring + body
        MULTI_CHANGED    → LLM handles all combined changes
        REG_ADDED+desc   → LLM generates config() + all field accessors
        FIELD_ADDED+desc → LLM generates getter/setter with inline comments

Function generation conventions (from SKILL.md):
    - Naming: IP_REG_FIELD_verb()
    - Return type: uint8_t (1-8 bit), uint16_t (9-16), uint32_t (17-32), uint64_t (33-64)
    - Word offset: base[byte_offset / 4]
    - RO → getter; RW → getter+setter; WO → setter; W1C → getter+clear; W1S → getter+set1
    - Setter: read-modify-write
    - W1C clear: write mask directly (no read phase)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from lld_gen.sfr_diff_analyzer import (
    ChangeRecord, ChangeType, FieldIR, RegisterIR, SfrIR,
)
from lld_gen.llm_client import LLMClient


# ---------------------------------------------------------------------------
# SRC_SHA anchor patterns
# ---------------------------------------------------------------------------
_SHA_BLOCK_BEGIN = re.compile(
    r"/\*\s*[═=]{10,}\s*\n"       # opening decoration line
    r"\s*\*\s*REGISTER:\s*(\w+)"  # register name
    r"[^\n]*\n"                   # rest of line (offset etc.)
    r"(?:[^\n]*\n)*?"             # optional description lines
    r"\s*\*\s*SRC_SHA:\s*([0-9a-f]+)"  # SRC_SHA
    r"[^\n]*\n"
    r"\s*\*\s*[═=]{10,}\s*\*/",   # closing decoration
    re.DOTALL
)

_STATIC_INLINE_RE = re.compile(
    r"(/\*\*.*?\*/\s*)?"                              # optional doxygen comment
    r"static\s+inline\s+\S+\s+(\w+)\s*\([^)]*\)"    # signature
    r"\s*\{",                                          # opening brace
    re.DOTALL
)

_WORD_OFFSET_RE = re.compile(r"base\[(\d+)\]")
_RETTYPE_RE     = re.compile(r"\b(uint8_t|uint16_t|uint32_t|uint64_t)\b")


# ---------------------------------------------------------------------------
# AST-based C function extractor
# ---------------------------------------------------------------------------
def extract_function(text: str, fn_name: str) -> Optional[str]:
    """
    Extract a complete function body from C source text using brace counting.
    Locates 'fn_name' in a static inline signature, then walks braces to find
    the complete body.
    Returns the full function text including signature, or None if not found.
    """
    # Find signature
    pattern = re.compile(
        r"(/\*\*.*?\*/\s*)?"
        r"static\s+inline\s+\S+\s+"
        + re.escape(fn_name)
        + r"\s*\([^)]*\)\s*\{",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return None

    start = m.start()
    brace_pos = m.end() - 1  # position of opening {

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
    return None  # unbalanced


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
# Template code generators (SKILL.md conventions)
# ---------------------------------------------------------------------------
def _fn_name(ip: str, reg: str, field: str, verb: str) -> str:
    """Build LLD function name: lld_ip_reg_field_verb (all lowercase)."""
    return f"lld_{ip.lower()}_{reg.lower()}_{field.lower()}_{verb.lower()}"


def _word_idx(reg_offset: int) -> int:
    return reg_offset // 4


def _getter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn    = _fn_name(ip, reg, field.name, "get")
    rt    = field.return_type
    widx  = _word_idx(reg_offset)
    desc  = field.desc or f"Get {field.name} field"
    return (
        f"/** @brief {desc} */\n"
        f"static inline {rt} {fn}(volatile uint32_t *base)\n"
        f"{{\n"
        f"    return ({rt})((base[{widx}] & 0x{field.mask:08X}U) >> {field.shift}U);\n"
        f"}}"
    )


def _setter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set")
    widx = _word_idx(reg_offset)
    desc = field.desc or f"Set {field.name} field"
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(volatile uint32_t *base, uint32_t val)\n"
        f"{{\n"
        f"    uint32_t r = base[{widx}];\n"
        f"    r &= ~0x{field.mask:08X}U;\n"
        f"    r |= ((uint32_t)val << {field.shift}U) & 0x{field.mask:08X}U;\n"
        f"    base[{widx}] = r;\n"
        f"}}"
    )


def _wo_setter(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set")
    widx = _word_idx(reg_offset)
    desc = field.desc or f"Write {field.name} field (WO)"
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(volatile uint32_t *base, uint32_t val)\n"
        f"{{\n"
        f"    base[{widx}] = ((uint32_t)val << {field.shift}U) & 0x{field.mask:08X}U;\n"
        f"}}"
    )


def _w1c_clear(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "clear")
    widx = _word_idx(reg_offset)
    desc = field.desc or f"Clear {field.name} (W1C)"
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(volatile uint32_t *base)\n"
        f"{{\n"
        f"    base[{widx}] = 0x{field.mask:08X}U; /* W1C: write 1 to clear */\n"
        f"}}"
    )


def _w1s_set1(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    fn   = _fn_name(ip, reg, field.name, "set1")
    widx = _word_idx(reg_offset)
    desc = field.desc or f"Set1 {field.name} (W1S)"
    return (
        f"/** @brief {desc} */\n"
        f"static inline void {fn}(volatile uint32_t *base)\n"
        f"{{\n"
        f"    base[{widx}] = 0x{field.mask:08X}U; /* W1S: write 1 to set */\n"
        f"}}"
    )


def _irq_helpers(ip: str, reg: str, field: FieldIR, reg_offset: int) -> str:
    """Four mandatory IRQ helpers for interrupt-capable fields."""
    widx = _word_idx(reg_offset)
    p    = f"lld_{ip.lower()}_{reg.lower()}_{field.name.lower()}_irq"
    return (
        f"static inline void {p}_enable(volatile uint32_t *base)  "
        f"{{ base[{widx}] |=  0x{field.mask:08X}U; }}\n"
        f"static inline void {p}_disable(volatile uint32_t *base) "
        f"{{ base[{widx}] &= ~0x{field.mask:08X}U; }}\n"
        f"static inline uint32_t {p}_status(volatile uint32_t *base) "
        f"{{ return (base[{widx}] & 0x{field.mask:08X}U); }}\n"
        f"static inline void {p}_clear(volatile uint32_t *base)   "
        f"{{ base[{widx}] = 0x{field.mask:08X}U; }}"
    )


def _is_irq_field(field: FieldIR) -> bool:
    return "irq" in field.name.lower() or "interrupt" in field.desc.lower()


def generate_field_functions(
    ip: str, reg_name: str, field: FieldIR, reg_offset: int
) -> str:
    """Generate complete function set for a field based on access type."""
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


def _sha16(reg: RegisterIR) -> str:
    return reg.sha16


def _reg_block_header(ip: str, reg: RegisterIR) -> str:
    sha = _sha16(reg)
    line = "═" * 63
    return (
        f"/* {line}\n"
        f" * REGISTER: {reg.name:<30} offset=0x{reg.offset:04X}\n"
        f" * {reg.desc or ''}\n"
        f" * SRC_SHA: {sha}\n"
        f" * {line} */\n"
    )


def generate_register_block(ip: str, reg: RegisterIR) -> str:
    """Generate complete LLD block for a register (header + all field functions)."""
    lines = [_reg_block_header(ip, reg)]
    for fname in sorted(reg.fields):
        fld = reg.fields[fname]
        lines.append(generate_field_functions(ip, reg.name, fld, reg.offset))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unit test generator
# ---------------------------------------------------------------------------
def generate_test_for_field(ip: str, reg_name: str, field: FieldIR, reg_offset: int) -> str:
    """
    Generate a static test function for one field.

    Uses volatile uint32_t regs[256] = {0} as mock register space.
    No malloc, no OS primitives, no external dependencies.
    """
    fn_base = f"lld_{ip.lower()}_{reg_name.lower()}_{field.name.lower()}"
    widx    = _word_idx(reg_offset)
    access  = field.access.upper()
    tests   = []

    if access in {"RO", "RW", "W1C", "W1S"}:
        shifted      = 1
        expected_raw = (shifted << field.shift) & field.mask
        tests.append(
            f"static void test_{fn_base}_get(void) {{\n"
            f"    volatile uint32_t regs[256] = {{0}};\n"
            f"    regs[{widx}] = 0x{expected_raw:08X}U;\n"
            f"    uint32_t v = (uint32_t){fn_base}_get(regs);\n"
            f"    assert(v == {shifted}U);\n"
            f"}}"
        )

    if access == "RW":
        tests.append(
            f"static void test_{fn_base}_set(void) {{\n"
            f"    volatile uint32_t regs[256] = {{0}};\n"
            f"    regs[{widx}] = 0xFFFFFFFFU;\n"
            f"    {fn_base}_set(regs, 0U);\n"
            f"    assert((regs[{widx}] & 0x{field.mask:08X}U) == 0U);\n"
            f"    assert((regs[{widx}] & ~0x{field.mask:08X}U) == (~0x{field.mask:08X}U & 0xFFFFFFFFU));\n"
            f"}}"
        )

    if access == "WO":
        tests.append(
            f"static void test_{fn_base}_set(void) {{\n"
            f"    volatile uint32_t regs[256] = {{0}};\n"
            f"    {fn_base}_set(regs, 1U);\n"
            f"    assert((regs[{widx}] & 0x{field.mask:08X}U) != 0U);\n"
            f"}}"
        )

    if access == "W1C":
        tests.append(
            f"static void test_{fn_base}_clear(void) {{\n"
            f"    volatile uint32_t regs[256] = {{0}};\n"
            f"    {fn_base}_clear(regs);\n"
            f"    assert(regs[{widx}] == 0x{field.mask:08X}U);\n"
            f"}}"
        )

    if access == "W1S":
        tests.append(
            f"static void test_{fn_base}_set1(void) {{\n"
            f"    volatile uint32_t regs[256] = {{0}};\n"
            f"    {fn_base}_set1(regs);\n"
            f"    assert(regs[{widx}] == 0x{field.mask:08X}U);\n"
            f"}}"
        )

    return "\n\n".join(tests)


# ---------------------------------------------------------------------------
# LLD Patcher
# ---------------------------------------------------------------------------
class LLDPatcher:
    """
    Surgically patches lld.h for a list of ChangeRecord objects.

    Algorithm:
    1. Split lld.h into SRC_SHA register blocks + free zones
    2. For each change:
       - Template path: apply deterministic transformation
       - LLM path: call Qwen2.5-Coder-7B for new function text
    3. Reassemble; validate brace balance; write back
    4. Generate unit test stubs for all changed/added functions
    """

    def __init__(
        self,
        ip:          str,
        llm_client:  Optional[LLMClient] = None,
        no_llm:      bool = False,
    ):
        self.ip         = ip.upper()
        self._llm       = llm_client
        self._no_llm    = no_llm
        self._test_stubs: List[str] = []

    # ── File split ─────────────────────────────────────────────────────────
    def _split_blocks(self, text: str) -> List[Tuple[Optional[str], Optional[str], str]]:
        """
        Split lld.h into segments: (reg_name_or_None, sha_or_None, block_text).
        Free zones have reg_name=None, sha=None.
        """
        segments: List[Tuple[Optional[str], Optional[str], str]] = []
        pos = 0

        for m in _SHA_BLOCK_BEGIN.finditer(text):
            # Free zone before this block
            if m.start() > pos:
                segments.append((None, None, text[pos:m.start()]))

            reg_name = m.group(1)
            sha      = m.group(2)
            block_start = m.start()

            # Find the end of the register block: next SHA block or EOF
            next_m = _SHA_BLOCK_BEGIN.search(text, m.end())
            block_end = next_m.start() if next_m else len(text)
            segments.append((reg_name, sha, text[block_start:block_end]))
            pos = block_end

        # Trailing free zone
        if pos < len(text):
            segments.append((None, None, text[pos:]))

        return segments

    # ── Template transformations ───────────────────────────────────────────
    def _rename_in_block(self, block: str, old_name: str, new_name: str) -> str:
        """Atomic string replace for all function name occurrences."""
        old_ip_reg = f"lld_{self.ip.lower()}_{old_name.lower()}"
        new_ip_reg = f"lld_{self.ip.lower()}_{new_name.lower()}"
        block = block.replace(old_ip_reg, new_ip_reg)
        block = block.replace(f"REGISTER: {old_name}", f"REGISTER: {new_name}")
        return block

    def _rename_field_in_block(self, block: str, old_field: str, new_field: str, reg_name: str) -> str:
        """Replace field name within the scoped register block."""
        old_prefix = f"lld_{self.ip.lower()}_{reg_name.lower()}_{old_field.lower()}"
        new_prefix = f"lld_{self.ip.lower()}_{reg_name.lower()}_{new_field.lower()}"
        return block.replace(old_prefix, new_prefix)

    def _update_return_type(self, block: str, field: FieldIR) -> str:
        """Substitute uint8_t/uint16_t/uint32_t/uint64_t based on new field width."""
        old_types = {"uint8_t", "uint16_t", "uint32_t", "uint64_t"}
        new_type  = field.return_type
        fn_prefix = f"lld_{self.ip.lower()}_{field.reg_name.lower()}_{field.name.lower()}_get"

        # Only replace return type in getter signature lines
        lines = block.splitlines(keepends=True)
        result = []
        for ln in lines:
            if fn_prefix in ln and any(t in ln for t in old_types):
                ln = _RETTYPE_RE.sub(new_type, ln, count=1)
            result.append(ln)
        return "".join(result)

    def _update_word_offset(self, block: str, old_offset: int, new_offset: int) -> str:
        """Update base[N] word-offset literal."""
        old_widx = old_offset // 4
        new_widx = new_offset // 4
        return block.replace(f"base[{old_widx}]", f"base[{new_widx}]")

    def _update_sha_anchor(self, block: str, new_sha: str) -> str:
        """Replace SRC_SHA value in the anchor comment."""
        return re.sub(r"SRC_SHA:\s*[0-9a-f]+", f"SRC_SHA: {new_sha}", block)

    def _remove_field_functions(self, block: str, ip: str, reg: str, field: str) -> str:
        """Remove all static inline functions for lld_ip_reg_field_* from block.

        Uses character-based extraction to correctly handle Doxygen /** ... */
        comments that precede the function signature.
        """
        prefix = f"lld_{ip.lower()}_{reg.lower()}_{field.lower()}_"

        # Collect spans to remove: (start, end) of each function including preceding doc comment
        to_remove: List[tuple] = []

        for m in _STATIC_INLINE_RE.finditer(block):
            fn_name = m.group(2)
            if not fn_name.startswith(prefix):
                continue

            # Walk back to include preceding /** ... */ doc comment
            fn_start = m.start()
            # Look backward from fn_start for a /** comment
            before = block[:fn_start]
            doc_match = re.search(r"/\*\*[^*]*(?:\*(?!/)[^*]*)*\*/\s*$", before, re.DOTALL)
            if doc_match:
                fn_start = fn_start - (len(before) - doc_match.start())

            # Walk braces forward from opening brace to find function end
            brace_pos = block.index("{", m.start())
            depth  = 0
            i      = brace_pos
            in_str = False
            prev   = ""
            while i < len(block):
                ch = block[i]
                if in_str:
                    if ch == '"' and prev != "\\": in_str = False
                elif ch == '"': in_str = True
                elif ch == "{": depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        fn_end = i + 1
                        to_remove.append((fn_start, fn_end))
                        break
                prev = ch
                i   += 1

        if not to_remove:
            return block

        # Remove spans in reverse order to preserve offsets
        result = block
        for start, end in sorted(to_remove, reverse=True):
            # Also strip trailing newline(s) after the closing brace
            tail_end = end
            while tail_end < len(result) and result[tail_end] in "\n\r":
                tail_end += 1
            result = result[:start] + result[tail_end:]
        return result

    def _regen_access_template(self, block: str, cr: ChangeRecord) -> str:
        """Regenerate complete function set from template for ACCESS_CHANGED."""
        new_f   = cr.new_field
        new_reg = cr.new_reg
        if new_f is None or new_reg is None:
            return block
        # Remove old functions for this field
        block = self._remove_field_functions(
            block, self.ip, cr.reg_name, cr.field_name or ""
        )
        # Generate new functions and append before SHA block closes
        new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
        self._test_stubs.extend(self._make_test(cr))
        # Insert before the #endif or closing comment region
        return block.rstrip() + "\n\n" + new_fns + "\n"

    def _make_test(self, cr: ChangeRecord) -> List[str]:
        """Generate test code for a change record."""
        fld = cr.new_field
        reg = cr.new_reg
        if fld is None or reg is None:
            return []
        test = generate_test_for_field(self.ip, cr.reg_name, fld, reg.offset)
        return [test] if test else []

    # ── LLM path ──────────────────────────────────────────────────────────
    def _llm_regenerate(self, block: str, cr: ChangeRecord) -> str:
        """Call Qwen2.5-Coder-7B to update function body for semantic changes."""
        new_f   = cr.new_field
        old_f   = cr.old_field
        new_reg = cr.new_reg
        if new_f is None or new_reg is None:
            return block

        old_code = ""
        if old_f:
            old_code = extract_all_functions_for_field(
                block, self.ip, cr.reg_name, old_f.name
            )

        if self._no_llm or self._llm is None:
            print(f"  [LLM-SKIP] {cr.reg_name}.{cr.field_name} — using template fallback")
            # Template fallback
            if cr.field_name:
                block = self._remove_field_functions(block, self.ip, cr.reg_name, cr.field_name)
            new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
            self._test_stubs.extend(self._make_test(cr))
            return block.rstrip() + "\n\n" + new_fns + "\n"

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
            )
            if cr.field_name:
                block = self._remove_field_functions(block, self.ip, cr.reg_name, cr.field_name)
            self._test_stubs.extend(self._make_test(cr))
            return block.rstrip() + "\n\n" + new_code + "\n"
        except RuntimeError as exc:
            print(f"  [LLM-ERROR] {exc}; using template fallback")
            if cr.field_name:
                block = self._remove_field_functions(block, self.ip, cr.reg_name, cr.field_name)
            new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
            self._test_stubs.extend(self._make_test(cr))
            return block.rstrip() + "\n\n" + new_fns + "\n"

    # ── Apply one change record ────────────────────────────────────────────
    def _apply(self, block: str, sha: Optional[str], cr: ChangeRecord) -> str:
        ct = cr.change_type

        if ct == ChangeType.REG_RENAMED:
            old_name = cr.old_reg.name if cr.old_reg else cr.reg_name
            new_name = cr.new_reg.name if cr.new_reg else cr.reg_name
            return self._rename_in_block(block, old_name, new_name)

        elif ct == ChangeType.FIELD_RENAMED:
            old_fname = cr.old_field.name if cr.old_field else (cr.field_name or "")
            new_fname = cr.new_field.name if cr.new_field else (cr.field_name or "")
            return self._rename_field_in_block(block, old_fname, new_fname, cr.reg_name)

        elif ct == ChangeType.BITWIDTH_CHANGED:
            # Mask, shift, and return type all potentially changed.
            # Safest: remove old functions and regenerate from new FieldIR.
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f and new_reg:
                block = self._remove_field_functions(
                    block, self.ip, cr.reg_name, cr.field_name or ""
                )
                new_fns = generate_field_functions(
                    self.ip, cr.reg_name, new_f, new_reg.offset
                )
                self._test_stubs.extend(self._make_test(cr))
                block = block.rstrip() + "\n\n" + new_fns + "\n"
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        elif ct == ChangeType.ACCESS_CHANGED:
            return self._regen_access_template(block, cr)

        elif ct == ChangeType.OFFSET_CHANGED:
            # Mask bits move with the shift — regenerate full function set.
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f and new_reg:
                block = self._remove_field_functions(
                    block, self.ip, cr.reg_name, cr.field_name or ""
                )
                new_fns = generate_field_functions(
                    self.ip, cr.reg_name, new_f, new_reg.offset
                )
                self._test_stubs.extend(self._make_test(cr))
                block = block.rstrip() + "\n\n" + new_fns + "\n"
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block


        elif ct == ChangeType.RESET_CHANGED:
            if cr.new_reg:
                block = self._update_sha_anchor(block, cr.new_reg.sha16)
            return block

        elif ct == ChangeType.FIELD_DELETED:
            return self._remove_field_functions(
                block, self.ip, cr.reg_name, cr.field_name or ""
            )

        elif ct in {ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED}:
            return self._llm_regenerate(block, cr)

        elif ct == ChangeType.FIELD_ADDED:
            new_f   = cr.new_field
            new_reg = cr.new_reg
            if new_f is None or new_reg is None:
                return block
            if cr.needs_llm:
                return self._llm_regenerate(block, cr)
            new_fns = generate_field_functions(self.ip, cr.reg_name, new_f, new_reg.offset)
            self._test_stubs.extend(self._make_test(cr))
            return block.rstrip() + "\n\n" + new_fns + "\n"

        return block  # UNCHANGED or unhandled

    # ── Public patch API ───────────────────────────────────────────────────
    def patch(
        self,
        lld_path:     str | Path,
        changes:      List[ChangeRecord],
        new_ir:       Optional[SfrIR] = None,
        out_path:     Optional[str | Path] = None,
    ) -> str:
        """
        Patch lld.h with the given changes. Returns the updated content.

        Args:
            lld_path:  Path to existing lld.h
            changes:   Classified change list from SfrDiffAnalyzer
            new_ir:    New SfrIR (for REG_ADDED blocks)
            out_path:  Write output here; defaults to lld_path (in-place)
        """
        lld_path = Path(lld_path)
        out_path = Path(out_path) if out_path else lld_path

        original = lld_path.read_text(encoding="utf-8")
        backup   = lld_path.with_suffix(".h.bak")
        backup.write_text(original, encoding="utf-8")

        self._test_stubs = []

        # Build lookup: reg_name → list of changes
        changes_by_reg: Dict[str, List[ChangeRecord]] = {}
        reg_deletes: set = set()
        reg_adds: List[ChangeRecord] = []

        for cr in changes:
            if cr.change_type == ChangeType.REG_DELETED:
                reg_deletes.add(cr.reg_name)
            elif cr.change_type == ChangeType.REG_ADDED:
                reg_adds.append(cr)
            else:
                changes_by_reg.setdefault(cr.reg_name, []).append(cr)

        # Split lld.h into blocks
        segments = self._split_blocks(original)

        out_parts: List[str] = []

        for (reg_name, sha, block) in segments:
            if reg_name is None:
                out_parts.append(block)
                continue

            if reg_name in reg_deletes:
                print(f"  [DEL] Register block: {reg_name}")
                continue  # drop the entire block

            block_changes = changes_by_reg.get(reg_name, [])
            current = block

            for cr in block_changes:
                print(f"  [{cr.change_type}] {reg_name}.{cr.field_name or ''}")
                current = self._apply(current, sha, cr)

            # Update SRC_SHA for any changed block
            if block_changes and reg_name in (
                {cr.reg_name for cr in changes_by_reg.get(reg_name, [])}
            ):
                if new_ir and reg_name in new_ir.registers:
                    new_sha = new_ir.registers[reg_name].sha16
                    current = self._update_sha_anchor(current, new_sha)

            out_parts.append(current)

        content = "".join(out_parts)

        # Append entirely new register blocks
        for cr in reg_adds:
            if new_ir and cr.reg_name in new_ir.registers:
                reg = new_ir.registers[cr.reg_name]
                print(f"  [ADD] Register block: {cr.reg_name}")
                new_block = generate_register_block(self.ip, reg)
                self._test_stubs.extend(
                    [generate_test_for_field(self.ip, cr.reg_name, f, reg.offset)
                     for f in reg.fields.values()]
                )
                # Insert before final #endif
                endif_pos = content.rfind("#endif")
                if endif_pos != -1:
                    content = content[:endif_pos] + "\n" + new_block + "\n" + content[endif_pos:]
                else:
                    content += "\n" + new_block + "\n"

        # Brace balance check
        if not self._check_braces(content):
            backup.replace(lld_path)
            raise RuntimeError(
                "lld_patcher: brace-balance check failed. Original restored."
            )

        out_path.write_text(content, encoding="utf-8")
        print(f"  Wrote {out_path}")
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

    def write_test_file(
        self, out_path: str | Path, sfr_new: str, lld_new: str
    ) -> str:
        """Write the auto-generated test file with all test stubs."""
        out_path = Path(out_path)
        header = (
            f"/* AUTO-GENERATED by lld_gen — DO NOT EDIT */\n"
            f"#include <stdint.h>\n"
            f"#include <assert.h>\n"
            f'#include "{sfr_new}"\n'
            f'#include "{lld_new}"\n\n'
        )
        body   = "\n\n".join(self._test_stubs)
        runner = "\n\nint main(void) {\n"
        for stub in self._test_stubs:
            # Extract function name from 'static void test_XXX(void)'
            m = re.search(r"static void (test_\w+)\(void\)", stub)
            if m:
                runner += f"    {m.group(1)}();\n"
        runner += "    return 0;\n}\n"

        content = header + body + runner
        out_path.write_text(content, encoding="utf-8")
        return content
