"""
Surgical updater for sfr.h.

Strategy:
  - The existing sfr.h is parsed line-by-line.
  - Register blocks are delimited by:
      /* [sfr_gen:reg:begin:REGNAME] */
      ...
      /* [sfr_gen:reg:end:REGNAME] */
  - Only blocks for changed/added/removed registers are rewritten.
  - Unchanged blocks are kept byte-for-byte identical.
  - New registers are appended before the #endif guard.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import Register, RegisterMap
from generators.sfr_generator import _register_block
from updater.diff_engine import DiffResult

_BEGIN_RE = re.compile(r"/\* \[sfr_gen:reg:begin:([A-Z0-9_]+)\] \*/")
_END_RE   = re.compile(r"/\* \[sfr_gen:reg:end:([A-Z0-9_]+)\] \*/")
_ENDIF_RE = re.compile(r"#endif\s*/\*.*\*/")


def _split_into_blocks(lines: List[str]) -> List[Tuple[Optional[str], List[str]]]:
    """
    Partition file lines into segments:
      (None,     <lines>)  — non-register content (header, #endif …)
      ("REGNAME", <lines>) — register block including sentinels
    """
    segments: List[Tuple[Optional[str], List[str]]] = []
    current_name: Optional[str] = None
    buffer: List[str] = []

    for line in lines:
        m_begin = _BEGIN_RE.search(line)
        m_end   = _END_RE.search(line)

        if m_begin:
            if buffer:
                segments.append((current_name, buffer))
            current_name = m_begin.group(1)
            buffer = [line]
        elif m_end and current_name is not None:
            buffer.append(line)
            segments.append((current_name, buffer))
            current_name = None
            buffer = []
        else:
            buffer.append(line)

    if buffer:
        segments.append((current_name, buffer))

    return segments


def update_sfr(
    existing_path: str | Path,
    new_map: RegisterMap,
    diff: DiffResult,
) -> str:
    """
    Update *existing_path* (sfr.h) in-place, touching only changed blocks.

    Returns the new file content (also writes it to *existing_path*).
    """
    existing_path = Path(existing_path)
    old_lines = existing_path.read_text(encoding="utf-8").splitlines(keepends=True)

    segments = _split_into_blocks(old_lines)

    # Build lookup: reg_name → new Register object
    new_reg_map: Dict[str, Register] = {r.name: r for r in new_map.registers}

    changed_names = {d.reg_name for d in diff.changed_registers}
    removed_names  = {d.reg_name for d in diff.removed_registers}
    added_names    = {d.reg_name for d in diff.added_registers}

    out_lines: List[str] = []
    endif_lines: List[str] = []

    # Walk existing segments and rebuild
    for (name, block_lines) in segments:
        if name is None:
            # Check if these lines contain the #endif — hold them for last
            if any(_ENDIF_RE.search(ln) for ln in block_lines):
                endif_lines = block_lines
            else:
                out_lines.extend(block_lines)
        elif name in removed_names:
            pass  # drop removed register block
        elif name in changed_names:
            reg = new_reg_map[name]
            new_block = _register_block(new_map.peripheral, reg) + "\n\n"
            out_lines.append(new_block)
        else:
            out_lines.extend(block_lines)   # unchanged — verbatim

    # Append added registers
    for reg_diff in diff.added_registers:
        reg = new_reg_map[reg_diff.reg_name]
        out_lines.append(_register_block(new_map.peripheral, reg) + "\n\n")

    # Re-append #endif
    out_lines.extend(endif_lines)

    content = "".join(out_lines)
    existing_path.write_text(content, encoding="utf-8")
    return content
