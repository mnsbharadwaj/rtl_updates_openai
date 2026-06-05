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
import time
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
from lld_gen.config import load_config, discover_jobs, load_workflow_config
from lld_gen.batch_runner import BatchRunner


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
    _t0 = time.perf_counter()

    print("=" * 70)
    print(f" LLD Auto-Patcher -- IP: {ip}  (full pipeline)")
    print("=" * 70)

    # Step 1: Diff + classify
    print("\n[1/4] Diff & Classify ...")
    changes = classify_sfr_diff(args.old, args.new, ip=ip)
    print(summarize_changes(changes))

    if not changes:
        print("No changes detected. lld.h is up to date.")
        elapsed = time.perf_counter() - _t0
        print(f"\n  Pipeline time: {elapsed:.2f}s")
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
        elapsed = time.perf_counter() - _t0
        print(f"\n  Pipeline time: {elapsed:.2f}s")
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

    elapsed = time.perf_counter() - _t0
    total_m, total_s = divmod(int(elapsed), 60)
    print("\n" + "=" * 70)
    print(" Done!")
    print(f"  IP detected   : {ip}")
    print(f"  lld.h         : {args.lld}")
    print(f"  test file     : {test_file}")
    print(f"  PR desc       : {pr_path}")
    print(f"  Pipeline time : {total_m}m {total_s:02d}s  ({elapsed:.2f}s)")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Sub-command: run-config  (batch mode from config file)
# ---------------------------------------------------------------------------
def _cmd_run_config(args: argparse.Namespace) -> None:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[ERROR] Config file not found: {cfg_path}")
        print("  Run:  python -m lld_gen.main_sfr init-config  to create a starter config")
        sys.exit(1)

    cfg = load_config(cfg_path)
    # CLI flags override config values
    if args.no_llm:
        cfg.no_llm = True
    if args.no_git:
        cfg.no_git = True

    runner  = BatchRunner(cfg)
    results = runner.run()   # timing printed inside run()

    fail_count = sum(1 for r in results if r.status == "FAIL")
    if fail_count:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sub-command: init-config  (generate starter lld_patcher.yaml)
# ---------------------------------------------------------------------------
_CONFIG_TEMPLATE = """\
# lld_patcher.yaml -- LLD Auto-Patcher Configuration
# Generated by: python -m lld_gen.main_sfr init-config
#
# Usage: python -m lld_gen.main_sfr run-config --config lld_patcher.yaml

# -----------------------------------------------------------------------
# Directories  (relative paths resolved from this config file location)
# -----------------------------------------------------------------------
sfr_old_dir: "./old_sfr"         # OLD SFR headers  (sfr_pmu.h, sfr_uart.h ...)
sfr_new_dir: "./new_sfr"         # NEW SFR headers  (sfr_pmu.h, sfr_uart.h ...)
lld_dir:     "./lld"             # Existing LLD headers (lld_pmu.h, lld_uart.h ...)
output_dir:  "./lld_patched"     # Patched LLD files written HERE
tests_dir:   "./tests_generated" # Generated unit test .c files written HERE

# -----------------------------------------------------------------------
# LLM settings
# -----------------------------------------------------------------------
no_llm:   true       # true = template fallback only (no LLM needed)
hf_token: ""         # HuggingFace token (or set HF_TOKEN env variable)

# -----------------------------------------------------------------------
# Git / PR settings
# -----------------------------------------------------------------------
no_git: true         # true = skip git add and PR_DESCRIPTION.md generation

# GitHub repository URL for PR creation link in PR_DESCRIPTION.md
# Auto-detected from git remote origin if not set here.
# Example: https://github.com/mnsbharadwaj/rtl_updates_openai
github_url: ""       # leave empty to auto-detect from git remote

# -----------------------------------------------------------------------
# Unit test generation mode
# -----------------------------------------------------------------------
# false (default) -- fast deterministic template tests
#   Each LLD function gets a basic test: mask/shift/RMW isolation checks.
#   No API calls, instant generation, fully reproducible.
#
# true            -- LLM-generated rich unit tests (requires LLM enabled)
#   The LLM writes tests that also cover:
#     - Boundary values (value=0, value=max_for_field)
#     - RMW isolation (adjacent field bits must not be touched)
#     - Reset value check (if register has non-zero reset)
#     - Access constraint (RO: no setter; W1C: write-1-to-clear)
#   Falls back silently to template if LLM fails or is unavailable.
#   Requires: no_llm: false  AND  hf_token set (or HF_TOKEN env var)
llm_test_gen: false

# -----------------------------------------------------------------------
# Compiler settings
# -----------------------------------------------------------------------
gcc: null            # Path to gcc (null = auto-search PATH)

# -----------------------------------------------------------------------
# Optional: Restrict which IPs to process (comment out to process all)
# -----------------------------------------------------------------------
# ip_list:
#   - PMU
#   - UART

# -----------------------------------------------------------------------
# Optional: Per-IP overrides (override global settings per IP)
# -----------------------------------------------------------------------
# ip_overrides:
#   PMU:
#     no_llm: false
#     hf_token: "hf_your_token"
"""


