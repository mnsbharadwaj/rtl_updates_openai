"""
batch_runner.py -- Batch pipeline runner driven by lld_patcher.yaml config

Processes multiple IPs in one shot:
  1. Reads config file
  2. Discovers all SFR old/new pairs + matching LLD files
  3. Runs the full patch pipeline per IP
  4. Writes patched LLD files to output_dir (config-specified)
  5. Writes unit test files to tests_dir (config-specified)
  6. Prints a summary report
"""
from __future__ import annotations

import logging

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lld_gen.config import (
    PatcherConfig, IPJob, load_config, discover_jobs,
    save_checkpoint, load_checkpoint, clear_checkpoint,
)
from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, summarize_changes, SfrParser, ChangeType,
)
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient
from lld_gen.compile_check import run_compile_check, _find_gcc, GccNotFoundError
from lld_gen.pr_stage import stage_pr
from lld_gen.lld_cross_ref import find_cross_refs, format_cross_ref_report

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-IP result
# ---------------------------------------------------------------------------
@dataclass
class IPResult:
    ip:           str
    old_sfr:      Path
    new_sfr:      Path
    lld_in:       Path
    lld_out:      Path
    n_changes:    int = 0
    status:       str = "PENDING"   # OK | WARN | FAIL | SKIP
    error:        str = ""
    compile_ok:   bool = False
    elapsed_s:    float = 0.0       # wall-clock seconds for this IP


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
class BatchRunner:
    """
    Runs the LLD Auto-Patcher pipeline for every IP discovered from config.

    Output directory layout (all paths from config):
        output_dir/
            lld_pmu.h           <- patched LLD for PMU
            lld_uart.h          <- patched LLD for UART
            ...
        tests_dir/
            test_lld_pmu.c      <- generated unit tests for PMU
            test_lld_uart.c     <- generated unit tests for UART
            ...
    """

    def __init__(self, cfg: PatcherConfig):
        self.cfg = cfg
        self._results: List[IPResult] = []

    def run(self) -> List[IPResult]:
        """Discover jobs from config and run each one."""
        logger.info("=" * 70)
        logger.info("  LLD Auto-Patcher -- Batch Mode")
        logger.info(f"  sfr_old_dir : {self.cfg.sfr_old_dir}")
        logger.info(f"  sfr_new_dir : {self.cfg.sfr_new_dir}")
        logger.info(f"  lld_dir     : {self.cfg.lld_dir}")
        logger.info(f"  output_dir  : {self.cfg.output_dir}")
        logger.info(f"  tests_dir   : {self.cfg.tests_dir}")
        logger.info("=" * 70)

        # Discover all IP jobs
        logger.info("[DISCOVER] Scanning directories for SFR pairs...")
        jobs = discover_jobs(self.cfg)

        if not jobs:
            logger.warning("[DISCOVER] No SFR pairs found. Check directory paths and naming conventions.")
            logger.warning(
                "  Expected naming convention (any of these work):\n"
                "    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/lld_pmu.h\n"
                "    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/pmu_lld.h\n"
                "    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/pmu.h"
            )
            return []

        logger.info(f"[DISCOVER] Found {len(jobs)} IP(s) to process:")
        for j in jobs:
            logger.info(f"  {j.ip:<12} {j.old_sfr.name} -> {j.new_sfr.name} -> {j.lld.name}")

        # Ensure output directories exist
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.tests_dir.mkdir(parents=True, exist_ok=True)

        # ── GCC gate (conditional on compile_check config) ────────────────
        compile_check = getattr(self.cfg, "compile_check", True)
        gcc_exe: Optional[str] = None

        if compile_check:
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)
            except GccNotFoundError:
                logger.error("=" * 70)
                logger.error("  [X]  GCC NOT FOUND -- PIPELINE STOPPED")
                logger.error("  -------------------------------------------------")
                logger.error("  compile_check: true in your config, but gcc is")
                logger.error("  not found in PATH.")
                logger.error("")
                logger.error("  gcc is REQUIRED for LLD compile verification.")
                logger.error("  Install gcc and re-run -- the pipeline will resume")
                logger.error("  from this point automatically.")
                logger.error("")
                logger.error("  Fix options:")
                logger.error("    * Linux:   sudo apt install gcc")
                logger.error("    * macOS:   brew install gcc")
                logger.error("    * Windows: choco install mingw")
                logger.error("    * Or set 'gcc: /path/to/gcc' in your config YAML")
                logger.error("    * Or set 'compile_check: false' to skip (not recommended)")
                logger.error("=" * 70)
                save_checkpoint(self.cfg.output_dir, completed_ips=[],
                                stage="pre_gcc",
                                context={"n_jobs": len(jobs)})
                logger.error("  [STOPPED] Install gcc and re-run to resume.")
                return []
            logger.debug(f"  [GCC] Found: {gcc_exe}")
        else:
            logger.info("  [GCC] compile_check: false -> skipping gcc verification")
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)
            except GccNotFoundError:
                gcc_exe = None
            if gcc_exe:
                logger.debug(f"  [GCC] Found anyway: {gcc_exe}")

        # ── LLM availability check ────────────────────────────────────────
        force_no_llm = getattr(self.cfg, "force_no_llm", False)
        if not self.cfg.no_llm and not force_no_llm:
            # Quick probe: create a temp LLM client to check availability
            from lld_gen.llm_client import load_llm_config
            _test_llm = LLMClient(load_llm_config({
                "no_llm": False,
                "hf_token": self.cfg.hf_token or "",
                "ollama_model": self.cfg.ollama_model or "",
            }))
            if not _test_llm.available:
                logger.warning("=" * 70)
                logger.warning("  [!]  LLM BACKEND NOT AVAILABLE")
                logger.warning("  -------------------------------------------------")
                logger.warning("  The following change types REQUIRE LLM but will be")
                logger.warning("  flagged for MANUAL REVIEW instead of auto-patched:")
                logger.warning("    COMMENT_CHANGED, MULTI_CHANGED, FIELD_SPLIT/MERGED,")
                logger.warning("    FIELD_POLARITY_CHANGED, FIELD_ENUM_CHANGED, etc.")
                logger.warning("")
                logger.warning("  Options:")
                logger.warning("    1. Fix LLM config and re-run (recommended)")
                logger.warning("       -> Set 'ollama_model:' or configure 'llm:' block")
                logger.warning("    2. Continue with template-only mode")
                logger.warning("       -> LLM-needing changes flagged for manual review")
                logger.warning("    3. Set 'force_no_llm: true' in config to skip this prompt")
                logger.warning("=" * 70)
                try:
                    choice = input("  Continue without LLM? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "n"
                if choice not in ("y", "yes"):
                    save_checkpoint(self.cfg.output_dir, completed_ips=[],
                                    stage="pre_llm",
                                    context={"reason": "user chose to abort — LLM not available"})
                    logger.error("  [ABORT] Fix LLM config and re-run.")
                    return []
                logger.warning("  [LLM] Proceeding with template-only mode")
        elif force_no_llm and not self.cfg.no_llm:
            logger.warning("  [LLM] force_no_llm=true -> template-only mode "
                  "(LLM changes flagged for MANUAL REVIEW)")

        # ── Load checkpoint for resume ────────────────────────────────────
        checkpoint = load_checkpoint(self.cfg.output_dir)
        checkpoint_completed: set = set()
        if checkpoint:
            checkpoint_completed = set(checkpoint.get("completed_ips", []))

        # Process each IP
        self._pipeline_start = time.perf_counter()
        completed_ips: list = list(checkpoint_completed)

        for job in jobs:
            # Skip already-completed IPs (resume mode)
            if job.ip in checkpoint_completed:
                logger.info(f"  [RESUME] {job.ip}: already completed -- skipping")
                continue

            ip_start = time.perf_counter()
            result = self._run_one(job, gcc_exe=gcc_exe)
            result.elapsed_s = time.perf_counter() - ip_start
            self._results.append(result)
            completed_ips.append(job.ip)

            # Save checkpoint after each IP
            save_checkpoint(self.cfg.output_dir, completed_ips=completed_ips,
                            stage="ip_done", context={"last_ip": job.ip})

        self._total_elapsed = time.perf_counter() - self._pipeline_start

        # Clear checkpoint — pipeline completed successfully
        clear_checkpoint(self.cfg.output_dir)

        # Print summary
        self._print_summary()
        return self._results

    def _run_one(self, job: IPJob, gcc_exe: Optional[str] = None) -> IPResult:
        """Run the full pipeline for one IP."""
        logger.info("=" * 70)
        logger.info(f"  IP: {job.ip}")
        logger.info(f"  old: {job.old_sfr.name}  new: {job.new_sfr.name}  lld: {job.lld.name}")
        logger.info(f"  out: {job.out_lld}")
        logger.info("=" * 70)

        result = IPResult(
            ip=job.ip,
            old_sfr=job.old_sfr,
            new_sfr=job.new_sfr,
            lld_in=job.lld,
            lld_out=job.out_lld,
        )

        # Get per-IP overrides from config
        overrides = self.cfg.ip_overrides.get(job.ip.upper(), {})
        no_llm   = bool(overrides.get("no_llm",  self.cfg.no_llm))
        hf_token = str(overrides.get("hf_token", self.cfg.hf_token) or "")
        no_git   = bool(overrides.get("no_git",  self.cfg.no_git))

        # v3 flags (with safe fallbacks for old PatcherConfig)
        max_llm_retries  = getattr(self.cfg, "max_llm_retries",  5)
        cross_lld_scan   = getattr(self.cfg, "cross_lld_scan",   True)
        lld_search_depth = getattr(self.cfg, "lld_search_depth", 3)
        atomic_commits   = getattr(self.cfg, "atomic_commits",   True)
        commit_prefix    = getattr(self.cfg, "commit_prefix",    "feat(lld)")
        skip_reg_added   = getattr(self.cfg, "skip_reg_added",   True)
        skip_reg_deleted = getattr(self.cfg, "skip_reg_deleted", True)

        try:
            # Step 1: Classify changes
            logger.info(f"  [1/5] Diff & Classify {job.ip}...")
            all_changes = classify_sfr_diff(job.old_sfr, job.new_sfr, ip=job.ip)
            result.n_changes = len(all_changes)
            logger.info(f"  {summarize_changes(all_changes).replace(chr(10), chr(10) + '  ')}")

            if not all_changes:
                logger.info(f"  [SKIP] No changes detected for {job.ip} -- lld.h is up to date.")
                shutil.copy2(job.lld, job.out_lld)
                result.status = "SKIP"
                return result

            # Separate manual-review vs auto-patchable changes
            manual_crs = [cr for cr in all_changes
                          if ChangeType.is_manual_review(cr.change_type)]
            auto_crs   = [cr for cr in all_changes
                          if not ChangeType.is_manual_review(cr.change_type)]

            manual_items = [
                f"{cr.change_type}: {cr.reg_name}"
                + (f".{cr.field_name}" if cr.field_name else "")
                for cr in manual_crs
            ]
            if manual_items:
                logger.warning(f"  [MANUAL-REVIEW] {len(manual_items)} items skipped: {', '.join(manual_items[:3])}"
                      + ("..." if len(manual_items) > 3 else ""))

            # Step 2: Copy LLD to output_dir, patch in-place
            logger.info(f"  [2/5] Patch {job.ip} LLD ({len(auto_crs)} auto-patchable changes)...")
            shutil.copy2(job.lld, job.out_lld)

            parser       = SfrParser(ip=job.ip)
            new_ir       = parser.parse_file(job.new_sfr)
            ollama_model = str(overrides.get("ollama_model", self.cfg.ollama_model) or "")
            from lld_gen.llm_client import load_llm_config
            llm_cfg = load_llm_config({
                "no_llm": no_llm,
                "hf_token": hf_token,
                "ollama_model": ollama_model,
            })
            llm = LLMClient(llm_cfg)
            patcher = LLDPatcher(ip=job.ip, llm_client=llm, no_llm=no_llm)

            patcher.patch(
                lld_path = job.out_lld,
                changes  = auto_crs,       # only auto-patchable changes
                new_ir   = new_ir,
                out_path = job.out_lld,
            )
            logger.debug(f"  Patched LLD written -> {job.out_lld}")

            # Write test file
            test_file    = job.tests_dir / f"test_lld_{job.ip.lower()}.c"
            lld_text     = job.out_lld.read_text(encoding="utf-8") if job.out_lld.exists() else ""
            llm_test_gen = bool(self.cfg.llm_test_gen)
            patcher.write_test_file(
                out_path   = test_file,
                sfr_new    = job.new_sfr.name,
                lld_new    = job.out_lld.name,
                new_ir     = new_ir,
                llm_client = llm if llm_test_gen else None,
                lld_text   = lld_text,
            )
            mode_tag = "[LLM+Template fallback]" if llm_test_gen else "[Template]"
            logger.debug(f"  Test file written  -> {test_file}  {mode_tag}")

            # Step 3: Compile check
            logger.info(f"  [3/5] Compile-check {job.ip}...")
            compile_result = run_compile_check(
                test_file  = test_file,
                sfr_new    = job.new_sfr,
                lld_file   = job.out_lld,
                llm_client = llm,
                gcc_exe    = gcc_exe,
                required   = self.cfg.compile_check,
            )
            result.compile_ok = compile_result.success
            if compile_result.success:
                logger.info(f"  [OK] Compile passed for {job.ip}")
            else:
                logger.warning(f"  [WARN] Compile failed for {job.ip} "
                      f"(needs_review={compile_result.needs_review})")

            # Step 4: Cross-LLD reference scan and AST refactoring
            cross_ref_md = ""
            ast_patched_files = []
            if cross_lld_scan and self.cfg.lld_dir.exists():
                logger.info(f"  [4/5] Cross-LLD scan and AST refactoring for {job.ip}...")
                # Build function names of changed fields to scan for
                changed_fn_names = []
                for cr in auto_crs:
                    if cr.field_name:
                        for verb in ("get", "set", "clear", "set1", "trigger"):
                            changed_fn_names.append(
                                f"lld_{job.ip.lower()}_"
                                f"{cr.reg_name.lower()}_{cr.field_name.lower()}_{verb}"
                            )
                if changed_fn_names:
                    cross_refs = find_cross_refs(
                        ip           = job.ip,
                        changed_fns  = changed_fn_names,
                        lld_dir      = self.cfg.lld_dir,
                        search_depth = lld_search_depth,
                    )
                    cross_ref_md = format_cross_ref_report(cross_refs)
                    if cross_refs:
                        logger.info(f"  [CROSS-REF] Found callers in "
                              f"{len(cross_refs)} file(s) -- flagged in PR description")

                from lld_gen.ast_refactor import refactor_cross_references
                ast_patched_files = refactor_cross_references(
                    cfg       = self.cfg,
                    ip        = job.ip,
                    new_ir    = new_ir,
                    auto_crs  = auto_crs,
                    out_lld   = job.out_lld,
                    test_file = test_file,
                )
            else:
                logger.info("  [4/5] Cross-LLD scan skipped")

            # Step 5: Stage PR
            logger.info(f"  [5/5] Stage PR for {job.ip}...")
            all_manual = manual_items + (compile_result.needs_review or [])
            stage_pr(
                ip                  = job.ip,
                changes             = all_changes,
                compile_result      = compile_result,
                lld_file            = job.out_lld,
                test_file           = test_file,
                sfr_new             = job.new_sfr,
                deprecated_fns      = patcher.get_deprecated_fns(),
                no_git              = no_git,
                github_url          = self.cfg.github_url,
                manual_review_items = all_manual if all_manual else None,
                cross_ref_report    = cross_ref_md,
            )

            # Atomic git commit: one commit per SFR file changed
            if not no_git and atomic_commits:
                import subprocess as _sp
                commit_files = [str(job.out_lld), str(test_file)] + [str(p) for p in ast_patched_files]
                pr_desc_path = job.out_lld.parent / "PR_DESCRIPTION.md"
                if pr_desc_path.exists():
                    commit_files.append(str(pr_desc_path))
                _sp.run(["git", "add"] + commit_files, capture_output=True)
                commit_msg = (
                    f"{commit_prefix}({job.ip.lower()}): patch LLD for {job.new_sfr.name}\n\n"
                    f"Changes: {len(all_changes)} total "
                    f"({len(auto_crs)} auto, {len(manual_crs)} manual)\n"
                    f"Compile: {'OK' if compile_result.success else 'NEEDS_REVIEW'}\n"
                )
                rc = _sp.run(
                    ["git", "commit", "-m", commit_msg],
                    capture_output=True, text=True
                )
                if rc.returncode == 0:
                    sha = rc.stdout.strip().split("[")[-1].split(" ")[0] if "[" in rc.stdout else ""
                    logger.debug(f"  [GIT] Committed {job.ip}: {sha}")
                else:
                    logger.warning(f"  [GIT-WARN] Commit skipped: {rc.stderr.strip()[:80]}")

            result.status = "OK"

        except Exception as exc:
            result.status = "FAIL"
            result.error  = str(exc)
            logger.error(f"  [ERROR] {job.ip}: {exc}")

        return result

    def _print_summary(self) -> None:
        """Print final summary table with timing."""
        logger.info("=" * 70)
        logger.info("  BATCH SUMMARY")
        logger.info("=" * 70)
        logger.info(f"  {'IP':<12} {'Status':<8} {'Changes':<10} {'Time':>7}  {'Output LLD'}")
        logger.info(f"  {'-'*12} {'-'*8} {'-'*10} {'-'*7}  {'-'*35}")

        ok = warn = fail = skip = 0
        for r in self._results:
            icon = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}.get(r.status, "     ")
            elapsed_str = f"{r.elapsed_s:.1f}s"
            logger.info(f"  {r.ip:<12} {icon:<8} {r.n_changes:<10} {elapsed_str:>7}  {r.lld_out.name}")
            if r.error:
                logger.error(f"    Error: {r.error}")
            if r.status == "OK":   ok   += 1
            if r.status == "WARN": warn += 1
            if r.status == "FAIL": fail += 1
            if r.status == "SKIP": skip += 1

        total_elapsed = getattr(self, "_total_elapsed", 0.0)
        total_m, total_s = divmod(int(total_elapsed), 60)

        logger.info("-" * 70)
        logger.info(f"  Total: {len(self._results)}  OK={ok}  WARN={warn}  FAIL={fail}  SKIP={skip}")
        logger.info(f"  Patched LLD files -> {self.cfg.output_dir}")
        logger.info(f"  Test files        -> {self.cfg.tests_dir}")
        logger.info(f"  Pipeline time     : {total_m}m {total_s:02d}s  ({total_elapsed:.2f}s)")
        logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------
def run_from_config(config_path: str | Path) -> List[IPResult]:
    """
    Load config and run the full batch pipeline.

    Usage:
        from lld_gen.batch_runner import run_from_config
        results = run_from_config("lld_patcher.yaml")
    """
    cfg     = load_config(config_path)
    runner  = BatchRunner(cfg)
    return runner.run()
