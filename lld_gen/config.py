"""
config.py -- Config file reader and batch-run file matcher

Reads lld_patcher.yaml and discovers old/new SFR pairs matched to LLD files.

Matching strategy (in priority order):
    1. Same basename in both dirs:
           old/sfr_pmu.h + new/sfr_pmu.h -> IP=PMU, lld=lld_pmu.h
    2. _old/_new suffix convention:
           old/sfr_pmu_old.h + new/sfr_pmu_new.h -> IP=PMU, lld=lld_pmu.h
    3. LLD file name searched as: lld_{ip}.h, {ip}_lld.h, {ip}.h (case-insensitive)
"""
from __future__ import annotations

import logging

import json as _json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Try PyYAML first, fall back to a minimal YAML-ish parser for simple configs
try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class IPJob:
    """One SFR-pair + LLD file to process."""
    ip:        str
    old_sfr:   Path
    new_sfr:   Path
    lld:       Optional[Path] = None
    out_lld:   Optional[Path] = None          # where to write the patched file
    tests_dir: Optional[Path] = None

    def __str__(self) -> str:
        return (
            f"IP={self.ip}\n"
            f"  old_sfr  : {self.old_sfr}\n"
            f"  new_sfr  : {self.new_sfr}\n"
            f"  lld      : {self.lld}\n"
            f"  out_lld  : {self.out_lld}\n"
            f"  tests_dir: {self.tests_dir}"
        )


@dataclass
class PatcherConfig:
    """Parsed configuration from lld_patcher.yaml."""
    sfr_old_dir:   Path
    sfr_new_dir:   Path
    lld_dir:       Path
    output_dir:    Path
    tests_dir:     Path
    no_llm:        bool
    no_git:        bool
    hf_token:      str
    gcc:           Optional[str]
    github_url:    str               = ""     # e.g. https://github.com/user/repo
    llm_test_gen:  bool              = False  # true = use LLM to write richer unit tests
    ollama_model:  str               = ""     # e.g. "qwen2.5-coder:1.5b" for local Ollama
    ip_overrides:  Dict[str, dict]   = field(default_factory=dict)
    ip_list:       List[str]         = field(default_factory=list)
    # v3.0 patching flags
    max_llm_retries:   int  = 5      # LLM fix retries per function
    per_fn_compile:    bool = True   # gcc check after every LLM patch
    skip_reg_added:    bool = True   # flag REG_ADDED for manual review
    skip_reg_deleted:  bool = True   # flag REG_DELETED for manual review
    cross_lld_scan:    bool = True   # scan lld_dir for callers of changed fns
    lld_search_depth:  int  = 3      # cross-LLD scan depth
    atomic_commits:    bool = True   # one commit per SFR file
    commit_prefix:     str  = "feat(lld)"  # git commit message prefix
    emit_enum_defines: bool = False  # generate #define enums for FIELD_ENUM_CHANGED
    # v3.1 — gcc gate + LLM prompt
    compile_check:     bool = True   # True = gcc REQUIRED (stop if not found)
    force_no_llm:      bool = False  # True = skip LLM prompt, use template mode silently
    # v3.2 — flexible LLD mapping (no 1-to-1 naming required)
    lld_all_files:     bool = False  # True = apply every SFR change to ALL .h files in lld_dir
    # v3.3 — semantic description equivalence gate
    semantic_similarity_threshold_low:  float = 0.75  # below → auto-patch (skip check)
    semantic_similarity_threshold_high: float = 0.85  # above → auto-skip (mark equivalent)
    verbose:                           bool  = False # Enable verbose logging and classifier reports


@dataclass
class RepoSpec:
    """Configuration for one Git/Bitbucket repository."""
    url:    str  = ""
    branch: str  = "main"
    tag:    str  = ""
    commit: str  = ""
    path:   str  = ""    # sub-path inside repo
    token:  str  = ""    # access token (or set BB_TOKEN env var)


@dataclass
class WorkflowConfig(PatcherConfig):
    """
    Full end-to-end workflow configuration.
    Extends PatcherConfig with:
      - ipxact:         repo/path for new IP-XACT (.xml) files
      - lld_repo:       repo holding current SFR_*.h + lld_*.h
      - pr_target:      where to raise the PR (defaults to lld_repo)
      - convert_script: path to ipxact->SFR converter script
      - llm:            raw llm: dict from YAML (passed to make_llm_client)
    """
    # IP-XACT source (new register descriptions)
    ipxact:          RepoSpec = field(default_factory=RepoSpec)
    # LLD/SFR current code
    lld_repo:        RepoSpec = field(default_factory=RepoSpec)
    # PR destination (optional — defaults to lld_repo)
    pr_target:       RepoSpec = field(default_factory=RepoSpec)
    # Converter script
    convert_script:  str = "./convert.py"
    # PR title prefix
    pr_title_prefix: str = "feat(lld): SFR auto-patch"
    # Raw llm: dict from YAML (used by make_llm_client)
    llm:             dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config reader
