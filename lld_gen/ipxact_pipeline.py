"""
ipxact_pipeline.py — IP-XACT → SFR Header Conversion Pipeline

Orchestrates the conversion of IP-XACT (.xml) register map files to
Samsung-format SFR_<IP>.h headers using an external convert.py script.

Workflow:
    1. discover_xmls()  — find all .xml files in the ipxact repo checkout
    2. run_convert()    — call convert.py for each .xml → SFR_<IP>.h
    3. IpxactPipeline.run() — full pipeline, returns {ip: Path} mapping

The convert.py script interface (assumed):
    python convert.py <input.xml> -o <output.h> [--ip <IPNAME>]

If convert.py has a different interface, set `convert_args_template` in config.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ConvertResult:
    xml_path:   Path
    sfr_path:   Optional[Path]  # None on failure
    ip:         str
    success:    bool
    error:      str = ""


# ---------------------------------------------------------------------------
# IP name extraction helpers
# ---------------------------------------------------------------------------
_IP_FROM_XML_RE = re.compile(
    r"<(component|ipxactFile|spirit:component).*?name[^>]*>([A-Za-z0-9_]+)<",
    re.DOTALL | re.IGNORECASE,
)

def _ip_from_xml(xml_path: Path) -> str:
    """
    Best-effort: extract IP name from .xml file content.
    Falls back to filename stem.
    """
    try:
        text = xml_path.read_text(encoding="utf-8", errors="replace")[:4096]
        m = _IP_FROM_XML_RE.search(text)
        if m:
            return m.group(2).upper()
    except Exception:
        pass
    # Fallback: sfr_pmu.xml → PMU,  pmu_regs.xml → PMU
    stem = xml_path.stem.upper()
    # Strip common prefixes/suffixes
    for prefix in ("SFR_", "IPXACT_", "REGS_", "REG_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
    for suffix in ("_REGS", "_SFR", "_IPXACT", "_REG"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
    return stem


def _ip_from_sfr_header(h_path: Path) -> str:
    """Extract IP from SFR_<IP>.h filename."""
    stem = h_path.stem.upper()
    for prefix in ("SFR_", "sfr_"):
        if stem.startswith(prefix.upper()):
            return stem[len(prefix):]
    return stem


# ---------------------------------------------------------------------------
# Single-file conversion
# ---------------------------------------------------------------------------
def run_convert(
    xml_path:        Path,
    convert_script:  Path,
    out_dir:         Path,
    ip:              Optional[str] = None,
    extra_args:      Optional[List[str]] = None,
) -> ConvertResult:
    """
    Run convert.py on one .xml file.

    Calls:
        python <convert_script> <xml_path> -o <out_dir>/SFR_<IP>.h [--ip <IP>]

    Returns a ConvertResult with the path to the generated .h file.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ip_name = ip or _ip_from_xml(xml_path)
    out_h   = out_dir / f"SFR_{ip_name}.h"

    cmd = [
        sys.executable,
        str(convert_script),
        str(xml_path),
        "-o", str(out_h),
    ]
    if ip_name:
        cmd += ["--ip", ip_name]
    if extra_args:
        cmd += extra_args

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return ConvertResult(
                xml_path=xml_path,
                sfr_path=None,
                ip=ip_name,
                success=False,
                error=proc.stderr.strip() or proc.stdout.strip(),
            )
        if not out_h.exists():
            # Some converters may write with IP-based name — scan for any new .h
            candidates = sorted(out_dir.glob("SFR_*.h"), key=lambda p: p.stat().st_mtime, reverse=True)
            out_h = candidates[0] if candidates else out_h

        return ConvertResult(
            xml_path=xml_path,
            sfr_path=out_h if out_h.exists() else None,
            ip=ip_name,
            success=out_h.exists(),
            error="" if out_h.exists() else "output file not created",
        )
    except FileNotFoundError:
        return ConvertResult(
            xml_path=xml_path, sfr_path=None, ip=ip_name, success=False,
            error=f"convert script not found: {convert_script}",
        )
    except subprocess.TimeoutExpired:
        return ConvertResult(
            xml_path=xml_path, sfr_path=None, ip=ip_name, success=False,
            error="convert.py timed out after 120s",
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_xmls(
    repo_path:   Path,
    ipxact_path: str = "",
    ip_filter:   Optional[List[str]] = None,
) -> List[Path]:
    """
    Find all .xml files in repo_path / ipxact_path.
    Optionally filter to only those whose IP name matches ip_filter.
    """
    base = Path(repo_path) / ipxact_path if ipxact_path else Path(repo_path)
    if not base.exists():
        print(f"  [IPXACT] Path not found: {base}")
        return []

    xmls = sorted(base.rglob("*.xml"))
    if ip_filter:
        ip_filter_upper = {ip.upper() for ip in ip_filter}
        xmls = [x for x in xmls if _ip_from_xml(x).upper() in ip_filter_upper]

    return xmls


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------
class IpxactPipeline:
    """
    Converts all IP-XACT .xml files in a repo checkout to SFR_<IP>.h headers.

    Usage:
        pipeline = IpxactPipeline(
            repo_path      = Path("/tmp/ipxact_checkout"),
            convert_script = Path("./convert.py"),
            ipxact_path    = "ipxact/",
            out_dir        = Path("/tmp/new_sfrs"),
        )
        ip_sfr_map = pipeline.run()
        # → {"PMU": Path("/tmp/new_sfrs/SFR_PMU.h"), "DMA": Path(...)}
    """

    def __init__(
        self,
        repo_path:       Path,
        convert_script:  Path,
        out_dir:         Path,
        ipxact_path:     str = "",
        ip_filter:       Optional[List[str]] = None,
        extra_args:      Optional[List[str]] = None,
    ):
        self.repo_path      = Path(repo_path)
        self.convert_script = Path(convert_script)
        self.out_dir        = Path(out_dir)
        self.ipxact_path    = ipxact_path
        self.ip_filter      = ip_filter or []
        self.extra_args     = extra_args or []
        self._results: List[ConvertResult] = []

    def run(self) -> Dict[str, Path]:
        """
        Run the full conversion pipeline.
        Returns a dict mapping IP name → Path to generated SFR_<IP>.h.
        Skips failed conversions with a warning.
        """
        xmls = discover_xmls(self.repo_path, self.ipxact_path, self.ip_filter or None)
        if not xmls:
            print(f"  [IPXACT] No .xml files found in {self.repo_path / self.ipxact_path}")
            return {}

        print(f"  [IPXACT] Found {len(xmls)} .xml file(s) to convert")
        ip_map: Dict[str, Path] = {}
        self._results = []

        for xml in xmls:
            ip = _ip_from_xml(xml)
            print(f"  [IPXACT] Converting {xml.name} → SFR_{ip}.h …")
            result = run_convert(
                xml_path       = xml,
                convert_script = self.convert_script,
                out_dir        = self.out_dir,
                ip             = ip,
                extra_args     = self.extra_args,
            )
            self._results.append(result)
            if result.success and result.sfr_path:
                ip_map[ip] = result.sfr_path
                print(f"  [IPXACT] OK  → {result.sfr_path.name}")
            else:
                print(f"  [IPXACT] FAIL {xml.name}: {result.error}")

        print(f"  [IPXACT] Converted {len(ip_map)}/{len(xmls)} IPs successfully")
        return ip_map

    def results(self) -> List[ConvertResult]:
        """Return per-file conversion results."""
        return self._results

    def failed(self) -> List[ConvertResult]:
        """Return only failed conversions."""
        return [r for r in self._results if not r.success]
