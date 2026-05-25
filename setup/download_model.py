"""
One-time setup: download llama-server.exe and the Qwen GGUF model.

Run once per machine:
    python setup/download_model.py

What it does
────────────
1. Creates llm_runtime/ directory
2. Downloads llama-server.exe from GitHub releases
   Priority: CUDA 12 → CUDA 13 → CPU x64 → any Windows build
   Use --cpu-only to skip CUDA and always pick the CPU build.
3. Downloads qwen2.5-coder-7b-instruct-q4_k_m.gguf from Hugging Face
4. Verifies file sizes
5. Writes llm_runtime/model.cfg so sfr_gen knows the paths

Approximate download sizes
──────────────────────────
  llama-server.exe  ~  15 MB
  7B Q4_K_M model   ~   4.7 GB
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RUNTIME_DIR = Path(__file__).parent.parent / "llm_runtime"

# Hugging Face model details
HF_REPO     = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
HF_FILE_1B5 = "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"   # ~1 GB  — fast, basic
HF_FILE_7B  = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"     # ~4.7 GB — recommended
HF_FILE_14B = "qwen2.5-coder-14b-instruct-q4_k_m.gguf"    # ~9 GB  — best quality

MODEL_SIZES = {
    "1.5b": HF_FILE_1B5,
    "7b":   HF_FILE_7B,
    "14b":  HF_FILE_14B,
}

# Minimum acceptable file sizes (bytes) — used to detect truncated downloads.
# Set conservatively (~80% of real size) so a fresh re-download is always triggered
# if the file was cut short.
_MODEL_MIN_BYTES = {
    HF_FILE_1B5: 800_000_000,     # real ~1.0 GB  → min 0.8 GB
    HF_FILE_7B:  4_000_000_000,   # real ~4.68 GB → min 4.0 GB
    HF_FILE_14B: 7_500_000_000,   # real ~9.0 GB  → min 7.5 GB
}

# llama.cpp GitHub releases API
LLAMA_GITHUB_API = (
    "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
)

# Asset search patterns tried in priority order (newer releases use bin-win-cpu-x64).
# CUDA builds are faster when a compatible GPU is present; CPU is the safe fallback.
_WIN_ASSET_PATTERNS = [
    "bin-win-cuda-12",    # NVIDIA CUDA 12.x  (RTX 20xx/30xx/40xx)
    "bin-win-cuda-13",    # NVIDIA CUDA 13.x  (RTX 50xx)
    "bin-win-cpu-x64",    # Pure CPU, x86-64  ← safe universal fallback
    "bin-win-cpu-arm64",  # ARM64 Windows
    "bin-win-avx2",       # Legacy naming (pre-b9090)
    "bin-win-vulkan",     # Vulkan (AMD / Intel)
    "bin-win",            # Any other Windows build
]


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
    if total_size <= 0:
        return
    downloaded = min(block_num * block_size, total_size)
    pct  = downloaded / total_size * 100
    bar  = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
    mb   = downloaded / 1_048_576
    tot  = total_size / 1_048_576
    print(f"\r  [{bar}] {pct:5.1f}%  {mb:.1f}/{tot:.1f} MB", end="", flush=True)


def _download(url: str, dest: Path, label: str) -> None:
    print(f"\nDownloading {label}")
    print(f"  URL: {url}")
    print(f"  → {dest}")
    urllib.request.urlretrieve(url, str(dest), reporthook=_progress_hook)
    print()   # newline after progress bar
    size_mb = dest.stat().st_size / 1_048_576
    print(f"  Saved: {size_mb:.1f} MB")


def _get_latest_llama_url(cpu_only: bool = False) -> str:
    """
    Fetch the latest llama.cpp GitHub release and return the best Windows zip URL.

    Priority (default): CUDA 12 → CUDA 13 → CPU x64 → Vulkan → any Windows zip
    If cpu_only=True: CPU x64 is tried first, CUDA variants skipped.
    """
    print("\nQuerying GitHub for latest llama.cpp release …")
    req = urllib.request.Request(
        LLAMA_GITHUB_API,
        headers={"User-Agent": "sfr_gen-setup/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    assets = data.get("assets", [])
    tag    = data.get("tag_name", "unknown")
    print(f"  Latest release: {tag}")

    name_to_url = {a["name"]: a["browser_download_url"] for a in assets}

    patterns = list(_WIN_ASSET_PATTERNS)
    if cpu_only:
        # Move CPU patterns first, filter CUDA out entirely
        patterns = [p for p in patterns if "cpu" in p] + \
                   [p for p in patterns if "cpu" not in p and "cuda" not in p]

    for pattern in patterns:
        matches = [n for n in name_to_url if pattern in n and n.endswith(".zip")]
        if matches:
            chosen = sorted(matches)[0]   # deterministic — alphabetically first
            print(f"  Selected asset : {chosen}")
            return name_to_url[chosen]

    # Nothing matched — show available Windows assets for the user
    win_assets = sorted(
        n for n in name_to_url if "win" in n.lower() or n.endswith(".zip")
    )
    raise RuntimeError(
        f"Could not find a suitable Windows binary in release {tag}.\n"
        f"Available Windows assets:\n"
        + "\n".join(f"  {n}" for n in win_assets)
        + "\n\nManually download llama-server.exe and place it in: "
        + str(RUNTIME_DIR)
        + "\nRelease page: https://github.com/ggerganov/llama.cpp/releases"
    )


def _extract_server_exe(zip_path: Path, dest_dir: Path) -> Path:
    """
    Extract llama-server.exe **and all companion DLLs** from the zip into dest_dir.

    Recent llama.cpp Windows releases ship as a zip containing:
        llama-server.exe, llama.dll, ggml.dll, ggml-cpu.dll, etc.
    All DLLs must sit next to the exe or Windows will refuse to load it.
    """
    print(f"\nExtracting llama-server and companion DLLs …")
    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = zf.namelist()

        # ── Find the server exe ───────────────────────────────────────────────
        candidates = [
            n for n in all_names
            if "llama-server" in n.lower() and n.endswith(".exe")
        ]
        if not candidates:
            candidates = [
                n for n in all_names
                if "server" in n.lower() and n.endswith(".exe")
            ]
        if not candidates:
            raise RuntimeError(
                f"llama-server.exe not found in zip.\n"
                f"Contents: {all_names}"
            )

        chosen_exe = candidates[0]
        # The DLLs live in the same zip directory as the exe
        exe_prefix = chosen_exe.rsplit("/", 1)[0] + "/" if "/" in chosen_exe else ""

        # ── Collect companion DLLs at the same level ──────────────────────────
        dll_names = [
            n for n in all_names
            if n.endswith(".dll") and n.startswith(exe_prefix)
        ]

        # ── Extract exe ───────────────────────────────────────────────────────
        print(f"  Extracting exe : {chosen_exe}")
        zf.extract(chosen_exe, dest_dir)
        extracted_exe = dest_dir / chosen_exe
        final_exe     = dest_dir / "llama-server.exe"
        extracted_exe.rename(final_exe)

        # ── Extract each DLL flat into dest_dir ───────────────────────────────
        for dll_entry in dll_names:
            dll_name = dll_entry.rsplit("/", 1)[-1]   # strip any sub-folder prefix
            print(f"  Extracting dll : {dll_name}")
            zf.extract(dll_entry, dest_dir)
            extracted_dll = dest_dir / dll_entry
            final_dll     = dest_dir / dll_name
            if extracted_dll != final_dll:
                final_dll.unlink(missing_ok=True)
                extracted_dll.rename(final_dll)

        if dll_names:
            print(f"  {len(dll_names)} DLL(s) extracted alongside exe.")
        else:
            print("  No companion DLLs found in zip (static build or Linux binary).")

        return final_exe


def _hf_download_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


def _write_config(runtime_dir: Path, server_exe: Path, model_path: Path, port: int) -> None:
    cfg = {
        "server_exe":  str(server_exe),
        "model_path":  str(model_path),
        "server_port": port,
        "base_url":    f"http://127.0.0.1:{port}/v1",
    }
    cfg_path = runtime_dir / "model.cfg"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"\n  Config written: {cfg_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def download(
    model_size: str = "7b",
    port: int = 8080,
    skip_llama: bool = False,
    cpu_only: bool = False,
) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Setup directory: {RUNTIME_DIR.resolve()}")

    gguf_file = MODEL_SIZES.get(model_size)
    if gguf_file is None:
        raise ValueError(f"Unknown model size '{model_size}'. Choose: {list(MODEL_SIZES)}")

    # ── 1. Download llama-server.exe ─────────────────────────────────────────
    server_exe = RUNTIME_DIR / "llama-server.exe"
    if server_exe.exists() and not skip_llama:
        print(f"\nllama-server.exe already present ({server_exe.stat().st_size/1e6:.1f} MB) — skipping.")
    elif not skip_llama:
        zip_url  = _get_latest_llama_url(cpu_only=cpu_only)
        zip_path = RUNTIME_DIR / "_llama_tmp.zip"
        try:
            _download(zip_url, zip_path, "llama.cpp release (zip)")
            _extract_server_exe(zip_path, RUNTIME_DIR)
        finally:
            if zip_path.exists():
                zip_path.unlink()
        print(f"  llama-server.exe ready: {server_exe}")

    # ── 2. Download GGUF model ───────────────────────────────────────────────
    model_path = RUNTIME_DIR / gguf_file
    min_bytes  = _MODEL_MIN_BYTES.get(gguf_file, 0)

    if model_path.exists():
        actual = model_path.stat().st_size
        if actual >= min_bytes:
            size_gb = actual / 1_073_741_824
            print(f"\nModel already present: {model_path.name} ({size_gb:.2f} GB) — skipping.")
        else:
            size_gb = actual / 1_073_741_824
            exp_gb  = min_bytes / 1_073_741_824
            print(f"\nWARNING: {model_path.name} looks incomplete ({size_gb:.2f} GB, expected ≥ {exp_gb:.1f} GB).")
            print("  Deleting truncated file and re-downloading …")
            model_path.unlink()
            url = _hf_download_url(HF_REPO, gguf_file)
            _download(url, model_path, f"Qwen2.5-Coder {model_size.upper()} GGUF")
    else:
        url = _hf_download_url(HF_REPO, gguf_file)
        _download(url, model_path, f"Qwen2.5-Coder {model_size.upper()} GGUF")

    # ── 3. Write config ──────────────────────────────────────────────────────
    _write_config(RUNTIME_DIR, server_exe, model_path, port)

    print("\n" + "=" * 60)
    print("Setup complete!")
    print(f"  Server exe : {server_exe}")
    print(f"  Model      : {model_path.name}")
    print(f"  Port       : {port}")
    print()
    print("To start the server:")
    print("  python run_server.py")
    print("  — or —")
    print("  python sfr_gen.py --ipxact regs.xlsx --auto-start-server")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download llama-server.exe and Qwen GGUF model for sfr_gen."
    )
    parser.add_argument(
        "--size", default="7b",
        choices=list(MODEL_SIZES),
        help="Model size to download. Default: 7b (~4.7 GB, recommended).",
    )
    parser.add_argument(
        "--port", default=8080, type=int,
        help="Port the server will listen on. Default: 8080.",
    )
    parser.add_argument(
        "--skip-llama", action="store_true",
        help="Skip downloading llama-server.exe (already present).",
    )
    parser.add_argument(
        "--cpu-only", action="store_true",
        help="Download CPU-only build (skip CUDA). Use on machines without NVIDIA GPU.",
    )
    args = parser.parse_args()
    download(
        model_size=args.size,
        port=args.port,
        skip_llama=args.skip_llama,
        cpu_only=args.cpu_only,
    )
