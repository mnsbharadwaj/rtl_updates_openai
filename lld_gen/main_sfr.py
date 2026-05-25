"""
main_sfr.py -- LLD Auto-Patcher CLI Entry Point

Usage:

  Full pipeline (recommended):
    export HF_TOKEN=hf_xxxx
    python -m lld_gen.main_sfr run \\
        --old sfr_old.h --new sfr_new.h \\
        --lld lld.h --ip DMA

  Step by step:
    python -m lld_gen.main_sfr diff \\
        --old sfr_old.h --new sfr_new.h --classify-out changes.json

    python -m lld_gen.main_sfr patch \\
        --changes changes.json --lld lld.h \\
        --sfr-new sfr_new.h --out lld.h

    python -m lld_gen.main_sfr compile-check \\
        --sfr sfr_new.h --lld lld.h \\
        --tests tests/test_lld_generated.c

    python -m lld_gen.main_sfr stage \\
        --changes changes.json \\
        --old-sfr sfr_old.h --new-sfr sfr_new.h \\
        --lld lld.h
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as python -m lld_gen.main_sfr
sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.sfr_diff_analyzer import (
    SfrDiffAnalyzer, SfrParser, classify_sfr_diff,
    changes_to_json, summarize_changes,
)
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient
from lld_gen.compile_check import run_compile_check
from lld_gen.pr_stage import stage_pr


# ---------------------------------------------------------------------------
# Sub-command: diff
# ---------------------------------------------------------------------------
def _cmd_diff(args: argparse.Namespace) -> None:
    print(f"[diff] Parsing {args.old} ...")
    print(f"[diff] Parsing {args.new} ...")
    changes = classify_sfr_diff(args.old, args.new, ip=args.ip or "")
    print(summarize_changes(changes))

    if args.classify_out:
        out = Path(args.classify_out)
        out.write_text(changes_to_json(changes), encoding="utf-8")
        print(f"[diff] Changes written to {out}")


# ---------------------------------------------------------------------------
# Sub-command: patch
# ---------------------------------------------------------------------------
def _cmd_patch(args: argparse.Namespace) -> None:
    from lld_gen.sfr_diff_analyzer import ChangeRecord

    # Load changes
    if args.changes:
        raw    = Path(args.changes).read_text(encoding="utf-8")
        data   = json.loads(raw)
        # Reconstruct ChangeRecord list (simplified -- fields as dicts)
        changes = _load_change_records(data)
        print(f"[patch] Loaded {len(changes)} changes from {args.changes}")
    else:
        if not args.old or not args.new:
            print("[patch] ERROR: provide --changes or both --old + --new")
            sys.exit(1)
        changes = classify_sfr_diff(args.old, args.new, ip=args.ip or "")
        print(summarize_changes(changes))

    # Build LLM client
    llm = _make_llm(args)

    # Parse new SFR for REG_ADDED blocks
    new_ir = None
    if args.sfr_new:
        parser = SfrParser(ip=args.ip or "")
        new_ir = parser.parse_file(args.sfr_new)

    ip      = args.ip or ""
    patcher = LLDPatcher(ip=ip, llm_client=llm, no_llm=getattr(args, "no_llm", False))

    print(f"[patch] Patching {args.lld} ...")
    patcher.patch(args.lld, changes, new_ir=new_ir, out_path=args.out or args.lld)

    # Write test file
    tests_dir = Path(args.tests_dir) if getattr(args, "tests_dir", None) else Path(args.lld).parent
    test_file = tests_dir / "test_lld_generated.c"
    sfr_name  = Path(args.sfr_new).name if args.sfr_new else "sfr_new.h"
    lld_name  = Path(args.out or args.lld).name
    patcher.write_test_file(test_file, sfr_name, lld_name)
    print(f"[patch] Test file -> {test_file}")


# ---------------------------------------------------------------------------
# Sub-command: compile-check
# ---------------------------------------------------------------------------
def _cmd_compile_check(args: argparse.Namespace) -> None:
    llm = _make_llm(args)
    result = run_compile_check(
        test_file=args.tests,
        sfr_new=args.sfr,
        lld_file=args.lld,
        llm_client=llm,
        gcc_exe=getattr(args, "gcc", None),
    )
    if result.success:
        print("[compile-check] PASSED")
        if result.needs_review:
            print(f"[compile-check] NOTE: {len(result.needs_review)} function(s) need manual review")
    else:
        print("[compile-check] FAILED")
        print(result.stderr)
        if result.needs_review:
            print("Functions requiring manual review:")
            for fn in result.needs_review:
                print(f"  - {fn}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sub-command: stage
# ---------------------------------------------------------------------------
def _cmd_stage(args: argparse.Namespace) -> None:
    # Re-classify to get full change list
    changes = classify_sfr_diff(args.old_sfr, args.new_sfr, ip=args.ip or "")
    compile_result_mock = type("CR", (), {"success": True, "needs_review": [], "retries_used": 0})()

    test_file = getattr(args, "tests", None) or (
        str(Path(args.lld).parent / "test_lld_generated.c")
    )
    pr_path = stage_pr(
        ip=args.ip or "",
        changes=changes,
        compile_result=compile_result_mock,
        lld_file=args.lld,
        test_file=test_file,
        sfr_new=args.new_sfr,
        no_git=getattr(args, "no_git", False),
    )
    print(f"[stage] PR description -> {pr_path}")


# ---------------------------------------------------------------------------
# Sub-command: run (full pipeline)
# ---------------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> None:
    # Auto-detect IP from filename if not provided
    from lld_gen.sfr_diff_analyzer import _ip_from_filename
    ip = args.ip or _ip_from_filename(args.old)

    print("=" * 70)
    print(f" LLD Auto-Patcher -- IP: {ip}  (full pipeline)")
    print("=" * 70)

    # Step 1: Diff + classify
    print("\n[1/4] Diff & Classify ...")
    changes = classify_sfr_diff(args.old, args.new, ip=ip)
    print(summarize_changes(changes))

    if not changes:
        print("No changes detected. lld.h is up to date.")
        return

    # Step 2: Patch
    print("\n[2/4] Patch lld.h ...")
    parser = SfrParser(ip=ip)
    new_ir = parser.parse_file(args.new)
    llm    = _make_llm(args)
    patcher = LLDPatcher(
        ip=ip,
        llm_client=llm,
        no_llm=getattr(args, "no_llm", False),
    )
    patcher.patch(args.lld, changes, new_ir=new_ir)

    test_file = Path(args.lld).parent / "test_lld_generated.c"
    sfr_name  = Path(args.new).name
    lld_name  = Path(args.lld).name
    patcher.write_test_file(test_file, sfr_name, lld_name)

    # Step 3: Compile check
    print("\n[3/4] Compile-check ...")
    compile_result = run_compile_check(
        test_file=test_file,
        sfr_new=args.new,
        lld_file=args.lld,
        llm_client=llm,
        gcc_exe=getattr(args, "gcc", None),
    )

    if not compile_result.success and not compile_result.needs_review:
        print("[FATAL] Compile check failed. PR staging aborted.")
        sys.exit(1)

    # Step 4: Stage PR
    print("\n[4/4] Stage PR ...")
    pr_path = stage_pr(
        ip=ip,
        changes=changes,
        compile_result=compile_result,
        lld_file=args.lld,
        test_file=test_file,
        sfr_new=args.new,
        no_git=getattr(args, "no_git", False),
    )

    print("\n" + "=" * 70)
    print(" Done!")
    print(f"  IP detected: {ip}")
    print(f"  lld.h      : {args.lld}")
    print(f"  test file  : {test_file}")
    print(f"  PR desc    : {pr_path}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_llm(args: argparse.Namespace) -> LLMClient:
    if getattr(args, "no_llm", False):
        return LLMClient(hf_token="")   # no-op client
    token = getattr(args, "hf_token", None) or os.environ.get("HF_TOKEN", "")
    client = LLMClient(hf_token=token)
    if not client.available:
        print("[LLM] WARNING: HF_TOKEN not set -- LLM calls will use template fallback")
    return client


def _load_change_records(data: list) -> list:
    """Reconstruct ChangeRecord list from JSON (simplified -- fields as plain dicts)."""
    from lld_gen.sfr_diff_analyzer import ChangeRecord, FieldIR, RegisterIR

    def _fir(d: dict | None) -> FieldIR | None:
        if not d:
            return None
        mask = int(d["mask"].rstrip("U"), 16) if isinstance(d["mask"], str) else d["mask"]
        rst  = int(d["reset"].rstrip("U"), 16) if isinstance(d.get("reset", "0"), str) else 0
        return FieldIR(
            name=d["name"], reg_name=d["reg_name"],
            mask=mask, shift=d["shift"],
            msb=d["msb"], lsb=d["lsb"],
            access=d["access"], reset=rst,
            desc=d.get("desc", ""), ip=d.get("ip", ""),
        )

    def _rir(d: dict | None) -> RegisterIR | None:
        if not d:
            return None
        return RegisterIR(name=d["name"], offset=d["offset"], desc=d.get("desc", ""))

    records = []
    for item in data:
        records.append(ChangeRecord(
            change_type=item["change_type"],
            reg_name=item["reg_name"],
            field_name=item.get("field_name"),
            old_field=_fir(item.get("old_field")),
            new_field=_fir(item.get("new_field")),
            old_reg=_rir(item.get("old_reg")),
            new_reg=_rir(item.get("new_reg")),
            needs_llm=item.get("needs_llm", False),
            details=item.get("details", []),
        ))
    return records


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m lld_gen.main_sfr",
        description="LLD Auto-Patcher -- Automated LLD generation from SFR diff",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ── diff ────────────────────────────────────────────────────────────────
    d = sub.add_parser("diff", help="Parse & classify SFR changes")
    d.add_argument("--old",  required=True, metavar="FILE", help="Old sfr.h")
    d.add_argument("--new",  required=True, metavar="FILE", help="New sfr.h")
    d.add_argument("--ip",   default="",   metavar="NAME", help="Peripheral IP name")
    d.add_argument("--classify-out", metavar="FILE", help="Write changes.json here")

    # ── patch ───────────────────────────────────────────────────────────────
    pa = sub.add_parser("patch", help="Apply patches to lld.h")
    pa.add_argument("--changes",  metavar="FILE", help="changes.json from diff step")
    pa.add_argument("--old",      metavar="FILE", help="Old sfr.h (alternative to --changes)")
    pa.add_argument("--new",      metavar="FILE", help="New sfr.h (alternative to --changes)")
    pa.add_argument("--lld",      required=True,  metavar="FILE", help="Existing lld.h")
    pa.add_argument("--sfr-new",  metavar="FILE", help="New sfr.h (for REG_ADDED)")
    pa.add_argument("--out",      metavar="FILE", help="Output path for lld.h (default: in-place)")
    pa.add_argument("--ip",       default="",   metavar="NAME")
    pa.add_argument("--no-llm",   action="store_true", help="Disable LLM; use template fallback")
    pa.add_argument("--hf-token", default="",   metavar="TOKEN", help="HuggingFace API token")
    pa.add_argument("--tests-dir",metavar="DIR", help="Directory for test_lld_generated.c")

    # ── compile-check ────────────────────────────────────────────────────────
    cc = sub.add_parser("compile-check", help="Run gcc compile verification")
    cc.add_argument("--sfr",   required=True, metavar="FILE", help="New sfr.h")
    cc.add_argument("--lld",   required=True, metavar="FILE", help="lld.h to check")
    cc.add_argument("--tests", required=True, metavar="FILE", help="test_lld_generated.c")
    cc.add_argument("--gcc",   default=None,  metavar="EXE",  help="Path to gcc executable")
    cc.add_argument("--no-llm",action="store_true")
    cc.add_argument("--hf-token", default="", metavar="TOKEN")

    # ── stage ────────────────────────────────────────────────────────────────
    st = sub.add_parser("stage", help="Generate PR_DESCRIPTION.md and git add")
    st.add_argument("--changes",  metavar="FILE")
    st.add_argument("--old-sfr",  required=True, metavar="FILE")
    st.add_argument("--new-sfr",  required=True, metavar="FILE")
    st.add_argument("--lld",      required=True, metavar="FILE")
    st.add_argument("--tests",    metavar="FILE")
    st.add_argument("--ip",       default="",   metavar="NAME")
    st.add_argument("--no-git",   action="store_true", help="Skip git add")

    # ── run (all-in-one) ─────────────────────────────────────────────────────
    r = sub.add_parser("run", help="Full pipeline: diff -> patch -> compile-check -> stage")
    r.add_argument("--old",     required=True, metavar="FILE", help="Old sfr.h")
    r.add_argument("--new",     required=True, metavar="FILE", help="New sfr.h")
    r.add_argument("--lld",     required=True, metavar="FILE", help="Existing lld.h")
    r.add_argument("--ip",      default="",   metavar="NAME",  help="Peripheral IP name (auto-detected from filename if omitted)")
    r.add_argument("--no-llm",  action="store_true", help="Disable LLM")
    r.add_argument("--no-git",  action="store_true", help="Skip git add")
    r.add_argument("--gcc",     default=None,  metavar="EXE")
    r.add_argument("--hf-token",default="",   metavar="TOKEN")

    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    dispatch = {
        "diff":           _cmd_diff,
        "patch":          _cmd_patch,
        "compile-check":  _cmd_compile_check,
        "stage":          _cmd_stage,
        "run":            _cmd_run,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
