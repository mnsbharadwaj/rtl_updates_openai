"""
lld_cross_ref.py — Cross-LLD AST-Based Function Reference Scanner

Given a set of changed LLD function names (from one IP's SFR patch),
scans all .h and .c files in lld_dir to find:
    1. Which files #include the changed lld_{ip}.h
    2. Which functions in those files call any of the changed LLD functions
    3. The exact call site (file, line, caller function name, context lines)

Returns a mapping: Dict[Path → List[FunctionCallSite]]

Strategy (AST-light regex — no real C parser needed):
    Phase 1: grep for #include "lld_{ip}.h" or #include <lld_{ip}.h>
    Phase 2: for each including file, grep for fn_name( calls
    Phase 3: extract the surrounding caller function body (±10 lines)
    Phase 4: return FunctionCallSite objects for targeted review/patching

Usage:
    from lld_gen.lld_cross_ref import find_cross_refs
    sites = find_cross_refs("PMU", ["lld_pmu_clk_con_clk_sel_get"], lld_dir)
    for file, call_sites in sites.items():
        for cs in call_sites:
            print(f"  {cs.file}:{cs.line_number}  {cs.caller_fn} → {cs.callee_fn}")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class FunctionCallSite:
    """One call to an LLD function found in another file."""
    file:       Path
    line_number: int
    caller_fn:  str           # name of the C function that contains the call
    callee_fn:  str           # lld function being called
    context:    str           # ±10 lines of source around the call


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------
_INCLUDE_RE = re.compile(
    r'#\s*include\s+[<"]([^>"]+\.h)[>"]'
)
_STATIC_FN_START_RE = re.compile(
    r'^[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*\{',
    re.MULTILINE,
)
_CALL_RE_TEMPLATE = r'\b{fn}\s*\('


def _make_call_re(fn_name: str) -> re.Pattern:
    """Build a regex that matches one function call."""
    return re.compile(_CALL_RE_TEMPLATE.format(fn=re.escape(fn_name)))


def _extract_caller_name(text: str, call_line_idx: int) -> str:
    """
    Walk backwards from call_line_idx to find the enclosing function name.
    Returns 'unknown' if no enclosing function found.
    """
    lines = text.splitlines()
    # Look backward for a line with 'type name(...)' signature pattern
    for i in range(call_line_idx, max(0, call_line_idx - 100), -1):
        line = lines[i].strip()
        # Simple heuristic: a function start line contains '{' and a '(' before it
        m = re.search(r'(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{', line)
        if m:
            candidate = m.group(1)
            # Skip keywords
            if candidate not in {"if", "for", "while", "switch", "do"}:
                return candidate
    return "unknown"


def _context_lines(lines: List[str], idx: int, window: int = 10) -> str:
    """Return ±window lines around line idx, with line numbers."""
    start = max(0, idx - window)
    end   = min(len(lines), idx + window + 1)
    result = []
    for i in range(start, end):
        prefix = ">>>" if i == idx else "   "
        result.append(f"{prefix} {i+1:4d}: {lines[i]}")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------
def _find_including_files(
    lld_dir:     Path,
    ip:          str,
    depth:       int = 3,
) -> List[Path]:
    """
    Find all .h/.c files under lld_dir that #include lld_{ip}.h
    (case-insensitive match).
    """
    lld_header = f"lld_{ip.lower()}.h"
    candidates: List[Path] = []

    for ext in ("*.h", "*.c", "*.cpp"):
        for f in lld_dir.rglob(ext):
            # Respect depth limit
            try:
                rel = f.relative_to(lld_dir)
                if len(rel.parts) - 1 > depth:
                    continue
            except ValueError:
                continue

            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for m in _INCLUDE_RE.finditer(text):
                inc_file = m.group(1).lower()
                if inc_file.endswith(lld_header) or inc_file == lld_header:
                    candidates.append(f)
                    break

    return candidates


def _scan_file_for_calls(
    file_path:   Path,
    fn_names:    List[str],
) -> List[FunctionCallSite]:
    """Scan one file for calls to any of fn_names."""
    sites: List[FunctionCallSite] = []
    try:
        text  = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
    except OSError:
        return sites

    # Pre-compile regexes
    call_res = [(fn, _make_call_re(fn)) for fn in fn_names]

    for fn_name, call_re in call_res:
        for m in call_re.finditer(text):
            # Find line number (0-indexed)
            line_idx = text[:m.start()].count("\n")
            caller   = _extract_caller_name(text, line_idx)
            ctx      = _context_lines(lines, line_idx)

            sites.append(FunctionCallSite(
                file        = file_path,
                line_number = line_idx + 1,
                caller_fn   = caller,
                callee_fn   = fn_name,
                context     = ctx,
            ))

    return sites


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------
def find_cross_refs(
    ip:           str,
    changed_fns:  List[str],
    lld_dir:      Path,
    search_depth: int = 3,
) -> Dict[Path, List[FunctionCallSite]]:
    """
    Find all callers of `changed_fns` across all .h/.c files under `lld_dir`.

    Args:
        ip:           IP name (e.g. "PMU") — used to find lld_pmu.h includers
        changed_fns:  List of LLD function names that were changed
        lld_dir:      Root directory to scan
        search_depth: Maximum subdirectory depth to scan

    Returns:
        Dict mapping file Path → list of FunctionCallSite objects found in that file.
        Only files that actually contain calls are included.
    """
    lld_dir = Path(lld_dir)
    if not lld_dir.exists() or not changed_fns:
        return {}

    print(f"  [CROSS-REF] Scanning {lld_dir} for callers of {len(changed_fns)} changed fn(s)…")

    # Phase 1: files that include lld_{ip}.h
    including_files = _find_including_files(lld_dir, ip, depth=search_depth)
    if not including_files:
        # Fallback: scan all files (slower but thorough)
        including_files = []
        for ext in ("*.h", "*.c"):
            including_files.extend(lld_dir.rglob(ext))

    # Phase 2: scan each including file for calls
    result: Dict[Path, List[FunctionCallSite]] = {}
    for f in including_files:
        sites = _scan_file_for_calls(f, changed_fns)
        if sites:
            result[f] = sites
            print(f"  [CROSS-REF]   {f.name}: {len(sites)} call site(s)")

    total = sum(len(v) for v in result.values())
    print(f"  [CROSS-REF] Found {total} call site(s) across {len(result)} file(s)")
    return result


def format_cross_ref_report(
    cross_refs: Dict[Path, List[FunctionCallSite]],
) -> str:
    """Format cross-ref findings as a Markdown table for PR description."""
    if not cross_refs:
        return ""

    lines = [
        "### Cross-LLD Call Sites (verify signature compatibility)",
        "",
        "| Caller File | Line | Caller Function | Calls LLD Function |",
        "|-------------|------|-----------------|-------------------|",
    ]
    for file_path, sites in sorted(cross_refs.items(), key=lambda x: x[0].name):
        for cs in sites:
            lines.append(
                f"| `{file_path.name}` | {cs.line_number} "
                f"| `{cs.caller_fn}` | `{cs.callee_fn}` |"
            )
    lines.append("")
    lines.append(
        "> These files call patched LLD functions. "
        "Verify call-site compatibility, especially if ACCESS_CHANGED or BITWIDTH_CHANGED."
    )
    return "\n".join(lines)
