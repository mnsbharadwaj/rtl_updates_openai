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

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Try PyYAML first, fall back to a minimal YAML-ish parser for simple configs
try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class IPJob:
    """One SFR-pair + LLD file to process."""
    ip:        str
    old_sfr:   Path
    new_sfr:   Path
    lld:       Path
    out_lld:   Path          # where to write the patched file
    tests_dir: Path

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
    sfr_old_dir: Path
    sfr_new_dir: Path
    lld_dir:     Path
    output_dir:  Path
    tests_dir:   Path
    no_llm:      bool
    no_git:      bool
    hf_token:    str
    gcc:         Optional[str]
    ip_overrides: Dict[str, dict] = field(default_factory=dict)
    ip_list:     List[str]        = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config reader
# ---------------------------------------------------------------------------
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
        no_llm       = bool(data.get("no_llm",   True)),
        no_git       = bool(data.get("no_git",   True)),
        hf_token     = str(data.get("hf_token",  "") or ""),
        gcc          = data.get("gcc") or None,
        ip_overrides = dict(data.get("ip_overrides") or {}),
        ip_list      = list(data.get("ip_list") or []),
    )


def _parse_yaml(text: str) -> dict:
    """Parse YAML using PyYAML if available, else minimal key:value parser."""
    if _HAS_YAML:
        return _yaml.safe_load(text) or {}
    # Minimal fallback: handle simple key: value and key: null lines
    # Sufficient for lld_patcher.yaml which has no nested structures
    # (ip_overrides block is ignored without PyYAML)
    data: dict = {}
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        # Strip inline comment
        if " #" in val:
            val = val[:val.index(" #")].strip()
        # Type coercion
        if val.lower() in ("null", "~", ""):
            data[key] = None
        elif val.lower() == "true":
            data[key] = True
        elif val.lower() == "false":
            data[key] = False
        else:
            # Try int/float, else keep as string (strip quotes)
            val = val.strip("\"'")
            try:
                data[key] = int(val)
            except ValueError:
                try:
                    data[key] = float(val)
                except ValueError:
                    data[key] = val
    return data


# ---------------------------------------------------------------------------
# File matcher
# ---------------------------------------------------------------------------
_SFR_GLOB_PATTERNS = ["sfr_*.h", "*_sfr.h", "SFR_*.h", "*_SFR.h"]
_LLD_GLOB_PATTERNS = ["lld_{ip}.h", "{ip}_lld.h", "{ip}.h", "lld_{ip_lower}.h"]


def discover_jobs(cfg: PatcherConfig) -> List[IPJob]:
    """
    Scan old/new SFR directories and match files into IPJob list.

    Matching rules (all case-insensitive):
      1. Same filename in old + new dirs -> IP from filename
      2. *_old.h in old_dir + *_new.h in new_dir -> strip suffix -> IP
      3. LLD file searched as lld_{ip}.h, {ip}_lld.h, {ip}.h in lld_dir
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

    jobs: List[IPJob] = []
    matched_old: set = set()

    # ── Rule 1: same basename in both dirs ──────────────────────────────────
    for stem, old_path in old_files.items():
        if stem in new_files:
            ip = _ip_from_filename(old_path)
            new_path = new_files[stem]
            lld_path = _find_lld(ip, lld_files, cfg.lld_dir)
            if lld_path:
                out_lld  = _out_path(cfg.output_dir, lld_path)
                tst_dir  = _ensure_dir(cfg.tests_dir)
                jobs.append(IPJob(
                    ip=ip, old_sfr=old_path, new_sfr=new_path,
                    lld=lld_path, out_lld=out_lld, tests_dir=tst_dir,
                ))
                matched_old.add(stem)
            else:
                print(f"  [WARN] No LLD file found for IP={ip} in {cfg.lld_dir}")

    # ── Rule 2: _old/_new suffix pairing ────────────────────────────────────
    for stem, old_path in old_files.items():
        if stem in matched_old:
            continue
        # Check if this looks like a *_old.h file
        for old_suf in ("_old", "_prev", "_v1", "_baseline", "_bak"):
            if stem.endswith(old_suf):
                base = stem[: -len(old_suf)]
                # Find matching new file
                new_stem = None
                for new_suf in ("_new", "_updated", "_v2", "_latest"):
                    candidate = base + new_suf
                    if candidate in new_files:
                        new_stem = candidate
                        break
                if new_stem is None and base in new_files:
                    new_stem = base   # same name without suffix
                if new_stem:
                    ip = _ip_from_filename(old_path).replace(old_suf.upper().lstrip("_"), "").rstrip("_") or _ip_from_filename(old_path)
                    new_path = new_files[new_stem]
                    lld_path = _find_lld(ip, lld_files, cfg.lld_dir)
                    if lld_path:
                        out_lld = _out_path(cfg.output_dir, lld_path)
                        tst_dir = _ensure_dir(cfg.tests_dir)
                        jobs.append(IPJob(
                            ip=ip, old_sfr=old_path, new_sfr=new_path,
                            lld=lld_path, out_lld=out_lld, tests_dir=tst_dir,
                        ))
                        matched_old.add(stem)
                    else:
                        print(f"  [WARN] No LLD file found for IP={ip} in {cfg.lld_dir}")
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