def _cmd_init_config(args: argparse.Namespace) -> None:
    out = Path(args.output)

    # ── Write config file ────────────────────────────────────────────────────
    out.parent.mkdir(parents=True, exist_ok=True)   # create workspace dir if needed
    if out.exists() and not args.force:
        print(f"[SKIP] {out} already exists. Use --force to overwrite.")
    else:
        out.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"[OK] Config written -> {out}")


    # ── Auto-create all directories ──────────────────────────────────────────
    base = out.parent
    dirs = {
        "old_sfr":          "Place your OLD SFR header files here (sfr_pmu.h, sfr_uart.h ...)",
        "new_sfr":          "Place your NEW/UPDATED SFR header files here (sfr_pmu.h, sfr_uart.h ...)",
        "lld":              "Place your existing LLD header files here (lld_pmu.h, lld_uart.h ...)\nThese files will NEVER be modified. Patched copies go to lld_patched/.",
        "lld_patched":      "Patched LLD files will be written here automatically. Do not edit manually.",
        "tests_generated":  "Generated unit test .c files will be written here automatically.",
    }

    print("\n[SETUP] Creating project directory structure...")
    for dirname, description in dirs.items():
        d = base / dirname
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.txt"
        if not readme.exists():
            readme.write_text(
                f"{dirname}/\n{'=' * len(dirname)}\n\n{description}\n\n"
                f"File naming convention:\n"
                f"  SFR files : sfr_<ipname>.h   (e.g. sfr_pmu.h, sfr_uart.h)\n"
                f"  LLD files : lld_<ipname>.h   (e.g. lld_pmu.h, lld_uart.h)\n"
                f"              OR <ipname>_lld.h (e.g. pmu_lld.h)\n",
                encoding="utf-8",
            )
        status = "[created]" if not (d / "README.txt").exists() else "[ready]  "
        print(f"  {status} {d}")

    print(f"""
[DONE] Project workspace ready at: {base}

  Directory layout:
    {base}/
    |-- lld_patcher.yaml      <- config file (edit paths if needed)
    |-- old_sfr/              <- DROP your old SFR .h files here
    |-- new_sfr/              <- DROP your new SFR .h files here
    |-- lld/                  <- DROP your existing LLD .h files here
    |-- lld_patched/          <- patched LLD files written here (auto)
    `-- tests_generated/      <- unit test .c files written here (auto)

  File naming (IP auto-detected):
    sfr_pmu.h  ->  IP = PMU  ->  lld_pmu.h (or pmu_lld.h or pmu.h)
    sfr_uart.h ->  IP = UART ->  lld_uart.h

  Next steps:
    1. Copy your old SFR files into old_sfr/
    2. Copy your new SFR files into new_sfr/
    3. Copy your existing LLD files into lld/
    4. Run:
         python -m lld_gen.main_sfr run-config --config {out}
""")


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
# Sub-command: workflow (v3.0 full pipeline)
# ---------------------------------------------------------------------------
def _cmd_workflow(args: argparse.Namespace) -> None:
    """Full end-to-end: IPxact repo -> SFR -> LLD patch -> Bitbucket PR."""
    from lld_gen.workflow_runner import WorkflowRunner

    cfg = load_workflow_config(args.config)
    if args.no_llm:
        cfg.no_llm = True
    if args.no_git:
        cfg.no_git = True

    runner = WorkflowRunner(cfg)

    if getattr(args, "dry_run", False):
        # Dry-run: clone and diff only, no patching
        _t0 = time.perf_counter()
        ipxact_path, lld_path = runner._clone_repos()
        ip_sfr_map = runner._convert_ipxact(ipxact_path)
        print(f"\n[DRY-RUN] Would process {len(ip_sfr_map)} IP(s): {', '.join(ip_sfr_map)}")
        for ip, new_sfr in sorted(ip_sfr_map.items()):
            old_sfr = runner._find_current_sfr(lld_path, ip)
            if old_sfr:
                changes = classify_sfr_diff(old_sfr, new_sfr, ip=ip)
                print(f"  {ip}: {len(changes)} change(s)")
                print("  " + "\n  ".join(summarize_changes(changes).splitlines()[1:]))
            else:
                print(f"  {ip}: No current SFR found -- would be skipped")
        elapsed = time.perf_counter() - _t0
        total_m, total_s = divmod(int(elapsed), 60)
        print(f"\n  [DRY-RUN] Pipeline time: {total_m}m {total_s:02d}s  ({elapsed:.2f}s)")
        return

    _t0 = time.perf_counter()
    result = runner.run()   # timing printed inside WorkflowRunResult.print_summary()
    elapsed = time.perf_counter() - _t0
    total_m, total_s = divmod(int(elapsed), 60)
    print(f"\n[SUMMARY] Pipeline time: {total_m}m {total_s:02d}s ({elapsed:.2f}s)")
    sys.exit(0 if result.all_ok else 1)