# ---------------------------------------------------------------------------
def load_workflow_config(config_path: str | Path) -> "WorkflowConfig":
    """
    Parse a workflow_config.yaml and return a WorkflowConfig.
    This is the v3 full-pipeline config (ipxact + lld_repo + pr_target).
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    data = _parse_yaml(text)
    base = path.parent

    def _str(key: str, default: str = "") -> str:
        return str(data.get(key, default) or default)

    def _bool(key: str, default: bool = False) -> bool:
        v = data.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1", "yes")

    def _int(key: str, default: int = 0) -> int:
        try:
            return int(data.get(key, default))
        except (TypeError, ValueError):
            return default

    def _path(key: str, default: str = ".") -> Path:
        v = data.get(key, default) or default
        p = Path(str(v))
        return p if p.is_absolute() else (base / p).resolve()

    def _repo_spec(key: str) -> RepoSpec:
        d = data.get(key) or {}
        if not isinstance(d, dict):
            return RepoSpec()
        return RepoSpec(
            url    = str(d.get("url",    "") or ""),
            branch = str(d.get("branch", "main") or "main"),
            tag    = str(d.get("tag",    "") or ""),
            commit = str(d.get("commit", "") or ""),
            path   = str(d.get("path",   "") or ""),
            token  = str(d.get("token",  "") or ""),
        )

    # Resolve sfr_old/new/lld dirs — in workflow mode they come from checkouts
    # but can still be specified directly for simple mode
    sfr_old = _path("sfr_old_dir", "./old_sfr")
    sfr_new = _path("sfr_new_dir", "./new_sfr")
    lld_dir  = _path("lld_dir",    "./lld")
    out_dir  = _path("output_dir", str(lld_dir))
    tst_dir  = _path("tests_dir",  str(lld_dir))

    # Resolve token for lld_repo from env if not in config
    import os as _os
    def _resolve_token(spec: RepoSpec) -> RepoSpec:
        if not spec.token:
            from lld_gen.git_manager import _detect_host
            host = _detect_host(spec.url)
            if host == "github":
                spec.token = _os.environ.get("GITHUB_TOKEN",
                             _os.environ.get("GH_TOKEN", ""))
            else:
                spec.token = _os.environ.get("BB_TOKEN",
                             _os.environ.get("BITBUCKET_TOKEN", ""))
        return spec

    ipxact_spec  = _resolve_token(_repo_spec("ipxact"))
    lld_spec     = _resolve_token(_repo_spec("lld_repo"))
    pr_spec      = _repo_spec("pr_target")
    # pr_target token inherits from lld_repo if not set
    if not pr_spec.token:
        pr_spec.token = lld_spec.token

    return WorkflowConfig(
        # PatcherConfig fields
        sfr_old_dir  = sfr_old,
        sfr_new_dir  = sfr_new,
        lld_dir      = lld_dir,
        output_dir   = out_dir,
        tests_dir    = tst_dir,
        no_llm       = _bool("no_llm",       False),
        no_git       = _bool("no_git",        False),
        hf_token     = _str("hf_token"),
        gcc          = data.get("gcc") or None,
        github_url   = _str("github_url"),
        llm_test_gen = _bool("llm_test_gen",  True),
        ollama_model = _str("ollama_model"),
        ip_overrides = dict(data.get("ip_overrides") or {}),
        ip_list      = list(data.get("ip_filter") or data.get("ip_list") or []),
        # v3 flags
        max_llm_retries   = _int("max_llm_retries", 5),
        per_fn_compile    = _bool("per_fn_compile",  True),
        skip_reg_added    = _bool("skip_reg_added",  True),
        skip_reg_deleted  = _bool("skip_reg_deleted", True),
        cross_lld_scan    = _bool("cross_lld_scan",  True),
        lld_search_depth  = _int("lld_search_depth", 3),
        atomic_commits    = _bool("atomic_commits",  True),
        commit_prefix     = _str("commit_prefix",    "feat(lld)"),
        emit_enum_defines = _bool("emit_enum_defines", False),
        compile_check     = _bool("compile_check",   True),
        force_no_llm      = _bool("force_no_llm",    False),
        # Workflow-specific
        ipxact          = ipxact_spec,
        lld_repo        = lld_spec,
        pr_target       = pr_spec,
        convert_script  = _str("convert_script", "./convert.py"),
        pr_title_prefix = _str("pr_title_prefix", "feat(lld): SFR auto-patch"),
        # Pass raw llm: block for make_llm_client()
        llm             = dict(data.get("llm") or {}),
        # v3.3 — semantic description equivalence gate
        semantic_similarity_threshold_low  = float(data.get("semantic_similarity_threshold_low",  0.75)),
        semantic_similarity_threshold_high = float(data.get("semantic_similarity_threshold_high", 0.85)),
        verbose                            = bool(data.get("verbose", False)),
    )


def load_config(config_path: str | Path) -> PatcherConfig:
    """
    Parse lld_patcher.yaml (or .json) and return a PatcherConfig.

    Supports YAML (requires PyYAML) or plain JSON.
    Falls back to a minimal key: value parser for simple YAML without PyYAML.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    text = path.read_text(encoding="utf-8")
    base = path.parent   # resolve relative paths against config file location

    if path.suffix in (".yaml", ".yml"):
        data = _parse_yaml(text)
    elif path.suffix == ".json":
        import json
        data = json.loads(text)
    else:
        data = _parse_yaml(text)  # try YAML by default

    def _p(key: str, default=None) -> Optional[str]:
        v = data.get(key, default)
        return v

    def _path(key: str, default: str) -> Path:
        v = data.get(key, default)
        if v is None:
            raise ValueError(f"Config missing required key: '{key}'")
        p = Path(v)
        return p if p.is_absolute() else (base / p).resolve()

    sfr_old = _path("sfr_old_dir", "./old_sfr")
    sfr_new = _path("sfr_new_dir", "./new_sfr")
    lld_dir  = _path("lld_dir",    "./lld")
    out_dir  = _path("output_dir", str(lld_dir))
    tst_dir  = _path("tests_dir",  str(lld_dir))

    return PatcherConfig(
        sfr_old_dir  = sfr_old,
        sfr_new_dir  = sfr_new,
        lld_dir      = lld_dir,
        output_dir   = out_dir,
        tests_dir    = tst_dir,
        no_llm       = bool(data.get("no_llm",        True)),
        no_git       = bool(data.get("no_git",        True)),
        hf_token     = str(data.get("hf_token",       "") or ""),
        gcc          = data.get("gcc") or None,
        github_url   = str(data.get("github_url",     "") or ""),
        llm_test_gen = bool(data.get("llm_test_gen",  False)),
        ollama_model = str(data.get("ollama_model",   "") or ""),
        ip_overrides = dict(data.get("ip_overrides") or {}),
        ip_list      = list(data.get("ip_list") or []),
        compile_check = bool(data.get("compile_check", True)),
        force_no_llm  = bool(data.get("force_no_llm",  False)),
        lld_all_files = bool(data.get("lld_all_files", False)),
        semantic_similarity_threshold_low  = float(data.get("semantic_similarity_threshold_low",  0.75)),
        semantic_similarity_threshold_high = float(data.get("semantic_similarity_threshold_high", 0.85)),
        verbose       = bool(data.get("verbose", False)),
    )


