"""
Generates lld.h (Low-Level Driver) from a RegisterMap.

Rules:
  - RO  field → getter only
  - WO  field → setter only
  - RW  field → getter + setter
  - W1C field → getter + clear function
  - No description → only getter/setter (template-based)
  - Has description → also an LLM-generated semantic action function

Every function is wrapped in sentinel comments keyed by register+field name
so the updater can locate and replace individual functions without touching
anything else:

  /* [sfr_gen:lld:begin:CTRL_REG:EN] */
  ...functions...
  /* [sfr_gen:lld:end:CTRL_REG:EN] */

Programmer-written code OUTSIDE these sentinels is never modified.
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from models import Field, Register, RegisterMap

_BLOCK_BEGIN = "/* [sfr_gen:lld:begin:{reg}:{field}] */"
_BLOCK_END   = "/* [sfr_gen:lld:end:{reg}:{field}] */"


def _fn_prefix(peripheral: str, reg: str, field: str) -> str:
    return f"{peripheral.lower()}_{reg.lower()}_{field.lower()}"


def _pfx_macro(peripheral: str, reg: str) -> str:
    return f"{peripheral.upper()}_{reg.upper()}"


# ---------------------------------------------------------------------------
# Template-based getter / setter / clear
# ---------------------------------------------------------------------------
def _getter(fn: str, pfx_macro: str, field: Field, desc: str = "") -> str:
    doc = f"/** @brief Get {field.name} field. {desc} */" if desc else f"/** @brief Get {field.name} field. */"
    return (
        f"{doc}\n"
        f"static inline uint32_t {fn}_get(uintptr_t base) {{\n"
        f"    uint32_t val = REG_READ32(base + {pfx_macro}_OFFSET);\n"
        f"    return (val & {pfx_macro}_{field.name}_MASK) >> {pfx_macro}_{field.name}_SHIFT;\n"
        f"}}"
    )


def _setter(fn: str, pfx_macro: str, field: Field, desc: str = "") -> str:
    doc = f"/** @brief Set {field.name} field. {desc} */" if desc else f"/** @brief Set {field.name} field. */"
    return (
        f"{doc}\n"
        f"static inline void {fn}_set(uintptr_t base, uint32_t val) {{\n"
        f"    uint32_t reg = REG_READ32(base + {pfx_macro}_OFFSET);\n"
        f"    reg &= ~{pfx_macro}_{field.name}_MASK;\n"
        f"    reg |= (val << {pfx_macro}_{field.name}_SHIFT) & {pfx_macro}_{field.name}_MASK;\n"
        f"    REG_WRITE32(base + {pfx_macro}_OFFSET, reg);\n"
        f"}}"
    )


def _wo_setter(fn: str, pfx_macro: str, field: Field, desc: str = "") -> str:
    doc = f"/** @brief Write {field.name} field (WO). {desc} */" if desc else f"/** @brief Write {field.name} field (WO). */"
    return (
        f"{doc}\n"
        f"static inline void {fn}_write(uintptr_t base, uint32_t val) {{\n"
        f"    REG_WRITE32(base + {pfx_macro}_OFFSET,\n"
        f"        (val << {pfx_macro}_{field.name}_SHIFT) & {pfx_macro}_{field.name}_MASK);\n"
        f"}}"
    )


def _clear_w1c(fn: str, pfx_macro: str, field: Field, desc: str = "") -> str:
    doc = f"/** @brief Clear {field.name} status bit (W1C). {desc} */" if desc else f"/** @brief Clear {field.name} status bit (W1C). */"
    return (
        f"{doc}\n"
        f"static inline void {fn}_clear(uintptr_t base) {{\n"
        f"    REG_WRITE32(base + {pfx_macro}_OFFSET, {pfx_macro}_{field.name}_MASK);\n"
        f"}}"
    )


def _stub_llm(fn: str, field: Field) -> str:
    """Placeholder when LLM is disabled (--no-llm)."""
    return (
        f"/** @brief [LLM-TODO] Semantic action for {field.name}.\n"
        f" *  Description: {field.description}\n"
        f" */\n"
        f"/* static inline void {fn}_action(uintptr_t base) {{ */\n"
        f"/*     // TODO: implement based on description            */\n"
        f"/* }}                                                      */"
    )


# ---------------------------------------------------------------------------
# Field block builder
# ---------------------------------------------------------------------------
def _field_block(
    peripheral: str,
    reg: Register,
    f: Field,
    llm_fn: Optional[Callable[[Field, str], str]] = None,
) -> str:
    fn = _fn_prefix(peripheral, reg.name, f.name)
    pfx = _pfx_macro(peripheral, reg.name)
    desc = f.description

    parts: List[str] = []

    # getter
    if f.can_read():
        parts.append(_getter(fn, pfx, f, desc))

    # setter / write
    if f.is_write_one_clear():
        parts.append(_getter(fn, pfx, f))   # can also read status
        parts.append(_clear_w1c(fn, pfx, f, desc))
    elif f.access == "WO":
        parts.append(_wo_setter(fn, pfx, f, desc))
    elif f.can_write():
        parts.append(_setter(fn, pfx, f, desc))

    # LLM semantic function (only when description is provided)
    if f.has_description:
        if llm_fn is not None:
            try:
                llm_code = llm_fn(f, fn)
                if llm_code and llm_code.strip():
                    parts.append(llm_code.strip())
            except Exception as exc:
                parts.append(f"/* [LLM-ERROR] {exc} */")
                parts.append(_stub_llm(fn, f))
        else:
            parts.append(_stub_llm(fn, f))

    begin = _BLOCK_BEGIN.format(reg=reg.name, field=f.name)
    end   = _BLOCK_END.format(reg=reg.name, field=f.name)
    body  = "\n\n".join(parts)
    return f"{begin}\n{body}\n{end}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_lld(
    rm: RegisterMap,
    output_path: Optional[str | Path] = None,
    llm_fn: Optional[Callable[[Field, str], str]] = None,
) -> str:
    """
    Generate lld.h content for *rm*.

    Args:
        rm:          Parsed register map.
        output_path: If provided, write the result to this path.
        llm_fn:      Callable(field, fn_prefix) → C code string.
                     If None, semantic action functions are emitted as stubs.

    Returns:
        Generated C source as a string.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    guard = f"{rm.peripheral.upper()}_LLD_H"

    header = textwrap.dedent(f"""\
        /*
         * AUTO-GENERATED by sfr_gen — programmer edits outside sentinel blocks are preserved.
         * Peripheral : {rm.peripheral}
         * Source     : {Path(rm.source_file).name if rm.source_file else 'N/A'}
         * Generated  : {now}
         *
         * Macro dependencies:
         *   REG_READ32(addr)       — 32-bit MMIO read
         *   REG_WRITE32(addr, val) — 32-bit MMIO write
         */
        #ifndef {guard}
        #define {guard}

        #include <stdint.h>
        #include "{rm.peripheral.lower()}_sfr.h"

    """)

    blocks: List[str] = []
    for reg in rm.registers:
        blocks.append(f"/* {'=' * 60}")
        blocks.append(f" * Register: {reg.name}  offset=0x{reg.offset:04X}")
        if reg.description:
            blocks.append(f" * {reg.description}")
        blocks.append(f" {'=' * 60} */")
        for f in reg.fields:
            blocks.append(_field_block(rm.peripheral, reg, f, llm_fn))
            blocks.append("")

    footer = f"\n#endif /* {guard} */\n"
    content = header + "\n".join(blocks) + footer

    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")

    return content
