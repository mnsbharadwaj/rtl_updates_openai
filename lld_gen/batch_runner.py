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
import re
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
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient, load_llm_config
from lld_gen.compile_check import run_compile_check, _find_gcc, GccNotFoundError
from lld_gen.pr_stage import stage_pr

logger = logging.getLogger(__name__)

# Separator widths
_W  = 70   # wide bar
_W2 = 50   # medium bar


def _bar(char: str = "─", width: int = _W) -> str:
    return char * width


def _hdr(title: str, char: str = "═", width: int = _W) -> None:
    logger.info(_bar(char, width))
    logger.info("  %s", title)
    logger.info(_bar(char, width))


def _step(n: str, total: str, label: str) -> None:
    logger.info("")
    logger.info("  ┌─ STEP %s/%s: %s", n, total, label)


def _ok(msg: str) -> None:
    logger.info("  │  ✔  %s", msg)


def _info(msg: str) -> None:
    logger.info("  │     %s", msg)


def _warn(msg: str) -> None:
    logger.warning("  │  ⚠  %s", msg)


def _fail(msg: str) -> None:
    logger.error("  │  ✘  %s", msg)


def _step_done(label: str = "done") -> None:
    logger.info("  └─ %s", label)
    logger.info("")


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
# LLD file scanner: find .h files that reference changed SFR registers
# ---------------------------------------------------------------------------
def _lld_files_for_changes(
    changes: List[ChangeRecord],
    ip: str,
    lld_dir: Path,
) -> Dict[Path, List[ChangeRecord]]:
    """
    Scan ALL .h files in lld_dir and return those that contain functions
    referencing the changed register/fields. No filename matching needed.

    Search strategy per ChangeRecord:
      • Function prefix:  lld_{ip}_{reg}_   (e.g. lld_pmu_clk_con_)
      • Struct member:    st{REG}            (e.g. stCLK_CON)
    """
    if not lld_dir.exists() or not changes:
        return {}

    ip_lo = ip.lower()
    all_h = list(lld_dir.glob("*.h"))
    logger.info("  │     Scanning %d .h file(s) in %s", len(all_h), lld_dir)

    result: Dict[Path, List[ChangeRecord]] = {}

    for h_file in all_h:
        try:
            text = h_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("  │     Cannot read %s: %s", h_file.name, exc)
            continue

        hits: List[ChangeRecord] = []
        for cr in changes:
            fn_prefix  = f"lld_{ip_lo}_{cr.reg_name.lower()}_"
            struct_ref = f"st{cr.reg_name}"
            if fn_prefix in text or struct_ref in text:
                hits.append(cr)

        if hits:
            regs = sorted({cr.reg_name for cr in hits})
            result[h_file] = hits
            logger.info(
                "  │     ✔ %-30s  matches registers: %s",
                h_file.name, ", ".join(regs),
            )
        else:
            logger.debug("  │       ✗ %-28s  no matching functions", h_file.name)

    return result


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------
_CLOUD_LLM_CFG = {
    "no_llm": False,
    "llm": {
        "backend":     "ollama",
        "model":       "gpt-oss",
        "url":         "http://107.99.41.85/ollama/srv1/api/generate",
        "location":    "cloud",
        "timeout":     120,
        "max_retries": 3,
    }
}