def _parse_yaml(text: str) -> dict:
    """Parse YAML using PyYAML if available, else minimal key:value parser supporting 1-level indentation."""
    if _HAS_YAML:
        return _yaml.safe_load(text) or {}
    data: dict = {}
    current_parent: Optional[str] = None
    for ln in text.splitlines():
        stripped = ln.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(ln) - len(stripped)
        if ":" not in stripped:
            continue
        # Strip inline comment
        comment_idx = stripped.find(" #")
        if comment_idx != -1:
            line_content = stripped[:comment_idx].strip()
        else:
            line_content = stripped.strip()
        key, _, val = line_content.partition(":")
        key = key.strip()
        val = val.strip()
        
        parsed_val: Any = None
        if val.lower() in ("null", "~", ""):
            parsed_val = None
        elif val.lower() == "true":
            parsed_val = True
        elif val.lower() == "false":
            parsed_val = False
        else:
            val = val.strip("\"'")
            try:
                parsed_val = int(val)
            except ValueError:
                try:
                    parsed_val = float(val)
                except ValueError:
                    parsed_val = val
        if indent == 0:
            if val == "":
                current_parent = key
                data[key] = {}
            else:
                current_parent = None
                data[key] = parsed_val
        else:
            if current_parent is not None:
                data[current_parent][key] = parsed_val
            else:
                data[key] = parsed_val
    return data



