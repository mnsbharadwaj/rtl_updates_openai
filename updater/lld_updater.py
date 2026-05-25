"""
Surgical updater for lld.h — AST-guided approach.

How it works
────────────
1. The file is split into "sentinel blocks" and "free zones":
     /* [sfr_gen:lld:begin:REG:FIELD] */
     ... auto-generated functions ...
     /* [sfr_gen:lld:end:REG:FIELD] */

2. Free zones (programmer-written code OUTSIDE sentinels) are NEVER touched.

3. For each field that changed (from DiffResult), the matching sentinel block
   is replaced with freshly generated code (template or LLM).

4. For added fields, new sentinel blocks are inserted after their parent
   register's last existing block (or appended near the end).

5. For removed fields, their sentinel block is deleted.

6. After reassembly, a lightweight C syntax check is performed using a
   brace-counter to verify the file remains balanced (no runaway braces).
   If the check fails, the original file is restored and an error is raised.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from models import Field, Register, RegisterMap
from generators.lld_generator import _field_block
from updater.diff_engine import DiffResult, FieldDiff, RegisterDiff

_BEGIN_RE = re.compile(
    r"/\* \[sfr_gen:lld:begin:([A-Z0-9_]+):([A-Z0-9_]+)\] \*/"
)
_END_RE = re.compile(
    r"/\* \[sfr_gen:lld:end:([A-Z0-9_]+):([A-Z0-9_]+)\] \*/"
)


# ---------------------------------------------------------------------------
# Segment splitter
# ---------------------------------------------------------------------------
Segment = Tuple[Optional[Tuple[str, str]], List[str]]
# key=(reg,field) or None for free zone


def _split_segments(lines: List[str]) -> List[Segment]:
    segments: List[Segment] = []
    current_key: Optional[Tuple[str, str]] = None
    buffer: List[str] = []

    for line in lines:
        m_begin = _BEGIN_RE.search(line)
        m_end   = _END_RE.search(line)

        if m_begin:
            if buffer:
                segments.append((current_key, buffer))
            current_key = (m_begin.group(1), m_begin.group(2))
            buffer = [line]
        elif m_end and current_key is not None:
            buffer.append(line)
            segments.append((current_key, buffer))
            current_key = None
            buffer = []
        else:
            buffer.append(line)

    if buffer:
        segments.append((current_key, buffer))

    return segments


# ---------------------------------------------------------------------------
# Brace-balance validator
# ---------------------------------------------------------------------------
def _check_braces(content: str) -> bool:
    """Return True if opening and closing braces are balanced."""
    depth = 0
    in_str  = False
    in_char = False
    prev    = ""
    for ch in content:
        if in_str:
            if ch == '"'  and prev != "\\": in_str  = False
        elif in_char:
            if ch == "'"  and prev != "\\": in_char = False
        elif ch == '"': in_str  = True
        elif ch == "'": in_char = True
        elif ch == "{": depth += 1
        elif ch == "}": depth -= 1
        prev = ch
    return depth == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def update_lld(
    existing_path: str | Path,
    new_map: RegisterMap,
    diff: DiffResult,
    llm_fn: Optional[Callable[[Field, str], str]] = None,
) -> str:
    """
    Surgically update *existing_path* (lld.h), touching only changed/added/
    removed field blocks.  Programmer code outside sentinels is preserved.

    Args:
        existing_path: Path to the existing lld.h.
        new_map:       Newly parsed RegisterMap.
        diff:          DiffResult from diff_engine.diff_register_maps().
        llm_fn:        Optional Callable(field, fn_prefix) → C code string.

    Returns:
        Updated file content string.

    Raises:
        RuntimeError if brace validation fails (original file restored).
    """
    existing_path = Path(existing_path)
    original_content = existing_path.read_text(encoding="utf-8")
    old_lines = original_content.splitlines(keepends=True)

    segments = _split_segments(old_lines)

    # Build lookup structures
    new_reg_map: Dict[str, Register]  = {r.name: r for r in new_map.registers}
    new_field_map: Dict[Tuple[str, str], Field] = {}
    for r in new_map.registers:
        for f in r.fields:
            new_field_map[(r.name, f.name)] = f

    changed_keys: Dict[Tuple[str, str], FieldDiff] = {}
    removed_keys: set                               = set()
    added_per_reg: Dict[str, List[FieldDiff]]       = {}

    for rd in diff.register_diffs:
        if rd.is_removed:
            for f in rd.old_reg.fields:
                removed_keys.add((rd.reg_name, f.name))
        elif rd.is_added:
            added_per_reg[rd.reg_name] = rd.field_diffs  # all fields "added"
        else:
            for fd in rd.field_diffs:
                key = (rd.reg_name, fd.field_name)
                if fd.is_removed:
                    removed_keys.add(key)
                elif fd.is_added or fd.is_changed:
                    changed_keys[key] = fd
            # Also collect purely-added fields within a changed register
            if rd.reg_name not in added_per_reg:
                added_per_reg[rd.reg_name] = [
                    fd for fd in rd.field_diffs if fd.is_added
                ]

    # Track which registers we've already appended new fields after
    appended_for_reg: set = set()

    out_lines: List[str] = []

    for (key, block_lines) in segments:
        if key is None:
            out_lines.extend(block_lines)
            continue

        reg_name, field_name = key

        if key in removed_keys:
            continue  # drop deleted field block

        if key in changed_keys:
            fd  = changed_keys[key]
            reg = new_reg_map[reg_name]
            fld = new_field_map[key]
            from generators.lld_generator import _fn_prefix
            new_block = _field_block(new_map.peripheral, reg, fld, llm_fn)
            out_lines.append(new_block + "\n\n")
        else:
            out_lines.extend(block_lines)  # unchanged — verbatim

        # After the last field of a register, append any newly added fields
        # for that register (we detect "last field of register" by checking
        # if the next segment belongs to a different register or is a free zone)
        if reg_name in added_per_reg and reg_name not in appended_for_reg:
            # Check if this is the last block for this register
            key_idx = next(
                i for i, (k, _) in enumerate(segments) if k == key
            )
            next_key = segments[key_idx + 1][0] if key_idx + 1 < len(segments) else None
            if next_key is None or (next_key and next_key[0] != reg_name):
                reg = new_reg_map[reg_name]
                for fd in added_per_reg[reg_name]:
                    fld = fd.new_field
                    new_block = _field_block(new_map.peripheral, reg, fld, llm_fn)
                    out_lines.append(new_block + "\n\n")
                appended_for_reg.add(reg_name)

    # Append entirely new registers (no prior blocks in the file)
    for rd in diff.added_registers:
        reg = new_reg_map[rd.reg_name]
        out_lines.append(f"/* {'=' * 60}\n")
        out_lines.append(f" * [NEW] Register: {reg.name}  offset=0x{reg.offset:04X}\n")
        out_lines.append(f" {'=' * 60} */\n")
        for f in reg.fields:
            new_block = _field_block(new_map.peripheral, reg, f, llm_fn)
            out_lines.append(new_block + "\n\n")

    content = "".join(out_lines)

    # Brace-balance validation
    if not _check_braces(content):
        # Restore original and bail
        existing_path.write_text(original_content, encoding="utf-8")
        raise RuntimeError(
            "lld_updater: brace-balance check failed after update. "
            "Original file restored. Please review the LLM output."
        )

    existing_path.write_text(content, encoding="utf-8")
    return content
