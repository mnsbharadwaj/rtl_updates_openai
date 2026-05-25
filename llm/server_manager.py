"""
llama.cpp server lifecycle manager.

Manages the llama-server.exe process:
  - Checks if the server is already running on the configured port
  - Starts it as a subprocess if not
  - Waits until the /health endpoint responds
  - Can be used as a context manager for clean shutdown

Usage:
    from llm.server_manager import LlamaCppServer

    server = LlamaCppServer(
        server_exe="llm_runtime/llama-server.exe",
        model_path="llm_runtime/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        port=8080,
    )
    with server:          # auto-start, auto-stop
        # ... call generate_lld() ...
        pass

    # Or manually:
    server.start()
    server.stop()
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional


class LlamaCppServer:
    """
    Manages a llama-server.exe process for local Qwen inference.

    If the port is already open (server started externally), start() is a
    no-op so sfr_gen can be used alongside a persistently running server.
    """

    DEFAULT_PORT      = 8080
    DEFAULT_CTX_SIZE  = 4096
    HEALTH_ENDPOINT   = "http://127.0.0.1:{port}/health"
    STARTUP_TIMEOUT_S = 120     # seconds to wait for server to become ready

    def __init__(
        self,
        server_exe: str | Path,
        model_path: str | Path,
        port: int = DEFAULT_PORT,
        ctx_size: int = DEFAULT_CTX_SIZE,
        n_gpu_layers: int = 0,          # 0 = CPU only; set e.g. 35 for GPU offload
        host: str = "127.0.0.1",
        extra_args: Optional[list] = None,
    ):
        self.server_exe   = Path(server_exe)
        self.model_path   = Path(model_path)
        self.port         = port
        self.ctx_size     = ctx_size
        self.n_gpu_layers = n_gpu_layers
        self.host         = host
        self.extra_args   = extra_args or []
        self._process: Optional[subprocess.Popen] = None
        self._we_started  = False      # True if WE launched the process

    # ── Validation ───────────────────────────────────────────────────────────
    def validate(self) -> None:
        """Raise FileNotFoundError if required files are missing."""
        if not self.server_exe.exists():
            raise FileNotFoundError(
                f"llama-server not found: {self.server_exe}\n"
                "Run:  python setup/download_model.py"
            )
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model GGUF not found: {self.model_path}\n"
                "Run:  python setup/download_model.py"
            )

    # ── Port probe ───────────────────────────────────────────────────────────
    def is_port_open(self) -> bool:
        """Return True if something is already listening on self.port."""
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                return True
        except OSError:
            return False

    def wait_ready(self, timeout: float = STARTUP_TIMEOUT_S) -> bool:
        """
        Poll /health until the server responds 200 OK or *timeout* expires.
        Returns True if ready, False on timeout.
        """
        url      = self.HEALTH_ENDPOINT.format(port=self.port)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(1)
        return False

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """
        Start llama-server.exe if not already running.
        Blocks until the server reports healthy or raises RuntimeError.
        """
        if self.is_port_open():
            print(f"[server] Port {self.port} already open — using existing server.")
            self._we_started = False
            return

        self.validate()

        cmd = [
            str(self.server_exe),
            "--model",       str(self.model_path),
            "--port",        str(self.port),
            "--host",        self.host,
            "--ctx-size",    str(self.ctx_size),
            "--n-gpu-layers", str(self.n_gpu_layers),
            "--log-disable",          # suppress verbose startup logs to console
        ] + self.extra_args

        print(f"[server] Starting llama-server on port {self.port} …")
        print(f"[server] Model: {self.model_path.name}")

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._we_started = True

        if not self.wait_ready(self.STARTUP_TIMEOUT_S):
            stderr = ""
            if self._process.stderr:
                stderr = self._process.stderr.read(2000)
            self.stop()
            raise RuntimeError(
                f"llama-server did not become ready within "
                f"{self.STARTUP_TIMEOUT_S}s.\n{stderr}"
            )

        print(f"[server] Ready at http://{self.host}:{self.port}/v1")

    def stop(self) -> None:
        """Terminate the server process (only if we started it)."""
        if self._process and self._we_started:
            print("[server] Stopping llama-server …")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process    = None
            self._we_started = False

    # ── Context manager ──────────────────────────────────────────────────────
    def __enter__(self) -> "LlamaCppServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ── Properties ───────────────────────────────────────────────────────────
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"