# ---------------------------------------------------------------------------
# Checkpoint — resume pipeline from where it stopped
# ---------------------------------------------------------------------------
_CHECKPOINT_FILE = ".lld_patcher_checkpoint.json"


def save_checkpoint(
    out_dir: Path,
    completed_ips: List[str],
    stage: str,
    context: Optional[Dict] = None,
) -> Path:
    """
    Save pipeline checkpoint so it can resume after a failure (e.g. gcc missing).

    Args:
        out_dir:        Output directory where checkpoint file is written.
        completed_ips:  List of IP names already processed successfully.
        stage:          Pipeline stage where checkpoint was saved
                        ('pre_gcc', 'pre_llm', 'ip_done', 'compile').
        context:        Optional extra data to persist (e.g. ip_sfr_map).

    Returns:
        Path to the checkpoint file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = out_dir / _CHECKPOINT_FILE
    data = {
        "completed_ips": completed_ips,
        "stage":         stage,
        "context":       context or {},
        "timestamp":     datetime.now().isoformat(),
    }
    ckpt_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    logger.debug("[CHECKPOINT] Saved -> %s", ckpt_path)
    return ckpt_path


def load_checkpoint(out_dir: Path) -> Optional[Dict]:
    """
    Load checkpoint if it exists. Returns the checkpoint dict, or None.

    Checkpoint dict:
        {"completed_ips": [...], "stage": "...", "context": {...}, "timestamp": "..."}
    """
    ckpt_path = out_dir / _CHECKPOINT_FILE
    if not ckpt_path.exists():
        return None
    try:
        data = _json.loads(ckpt_path.read_text(encoding="utf-8"))
        logger.info("[CHECKPOINT] Resuming from %s (%d IP(s) already done)",
                    data.get('stage', '?'), len(data.get('completed_ips', [])))
        return data
    except Exception:
        return None


def clear_checkpoint(out_dir: Path) -> None:
    """Remove checkpoint file after successful completion."""
    ckpt_path = out_dir / _CHECKPOINT_FILE
    if ckpt_path.exists():
        ckpt_path.unlink()
        logger.info("[CHECKPOINT] Cleared -- pipeline completed successfully")


# ---------------------------------------------------------------------------
# File matcher
# ---------------------------------------------------------------------------
_SFR_GLOB_PATTERNS = ["sfr_*.h", "*_sfr.h", "SFR_*.h", "*_SFR.h"]
_LLD_GLOB_PATTERNS = ["lld_{ip}.h", "{ip}_lld.h", "{ip}.h", "lld_{ip_lower}.h"]


def discover_jobs(cfg: PatcherConfig) -> List[IPJob]:
    """
    Scan old/new SFR directories and match files into IPJob list.

    Matching rules (all case-insensitive, in priority order):
      1. ip_overrides[IP].lld_file  -- explicit per-IP LLD file mapping (wins over everything)
      2. lld_all_files: true        -- patch ALL .h files in lld_dir for every SFR pair found
      3. Same filename in old + new dirs -> IP from filename -> lld_{ip}.h searched
      4. *_old.h in old_dir + *_new.h in new_dir -> strip suffix -> IP -> lld searched

    When there is no 1-to-1 naming match and no explicit mapping, each SFR pair is
    still added to the job list with lld=None so that cross-LLD AST refactoring can run.
    """
    from lld_gen.sfr_diff_analyzer import _ip_from_filename

    if not cfg.sfr_old_dir.exists():
        raise FileNotFoundError(f"sfr_old_dir not found: {cfg.sfr_old_dir}")
    if not cfg.sfr_new_dir.exists():
        raise FileNotFoundError(f"sfr_new_dir not found: {cfg.sfr_new_dir}")
    if not cfg.lld_dir.exists():
        raise FileNotFoundError(f"lld_dir not found: {cfg.lld_dir}")

    # Collect all .h files in each directory (case-insensitive stem -> path)
    old_files: Dict[str, Path] = {f.stem.lower(): f for f in cfg.sfr_old_dir.glob("*.h")}
    new_files: Dict[str, Path] = {f.stem.lower(): f for f in cfg.sfr_new_dir.glob("*.h")}
    lld_files: Dict[str, Path] = {f.stem.lower(): f for f in cfg.lld_dir.glob("*.h")}
    all_lld_paths: List[Path]  = list(lld_files.values())

    # lld_all_files flag: should we apply every SFR change to ALL LLD files?
    lld_all_files: bool = getattr(cfg, "lld_all_files", False)

    jobs: List[IPJob] = []
    matched_old: set = set()

    def _resolve_lld_for_ip(ip: str) -> Optional[Path]:
        """Find the LLD file for IP, checking ip_overrides first."""
        override = cfg.ip_overrides.get(ip.upper(), {})
        explicit = override.get("lld_file", "")
        if explicit:
            ep = Path(explicit)
            if not ep.is_absolute():
                ep = cfg.lld_dir / ep
            if ep.exists():
                return ep
            logger.warning("[DISCOVER] ip_overrides[%s].lld_file not found: %s", ip, ep)
        return _find_lld(ip, lld_files, cfg.lld_dir)

    def _make_job(ip: str, old_path: Path, new_path: Path) -> List[IPJob]:
        """Build one or more IPJob entries for an SFR pair."""
        tst_dir = _ensure_dir(cfg.tests_dir) if cfg.tests_dir else None
        if lld_all_files:
            # Produce one job per LLD file in lld_dir
            job_list = []
            for lld_path in all_lld_paths:
                out_lld = _out_path(cfg.output_dir, lld_path)
                job_list.append(IPJob(
                    ip=ip, old_sfr=old_path, new_sfr=new_path,
                    lld=lld_path, out_lld=out_lld, tests_dir=tst_dir,
                ))
            if not job_list:
                logger.warning("[DISCOVER] lld_all_files=true but no .h files found in %s", cfg.lld_dir)
            return job_list
        else:
            lld_path = _resolve_lld_for_ip(ip)
            out_lld  = _out_path(cfg.output_dir, lld_path) if lld_path else None
            if not lld_path:
                logger.warning(
                    "[DISCOVER] No LLD file for IP=%s in %s. "
                    "Tip: set ip_overrides[%s].lld_file in your YAML, "
                    "or use lld_all_files: true to patch all LLD files. "
                    "Only cross-file AST refactoring will run for this IP.",
                    ip, cfg.lld_dir, ip,
                )
            return [IPJob(ip=ip, old_sfr=old_path, new_sfr=new_path,
                          lld=lld_path, out_lld=out_lld, tests_dir=tst_dir)]

    # ── Rule 1: same basename in both dirs ──────────────────────────────────
    for stem, old_path in old_files.items():
        if stem in new_files:
            ip = _ip_from_filename(old_path)
            new_path = new_files[stem]
            jobs.extend(_make_job(ip, old_path, new_path))
            matched_old.add(stem)

    # ── Rule 2: _old/_new suffix pairing ────────────────────────────────────
    for stem, old_path in old_files.items():
        if stem in matched_old:
            continue
        for old_suf in ("_old", "_prev", "_v1", "_baseline", "_bak"):
            if stem.endswith(old_suf):
                base = stem[: -len(old_suf)]
                new_stem = None
                for new_suf in ("_new", "_updated", "_v2", "_latest"):
                    candidate = base + new_suf
                    if candidate in new_files:
                        new_stem = candidate
                        break
                if new_stem is None and base in new_files:
                    new_stem = base
                if new_stem:
                    ip = _ip_from_filename(old_path).replace(
                        old_suf.upper().lstrip("_"), ""
                    ).rstrip("_") or _ip_from_filename(old_path)
                    new_path = new_files[new_stem]
                    jobs.extend(_make_job(ip, old_path, new_path))
                    matched_old.add(stem)
                break

    # ── Filter to ip_list if specified ─────────────────────────────────────
    if cfg.ip_list:
        ip_set = {ip.upper() for ip in cfg.ip_list}
        jobs = [j for j in jobs if j.ip.upper() in ip_set]

    return jobs


def _find_lld(ip: str, lld_files: Dict[str, Path], lld_dir: Path) -> Optional[Path]:
    """Find the LLD file for a given IP name using multiple naming conventions."""
    ip_lo = ip.lower()
    candidates = [
        f"lld_{ip_lo}",         # lld_pmu.h
        f"{ip_lo}_lld",         # pmu_lld.h
        ip_lo,                  # pmu.h
        f"lld_{ip_lo}_reg",     # lld_pmu_reg.h
        f"{ip_lo}_reg",         # pmu_reg.h
    ]
    for c in candidates:
        if c in lld_files:
            return lld_files[c]
    return None


def _out_path(output_dir: Path, lld_path: Path) -> Path:
    """Compute output path for a patched LLD file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / lld_path.name


def _ensure_dir(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d
