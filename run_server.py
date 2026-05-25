#!/usr/bin/env python3
"""
run_server.py — Start llama-server for sfr_gen (OS-agnostic).

Commit this to Git; it documents exactly how to start the inference server.
The .gguf model file and the llama-server binary are NOT in Git (see .gitignore).

Usage
-----
    python run_server.py [--port PORT] [--ctx-size N] [--gpu-layers N]

Defaults are read from llm_runtime/model.cfg (JSON).
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
RUNTIME_DIR  = SCRIPT_DIR / "llm_runtime"
CFG_FILE     = RUNTIME_DIR / "model.cfg"

# llama-server binary name differs by OS
_SERVER_BINARY = "llama-server.exe" if platform.system() == "Windows" else "llama-server"


def _colour(text: str, code: str) -> str:
    """ANSI colour wrapper (no-op on Windows without ANSI support, but works in most terminals)."""
    if platform.system() == "Windows":
        # Enable VT100 on Windows 10+
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
    return f"\033[{code}m{text}\033[0m"


green  = lambda t: _colour(t, "32")
yellow = lambda t: _colour(t, "33")
cyan   = lambda t: _colour(t, "36")
red    = lambda t: _colour(t, "31")


def load_config() -> dict:
    """Read model.cfg if it exists, else return empty dict."""
    if CFG_FILE.is_file():
        with CFG_FILE.open() as fh:
            return json.load(fh)
    return {}


def find_model_gguf() -> Path | None:
    """Fallback: pick the first .gguf found in llm_runtime/."""
    matches = sorted(RUNTIME_DIR.glob("*.gguf"))
    return matches[0] if matches else None


def port_in_use(port: int) -> bool:
    """Return True if something is already listening on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()

    # ── CLI arguments (override config values) ────────────────────────────────
    parser = argparse.ArgumentParser(
        description="Start llama-server for sfr_gen",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port",       type=int, default=cfg.get("server_port", 8080),
                        help="Port for llama-server to listen on")
    parser.add_argument("--ctx-size",   type=int, default=4096,
                        help="Context window size (tokens)")
    parser.add_argument("--gpu-layers", type=int, default=0,
                        help="Number of layers to offload to GPU (0 = CPU only)")
    args = parser.parse_args()

    port      = args.port
    ctx_size  = args.ctx_size
    gpu_layers = args.gpu_layers

    # ── Resolve server binary ─────────────────────────────────────────────────
    server_exe = Path(cfg.get("server_exe", RUNTIME_DIR / _SERVER_BINARY))
    if not server_exe.is_absolute():
        server_exe = RUNTIME_DIR / server_exe

    # ── Resolve model path ────────────────────────────────────────────────────
    model_path_str = cfg.get("model_path", "")
    if model_path_str:
        model_path = Path(model_path_str)
    else:
        model_path = find_model_gguf()
        if model_path is None:
            print(red("ERROR: No GGUF model found in llm_runtime/"))
            print(red("       Run: python setup/download_model.py"))
            sys.exit(1)

    # ── Validate files ────────────────────────────────────────────────────────
    if not server_exe.is_file():
        print(red(f"ERROR: llama-server not found at {server_exe}"))
        print(red("       Run: python setup/download_model.py"))
        sys.exit(1)

    if not model_path.is_file():
        print(red(f"ERROR: Model not found at {model_path}"))
        print(red("       Run: python setup/download_model.py"))
        sys.exit(1)

    # ── Check if already running ──────────────────────────────────────────────
    if port_in_use(port):
        print(yellow(f"[server] Port {port} already in use — server is already running."))
        print(cyan(f"[server] Endpoint: http://127.0.0.1:{port}/v1"))
        sys.exit(0)

    # ── Launch ────────────────────────────────────────────────────────────────
    model_name = model_path.name
    print()
    print(green("Starting llama-server …"))
    print(f"  Model      : {model_name}")
    print(f"  Port       : {port}")
    print(f"  Ctx size   : {ctx_size}")
    print(f"  GPU layers : {gpu_layers}  (0 = CPU only)")
    print()

    cmd = [
        str(server_exe),
        "--model",        str(model_path),
        "--port",         str(port),
        "--host",         "127.0.0.1",
        "--ctx-size",     str(ctx_size),
        "--n-gpu-layers", str(gpu_layers),
    ]

    try:
        # Replace the current process with llama-server (Unix) or run it as a
        # child and wait (Windows — os.execv not supported for .exe in all cases).
        if platform.system() != "Windows":
            os.execv(str(server_exe), cmd)   # replaces this process entirely
        else:
            subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print("\n[server] Stopped by user.")
    except FileNotFoundError:
        print(red(f"ERROR: Could not execute {server_exe}"))
        print(red("       Check file permissions / binary compatibility."))
        sys.exit(1)


if __name__ == "__main__":
    main()
