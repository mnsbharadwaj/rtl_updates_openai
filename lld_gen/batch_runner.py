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

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from lld_gen.config import PatcherConfig, IPJob, load_config, discover_jobs
from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, summarize_changes, SfrParser,
)
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient
from lld_gen.compile_check import run_compile_check
from lld_gen.pr_stage import stage_pr


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
        print("=" * 70)
        print("  LLD Auto-Patcher -- Batch Mode")
        print(f"  sfr_old_dir : {self.cfg.sfr_old_dir}")
        print(f"  sfr_new_dir : {self.cfg.sfr_new_dir}")
        print(f"  lld_dir     : {self.cfg.lld_dir}")
        print(f"  output_dir  : {self.cfg.output_dir}")
        print(f"  tests_dir   : {self.cfg.tests_dir}")
        print("=" * 70)

        # Discover all IP jobs
        print("\n[DISCOVER] Scanning directories for SFR pairs...")
        jobs = discover_jobs(self.cfg)

        if not jobs:
            print("[DISCOVER] No SFR pairs found. Check directory paths and naming conventions.")
            print("""
  Expected naming convention (any of these work):
    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/lld_pmu.h
    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/pmu_lld.h
    old_sfr/sfr_pmu.h  +  new_sfr/sfr_pmu.h  +  lld/pmu.h
""")
            return []

        print(f"[DISCOVER] Found {len(jobs)} IP(s) to process:")
        for j in jobs:
            print(f"  {j.ip:<12} {j.old_sfr.name} -> {j.new_sfr.name} -> {j.lld.name}")

        # Ensure output directories exist
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.tests_dir.mkdir(parents=True, exist_ok=True)

        # Process each IP
        for job in jobs:
            result = self._run_one(job)
            self._results.append(result)

        # Print summary
        self._print_summary()
        return self._results

    def _run_one(self, job: IPJob) -> IPResult:
        """Run the full pipeline for one IP."""
        print(f"\n{'=' * 70}")
        print(f"  IP: {job.ip}")
        print(f"  old: {job.old_sfr.name}  new: {job.new_sfr.name}  lld: {job.lld.name}")
        print(f"  out: {job.out_lld}")
        print(f"{'=' * 70}")

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

        try:
            # Step 1: Classify changes
            print(f"\n  [1/4] Diff & Classify {job.ip}...")
            changes = classify_sfr_diff(job.old_sfr, job.new_sfr, ip=job.ip)
            result.n_changes = len(changes)
            print(f"  {summarize_changes(changes).replace(chr(10), chr(10) + '  ')}")

            if not changes:
                print(f"  [SKIP] No changes detected for {job.ip} -- lld.h is up to date.")
                # Still copy to output_dir so user gets the file
                shutil.copy2(job.lld, job.out_lld)
                result.status = "SKIP"
                return result

            # Step 2: Copy LLD to output_dir first, then patch in-place there
            print(f"\n  [2/4] Patch {job.ip} LLD...")
            shutil.copy2(job.lld, job.out_lld)   # copy original to output

            parser  = SfrParser(ip=job.ip)
            new_ir  = parser.parse_file(job.new_sfr)
            ollama_model = str(overrides.get("ollama_model", self.cfg.ollama_model) or "")
            llm = LLMClient(
                hf_token     = hf_token,
                ollama_model = ollama_model,
            ) if not no_llm else LLMClient(hf_token="", ollama_model="")
            patcher = LLDPatcher(ip=job.ip, llm_client=llm, no_llm=no_llm)

            # Patch the COPY in output_dir (not the original)
            patcher.patch(
                lld_path=job.out_lld,
                changes=changes,
                new_ir=new_ir,
                out_path=job.out_lld,        # <- write patched file here
            )
            print(f"  Patched LLD written -> {job.out_lld}")

            # Write test file to tests_dir (full coverage: all fields in new_ir)
            test_file    = job.tests_dir / f"test_lld_{job.ip.lower()}.c"
            lld_text     = job.out_lld.read_text(encoding="utf-8") if job.out_lld.exists() else ""
            llm_test_gen = bool(self.cfg.llm_test_gen)
            patcher.write_test_file(
                out_path   = test_file,
                sfr_new    = job.new_sfr.name,
                lld_new    = job.out_lld.name,
                new_ir     = new_ir,                           # ensures 100% function coverage
                llm_client = llm if llm_test_gen else None,    # LLM only if config says so
                lld_text   = lld_text,                         # reference impl for LLM prompt
            )
            mode_tag = "[LLM+Template fallback]" if llm_test_gen else "[Template]"
            print(f"  Test file written  -> {test_file}  {mode_tag}")

            # Step 3: Compile check
            print(f"\n  [3/4] Compile-check {job.ip}...")
            compile_result = run_compile_check(
                test_file=test_file,
                sfr_new=job.new_sfr,
                lld_file=job.out_lld,
                llm_client=llm,
                gcc_exe=self.cfg.gcc,
            )
            result.compile_ok = compile_result.success
            if compile_result.success:
                print(f"  [OK] Compile passed for {job.ip}")
            else:
                print(f"  [WARN] Compile check skipped/failed for {job.ip} (gcc not found or errors)")

            # Step 4: Stage PR
            print(f"\n  [4/4] Stage PR for {job.ip}...")
            stage_pr(
                ip=job.ip,
                changes=changes,
                compile_result=compile_result,
                lld_file=job.out_lld,
                test_file=test_file,
                sfr_new=job.new_sfr,
                deprecated_fns=patcher.get_deprecated_fns(),
                no_git=no_git,
                github_url=self.cfg.github_url,
            )

            result.status = "OK"

        except Exception as exc:
            result.status = "FAIL"
            result.error  = str(exc)
            print(f"  [ERROR] {job.ip}: {exc}")

        return result

    def _print_summary(self) -> None:
        """Print final summary table."""
        print("\n" + "=" * 70)
        print("  BATCH SUMMARY")
        print("=" * 70)
        print(f"  {'IP':<12} {'Status':<8} {'Changes':<10} {'Output LLD'}")
        print(f"  {'-'*12} {'-'*8} {'-'*10} {'-'*35}")

        ok = warn = fail = skip = 0
        for r in self._results:
            icon = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}.get(r.status, "     ")
            print(f"  {r.ip:<12} {icon:<8} {r.n_changes:<10} {r.lld_out.name}")
            if r.error:
                print(f"    Error: {r.error}")
            if r.status == "OK":   ok   += 1
            if r.status == "WARN": warn += 1
            if r.status == "FAIL": fail += 1
            if r.status == "SKIP": skip += 1

        print("-" * 70)
        print(f"  Total: {len(self._results)}  OK={ok}  WARN={warn}  FAIL={fail}  SKIP={skip}")
        print(f"\n  Patched LLD files -> {self.cfg.output_dir}")
        print(f"  Test files        -> {self.cfg.tests_dir}")
        print("=" * 70)


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
