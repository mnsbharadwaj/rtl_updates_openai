#!/usr/bin/env python3
"""
sfr_gen — IPXACT (Excel) → sfr.h + lld.h generator  (llama.cpp edition).

Scenarios
─────────
  Scenario 1 — Fresh generation (server already running):
    python sfr_gen.py --ipxact regs.xlsx --base 0x40000000

  Scenario 1 — Fresh generation (auto-start server):
    python sfr_gen.py --ipxact regs.xlsx --auto-start-server

  Scenario 2 — Incremental update:
    python sfr_gen.py --ipxact regs_v2.xlsx --old-ipxact regs.xlsx --update

  Skip LLM (template-only, no semantic functions):
    python sfr_gen.py --ipxact regs.xlsx --no-llm

First-time setup on a new PC:
    python setup/download_model.py     # downloads llama-server.exe + GGUF
    .\\run_server.ps1                   # start the server
    python sfr_gen.py --ipxact regs.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from parser.excel_parser import parse_excel
from generators.sfr_generator import generate_sfr
from generators.lld_generator import generate_lld
from updater.diff_engine import diff_register_maps
from updater.sfr_updater import update_sfr
from updater.lld_updater import update_lld


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sfr_gen",
        description="Generate sfr.h / lld.h from an IPXACT Excel register description.",
    )
    p.add_argument("--ipxact",  required=True, metavar="FILE",
                   help="Path to the IPXACT Excel workbook (.xlsx).")
    p.add_argument("--old-ipxact", metavar="FILE",
                   help="Previous IPXACT Excel — used for accurate diff in --update mode.")
    p.add_argument("--base",    default="0x0", metavar="ADDR",
                   help="Default peripheral base address (hex). Default: 0x0")
    p.add_argument("--outdir",  default=".", metavar="DIR",
                   help="Output directory for generated files. Default: current dir.")
    p.add_argument("--sfr",     metavar="FILE",
                   help="Existing sfr.h path (for --update mode).")
    p.add_argument("--lld",     metavar="FILE",
                   help="Existing lld.h path (for --update mode).")
    p.add_argument("--update",  action="store_true",
                   help="Incremental update mode: only patch changed sections.")
    p.add_argument("--skip-debug", action="store_true", default=True,
                   help="Skip registers/fields marked as Debug (default: True).")
    p.add_argument("--no-debug-skip", dest="skip_debug", action="store_false",
                   help="Include debug registers/fields.")

    llm = p.add_argument_group("LLM / llama.cpp options")
    llm.add_argument("--no-llm", action="store_true",
                     help="Disable LLM; emit stub comments for semantic functions.")
    llm.add_argument("--server-exe",
                     default="llm_runtime/llama-server.exe", metavar="PATH",
                     help="Path to llama-server.exe. Default: llm_runtime/llama-server.exe")
    llm.add_argument("--model-path",
                     default="llm_runtime/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                     metavar="PATH",
                     help="Path to the GGUF model file.")
    llm.add_argument("--server-port", default=8080, type=int,
                     help="Port llama-server listens on. Default: 8080")
    llm.add_argument("--llm-base-url", metavar="URL",
                     help="Override server URL (e.g. http://127.0.0.1:8080/v1).")
    llm.add_argument("--auto-start-server", action="store_true",
                     help="Auto-start llama-server.exe before generation and stop it after.")
    return p


# ---------------------------------------------------------------------------
# LLM factory  — returns (llm_fn_or_None, server_or_None)
# ---------------------------------------------------------------------------
def _make_llm_fn(args, peripheral: str):
    from llm.qwen_client import build_client_from_args, make_llm_fn
    client, server = build_client_from_args(args, peripheral)
    if client is None:
        return None, None
    return make_llm_fn(client, peripheral), server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args      = _build_parser().parse_args()
    base_addr = int(args.base, 0)
    outdir    = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # ── Parse IPXACT ─────────────────────────────────────────────────────────
    print(f"[sfr_gen] Parsing IPXACT: {args.ipxact}")
    new_maps = parse_excel(args.ipxact, base_addr=base_addr, skip_debug=args.skip_debug)
    if not new_maps:
        print("[sfr_gen] ERROR: No valid peripheral sheets found.")
        sys.exit(1)
    print(f"[sfr_gen] Peripherals: {', '.join(m.peripheral for m in new_maps)}")

    # ── Process each peripheral ───────────────────────────────────────────────
    # We start the server once (shared across all peripherals) if auto-start is on.
    # Each peripheral gets its own llm_fn closure but shares the same server.
    global_server = None

    try:
        for rm in new_maps:
            pname    = rm.peripheral.lower()
            sfr_path = Path(args.sfr) if args.sfr else outdir / f"{pname}_sfr.h"
            lld_path = Path(args.lld) if args.lld else outdir / f"{pname}_lld.h"

            # Build LLM fn (server is started at most once across all peripherals)
            if global_server is None:
                llm_fn, global_server = _make_llm_fn(args, rm.peripheral)
            else:
                from llm.qwen_client import LLMClient, make_llm_fn
                # Reuse running server — just wrap with new peripheral prefix
                client = LLMClient(
                    base_url=global_server.base_url if global_server else
                             getattr(args, "llm_base_url", None) or
                             f"http://127.0.0.1:{args.server_port}/v1"
                )
                llm_fn = make_llm_fn(client, rm.peripheral) if client else None

            if args.update and sfr_path.exists() and lld_path.exists():
                # ── Scenario 2: Incremental update ────────────────────────────
                print(f"\n[{rm.peripheral}] UPDATE mode")
                old_ipxact = getattr(args, "old_ipxact", None)
                if old_ipxact and Path(old_ipxact).exists():
                    old_maps = parse_excel(old_ipxact, base_addr=base_addr,
                                           skip_debug=args.skip_debug)
                    old_rm = next((m for m in old_maps if m.peripheral == rm.peripheral), None)
                else:
                    old_rm = None

                if old_rm is None:
                    print("  [WARN] No old IPXACT — treating all blocks as changed.")
                    from models import RegisterMap as RM
                    old_rm = RM(peripheral=rm.peripheral, base_addr=rm.base_addr)

                diff = diff_register_maps(old_rm, rm)
                print(diff.summary())
                if not diff.has_changes:
                    print("  No changes — files are up to date.")
                    continue

                update_sfr(sfr_path, rm, diff)
                print(f"  sfr.h → {sfr_path}")
                try:
                    update_lld(lld_path, rm, diff, llm_fn)
                    print(f"  lld.h → {lld_path}")
                except RuntimeError as exc:
                    print(f"  [ERROR] {exc}")

            else:
                # ── Scenario 1: Fresh generation ───────────────────────────────
                print(f"\n[{rm.peripheral}] FRESH generation")
                generate_sfr(rm, sfr_path)
                print(f"  sfr.h → {sfr_path}")
                generate_lld(rm, lld_path, llm_fn)
                print(f"  lld.h → {lld_path}")

    finally:
        if global_server:
            global_server.stop()

    print("\n[sfr_gen] Done.")


if __name__ == "__main__":
    main()