def _cmd_init_workflow_config(args: argparse.Namespace) -> None:
    """Generate a starter workflow_config.yaml."""
    import shutil as _shutil
    out = Path(getattr(args, "output", "workflow_config.yaml"))
    if out.exists() and not getattr(args, "force", False):
        print(f"[init-workflow-config] {out} already exists. Use --force to overwrite.")
        return
    # Copy the bundled template
    template = Path(__file__).parent.parent / "workflow_config.yaml"
    if template.exists():
        _shutil.copy2(template, out)
        print(f"[init-workflow-config] Written: {out}")
    else:
        print(f"[init-workflow-config] Template not found: {template}")
        print("  Create workflow_config.yaml manually using the documentation.")


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

    # ── run-config (batch from config file) ─────────────────────────────────
    rc = sub.add_parser(
        "run-config",
        help="Batch pipeline: process all SFR pairs defined in a config file",
    )
    rc.add_argument(
        "--config", default="lld_patcher.yaml", metavar="FILE",
        help="Path to lld_patcher.yaml config file (default: lld_patcher.yaml)",
    )
    rc.add_argument("--no-llm", action="store_true", help="Override config: disable LLM")
    rc.add_argument("--no-git", action="store_true", help="Override config: skip git add")

    # ── init-config (generate starter config) ────────────────────────────────
    ic = sub.add_parser(
        "init-config",
        help="Generate a starter lld_patcher.yaml in the current directory",
    )
    ic.add_argument(
        "--output", default="lld_patcher.yaml", metavar="FILE",
        help="Where to write the config file (default: lld_patcher.yaml)",
    )
    ic.add_argument("--force", action="store_true", help="Overwrite existing config")

    # ── workflow (full end-to-end: IPxact → PR) ────────────────────────────
    wf = sub.add_parser(
        "workflow",
        help="Full v3.0 pipeline: clone IPxact repo → convert → diff → patch LLD → PR",
    )
    wf.add_argument(
        "--config", default="workflow_config.yaml", metavar="FILE",
        help="Path to workflow_config.yaml (default: workflow_config.yaml)",
    )
    wf.add_argument("--no-llm",  action="store_true", help="Override: disable LLM")
    wf.add_argument("--no-git",  action="store_true", help="Override: skip git/PR")
    wf.add_argument("--dry-run", action="store_true", help="Clone + diff only, no patch")

    # ── init-workflow-config ─────────────────────────────────────────────────
    iwf = sub.add_parser(
        "init-workflow-config",
        help="Generate a starter workflow_config.yaml in the current directory",
    )
    iwf.add_argument(
        "--output", default="workflow_config.yaml", metavar="FILE",
        help="Where to write the config file",
    )
    iwf.add_argument("--force", action="store_true", help="Overwrite existing")

    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    dispatch = {
        "diff":                  _cmd_diff,
        "patch":                 _cmd_patch,
        "compile-check":         _cmd_compile_check,
        "stage":                 _cmd_stage,
        "run":                   _cmd_run,
        "run-config":            _cmd_run_config,
        "init-config":           _cmd_init_config,
        "workflow":               _cmd_workflow,
        "init-workflow-config":   _cmd_init_workflow_config,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
