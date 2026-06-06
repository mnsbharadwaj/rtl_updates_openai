"""
workflow_runner.py — End-to-End LLD Auto-Patcher Workflow (v3.0)

Full pipeline:
  [A] Clone IPxact repo  → checkout new IP-XACT .xml files
  [B] Convert .xml       → SFR_<IP>.h  (via convert.py)
  [C] Clone LLD repo     → checkout current SFR_*.h + lld_*.h
  [D] Diff new vs old SFR → 28 classified ChangeRecord objects
  [E] Patch LLD          → surgical AST-based function update
       - per-function gcc check (5 retries)
       - signature-locked LLM body-only prompting
       - skip REG_ADDED/DELETED → flag for manual review
  [F] Cross-LLD scan     → find callers of changed functions
  [G] Atomic commit      → one git commit per SFR file changed
  [H] Push + raise PR    → Bitbucket REST API

Usage:
    from lld_gen.workflow_runner import WorkflowRunner
    from lld_gen.config import load_workflow_config

    cfg = load_workflow_config("workflow_config.yaml")
    runner = WorkflowRunner(cfg)
    results = runner.run()
"""
from __future__ import annotations

import logging

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from lld_gen.config import WorkflowConfig, RepoSpec
from lld_gen.git_manager import GitManager, RepoConfig, PRConfig
from lld_gen.ipxact_pipeline import IpxactPipeline
from lld_gen.sfr_diff_analyzer import classify_sfr_diff, summarize_changes, ChangeType
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient, make_llm_client
from lld_gen.compile_check import run_compile_check, run_compile_check_one_fn
from lld_gen.pr_stage import stage_pr, build_pr_description
from lld_gen.lld_cross_ref import find_cross_refs, format_cross_ref_report

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class IPWorkflowResult:
    """Result for one IP's full workflow run."""
    ip:               str
    status:           str = "PENDING"    # OK | WARN | FAIL | SKIP | MANUAL
    n_changes:        int = 0
    n_auto_patched:   int = 0
    n_manual_review:  int = 0
    n_llm_used:       int = 0
    n_template_used:  int = 0
    compile_ok:       bool = False
    pr_url:           str = ""
    commit_sha:       str = ""
    error:            str = ""
    manual_review_items: List[str] = field(default_factory=list)
    cross_ref_files:  List[str] = field(default_factory=list)
    change_summary:   str = ""
    elapsed_s:        float = 0.0   # wall-clock seconds for this IP


