"""
batch_runner.py -- Batch pipeline runner driven by lld_patcher.yaml config

For every SFR old/new pair found under sfr_old_dir / sfr_new_dir:
  1. Diff old vs new SFR -> classify changes
  2. Scan ALL .h files in lld_dir (using AST/regex) for functions that
     reference the changed registers/fields -- no 1-to-1 naming required
  3. Copy each matching LLD file to output_dir and patch it
     (template or LLM depending on change type and config)
  4. Write generated unit-test file to tests_dir
  5. Print a summary report
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from lld_gen.config import (
    PatcherConfig, IPJob, load_config, discover_jobs,
    save_checkpoint, load_checkpoint, clear_checkpoint,
)
from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, summarize_changes, SfrParser, ChangeType, ChangeRecord,
)
from lld_gen.lld_patcher import LLDPatcher, extract_all_functions_for_field
from lld_gen.llm_client import LLMClient, load_llm_config
from lld_gen.compile_check import run_compile_check, _find_gcc, GccNotFoundError
from lld_gen.pr_stage import stage_pr

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-IP result
# ---------------------------------------------------------------------------
@dataclass
class IPResult:
    ip:           str
    old_sfr:      Path
    new_sfr:      Path
    patched_llds: List[Path] = field(default_factory=list)
    n_changes:    int = 0
    status:       str = "PENDING"   # OK | WARN | FAIL | SKIP
    error:        str = ""
    elapsed_s:    float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_FN_PREFIXES = re.compile(r'\blld_(\w+?)_')

def _lld_files_for_changes(
    changes: List[ChangeRecord],
    ip: str,
    lld_dir: Path,
) -> Dict[Path, List[ChangeRecord]]:
    """
    Scan ALL .h files in lld_dir (and output copies) and find which ones
    contain functions that reference any of the changed registers/fields.

    Returns: mapping of lld_file_path -> list of relevant ChangeRecords
    """
    import re as _re

    if not lld_dir.exists() or not changes:
        return {}

    # Build search terms: function name prefixes and struct member names
    # for each changed register/field
    search_terms: Set[str] = set()
    for cr in changes:
        reg_lo = cr.reg_name.lower()
        ip_lo  = ip.lower()
        # Function name pattern: lld_ip_reg_
        search_terms.add(f"lld_{ip_lo}_{reg_lo}_")
        # Struct member: stREG_NAME
        search_terms.add(f"st{cr.reg_name}")
        if cr.field_name:
            # stNative.FIELDNAME
            search_terms.add(cr.field_name)

    result: Dict[Path, List[ChangeRecord]] = {}

    for h_file in lld_dir.glob("*.h"):
        try:
            text = h_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Check if any search term appears in this file
        hits: List[ChangeRecord] = []
        for cr in changes:
            reg_lo = cr.reg_name.lower()
            ip_lo  = ip.lower()
            fn_prefix = f"lld_{ip_lo}_{reg_lo}_"
            struct_ref = f"st{cr.reg_name}"
            if fn_prefix in text or struct_ref in text:
                hits.append(cr)

        if hits:
            result[h_file] = hits
            logger.info(
                "  [SCAN] %s: contains functions for %d changed register(s)",
                h_file.name, len({cr.reg_name for cr in hits}),
            )

    if not result:
        logger.info(
            "  [SCAN] No LLD files in %s reference IP=%s registers "
            "-- nothing to patch for this SFR pair.", lld_dir, ip
        )

    return result


# ---------------------------------------------------------------------------
# LLM factory (with auto-cloud-fallback)
# ---------------------------------------------------------------------------
# Default cloud endpoint used when no other backend is configured
_CLOUD_LLM_CFG = {
    "no_llm": False,
    "llm": {
        "backend":  "ollama",
        "model":    "gpt-oss",
        "url":      "http://107.99.41.85/ollama/srv1/api/generate",
        "location": "cloud",
        "timeout":  120,
        "max_retries": 3,
    }
}


def _make_llm(cfg: PatcherConfig, ip_overrides: dict, no_llm_override: bool) -> LLMClient:
    """
    Build an LLMClient for this run.

    Priority:
      1. no_llm=true  -> return disabled client (template-only)
      2. llm: block present in config -> use that
      3. hf_token / ollama_model legacy keys -> use those
      4. Default -> cloud endpoint http://107.99.41.85/ollama/srv1/api/generate
    """
    no_llm = no_llm_override or cfg.no_llm
    if no_llm:
        logger.info("  [LLM] Disabled (no_llm=true) -- template-only mode")
        return LLMClient(load_llm_config({"no_llm": True}))

    # If the config has an explicit llm: block, honour it
    if hasattr(cfg, "llm") and isinstance(cfg.llm, dict) and cfg.llm:
        raw = {"no_llm": False, "llm": cfg.llm}
        llm = LLMClient(load_llm_config(raw))
        if llm.available:
            return llm
        logger.warning("  [LLM] Configured backend not reachable -- falling back to cloud")

    # Legacy: hf_token / ollama_model keys
    hf_token     = ip_overrides.get("hf_token",     cfg.hf_token or "")
    ollama_model = ip_overrides.get("ollama_model", cfg.ollama_model or "")
    if hf_token or ollama_model:
        raw = {"no_llm": False, "hf_token": hf_token, "ollama_model": ollama_model}
        llm = LLMClient(load_llm_config(raw))
        if llm.available:
            return llm
        logger.warning("  [LLM] Legacy backend not reachable -- falling back to cloud")

    # Default: cloud endpoint
    logger.info("  [LLM] Using default cloud endpoint: http://107.99.41.85/ollama/srv1/api/generate")
    return LLMClient(load_llm_config(_CLOUD_LLM_CFG))


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
class BatchRunner:
    """
    Runs the LLD Auto-Patcher pipeline for every SFR pair in config.

    For each SFR pair:
      1. Diff old vs new SFR -> classify changes
      2. Scan ALL .h files in lld_dir for functions referencing changed regs
      3. Copy matched files to output_dir and patch them via LLDPatcher
      4. Write unit tests to tests_dir
    """

    def __init__(self, cfg: PatcherConfig):
        self.cfg = cfg
        self._results: List[IPResult] = []

    # ------------------------------------------------------------------
    def run(self) -> List[IPResult]:
        logger.info("=" * 70)
        logger.info("  LLD Auto-Patcher -- Batch Mode")
        logger.info(f"  sfr_old_dir : {self.cfg.sfr_old_dir}")
        logger.info(f"  sfr_new_dir : {self.cfg.sfr_new_dir}")
        logger.info(f"  lld_dir     : {self.cfg.lld_dir}")
        logger.info(f"  output_dir  : {self.cfg.output_dir}")
        logger.info(f"  tests_dir   : {self.cfg.tests_dir}")
        logger.info("=" * 70)

        # Discover SFR pairs -- lld field is optional; we don't use it here
        logger.info("[DISCOVER] Scanning for SFR old/new pairs...")
        jobs = discover_jobs(self.cfg)

        if not jobs:
            logger.warning(
                "[DISCOVER] No SFR pairs found. "
                "Place sfr_*.h in old_sfr/ and new_sfr/ (matching filenames)."
            )
            return []

        logger.info("[DISCOVER] Found %d SFR pair(s):", len(jobs))
        for j in jobs:
            logger.info("  %-12s  %s -> %s", j.ip, j.old_sfr.name, j.new_sfr.name)

        # Ensure output dirs exist
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.tests_dir.mkdir(parents=True, exist_ok=True)

        # Compile check gate
        gcc_exe: Optional[str] = None
        if getattr(self.cfg, "compile_check", False):
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)
            except GccNotFoundError:
                logger.warning("  [GCC] Not found -- compile verification skipped")

        # Checkpoint resume
        checkpoint = load_checkpoint(self.cfg.output_dir)
        done_ips: Set[str] = set(
            (checkpoint or {}).get("completed_ips", [])
        )

        self._pipeline_start = time.perf_counter()
        completed: list = list(done_ips)

        for job in jobs:
            if job.ip in done_ips:
                logger.info("  [RESUME] %s: already done", job.ip)
                continue

            t0 = time.perf_counter()
            result = self._run_one(job, gcc_exe=gcc_exe)
            result.elapsed_s = time.perf_counter() - t0
            self._results.append(result)
            completed.append(job.ip)
            save_checkpoint(self.cfg.output_dir, completed, "ip_done", {"last_ip": job.ip})

        self._total_elapsed = time.perf_counter() - self._pipeline_start
        clear_checkpoint(self.cfg.output_dir)
        self._print_summary()
        return self._results

    # ------------------------------------------------------------------
    def _run_one(self, job: IPJob, gcc_exe: Optional[str] = None) -> IPResult:
        """Process one SFR pair: diff -> scan all LLD files -> patch each."""
        logger.info("=" * 70)
        logger.info("  SFR pair : %s  (%s -> %s)", job.ip, job.old_sfr.name, job.new_sfr.name)
        logger.info("=" * 70)

        result = IPResult(ip=job.ip, old_sfr=job.old_sfr, new_sfr=job.new_sfr)

        overrides = self.cfg.ip_overrides.get(job.ip.upper(), {})
        no_llm    = bool(overrides.get("no_llm", self.cfg.no_llm))
        no_git    = bool(overrides.get("no_git", self.cfg.no_git))

        try:
            # ── Step 1: Diff & classify ──────────────────────────────────────
            logger.info("  [1/3] Diff & Classify %s ...", job.ip)
            all_changes = classify_sfr_diff(job.old_sfr, job.new_sfr, ip=job.ip)
            result.n_changes = len(all_changes)
            logger.info("  %s", summarize_changes(all_changes).replace("\n", "\n  "))

            if not all_changes:
                logger.info("  [SKIP] No SFR changes for %s -- all LLD files up to date.", job.ip)
                result.status = "SKIP"
                return result

            # Separate manual-review vs auto-patchable
            manual_crs = [cr for cr in all_changes if ChangeType.is_manual_review(cr.change_type)]
            auto_crs   = [cr for cr in all_changes if not ChangeType.is_manual_review(cr.change_type)]

            if manual_crs:
                logger.warning(
                    "  [MANUAL] %d change(s) flagged for manual review: %s",
                    len(manual_crs),
                    ", ".join(f"{c.change_type}:{c.reg_name}" for c in manual_crs[:5])
                    + ("..." if len(manual_crs) > 5 else ""),
                )

            # Build LLM client (local or cloud auto-fallback)
            llm = _make_llm(self.cfg, overrides, no_llm)

            # Parse new SFR IR (needed for struct generation and test writing)
            parser = SfrParser(ip=job.ip)
            new_ir = parser.parse_file(job.new_sfr)

            # ── Step 2: Scan ALL LLD files for relevant functions ─────────────
            logger.info("  [2/3] Scanning %s for LLD files with changed register functions ...", self.cfg.lld_dir)
            lld_hits = _lld_files_for_changes(auto_crs, job.ip, self.cfg.lld_dir)

            if not lld_hits:
                logger.info("  [INFO] No LLD files reference %s registers -- nothing patched.", job.ip)
                result.status = "SKIP"
                return result

            # ── Step 3: Patch each matching LLD file ──────────────────────────
            logger.info("  [3/3] Patching %d LLD file(s) ...", len(lld_hits))
            all_manual: List[str] = [
                f"{cr.change_type}: {cr.reg_name}" + (f".{cr.field_name}" if cr.field_name else "")
                for cr in manual_crs
            ]

            for lld_src, relevant_changes in lld_hits.items():
                out_lld  = self.cfg.output_dir / lld_src.name
                test_file = self.cfg.tests_dir / f"test_{job.ip.lower()}_{lld_src.stem}.c"

                # Copy original to output dir (if not already there from earlier IP)
                if not out_lld.exists():
                    shutil.copy2(lld_src, out_lld)

                logger.info(
                    "    Patching %-20s  (%d relevant change(s)) -> %s",
                    lld_src.name, len(relevant_changes), out_lld.name,
                )

                patcher = LLDPatcher(ip=job.ip, llm_client=llm, no_llm=no_llm)
                patcher.patch(
                    lld_path = out_lld,
                    changes  = relevant_changes,
                    new_ir   = new_ir,
                    out_path = out_lld,
                )

                # Write test file
                lld_text = out_lld.read_text(encoding="utf-8") if out_lld.exists() else ""
                patcher.write_test_file(
                    out_path   = test_file,
                    sfr_new    = job.new_sfr.name,
                    lld_new    = out_lld.name,
                    new_ir     = new_ir,
                    llm_client = llm if getattr(self.cfg, "llm_test_gen", False) else None,
                    lld_text   = lld_text,
                )

                result.patched_llds.append(out_lld)

                # Optional compile check
                if gcc_exe and test_file.exists():
                    cr_result = run_compile_check(
                        test_file  = test_file,
                        sfr_new    = job.new_sfr,
                        lld_file   = out_lld,
                        llm_client = llm,
                        gcc_exe    = gcc_exe,
                        required   = getattr(self.cfg, "compile_check", False),
                    )
                    if not cr_result.success:
                        logger.warning("    [WARN] Compile failed for %s", out_lld.name)
                        if cr_result.needs_review:
                            all_manual.extend(cr_result.needs_review)
                    else:
                        logger.info("    [OK] Compile passed for %s", out_lld.name)

            # Stage PR (use first patched file for PR desc location)
            first_out = result.patched_llds[0] if result.patched_llds else None
            if first_out:
                stage_pr(
                    ip                  = job.ip,
                    changes             = all_changes,
                    compile_result      = type("CR", (), {"success": True, "needs_review": all_manual, "retries_used": 0})(),
                    lld_file            = first_out,
                    test_file           = self.cfg.tests_dir / f"test_{job.ip.lower()}_{first_out.stem}.c",
                    sfr_new             = job.new_sfr,
                    no_git              = no_git,
                    github_url          = getattr(self.cfg, "github_url", ""),
                    manual_review_items = all_manual or None,
                )

            result.status = "OK"

        except Exception as exc:
            result.status = "FAIL"
            result.error  = str(exc)
            logger.error("  [ERROR] %s: %s", job.ip, exc)

        return result

    # ------------------------------------------------------------------
    def _print_summary(self) -> None:
        logger.info("=" * 70)
        logger.info("  BATCH SUMMARY")
        logger.info("=" * 70)
        logger.info("  %-12s %-8s %-10s %7s  %s", "IP", "Status", "Changes", "Time", "Patched LLD(s)")
        logger.info("  %s %s %s %s  %s", "-"*12, "-"*8, "-"*10, "-"*7, "-"*35)

        ok = warn = fail = skip = 0
        for r in self._results:
            icon = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}.get(r.status, "     ")
            lld_names = ", ".join(p.name for p in r.patched_llds) or "(none)"
            logger.info(
                "  %-12s %-8s %-10d %6.1fs  %s",
                r.ip, icon, r.n_changes, r.elapsed_s, lld_names,
            )
            if r.error:
                logger.error("    Error: %s", r.error)
            if r.status == "OK":   ok   += 1
            if r.status == "WARN": warn += 1
            if r.status == "FAIL": fail += 1
            if r.status == "SKIP": skip += 1

        elapsed = getattr(self, "_total_elapsed", 0.0)
        m, s = divmod(int(elapsed), 60)
        logger.info("-" * 70)
        logger.info("  Total: %d  OK=%d  WARN=%d  FAIL=%d  SKIP=%d", len(self._results), ok, warn, fail, skip)
        logger.info("  Patched LLD files -> %s", self.cfg.output_dir)
        logger.info("  Test files        -> %s", self.cfg.tests_dir)
        logger.info("  Pipeline time     : %dm %02ds  (%.2fs)", m, s, elapsed)
        logger.info("=" * 70)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------
def run_from_config(config_path) -> List[IPResult]:
    """Load config and run the full batch pipeline."""
    cfg    = load_config(config_path)
    runner = BatchRunner(cfg)
    return runner.run()


# missing import used above
import re  # noqa: E402 (already imported transitively but make explicit)
