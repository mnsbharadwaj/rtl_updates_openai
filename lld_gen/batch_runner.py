"""
batch_runner.py -- Batch pipeline runner driven by lld_patcher.yaml config

For every SFR old/new pair found under sfr_old_dir / sfr_new_dir:
  1. Diff old vs new SFR -> classify changes (28 change types)
  2. Scan ALL .h files in lld_dir (using AST/regex) for functions that
     reference the changed registers/fields -- no 1-to-1 naming required
  3. Copy each matching LLD file to output_dir and patch it
     (template or LLM depending on change type and config)
  4. Write generated unit-test file to tests_dir
  5. Print a summary report

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO RUN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. NORMAL MODE  (clean summary output)
   ─────────────────────────────────────────────────────────────────────
   python -m lld_gen.batch_runner --config lld_patcher.yaml
   python -m lld_gen.batch_runner -c configs/lld_patcher.yaml

2. VERBOSE MODE  (per-change category + before/after diffs for LLM patches)
   ─────────────────────────────────────────────────────────────────────
   python -m lld_gen.batch_runner --config lld_patcher.yaml --verbose
   python -m lld_gen.batch_runner -c lld_patcher.yaml -v

3. VERBOSE + DEBUG LOG  (all logger.debug() lines also visible)
   ─────────────────────────────────────────────────────────────────────
   python -m lld_gen.batch_runner -c lld_patcher.yaml -v --log-level DEBUG

4. FROM PYTHON CODE
   ─────────────────────────────────────────────────────────────────────
   from lld_gen.batch_runner import run_from_config
   results = run_from_config("lld_patcher.yaml")            # normal
   results = run_from_config("lld_patcher.yaml", verbose=True)  # verbose

   # Or use the class directly:
   from lld_gen.config import load_config
   from lld_gen.batch_runner import BatchRunner
   cfg     = load_config("lld_patcher.yaml")
   runner  = BatchRunner(cfg, verbose=True)
   results = runner.run()

CONFIG FILE:  lld_patcher.yaml
   sfr_old_dir : path/to/old_sfr/          # folder with old SFR *.h files
   sfr_new_dir : path/to/new_sfr/          # folder with new SFR *.h files
   lld_dir     : path/to/lld/              # folder scanned for ALL lld *.h
   output_dir  : path/to/output/           # patched LLD files written here
   tests_dir   : path/to/tests/            # generated unit test .c files
   no_llm      : false                     # true = template-only mode
   no_git      : false                     # true = skip PR/git staging
   compile_check: false                    # true = run GCC after patch
   llm:
     backend  : ollama                     # ollama | openai | openai_compat
     model    : qwen2.5-coder:7b           # model name
     url      : http://localhost:11434     # local ollama
     location : local                      # local | cloud
     # For cloud endpoint:
     # url     : http://107.99.41.85/ollama/srv1/api/generate
     # model   : gpt-oss
     # location: cloud
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import logging
from lld_gen.encoding_utils import configure_logging_encoding, ensure_utf8_streams
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from lld_gen.config import (
    PatcherConfig, IPJob, load_config, discover_jobs,
    save_checkpoint, load_checkpoint, clear_checkpoint,
)
from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff, summarize_changes, SfrParser, ChangeType, ChangeRecord,
)
from lld_gen.semantic_check import apply_semantic_gate, SEMANTIC_CHECK_TYPES
from lld_gen.lld_patcher import LLDPatcher
from lld_gen.llm_client import LLMClient, load_llm_config
from lld_gen.compile_check import run_compile_check, _find_gcc, GccNotFoundError
from lld_gen.pr_stage import stage_pr

logger = logging.getLogger(__name__)


def _raw_diff(old_path: Path, new_path: Path) -> str:
    """Return unified diff text between two files (same as git diff --no-index)."""
    import difflib
    old_lines = old_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)
    new_lines = new_path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{old_path.name}",
        tofile=f"b/{new_path.name}",
    ))

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
# Verbose helpers
# ---------------------------------------------------------------------------
_LARGE_BLOCK_CHARS = 800   # threshold to print before/after in verbose mode


def _vlog(msg: str) -> None:
    """Verbose-only log line — same indent as _info but with V prefix."""
    logger.info("  │  [V]  %s", msg)


def _change_method(change_type: str, used_llm: bool) -> str:
    """Human-readable description of how a change will be applied."""
    # Categories that use deterministic AST patch — NO LLM needed
    template_only = {
        ChangeType.REG_RENAMED, ChangeType.FIELD_RENAMED,
        ChangeType.FIELD_DELETED,
        # NOTE: FIELD_ADDED is intentionally NOT here — it requires
        # generate_new_lld_function() via LLM when generate_new_functions=True
        ChangeType.BITWIDTH_CHANGED, ChangeType.ACCESS_CHANGED,
        ChangeType.OFFSET_CHANGED, ChangeType.RESET_CHANGED,
        ChangeType.REG_MOVED, ChangeType.REG_SIZE_CHANGED,
        ChangeType.FIELD_SPLIT, ChangeType.FIELD_MERGED,
        ChangeType.FIELD_MOVED_CROSS_REG,
        ChangeType.RESERVED_PROMOTED, ChangeType.RESERVED_PARTIAL_ACTIVATED,
        ChangeType.FIELD_WRITE_ONCE, ChangeType.FIELD_SELF_CLEARING,
        ChangeType.FIELD_STICKY_CHANGED,
    }
    # Categories that go to LLM (with template fallback)
    llm_preferred = {
        ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED,
        ChangeType.FIELD_POLARITY_CHANGED, ChangeType.DESCRIPTION_ADDED,
        ChangeType.FIELD_ENUM_CHANGED,
        # FIELD_ADDED: LLM generates NEW getter/setter from scratch
        ChangeType.FIELD_ADDED,
    }
    # Categories that are manual review
    manual = {
        ChangeType.REG_ADDED, ChangeType.REG_DELETED,
        ChangeType.REG_ARRAY_CHANGED, ChangeType.REG_CLUSTER_CHANGED,
        ChangeType.WRITE_MASK_CHANGED,
    }
    if change_type in manual:
        return "MANUAL REVIEW — not auto-patched"
    if change_type in template_only:
        return "template (deterministic AST patch)"
    if change_type in llm_preferred:
        if change_type == ChangeType.FIELD_ADDED:
            return "LLM new-fn generation" if used_llm else "SKIPPED (no LLM — set generate_new_functions: true)"
        return "LLM (cloud gpt-oss)" if used_llm else "template fallback (LLM unavailable/skipped)"
    return "LLM / template" if used_llm else "template"


def _extract_fn_snippets(block: str, max_chars: int = 600) -> str:
    """
    Extract a readable snippet of C functions from a register block.
    Returns at most max_chars characters to keep logs tidy.
    """
    lines = block.splitlines()
    # Skip the SHA header comment block — find first 'static inline' line
    fn_start = next(
        (i for i, ln in enumerate(lines) if "static inline" in ln or "/**" in ln),
        0
    )
    snippet = "\n".join(lines[fn_start:])
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n  // ... [truncated]"
    return snippet


def _print_before_after(
    reg_name: str,
    field_name: str,
    change_type: str,
    before: str,
    after: str,
    used_llm: bool,
    lld_file: str,
) -> None:
    """
    Verbose printer: emit a before/after diff for one LLD function change.
    Always prints category + application method.
    For large blocks or LLM-patched blocks, also prints the function diff.
    """
    method = _change_method(change_type, used_llm)
    label  = f"{reg_name}.{field_name}" if field_name else reg_name
    llm_tag = " ✓LLM" if used_llm else ""

    import re as _re
    from pathlib import Path
    file_path = Path(lld_file)
    display_name = file_path.name

    logger.info("  │")
    logger.info("  │  ├── [VERBOSE] Change applied in: %s", display_name)
    logger.info("  │  │   Field    : %s", label)
    logger.info("  │  │   Type     : %-30s  Method: %s%s",
                change_type, method, llm_tag)

    if file_path.exists():
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        pattern_str = rf"\blld_\w+_{reg_name.lower()}_{field_name.lower()}_(?:get|set|clear|set1|trigger)\b"
        fn_re = _re.compile(pattern_str, _re.IGNORECASE)
        found_fns = []
        for idx, line in enumerate(lines, 1):
            match = fn_re.search(line)
            if match:
                fn_name = match.group(0)
                if any(kw in line for kw in ("static", "inline", "void", "uint")):
                    if fn_name not in found_fns:
                        found_fns.append(fn_name)
                        logger.info("  │  │   Patched Function: %s (at line %d)", fn_name, idx)

    # Only print before/after for changed blocks or LLM-patched
    is_large   = len(before) > _LARGE_BLOCK_CHARS or len(after) > _LARGE_BLOCK_CHARS
    block_diff = before.strip() != after.strip()
    if block_diff and (used_llm or is_large):
        before_snip = _extract_fn_snippets(before)
        after_snip  = _extract_fn_snippets(after)
        logger.info("  │  │   ── BEFORE ──")
        for ln in before_snip.splitlines():
            logger.info("  │  │   - %s", ln)
        logger.info("  │  │   ── AFTER  ──")
        for ln in after_snip.splitlines():
            logger.info("  │  │   + %s", ln)
    elif block_diff:
        logger.info("  │  │   (block modified — use --verbose for before/after diff)")
    else:
        logger.info("  │  │   (block unchanged — SHA/comment-only update)")
    logger.info("  │  └%s", "─" * 55)


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
    n_added:      int = 0
    n_patched:    int = 0
    n_removed:    int = 0
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
      • Existing fields  — match by function prefix: lld_{ip}_{reg}_
      • FIELD_ADDED      — match by struct member only: st{REG}
                           (no function exists yet, but the register struct IS
                            in the file — new fn will be appended after the block)

    FIX: Previously FIELD_ADDED was only matched by fn_prefix which never
    exists, so new fields were silently skipped. Now struct_ref alone is
    sufficient to include the file for new-function generation.
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
            if cr.change_type == ChangeType.FIELD_ADDED:
                # New field: no function exists yet — match by register struct
                # presence only; new function will be appended to the block
                if struct_ref in text:
                    hits.append(cr)
                    logger.debug("  │       + FIELD_ADDED %s.%s matched via struct %s",
                                 cr.reg_name, cr.field_name, struct_ref)
            elif fn_prefix in text or struct_ref in text:
                hits.append(cr)

        if hits:
            regs      = sorted({cr.reg_name for cr in hits})
            new_flds  = [cr.field_name for cr in hits
                         if cr.change_type == ChangeType.FIELD_ADDED]
            result[h_file] = hits
            logger.info(
                "  │     ✔ %-30s  registers: %s%s",
                h_file.name, ", ".join(regs),
                ("  [+new: %s]" % ", ".join(new_flds)) if new_flds else "",
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

    def __init__(self, cfg: PatcherConfig, verbose: bool = False):
        self.cfg     = cfg
        self.verbose = verbose or cfg.verbose
        self._results: List[IPResult] = []

    def _print_verbose_classifier_report(
        self,
        ip: str,
        reg_name: str,
        field_name: str,
        change_type: str,
        used_llm: bool,
        lld_file: str,
        llm: Optional[object],
        out_lld: Path,
    ) -> None:
        """Prints a detailed verbose report for the patched change."""
        logger.info("  │")
        logger.info("  │  [V] ── VERBOSE CLASSIFIER REPORT ──────────────────")
        logger.info("  │  [V]  1. WHAT'S THE CHANGE:")
        fld_part = f".{field_name}" if field_name else ""
        logger.info("  │  [V]     Register/Field : %s%s", reg_name, fld_part)
        logger.info("  │  [V]     Classifier     : %s", change_type)
        
        # 2. Prompts
        logger.info("  │  [V]  2. PROMPTS SENT TO LLM:")
        if used_llm and llm and getattr(llm, "last_user_prompt", None):
            sys_prompt = getattr(llm, "last_system_prompt", "")
            usr_prompt = getattr(llm, "last_user_prompt", "")
            logger.info("  │  [V]     --- SYSTEM ---")
            for ln in sys_prompt.splitlines():
                logger.info("  │  [V]     | %s", ln)
            logger.info("  │  [V]     --- USER ---")
            for ln in usr_prompt.splitlines():
                logger.info("  │  [V]     | %s", ln)
        else:
            logger.info("  │  [V]     (N/A - Deterministic template patch used)")

        # 3. Function received from LLM
        logger.info("  │  [V]  3. FUNCTION RECEIVED FROM LLM:")
        if used_llm and llm and getattr(llm, "last_raw_response", None):
            raw_resp = getattr(llm, "last_raw_response", "")
            for ln in raw_resp.splitlines():
                logger.info("  │  [V]     | %s", ln)
        else:
            logger.info("  │  [V]     (N/A - Deterministic template patch used)")

        # 4. Files affected
        logger.info("  │  [V]  4. LIST OF LLD FILES AFFECTED:")
        logger.info("  │  [V]     - %s", out_lld.name)

        # 5. Line numbers in each file
        logger.info("  │  [V]  5. LINE NUMBER(S) WHERE PATCHED:")
        from lld_gen.change_summary import lld_function_names as _lfns
        fn_names = _lfns(ip, reg_name, field_name or "", "RW")
        found_any = False
        if out_lld.exists():
            lines = out_lld.read_text(encoding="utf-8", errors="replace").splitlines()
            import re as _re
            for fn in fn_names:
                for idx, line in enumerate(lines, 1):
                    if fn in line and _re.search(rf"\b{_re.escape(fn)}\b\s*\(", line):
                        logger.info("  │  [V]     - %s:%d (function: %s)", out_lld.name, idx, fn)
                        found_any = True
                        break
        if not found_any:
            logger.info("  │  [V]     - (No matching function signatures found; block/struct change)")
        logger.info("  │  [V] ───────────────────────────────────────────────")
        logger.info("  │")

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
        logger.info("  Verbose mode : %s", "✓ ON  (before/after diffs for LLM patches)" if self.verbose else "OFF (pass --verbose to enable)")
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

            # ── Semantic description equivalence gate ───────────────────────
            _llm_for_gate = _make_llm(self.cfg, overrides, no_llm)
            sem_low  = getattr(self.cfg, "semantic_similarity_threshold_low",  0.75)
            sem_high = getattr(self.cfg, "semantic_similarity_threshold_high", 0.85)
            n_sem_before = sum(1 for cr in all_changes if cr.needs_llm)
            apply_semantic_gate(
                changes        = all_changes,
                llm_client     = _llm_for_gate,
                threshold_low  = sem_low,
                threshold_high = sem_high,
            )
            n_sem_skip = n_sem_before - sum(1 for cr in all_changes if cr.needs_llm)
            if n_sem_skip:
                _info(f"Semantic gate: {n_sem_skip} description change(s) found "
                      "cosmetically equivalent — LLM patching skipped for those")

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
            # STEP 2 — LLM Change Summary  (raw diff → plain English)
            # ─────────────────────────────────────────────────────────────────
            _step("2", "5", "LLM Change Summary")
            _llm_early = _make_llm(self.cfg, overrides, no_llm)

            if _llm_early.available:
                _info("Sending raw diff to LLM for plain-English summary ...")
                raw_diff    = _raw_diff(job.old_sfr, job.new_sfr)
                cls_text    = summarize_changes(all_changes)
                llm_summary = _llm_early.summarize_diff(
                    ip=job.ip,
                    diff_text=raw_diff,
                    changes_summary=cls_text,
                )
                logger.info("  │")
                if llm_summary:
                    for line in llm_summary.splitlines():
                        logger.info("  │   %s", line)
                else:
                    _info("(LLM returned empty — see structured list above)")
                logger.info("  │")
            else:
                # Fallback: formatted table from ChangeRecords
                _info("LLM unavailable — structured change table:")
                logger.info("  │")
                logger.info("  │   %-28s  %-16s  %-16s  %s",
                            "Change Type", "Register", "Field", "Detail")
                logger.info("  │   %s  %s  %s  %s",
                            "─"*28, "─"*16, "─"*16, "─"*28)
                for cr in all_changes:
                    field_part = cr.field_name or ""
                    detail     = ", ".join(cr.details) if cr.details else ""
                    logger.info("  │   %-28s  %-16s  %-16s  %s",
                                cr.change_type, cr.reg_name, field_part, detail)
                logger.info("  │")
            _step_done()

            # ─────────────────────────────────────────────────────────────────
            # STEP 3 — Build LLM client (reuse if already built)
            # ─────────────────────────────────────────────────────────────────
            _step("3", "5", "Initialise LLM client")
            llm = _llm_early   # already created above
            _ok(f"LLM available={llm.available}  backend={llm.backend}")
            _step_done()

            # Parse new SFR IR
            parser = SfrParser(ip=job.ip)
            new_ir = parser.parse_file(job.new_sfr)

            # ─────────────────────────────────────────────────────────────────
            # STEP 3 — Scan ALL LLD files
            # ─────────────────────────────────────────────────────────────────
            _step("4", "5", f"Scan {self.cfg.lld_dir.name}/ for LLD files referencing changed registers")
            lld_hits = _lld_files_for_changes(auto_crs, job.ip, self.cfg.lld_dir)

            if not lld_hits:
                _warn("No LLD file references any of the changed registers")
                _warn("Nothing to patch. Check that lld_dir contains the right headers.")
                _step_done("SKIP")
                result.status = "SKIP"
                return result

            _ok(f"Found {len(lld_hits)} LLD file(s) to patch")
            _step_done()

            # ── Step 4 ── Patch each LLD file
            # ─────────────────────────────────────────────────────────────────
            _step("5", "5", f"Patch {len(lld_hits)} LLD file(s)")
            all_manual: List[str] = [
                f"{cr.change_type}: {cr.reg_name}" + (f".{cr.field_name}" if cr.field_name else "")
                for cr in manual_crs
            ]

            all_added_fns: List[str] = []
            all_patched_fns: List[str] = []
            all_removed_fns: List[str] = []
            all_deprecated_fns: List[str] = []

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

                # Build verbose callback if enabled
                if self.verbose:
                    def verbose_cb(reg_name: str, field_name: str, change_type: str,
                                   before: str, after: str, used_llm: bool, lld_file: str):
                        _print_before_after(reg_name, field_name, change_type, before, after, used_llm, lld_file)
                        self._print_verbose_classifier_report(
                            job.ip, reg_name, field_name, change_type, used_llm, lld_file, llm, out_lld
                        )
                else:
                    verbose_cb = None

                patcher = LLDPatcher(
                    ip=job.ip,
                    llm_client=llm,
                    no_llm=no_llm,
                    verbose_callback=verbose_cb,
                )
                t_patch = time.perf_counter()
                patcher.patch(
                    lld_path = out_lld,
                    changes  = relevant_changes,
                    new_ir   = new_ir,
                    out_path = out_lld,
                )
                patch_s = time.perf_counter() - t_patch
                added_count = len(patcher.get_added_fns())
                patched_count = len(patcher.get_patched_fns())
                removed_count = len(patcher.get_removed_fns())
                total_affected = added_count + patched_count + removed_count
                result.n_added   += added_count
                result.n_patched += patched_count
                result.n_removed += removed_count
                logger.info(
                    "  │     ✔ Patch complete in %.1fs  →  %s (%d function(s) affected: %d added, %d patched, %d removed)",
                    patch_s, out_lld, total_affected, added_count, patched_count, removed_count
                )

                # AST-Based Cross-Refactoring
                if getattr(self.cfg, "cross_lld_scan", True):
                    from lld_gen.ast_refactor import refactor_cross_references
                    logger.info("  │     Running AST-based cross-refactoring ...")
                    auto_crs = [cr for cr in relevant_changes if not ChangeType.is_manual_review(cr.change_type)]
                    ast_patched_files = refactor_cross_references(
                        cfg       = self.cfg,
                        ip        = job.ip,
                        new_ir    = new_ir,
                        auto_crs  = auto_crs,
                        out_lld   = out_lld,
                        test_file = test_file,
                    )
                    if ast_patched_files:
                        logger.info("  │     ✔ AST-based cross-refactoring updated %d file(s)", len(ast_patched_files))

                if self.verbose:
                    # Summarise what was patched in this file
                    auto_ct    = [cr for cr in relevant_changes if not ChangeType.is_manual_review(cr.change_type)]
                    manual_ct  = [cr for cr in relevant_changes if  ChangeType.is_manual_review(cr.change_type)]
                    logger.info("  │")
                    logger.info("  │  [V] Patch summary for %s:", lld_src.name)
                    for cr in auto_ct:
                        fld = f".{cr.field_name}" if cr.field_name else ""
                        method = _change_method(cr.change_type, llm.available)
                        logger.info("  │  [V]   ✔ %-28s  [%s%s]  via: %s",
                                    cr.change_type, cr.reg_name, fld, method)
                    for cr in manual_ct:
                        fld = f".{cr.field_name}" if cr.field_name else ""
                        logger.info("  │  [V]   ⚠ %-28s  [%s%s]  → MANUAL REVIEW",
                                    cr.change_type, cr.reg_name, fld)

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

                # Collect tracked functions
                all_added_fns.extend(patcher.get_added_fns())
                all_patched_fns.extend(patcher.get_patched_fns())
                all_removed_fns.extend(patcher.get_removed_fns())
                all_deprecated_fns.extend(patcher.get_deprecated_fns())

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
                    added_fns           = sorted(list(set(all_added_fns + all_patched_fns))),
                    removed_fns         = sorted(list(set(all_removed_fns))),
                    deprecated_fns      = sorted(list(set(all_deprecated_fns))),
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

        col = "  {:<12}  {:<8}  {:>8}  {:>18}  {:>7}  {}"
        logger.info(col.format("IP", "Status", "SFR Chg", "LLD Fns (+/~/-)", "Time", "Patched LLD(s)"))
        logger.info("  " + "─"*12 + "  " + "─"*8 + "  " + "─"*8 + "  " + "─"*18 + "  " + "─"*7 + "  " + "─"*35)

        ok = warn = fail = skip = 0
        for r in self._results:
            icon = {"OK": "✔ OK", "WARN": "⚠ WARN", "FAIL": "✘ FAIL", "SKIP": "○ SKIP"}.get(r.status, r.status)
            lld_names = ", ".join(p.name for p in r.patched_llds) or "(none)"
            lld_fns = f"{r.n_added}/{r.n_patched}/{r.n_removed}"
            logger.info(col.format(r.ip, icon, r.n_changes, lld_fns, f"{r.elapsed_s:.1f}s", lld_names))
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
def run_from_config(config_path, verbose: bool = False) -> List[IPResult]:
    """Load config and run the full batch pipeline."""
    cfg    = load_config(config_path)
    runner = BatchRunner(cfg, verbose=verbose or cfg.verbose)
    return runner.run()


# ---------------------------------------------------------------------------
# CLI main (python -m lld_gen.batch_runner --config <yaml> [--verbose])
# ---------------------------------------------------------------------------
def main() -> None:
    """
    Command-line entry point for the batch runner.

    Usage:
        python -m lld_gen.batch_runner --config lld_patcher.yaml [--verbose]

    Flags:
        --config  / -c   Path to lld_patcher.yaml  (required)
        --verbose / -v   Enable verbose output:
                           - Per-change category + application method log
                           - Before/after function diff for LLM-patched blocks
                           - Full patch summary per LLD file
        --log-level      Python log level: DEBUG|INFO|WARNING|ERROR (default INFO)
    """
    import argparse
    import sys
    import logging

    parser = argparse.ArgumentParser(
        prog="lld_batch_runner",
        description="LLD Auto-Patcher — batch mode driven by lld_patcher.yaml",
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        metavar="YAML",
        help="Path to lld_patcher.yaml config file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help=(
            "Verbose output: per-change category logs, before/after diffs "
            "for LLM-patched large functions, full patch summary per file"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    ensure_utf8_streams()        # make stdout/stderr utf-8 safe
    configure_logging_encoding()  # make log handlers utf-8 safe

    results = run_from_config(args.config, verbose=args.verbose)

    # Exit with non-zero if any IP failed
    if any(r.status == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