@dataclass
class WorkflowRunResult:
    """Aggregate result for the full multi-IP run."""
    ip_results:      List[IPWorkflowResult] = field(default_factory=list)
    pr_url:          str = ""
    error:           str = ""
    total_elapsed_s: float = 0.0   # total wall-clock seconds

    @property
    def all_ok(self) -> bool:
        return all(r.status in ("OK", "SKIP") for r in self.ip_results)

    def print_summary(self) -> None:
        logger.info("=" * 68)
        logger.info("  LLD Auto-Patcher v3.0 -- Workflow Summary")
        logger.info("=" * 68)
        for r in self.ip_results:
            icon = {"OK": "[OK]", "SKIP": "[SKIP]", "FAIL": "[X]", "WARN": "[!]",
                    "MANUAL": "[MANUAL]"}.get(r.status, "?")
            logger.info(
                f"  {icon} {r.ip:<12}  {r.status:<6}  "
                f"{r.n_changes:>3} changes  "
                f"{r.n_auto_patched:>3} auto  "
                f"{r.n_manual_review:>3} manual  "
                f"compile={'OK' if r.compile_ok else 'FAIL'}  "
                f"{r.elapsed_s:>6.1f}s"
            )
            if r.pr_url:
                logger.info(f"     PR: {r.pr_url}")
        logger.info("=" * 68)
        total_m, total_s = divmod(int(self.total_elapsed_s), 60)
        logger.info(f"  Total pipeline time : {total_m}m {total_s:02d}s  "
              f"({self.total_elapsed_s:.2f}s)")
        logger.info("=" * 68)


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------
class WorkflowRunner:
    """
    Orchestrates the complete IPxact → LLD-patch → PR workflow.

    Steps:
      A: Clone ipxact repo
      B: Convert .xml → SFR_*.h
      C: Clone lld_repo (checkout current SFRs + LLDs)
      D: Diff new vs old SFR per IP
      E: Patch LLD (28 change types, per-fn gcc, signature lock)
      F: Cross-LLD reference scan
      G: Atomic git commit per IP
      H: Push + create Bitbucket PR
    """

    def __init__(self, cfg: WorkflowConfig):
        self.cfg = cfg
        self._work_dir = Path(tempfile.mkdtemp(prefix="lld_workflow_"))
        self._ipxact_gm:  Optional[GitManager] = None
        self._lld_gm:     Optional[GitManager] = None

    def _repo_config(self, spec: RepoSpec) -> RepoConfig:
        """Convert RepoSpec → GitManager RepoConfig."""
        import os
        token = spec.token or os.environ.get("BB_TOKEN", os.environ.get("BITBUCKET_TOKEN", ""))
        return RepoConfig(
            url    = spec.url,
            branch = spec.branch or "main",
            tag    = spec.tag,
            commit = spec.commit,
            path   = spec.path,
            token  = token,
        )

    # ── Step A: Clone repos ──────────────────────────────────────────────────

    def _clone_repos(self) -> tuple[Path, Path]:
        """Clone ipxact and lld repos. Returns (ipxact_path, lld_path)."""
        ipxact_dest = self._work_dir / "ipxact_repo"
        lld_dest    = self._work_dir / "lld_repo"

        logger.info("[A] Cloning IP-XACT repository ...")
        if self.cfg.ipxact.url:
            self._ipxact_gm = GitManager(
                self._repo_config(self.cfg.ipxact), work_dir=ipxact_dest
            )
            self._ipxact_gm.clone(ipxact_dest)
            ipxact_path = ipxact_dest
        else:
            # Fallback: sfr_new_dir already contains converted SFR headers
            ipxact_path = self.cfg.sfr_new_dir
            logger.info(f"  [A] No ipxact.url -- using sfr_new_dir: {ipxact_path}")

        logger.info("[A] Cloning LLD repository ...")
        if self.cfg.lld_repo.url:
            self._lld_gm = GitManager(
                self._repo_config(self.cfg.lld_repo), work_dir=lld_dest
            )
            self._lld_gm.clone(lld_dest)

            # Create a patch branch
            patch_branch = f"lld-patch/{self.cfg.lld_repo.branch}"
            self._lld_gm.create_branch(patch_branch)
            lld_path = lld_dest
        else:
            # Fallback: use configured lld_dir / sfr_old_dir
            lld_path = self.cfg.lld_dir.parent
            logger.info(f"  [A] No lld_repo.url -- using lld_dir: {self.cfg.lld_dir}")

        return ipxact_path, lld_path

    # ── Step B: Convert IPxact → SFR headers ────────────────────────────────

    def _convert_ipxact(self, ipxact_path: Path) -> Dict[str, Path]:
        """
        Run convert.py on all .xml files.
        Returns {ip: Path(SFR_IP.h)}.
        Falls back to pre-existing SFR headers if no .xml files found.
        """
        logger.info("[B] Converting IP-XACT -> SFR headers ...")
        out_dir = self._work_dir / "new_sfrs"
        out_dir.mkdir(exist_ok=True)

        convert_script = Path(self.cfg.convert_script)
        if not convert_script.exists():
            convert_script = Path(__file__).parent.parent / self.cfg.convert_script

        pipeline = IpxactPipeline(
            repo_path      = ipxact_path,
            convert_script = convert_script,
            out_dir        = out_dir,
            ipxact_path    = self.cfg.ipxact.path,
            ip_filter      = self.cfg.ip_list or None,
        )
        ip_map = pipeline.run()

        if not ip_map:
            # Fallback: scan for pre-existing SFR_*.h files
            logger.info("  [B] No .xml converted -- scanning for existing SFR_*.h ...")
            for sfr_h in ipxact_path.rglob("SFR_*.h"):
                ip = sfr_h.stem.upper().removeprefix("SFR_")
                if not self.cfg.ip_list or ip in {x.upper() for x in self.cfg.ip_list}:
                    ip_map[ip] = sfr_h

        return ip_map

    # ── Step D: Diff SFR files ───────────────────────────────────────────────

    def _find_current_sfr(self, lld_path: Path, ip: str) -> Optional[Path]:
        """Find the current SFR_<IP>.h in the LLD repo checkout."""
        sfr_path = self.cfg.lld_repo.path if self.cfg.lld_repo.url else ""
        search_root = lld_path / sfr_path if sfr_path else lld_path

        for pat in (f"SFR_{ip}.h", f"sfr_{ip.lower()}.h", f"SFR_{ip.lower()}.h"):
            for candidate in search_root.rglob(pat):
                return candidate

        # Also check configured sfr_old_dir
        if self.cfg.sfr_old_dir.exists():
            for pat in (f"SFR_{ip}.h", f"sfr_{ip.lower()}.h"):
                candidate = self.cfg.sfr_old_dir / pat
                if candidate.exists():
                    return candidate

        return None

    def _find_current_lld(self, lld_path: Path, ip: str) -> Optional[Path]:
        """Find lld_<ip>.h in the LLD repo checkout."""
        lld_sub = self.cfg.lld_repo.path if self.cfg.lld_repo.url else ""
        search_root = lld_path / lld_sub if lld_sub else lld_path

        for pat in (f"lld_{ip.lower()}.h", f"lld_{ip}.h", f"{ip.lower()}_lld.h"):
            for candidate in search_root.rglob(pat):
                return candidate

        # Also check configured lld_dir
        if self.cfg.lld_dir.exists():
            for pat in (f"lld_{ip.lower()}.h", f"lld_{ip}.h"):
                candidate = self.cfg.lld_dir / pat
                if candidate.exists():
                    return candidate

        return None

    # ── Per-IP pipeline ──────────────────────────────────────────────────────

    def _run_one_ip(
        self,
        ip:          str,
        new_sfr:     Path,
        lld_path:    Path,
        llm_client:  Optional[LLMClient],
        gcc_exe:     Optional[str],
    ) -> IPWorkflowResult:
        """Run the full D→H pipeline for one IP."""
        result = IPWorkflowResult(ip=ip)

        # ── D: Find current SFR and LLD ──────────────────────────────────────
        old_sfr  = self._find_current_sfr(lld_path, ip)
        lld_file = self._find_current_lld(lld_path, ip)

        if old_sfr is None:
            result.status = "SKIP"
            result.error  = f"No current SFR found for IP={ip}"
            logger.info(f"  [D] SKIP {ip}: {result.error}")
            return result

        if lld_file is None:
            result.status = "SKIP"
            result.error  = f"No LLD file found for IP={ip}"
            logger.info(f"  [D] SKIP {ip}: {result.error}")
            return result

        logger.info(f"  [D] Diff: {old_sfr.name} vs {new_sfr.name} ...")
        changes = classify_sfr_diff(old_sfr, new_sfr, ip=ip)
        result.n_changes = len(changes)
        result.change_summary = summarize_changes(changes)
        logger.info(f"  [D] {result.change_summary.splitlines()[1]}")

        if not changes:
            result.status = "SKIP"
            logger.info(f"  [D] No changes for {ip} -- skipping")
            return result

        # Separate manual review items
        manual_crs = [cr for cr in changes if ChangeType.is_manual_review(cr.change_type)]
        auto_crs   = [cr for cr in changes if not ChangeType.is_manual_review(cr.change_type)]

        result.n_manual_review   = len(manual_crs)
        result.manual_review_items = [
            f"{cr.change_type}: {cr.reg_name}"
            + (f".{cr.field_name}" if cr.field_name else "")
            for cr in manual_crs
        ]

        # ── E: Patch LLD ─────────────────────────────────────────────────────
        logger.info(f"  [E] Patching {lld_file.name} ({len(auto_crs)} auto-patchable changes) ...")
        cfg = self.cfg

        out_dir = cfg.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_lld = out_dir / lld_file.name
        shutil.copy2(lld_file, out_lld)

        # Parse new SFR for struct generation
        from lld_gen.sfr_diff_analyzer import SfrParser
        new_ir = SfrParser(ip=ip).parse_file(new_sfr)

        patcher = LLDPatcher(
            ip         = ip,
            llm_client = llm_client,
            no_llm     = cfg.no_llm,
        )
        try:
            patcher.patch(
                lld_path = out_lld,
                changes  = auto_crs,
                new_ir   = new_ir,
                out_path = out_lld,
            )
        except RuntimeError as exc:
            result.status = "FAIL"
            result.error  = f"Patcher error: {exc}"
            logger.error(f"  [E] FAIL: {exc}")
            return result

        # Write test file
        tests_dir = cfg.tests_dir
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_file = tests_dir / f"test_lld_{ip.lower()}.c"
        lld_text  = out_lld.read_text(encoding="utf-8")
        patcher.write_test_file(
            out_path  = test_file,
            sfr_new   = new_sfr.name,
            lld_new   = out_lld.name,
            new_ir    = new_ir,
            llm_client= llm_client,
            lld_text  = lld_text,
        )

        result.n_auto_patched = len(auto_crs) - len(patcher.get_deprecated_fns() or [])

        # ── E: Compile gate (full file after all patches) ─────────────────────
        logger.info(f"  [E] Compile-checking {out_lld.name} ...")
        compile_result = run_compile_check(
            test_file  = test_file,
            sfr_new    = new_sfr,
            lld_file   = out_lld,
            llm_client = llm_client if not cfg.no_llm else None,
            gcc_exe    = gcc_exe,
            required   = cfg.compile_check,
        )
        result.compile_ok = compile_result.success
        if compile_result.needs_review:
            result.manual_review_items.extend(compile_result.needs_review)

        # ── F: Cross-LLD reference scan ───────────────────────────────────────
        cross_refs = {}
        if cfg.cross_lld_scan and cfg.lld_dir.exists():
            changed_fn_names = []
            for cr in auto_crs:
                if cr.field_name:
                    for verb in ("get", "set", "clear", "set1", "trigger"):
                        fn = f"lld_{ip.lower()}_{cr.reg_name.lower()}_{cr.field_name.lower()}_{verb}"
                        changed_fn_names.append(fn)
            if changed_fn_names:
                cross_refs = find_cross_refs(
                    ip           = ip,
                    changed_fns  = changed_fn_names,
                    lld_dir      = cfg.lld_dir,
                    search_depth = cfg.lld_search_depth,
                )
                result.cross_ref_files = [str(p) for p in cross_refs.keys()]

        # ── G: Stage PR description ───────────────────────────────────────────
        cross_ref_md = format_cross_ref_report(cross_refs)
        pr_desc = build_pr_description(
            ip              = ip,
            changes         = changes,
            compile_result  = compile_result,
            lld_file        = out_lld,
            test_file       = test_file,
            deprecated_fns  = patcher.get_deprecated_fns(),
        )
        # Append manual review and cross-ref sections
        manual_section = _build_manual_review_section(manual_crs, compile_result)
        if manual_section:
            pr_desc += "\n\n" + manual_section
        if cross_ref_md:
            pr_desc += "\n\n" + cross_ref_md

        pr_desc_path = out_dir / "PR_DESCRIPTION.md"
        pr_desc_path.write_text(pr_desc, encoding="utf-8")
        logger.debug(f"  [G] PR description written: {pr_desc_path.name}")

        # ── G: Atomic git commit ──────────────────────────────────────────────
        if not cfg.no_git and self._lld_gm:
            commit_msg = (
                f"{cfg.commit_prefix}({ip.lower()}): patch LLD for SFR update\n\n"
                f"SFR: {new_sfr.name} → {old_sfr.name}\n"
                f"Changes: {result.n_changes} total "
                f"({result.n_auto_patched} auto, {result.n_manual_review} manual)\n"
                f"Compile: {'OK' if result.compile_ok else 'NEEDS_REVIEW'}\n"
                f"\nChange summary:\n{result.change_summary}"
            )
            files_to_commit = [out_lld, test_file, pr_desc_path]
            result.commit_sha = self._lld_gm.commit(files_to_commit, commit_msg) or ""

        result.status = "OK" if compile_result.success else "WARN"
        return result

    # ── Main run ─────────────────────────────────────────────────────────────

    def run(self) -> WorkflowRunResult:
        """Execute the full workflow. Returns a WorkflowRunResult."""
        run_result = WorkflowRunResult()
        pipeline_start = time.perf_counter()

        # ── A: Clone repos ────────────────────────────────────────────────────
        try:
            ipxact_path, lld_path = self._clone_repos()
        except Exception as exc:
            run_result.error = f"Clone failed: {exc}"
            logger.error(f"{run_result.error}")
            return run_result

        # ── B: Convert IPxact → SFR headers ──────────────────────────────────
        try:
            ip_sfr_map = self._convert_ipxact(ipxact_path)
        except Exception as exc:
            run_result.error = f"IPxact conversion failed: {exc}"
            logger.error(f"{run_result.error}")
            return run_result

        if not ip_sfr_map:
            run_result.error = "No SFR headers produced from IPxact conversion"
            logger.error(f"{run_result.error}")
            return run_result

        # ── C: Set up LLM client (from llm: config block) ─────────────────────
        llm_client: Optional[LLMClient] = None
        if not self.cfg.no_llm:
            # Build config dict from WorkflowConfig fields for make_llm_client()
            llm_data = {
                "no_llm":      self.cfg.no_llm,
                "llm":         getattr(self.cfg, "llm", {}),
                # legacy flat fields still honoured for backward compat
                "ollama_model": getattr(self.cfg, "ollama_model", ""),
                "hf_token":    getattr(self.cfg, "hf_token", ""),
            }
            llm_client = make_llm_client(llm_data)
            if not llm_client or not llm_client.available:
                force_no_llm = getattr(self.cfg, "force_no_llm", False)
                if force_no_llm:
                    # Config says skip silently — template mode, flag for review
                    logger.warning("  [LLM] force_no_llm=true -> template-only mode "
                          "(LLM changes flagged for MANUAL REVIEW)")
                    llm_client = None
                else:
                    # Interactive prompt
                    logger.warning("=" * 70)
                    logger.warning("  [!] LLM BACKEND NOT AVAILABLE")
                    logger.warning("  -------------------------------------------------")
                    logger.warning("  The following change types REQUIRE LLM but will be")
                    logger.warning("  flagged for MANUAL REVIEW instead of auto-patched:")
                    logger.warning("    COMMENT_CHANGED, MULTI_CHANGED, FIELD_SPLIT/MERGED,")
                    logger.warning("    FIELD_POLARITY_CHANGED, FIELD_ENUM_CHANGED, etc.")
                    logger.warning("")
                    logger.warning("  Options:")
                    logger.warning("    1. Fix LLM config and re-run (recommended)")
                    logger.warning("       -> Set 'llm: backend: ollama' + start Ollama")
                    logger.warning("       -> Or 'llm: backend: openai' + OPENAI_API_KEY env")
                    logger.warning("    2. Continue with template-only mode")
                    logger.warning("       -> LLM-needing changes flagged for manual review")
                    logger.warning("=" * 70)
                    try:
                        choice = input("  Continue without LLM? [y/N]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "n"
                    if choice not in ("y", "yes"):
                        from lld_gen.config import save_checkpoint
                        save_checkpoint(self.cfg.output_dir, completed_ips=[],
                                        stage="pre_llm",
                                        context={"reason": "user chose to abort -- LLM not available"})
                        run_result.error = "LLM not available -- user chose to abort. Fix config and re-run."
                        logger.error(f"  [ABORT] {run_result.error}")
                        return run_result
                    logger.warning("  [LLM] Proceeding with template-only mode "
                          "(LLM changes -> NEEDS_REVIEW)")
                    llm_client = None

        # ── C2: GCC gate (conditional on compile_check config) ────────────────
        from lld_gen.compile_check import _find_gcc, GccNotFoundError
        compile_check = getattr(self.cfg, "compile_check", True)
        gcc_exe: Optional[str] = None

        if compile_check:
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)
            except GccNotFoundError:
                logger.error("=" * 70)
                logger.error("  [X] GCC NOT FOUND -- PIPELINE STOPPED")
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
                from lld_gen.config import save_checkpoint
                save_checkpoint(self.cfg.output_dir, completed_ips=[],
                                stage="pre_gcc",
                                context={"ip_sfr_map": {k: str(v) for k, v in ip_sfr_map.items()}})
                run_result.error = ("gcc not found -- pipeline stopped. "
                                    "Install gcc and re-run to resume.")
                run_result.total_elapsed_s = time.perf_counter() - pipeline_start
                run_result.print_summary()
                return run_result
            logger.debug(f"  [GCC] Found: {gcc_exe}")
        else:
            logger.info("  [GCC] compile_check: false -> skipping gcc verification")
            try:
                gcc_exe = _find_gcc(self.cfg.gcc)  # still try, but don't fail
            except GccNotFoundError:
                gcc_exe = None
            if gcc_exe:
                logger.debug(f"  [GCC] Found anyway: {gcc_exe} (will use for optional checks)")

        # ── Load checkpoint for resume ────────────────────────────────────────
        from lld_gen.config import load_checkpoint, save_checkpoint, clear_checkpoint
        checkpoint = load_checkpoint(self.cfg.output_dir)
        checkpoint_completed: set = set()
        if checkpoint:
            checkpoint_completed = set(checkpoint.get("completed_ips", []))

        # ── D-G: Run per-IP pipeline ──────────────────────────────────────────
        logger.info(f"[D-G] Processing {len(ip_sfr_map)} IP(s): {', '.join(ip_sfr_map)}")
        completed_ips: List[str] = list(checkpoint_completed)

        for ip, new_sfr in sorted(ip_sfr_map.items()):
            # Skip already-completed IPs (resume mode)
            if ip in checkpoint_completed:
                logger.info("-" * 60)
                logger.info(f"  IP: {ip}  [RESUME -- already completed, skipping]")
                continue

            logger.info("-" * 60)
            logger.info(f"  IP: {ip}")
            ip_start = time.perf_counter()
            try:
                ip_result = self._run_one_ip(
                    ip         = ip,
                    new_sfr    = new_sfr,
                    lld_path   = lld_path,
                    llm_client = llm_client,
                    gcc_exe    = gcc_exe,
                )
            except Exception as exc:
                ip_result = IPWorkflowResult(ip=ip, status="FAIL", error=str(exc))
                logger.error(f"  [FAIL] {ip}: {exc}")
            ip_result.elapsed_s = time.perf_counter() - ip_start
            run_result.ip_results.append(ip_result)
            completed_ips.append(ip)

            # Save checkpoint after each IP
            save_checkpoint(self.cfg.output_dir, completed_ips=completed_ips,
                            stage="ip_done", context={"last_ip": ip})

        # ── H: Push + raise PR ────────────────────────────────────────────────
        if not self.cfg.no_git and self._lld_gm:
            logger.info("[H] Pushing patched branch ...")
            try:
                patch_branch = self._lld_gm.current_branch()
                self._lld_gm.push(branch=patch_branch)

                # Build combined PR description
                all_changes_summary = "\n".join(
                    f"- {r.ip}: {r.n_changes} changes, "
                    f"{r.n_auto_patched} auto-patched, "
                    f"{r.n_manual_review} manual review"
                    for r in run_result.ip_results
                )
                pr_title = f"{self.cfg.pr_title_prefix} — {len(ip_sfr_map)} IP(s)"
                pr_desc  = (
                    f"# LLD Auto-Patcher v3.0 — SFR Update PR\n\n"
                    f"## IPs Updated\n\n{all_changes_summary}\n\n"
                    f"## Manual Review Items\n\nSee per-IP PR_DESCRIPTION.md files."
                )

                # Target branch
                pr_spec   = self.cfg.pr_target
                to_branch = (pr_spec.branch if pr_spec.branch
                             else self.cfg.lld_repo.branch)

                pr_cfg = PRConfig(
                    url    = pr_spec.url or self.cfg.lld_repo.url,
                    branch = to_branch,
                )
                pr_url = self._lld_gm.create_pr(
                    title       = pr_title,
                    description = pr_desc,
                    from_branch = patch_branch,
                    to_branch   = to_branch,
                    pr_cfg      = pr_cfg,
                )
                run_result.pr_url = pr_url
            except Exception as exc:
                logger.warning(f"  [H-WARN] Push/PR failed: {exc}")

        run_result.total_elapsed_s = time.perf_counter() - pipeline_start

        # Clear checkpoint — pipeline completed successfully
        clear_checkpoint(self.cfg.output_dir)

        run_result.print_summary()
        return run_result


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _build_manual_review_section(
    manual_crs: "List",
    compile_result: "object",
) -> str:
    """Build the ⚠ MANUAL REVIEW section for PR description."""
    lines = []

    if manual_crs:
        lines.append("## ⚠ MANUAL REVIEW REQUIRED\n")
        lines.append("These changes were **not auto-patched** — engineer action needed:\n")

        reg_added   = [cr for cr in manual_crs if cr.change_type == ChangeType.REG_ADDED]
        reg_deleted = [cr for cr in manual_crs if cr.change_type == ChangeType.REG_DELETED]
        other       = [cr for cr in manual_crs if cr.change_type not in
                       {ChangeType.REG_ADDED, ChangeType.REG_DELETED}]

        if reg_added:
            lines.append("### Registers Added (write new LLD functions)")
            lines.append("| Register | Fields | Action |")
            lines.append("|----------|--------|--------|")
            for cr in reg_added:
                fields = ", ".join(cr.new_reg.fields.keys()) if cr.new_reg else "?"
                lines.append(f"| `{cr.reg_name}` | {fields} | Write `lld_*_{cr.reg_name.lower()}_*` functions |")
            lines.append("")

        if reg_deleted:
            lines.append("### Registers Deleted (remove or deprecate LLD functions)")
            lines.append("| Register | Action |")
            lines.append("|----------|--------|")
            for cr in reg_deleted:
                lines.append(f"| `{cr.reg_name}` | Remove or deprecate `lld_*_{cr.reg_name.lower()}_*` functions |")
            lines.append("")

        if other:
            lines.append("### Other Changes Requiring Manual Review")
            lines.append("| Register | Field | Change Type | Reason |")
            lines.append("|----------|-------|-------------|--------|")
            for cr in other:
                field = cr.field_name or "-"
                reason = ", ".join(cr.details[:2]) if cr.details else cr.change_type
                lines.append(f"| `{cr.reg_name}` | `{field}` | {cr.change_type} | {reason} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------
def run_workflow(config_path: "str | Path") -> WorkflowRunResult:
    """
    Load a workflow_config.yaml and run the full pipeline.
    Returns WorkflowRunResult.
    """
    from lld_gen.config import load_workflow_config
    cfg = load_workflow_config(config_path)
    return WorkflowRunner(cfg).run()