def _make_llm(cfg: PatcherConfig, ip_overrides: dict, no_llm_override: bool) -> LLMClient:
    """
    Build an LLMClient. Priority:
      1. no_llm=true  -> template-only (no HTTP)
      2. llm: block in config -> use that backend
      3. Legacy hf_token / ollama_model keys
      4. Default -> cloud endpoint (gpt-oss)
    """
    no_llm = no_llm_override or cfg.no_llm
    if no_llm:
        logger.info("  │     LLM disabled (no_llm=true) — template-only mode")
        return LLMClient(load_llm_config({"no_llm": True}))

    # Explicit llm: block
    if hasattr(cfg, "llm") and isinstance(cfg.llm, dict) and cfg.llm:
        raw = {"no_llm": False, "llm": cfg.llm}
        llm = LLMClient(load_llm_config(raw))
        if llm.available:
            logger.info("  │     LLM: %s @ %s", cfg.llm.get("model", "?"), cfg.llm.get("url", "?"))
            return llm
        logger.warning("  │  ⚠  Configured backend not reachable — falling back to cloud")

    # Legacy keys
    hf_token     = ip_overrides.get("hf_token",     cfg.hf_token or "")
    ollama_model = ip_overrides.get("ollama_model", cfg.ollama_model or "")
    if hf_token or ollama_model:
        raw = {"no_llm": False, "hf_token": hf_token, "ollama_model": ollama_model}
        llm = LLMClient(load_llm_config(raw))
        if llm.available:
            logger.info("  │     LLM: legacy backend active (%s)", ollama_model or "hf")
            return llm
        logger.warning("  │  ⚠  Legacy backend not reachable — falling back to cloud")

    # Default cloud
    logger.info(
        "  │     LLM: cloud endpoint  http://107.99.41.85/ollama/srv1/api/generate  (model: gpt-oss)"
    )
    return LLMClient(load_llm_config(_CLOUD_LLM_CFG))


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
class BatchRunner:
    """Runs the LLD Auto-Patcher pipeline for every SFR pair in config."""

    def __init__(self, cfg: PatcherConfig):
        self.cfg = cfg
        self._results: List[IPResult] = []

    # -----------------------------------------------------------------------
    def run(self) -> List[IPResult]:
        _hdr("LLD Auto-Patcher  ▶  Batch Mode", "═")
        logger.info("  Config paths:")
        logger.info("    sfr_old_dir : %s", self.cfg.sfr_old_dir)
        logger.info("    sfr_new_dir : %s", self.cfg.sfr_new_dir)
        logger.info("    lld_dir     : %s", self.cfg.lld_dir)
        logger.info("    output_dir  : %s", self.cfg.output_dir)
        logger.info("    tests_dir   : %s", self.cfg.tests_dir)
        logger.info("  LLM mode     : %s", "DISABLED (template-only)" if self.cfg.no_llm else "ENABLED (cloud gpt-oss default)")
        logger.info(_bar())

        # ── Discover SFR pairs ───────────────────────────────────────────────
        logger.info("")
        logger.info("  ▶ Discovering SFR old/new pairs ...")
        jobs = discover_jobs(self.cfg)

        if not jobs:
            logger.error("  ✘ No SFR pairs found!")
            logger.error("    Place matching *.h files in old_sfr/ and new_sfr/")
            return []

        logger.info("  ✔ Found %d SFR pair(s):", len(jobs))
        for i, j in enumerate(jobs, 1):
            logger.info("    %2d. %-12s  %s  →  %s", i, j.ip, j.old_sfr.name, j.new_sfr.name)

        # ── Output dirs ──────────────────────────────────────────────────────
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.tests_dir.mkdir(parents=True, exist_ok=True)

        # ── GCC check ────────────────────────────────────────────────────────
        gcc_exe: Optional[str] = None
        if getattr(self.cfg, "compile_check", False):
            logger.info("")
            logger.info("  ▶ Locating GCC compiler ...")
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)
                logger.info("  ✔ GCC found: %s", gcc_exe)
            except GccNotFoundError:
                logger.warning("  ⚠ GCC not found — compile verification skipped")
        else:
            logger.info("  ℹ  compile_check=false — GCC verification skipped")

        # ── Checkpoint resume ────────────────────────────────────────────────
        checkpoint = load_checkpoint(self.cfg.output_dir)
        done_ips: Set[str] = set((checkpoint or {}).get("completed_ips", []))
        if done_ips:
            logger.info("  ↩  Resuming — already processed: %s", ", ".join(sorted(done_ips)))

        logger.info("")
        logger.info(_bar("─"))
        logger.info("  Processing %d IP(s)  [%s skipped — already done]",
                    len(jobs) - len(done_ips), len(done_ips))
        logger.info(_bar("─"))

        self._pipeline_start = time.perf_counter()
        completed: list = list(done_ips)

        for idx, job in enumerate(jobs, 1):
            logger.info("")
            _hdr(f"IP {idx}/{len(jobs)}: {job.ip}", "─", _W)

            if job.ip in done_ips:
                logger.info("  ↩  SKIP — already processed in a previous run")
                continue

            t0 = time.perf_counter()
            result = self._run_one(job, gcc_exe=gcc_exe)
            result.elapsed_s = time.perf_counter() - t0
            self._results.append(result)
            completed.append(job.ip)
            save_checkpoint(self.cfg.output_dir, completed, "ip_done", {"last_ip": job.ip})

            status_icon = {"OK": "✔", "WARN": "⚠", "FAIL": "✘", "SKIP": "○"}.get(result.status, "?")
            logger.info("  %s %s finished in %.1fs — status: %s",
                        status_icon, job.ip, result.elapsed_s, result.status)

        self._total_elapsed = time.perf_counter() - self._pipeline_start
        clear_checkpoint(self.cfg.output_dir)
        self._print_summary()
        return self._results

    # -----------------------------------------------------------------------
    def _run_one(self, job: IPJob, gcc_exe: Optional[str] = None) -> IPResult:
        """Full pipeline for one SFR pair."""
        result    = IPResult(ip=job.ip, old_sfr=job.old_sfr, new_sfr=job.new_sfr)
        overrides = self.cfg.ip_overrides.get(job.ip.upper(), {})
        no_llm    = bool(overrides.get("no_llm", self.cfg.no_llm))
        no_git    = bool(overrides.get("no_git", self.cfg.no_git))

        try:
            # ─────────────────────────────────────────────────────────────────
            # STEP 1 — Diff & Classify
            # ─────────────────────────────────────────────────────────────────
            _step("1", "4", "Diff & Classify SFR changes")
            _info(f"OLD: {job.old_sfr}")
            _info(f"NEW: {job.new_sfr}")

            all_changes = classify_sfr_diff(job.old_sfr, job.new_sfr, ip=job.ip)
            result.n_changes = len(all_changes)

            if not all_changes:
                _ok("No changes detected — files are identical")
                _step_done("SKIP — nothing to patch")
                result.status = "SKIP"
                return result

            # Print per-change breakdown
            auto_crs   = [cr for cr in all_changes if not ChangeType.is_manual_review(cr.change_type)]
            manual_crs = [cr for cr in all_changes if ChangeType.is_manual_review(cr.change_type)]

            _ok(f"Total changes: {len(all_changes)}  "
                f"(auto-patchable: {len(auto_crs)}, manual-review: {len(manual_crs)})")

            # Group by change type for readability
            from collections import defaultdict
            by_type: Dict[str, list] = defaultdict(list)
            for cr in all_changes:
                by_type[cr.change_type].append(cr)
            for ct, crs in sorted(by_type.items()):
                tag = "AUTO " if not ChangeType.is_manual_review(ct) else "MAN  "
                _info(f"  [{tag}] {ct:<30}  {len(crs)} field(s)")
                for cr in crs[:4]:          # show up to 4 examples
                    field_part = f".{cr.field_name}" if cr.field_name else ""
                    _info(f"           {cr.reg_name}{field_part}")
                if len(crs) > 4:
                    _info(f"           ... and {len(crs) - 4} more")

            if manual_crs:
                _warn(f"{len(manual_crs)} change(s) need MANUAL REVIEW "
                      f"(REG_ADDED/REG_DELETED/structural) — will be listed in PR description")

            if not auto_crs:
                _warn("No auto-patchable changes — all changes are manual-review only")
                _step_done("SKIP — nothing to auto-patch")
                result.status = "SKIP"
                return result

            _step_done(f"{len(auto_crs)} change(s) ready to patch")

            # ─────────────────────────────────────────────────────────────────
            # STEP 2 — Build LLM client
            # ─────────────────────────────────────────────────────────────────
            _step("2", "4", "Initialise LLM client")
            llm = _make_llm(self.cfg, overrides, no_llm)
            _ok(f"LLM available={llm.available}  backend={llm.backend}")
            _step_done()

            # Parse new SFR IR
            parser = SfrParser(ip=job.ip)
            new_ir = parser.parse_file(job.new_sfr)

            # ─────────────────────────────────────────────────────────────────
            # STEP 3 — Scan ALL LLD files
            # ─────────────────────────────────────────────────────────────────
            _step("3", "4", f"Scan {self.cfg.lld_dir.name}/ for LLD files referencing changed registers")
            lld_hits = _lld_files_for_changes(auto_crs, job.ip, self.cfg.lld_dir)

            if not lld_hits:
                _warn("No LLD file references any of the changed registers")
                _warn("Nothing to patch. Check that lld_dir contains the right headers.")
                _step_done("SKIP")
                result.status = "SKIP"
                return result

            _ok(f"Found {len(lld_hits)} LLD file(s) to patch")
            _step_done()

            # ─────────────────────────────────────────────────────────────────
            # STEP 4 — Patch each LLD file
            # ─────────────────────────────────────────────────────────────────
            _step("4", "4", f"Patch {len(lld_hits)} LLD file(s)")
            all_manual: List[str] = [
                f"{cr.change_type}: {cr.reg_name}" + (f".{cr.field_name}" if cr.field_name else "")
                for cr in manual_crs
            ]

            for file_idx, (lld_src, relevant_changes) in enumerate(lld_hits.items(), 1):
                out_lld   = self.cfg.output_dir / lld_src.name
                test_file = self.cfg.tests_dir / f"test_{job.ip.lower()}_{lld_src.stem}.c"

                logger.info("  │")
                logger.info("  │  ── File %d/%d: %s", file_idx, len(lld_hits), lld_src.name)
                logger.info("  │     Relevant changes : %d", len(relevant_changes))
                for cr in relevant_changes:
                    fld = f".{cr.field_name}" if cr.field_name else ""
                    logger.info("  │       • [%s] %s%s", cr.change_type, cr.reg_name, fld)

                # Copy source to output dir
                if not out_lld.exists():
                    shutil.copy2(lld_src, out_lld)
                    logger.info("  │     Copied  %-28s → %s", lld_src.name, out_lld)
                else:
                    logger.info("  │     Reusing %-28s (already in output_dir)", out_lld.name)

                # Patch via LLDPatcher
                logger.info("  │     Patching via %s ...",
                            "LLM (cloud gpt-oss)" if llm.available else "template")
                patcher = LLDPatcher(ip=job.ip, llm_client=llm, no_llm=no_llm)
                t_patch = time.perf_counter()
                patcher.patch(
                    lld_path = out_lld,
                    changes  = relevant_changes,
                    new_ir   = new_ir,
                    out_path = out_lld,
                )
                patch_s = time.perf_counter() - t_patch
                logger.info("  │     ✔ Patch complete in %.1fs  →  %s", patch_s, out_lld)

                # Write test file
                lld_text = out_lld.read_text(encoding="utf-8") if out_lld.exists() else ""
                logger.info("  │     Generating unit tests → %s ...", test_file.name)
                patcher.write_test_file(
                    out_path   = test_file,
                    sfr_new    = job.new_sfr.name,
                    lld_new    = out_lld.name,
                    new_ir     = new_ir,
                    llm_client = llm if getattr(self.cfg, "llm_test_gen", False) else None,
                    lld_text   = lld_text,
                )
                if test_file.exists():
                    logger.info("  │     ✔ Test file written: %s", test_file)
                else:
                    logger.warning("  │  ⚠  Test file not created for %s", lld_src.name)

                result.patched_llds.append(out_lld)

                # Optional compile check
                if gcc_exe and test_file.exists():
                    logger.info("  │     Running GCC compile check ...")
                    t_gcc = time.perf_counter()
                    cr_result = run_compile_check(
                        test_file  = test_file,
                        sfr_new    = job.new_sfr,
                        lld_file   = out_lld,
                        llm_client = llm,
                        gcc_exe    = gcc_exe,
                        required   = getattr(self.cfg, "compile_check", False),
                    )
                    gcc_s = time.perf_counter() - t_gcc
                    if cr_result.success:
                        logger.info("  │     ✔ Compile OK in %.1fs", gcc_s)
                    else:
                        logger.warning("  │  ⚠  Compile FAILED in %.1fs", gcc_s)
                        if cr_result.needs_review:
                            all_manual.extend(cr_result.needs_review)

            # ── PR staging ────────────────────────────────────────────────────
            first_out = result.patched_llds[0] if result.patched_llds else None
            if first_out and not no_git:
                logger.info("  │")
                logger.info("  │  ── Staging PR description ...")
                stage_pr(
                    ip                  = job.ip,
                    changes             = all_changes,
                    compile_result      = type("CR", (), {
                        "success": True,
                        "needs_review": all_manual,
                        "retries_used": 0,
                    })(),
                    lld_file            = first_out,
                    test_file           = self.cfg.tests_dir / f"test_{job.ip.lower()}_{first_out.stem}.c",
                    sfr_new             = job.new_sfr,
                    no_git              = no_git,
                    github_url          = getattr(self.cfg, "github_url", ""),
                    manual_review_items = all_manual or None,
                )
                logger.info("  │     ✔ PR description written")
            elif no_git:
                logger.info("  │     ℹ  no_git=true — PR/git staging skipped")

            _step_done(f"All {len(result.patched_llds)} file(s) patched successfully")
            result.status = "WARN" if manual_crs else "OK"

        except Exception as exc:
            import traceback
            result.status = "FAIL"
            result.error  = str(exc)
            logger.error("")
            logger.error("  ✘ EXCEPTION in %s:", job.ip)
            for line in traceback.format_exc().splitlines():
                logger.error("    %s", line)

        return result

    # -----------------------------------------------------------------------
    def _print_summary(self) -> None:
        logger.info("")
        _hdr("BATCH SUMMARY", "═")

        col = "  {:<12}  {:<8}  {:>8}  {:>7}  {}"
        logger.info(col.format("IP", "Status", "Changes", "Time", "Patched LLD(s)"))
        logger.info("  " + "─"*12 + "  " + "─"*8 + "  " + "─"*8 + "  " + "─"*7 + "  " + "─"*35)

        ok = warn = fail = skip = 0
        for r in self._results:
            icon = {"OK": "✔ OK", "WARN": "⚠ WARN", "FAIL": "✘ FAIL", "SKIP": "○ SKIP"}.get(r.status, r.status)
            lld_names = ", ".join(p.name for p in r.patched_llds) or "(none)"
            logger.info(col.format(r.ip, icon, r.n_changes, f"{r.elapsed_s:.1f}s", lld_names))
            if r.error:
                logger.error("               └─ Error: %s", r.error)
            ok   += r.status == "OK"
            warn += r.status == "WARN"
            fail += r.status == "FAIL"
            skip += r.status == "SKIP"

        elapsed = getattr(self, "_total_elapsed", 0.0)
        m, s = divmod(int(elapsed), 60)
        logger.info(_bar("─"))
        logger.info("  IPs total    : %d   ✔ OK=%d  ⚠ WARN=%d  ✘ FAIL=%d  ○ SKIP=%d",
                    len(self._results), ok, warn, fail, skip)
        logger.info("  Output LLDs  : %s", self.cfg.output_dir)
        logger.info("  Test files   : %s", self.cfg.tests_dir)
        logger.info("  Total time   : %dm %02ds  (%.2fs)", m, s, elapsed)
        logger.info(_bar("═"))
        logger.info("")


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------
def run_from_config(config_path) -> List[IPResult]:
    """Load config and run the full batch pipeline."""
    cfg    = load_config(config_path)
    runner = BatchRunner(cfg)
    return runner.run()
