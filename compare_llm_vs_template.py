"""
compare_llm_vs_template.py
==========================
Runs BOTH template and Ollama-LLM generation for each of the 12 SFR
change types on the demo PMU SFR pair and prints a side-by-side diff
report showing how LLM output differs from the deterministic template.

Usage:
    python compare_llm_vs_template.py [--ip DMA|PMU] [--model qwen2.5-coder]

Output:
    - Console: coloured summary table  +  per-change diffs
    - File:    llm_vs_template_report.md  (markdown report)
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, SfrParser, ChangeType, ChangeRecord,
)
from lld_gen.lld_patcher import LLDPatcher, generate_field_functions
from lld_gen.llm_client import LLMClient

# ── Demo files ───────────────────────────────────────────────────────────────
DEMO_DIR       = ROOT / "tests" / "workspace_demo"
OLD_SFR        = DEMO_DIR / "old_sfr" / "sfr_pmu.h"
NEW_SFR        = DEMO_DIR / "new_sfr" / "sfr_pmu.h"
LLD_SRC        = DEMO_DIR / "lld"     / "lld_pmu.h"
IP             = "PMU"
REPORT_OUT     = ROOT / "llm_vs_template_report.md"


# ── Helpers ──────────────────────────────────────────────────────────────────
def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + ln for ln in text.splitlines())


def _unified_diff(a: str, b: str, from_file: str = "template", to_file: str = "llm") -> str:
    lines_a = a.splitlines(keepends=True)
    lines_b = b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=from_file, tofile=to_file, n=2))
    return "".join(diff) if diff else "(identical)"


def _structural_match(a: str, b: str) -> dict:
    """Compare two code strings at function-name and struct-path level."""
    fn_re   = re.compile(r'\b(lld_\w+)\s*\(')
    path_re = re.compile(r'lld->\w+->(\w+)\.stNative\.(\w+)')

    fns_a   = set(fn_re.findall(a))
    fns_b   = set(fn_re.findall(b))
    paths_a = set(path_re.findall(a))
    paths_b = set(path_re.findall(b))

    return {
        "fn_match":      fns_a == fns_b,
        "fns_only_tmpl": fns_a - fns_b,
        "fns_only_llm":  fns_b - fns_a,
        "path_match":    paths_a == paths_b,
        "struct_paths_tmpl": paths_a,
        "struct_paths_llm":  paths_b,
        "uses_no_mask":  "0x" not in b and ">>" not in b,
        "uses_struct_param": "struct lld_" in b,
    }


def _patch_one(ip: str, changes, new_ir, lld_src: Path, no_llm: bool,
               llm: Optional[LLMClient] = None) -> str:
    """Apply patch with template or LLM and return full file content."""
    td = Path(tempfile.mkdtemp())
    lld = td / "lld.h"
    shutil.copy2(lld_src, lld)
    patcher = LLDPatcher(ip=ip, llm_client=llm, no_llm=no_llm)
    content = patcher.patch(lld, changes, new_ir=new_ir)
    shutil.rmtree(td, ignore_errors=True)
    return content


def _extract_block(content: str, reg: str) -> str:
    """Extract one register block from the full lld.h content."""
    start = content.find(f"REGISTER: {reg}")
    if start == -1:
        return f"(no block found for {reg})"
    end = content.find("REGISTER:", start + 1)
    return content[start:end] if end != -1 else content[start:]


# ── Main comparison ───────────────────────────────────────────────────────────
def run_comparison(ip: str, model: str) -> None:
    print(f"\n{'='*70}")
    print(f"  LLM vs Template Comparison")
    print(f"  IP      : {ip}")
    print(f"  Model   : {model}")
    print(f"  Old SFR : {OLD_SFR}")
    print(f"  New SFR : {NEW_SFR}")
    print(f"{'='*70}\n")

    # ── Load LLM client ──────────────────────────────────────────────────────
    llm = LLMClient(ollama_model=model)
    if not llm.available:
        print(f"[ERROR] Ollama model '{model}' not available.")
        print(f"  Make sure Ollama is running:  ollama serve")
        print(f"  And the model is pulled:      ollama pull {model}")
        sys.exit(1)

    print(f"[LLM] Backend: {llm.backend}  model: {llm._ollama_model}\n")

    # ── Parse SFRs ──────────────────────────────────────────────────────────
    changes = classify_sfr_diff(OLD_SFR, NEW_SFR, ip=ip)
    new_ir  = SfrParser(ip=ip).parse_file(NEW_SFR)

    # ── Template run (no LLM) ────────────────────────────────────────────────
    print("[1/2] Generating template output...")
    tmpl_content = _patch_one(ip, changes, new_ir, LLD_SRC, no_llm=True)
    print(f"      Template: {len(tmpl_content)} chars\n")

    # ── LLM run ─────────────────────────────────────────────────────────────
    print("[2/2] Generating LLM output (Ollama)...")
    # Clear cache so we always get fresh LLM output for comparison
    cache_dir = Path(".lld_gen_cache")
    cache_dir.mkdir(exist_ok=True)
    llm_content = _patch_one(ip, changes, new_ir, LLD_SRC, no_llm=False, llm=llm)
    print(f"      LLM:      {len(llm_content)} chars\n")

    # ── Per-change comparison ─────────────────────────────────────────────────
    report_lines = [
        f"# LLM vs Template Comparison Report",
        f"",
        f"- **IP:** {ip}",
        f"- **Ollama model:** {llm._ollama_model}",
        f"- **Old SFR:** `{OLD_SFR.name}`",
        f"- **New SFR:** `{NEW_SFR.name}`",
        f"",
        f"## Change Summary",
        f"",
        f"| # | Change Type | Register | Field | Fn Match | Struct Path | No Masks | Status |",
        f"|---|-------------|----------|-------|----------|-------------|----------|--------|",
    ]

    table_rows = []
    per_change_details = []
    n_match = n_llm_wins = n_tmpl_only = 0

    # Group changes by register
    by_reg: dict[str, list[ChangeRecord]] = {}
    for cr in changes:
        by_reg.setdefault(cr.reg_name, []).append(cr)

    change_idx = 0
    for reg, crs in by_reg.items():
        tmpl_block = _extract_block(tmpl_content, reg)
        llm_block  = _extract_block(llm_content, reg)

        for cr in crs:
            change_idx += 1
            m = _structural_match(tmpl_block, llm_block)

            fn_ok   = "✅" if m["fn_match"]    else "⚠️"
            path_ok = "✅" if m["path_match"]  else "⚠️"
            mask_ok = "✅" if m["uses_no_mask"] else "❌"
            sp_ok   = "✅" if m["uses_struct_param"] else "❌"

            if m["fn_match"] and m["uses_no_mask"] and m["uses_struct_param"]:
                status = "MATCH"
                n_match += 1
            elif not m["fn_match"]:
                status = "FN_DIFF"
                n_llm_wins += 1
            else:
                status = "TMPL_ONLY"
                n_tmpl_only += 1

            row = (
                f"| {change_idx} | {cr.change_type} | {reg} | {cr.field_name or '-'} "
                f"| {fn_ok} | {path_ok} | {mask_ok} | **{status}** |"
            )
            table_rows.append(row)

            # Detailed diff for this change
            diff_text = _unified_diff(
                tmpl_block.strip(), llm_block.strip(),
                from_file=f"template/{reg}", to_file=f"llm/{reg}"
            )
            per_change_details.append(
                f"\n---\n"
                f"### Change {change_idx}: `{cr.change_type}` — `{reg}.{cr.field_name or reg}`\n"
                f"\n**Structural analysis:**\n"
                f"- Function names match: {fn_ok} {'(same set)' if m['fn_match'] else str(m['fns_only_tmpl']) + ' missing from LLM'}\n"
                f"- Struct paths match: {path_ok}\n"
                f"  - Template paths: `{', '.join(str(p) for p in m['struct_paths_tmpl']) or 'none'}`\n"
                f"  - LLM paths:      `{', '.join(str(p) for p in m['struct_paths_llm']) or 'none'}`\n"
                f"- No raw masks in LLM output: {mask_ok}\n"
                f"- Uses struct param: {sp_ok}\n"
                f"\n**Unified diff (template → LLM):**\n"
                f"```diff\n{diff_text}\n```\n"
            )

            # Print to console
            print(f"  [{change_idx:2d}] {cr.change_type:22} {reg:12} {cr.field_name or '-':15} "
                  f"fn:{fn_ok} path:{path_ok} masks:{mask_ok} → {status}")

    # ── Full diff ────────────────────────────────────────────────────────────
    full_diff = _unified_diff(
        tmpl_content, llm_content,
        from_file="lld_pmu.h (template)", to_file="lld_pmu.h (llm)"
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    total = change_idx
    print(f"\n{'─'*60}")
    print(f"  Total changes : {total}")
    print(f"  MATCH (same)  : {n_match}")
    print(f"  FN_DIFF       : {n_llm_wins}  (LLM generated different fn names)")
    print(f"  TMPL_ONLY     : {n_tmpl_only}")
    print(f"{'─'*60}")
    print(f"\nReport written → {REPORT_OUT}")

    # ── Write report ─────────────────────────────────────────────────────────
    report_lines += table_rows
    report_lines += [
        f"",
        f"**Legend:** ✅ correct  ⚠️ differs  ❌ missing",
        f"",
        f"| | Count |",
        f"|---|---|",
        f"| **MATCH** (template == LLM, struct-correct) | **{n_match}** |",
        f"| **FN_DIFF** (LLM used different fn names) | **{n_llm_wins}** |",
        f"| **TMPL_ONLY** (LLM missing functions) | **{n_tmpl_only}** |",
        f"| **Total** | **{total}** |",
        f"",
        f"## Per-Change Details",
    ]
    report_lines += per_change_details
    report_lines += [
        f"",
        f"---",
        f"## Full File Diff (template → LLM)",
        f"",
        f"```diff",
        full_diff[:8000],  # cap at 8KB
        f"```",
        f"",
        f"*(diff truncated to first 8000 chars if very long)*",
    ]

    REPORT_OUT.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nFull report: file:///{REPORT_OUT.as_posix()}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Compare LLM vs template LLD generation")
    ap.add_argument("--ip",    default="PMU",             help="IP name (default: PMU)")
    ap.add_argument("--model", default="qwen2.5-coder",   help="Ollama model name")
    args = ap.parse_args()
    run_comparison(ip=args.ip, model=args.model)
