"""
LLM client for sfr_gen — llama.cpp backend (Option C).

Architecture
────────────
  sfr_gen.py
      └── LLMClient  (this file)
              └── llama-server.exe  (llama.cpp HTTP server)
                      └── qwen2.5-coder-7b-instruct-q4_k_m.gguf

The LLMClient connects to llama-server's OpenAI-compatible endpoint
(http://localhost:8080/v1) and calls /chat/completions.

All responses are cached in .sfr_gen_cache/ keyed by field content,
so unchanged fields never re-query the model.

Quick-start
───────────
  1. Run setup:    python setup/download_model.py
  2. Start server: .\\run_server.ps1          (or let sfr_gen auto-start)
  3. Generate:     python sfr_gen.py --ipxact regs.xlsx

Auto-start mode (sfr_gen starts/stops server automatically):
  python sfr_gen.py --ipxact regs.xlsx --auto-start-server
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Optional

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

from models import Field

# Default paths (relative to sfr_gen/ project root)
_DEFAULT_SERVER_EXE  = Path("llm_runtime") / "llama-server.exe"
_DEFAULT_MODEL_GGUF  = Path("llm_runtime") / "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
_DEFAULT_SERVER_PORT = 8080
_CACHE_DIR           = Path(".sfr_gen_cache")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_PROMPT = """\
You are an expert C firmware engineer writing a low-level hardware driver.

Register field:
  Register   : {reg_name}  (base+0x{offset:04X})
  Field      : {field_name}  bits[{msb}:{lsb}]
  Access     : {access}
  Reset value: 0x{reset:X}
  Description: {description}

Macros already defined in sfr.h (use exactly as shown):
  {pfx_macro}_OFFSET
  {pfx_macro}_{field_name}_MASK
  {pfx_macro}_{field_name}_SHIFT
  REG_READ32(addr)        — 32-bit MMIO read
  REG_WRITE32(addr, val)  — 32-bit MMIO write

Write:
1. All standard accessors with prefix `{fn_prefix}`:
   - readable fields  → static inline uint32_t {fn_prefix}_get(uintptr_t base)
   - writable fields  → static inline void {fn_prefix}_set(uintptr_t base, uint32_t val)
   - W1C fields       → static inline void {fn_prefix}_clear(uintptr_t base)
2. ONE semantic action function named {fn_prefix}_<verb>() where <verb>
   comes from the description (e.g. enable, disable, reset, trigger).
3. A Doxygen /** @brief ... */ comment before each function.

Output ONLY raw C — no #include, no #define, no markdown fences.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _cache_key(field: Field, reg_name: str) -> str:
    raw = (
        f"{reg_name}|{field.name}|{field.msb}|{field.lsb}"
        f"|{field.access}|{field.reset_value}|{field.description}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models add despite instructions."""
    text = re.sub(r"```(?:c|cpp|C)?\s*\n", "", text)
    text = re.sub(r"\n?```", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# LLMClient — connects to a running llama-server instance
# ---------------------------------------------------------------------------
class LLMClient:
    """
    Sends prompts to a llama-server.exe OpenAI-compatible endpoint.

    The server must be running before calling generate_function().
    Use LlamaCppServer (server_manager.py) to start/stop it, or start it
    manually with run_server.ps1.
    """

    def __init__(
        self,
        base_url: str = f"http://127.0.0.1:{_DEFAULT_SERVER_PORT}/v1",
        model: str = "default",          # llama-server ignores this; kept for compat
        cache_dir: Optional[Path] = None,
        max_retries: int = 3,
        timeout: float = 120.0,
    ):
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "openai package not installed.\n"
                "Run: pip install openai"
            )

        self.base_url    = base_url
        self.model       = model
        self.max_retries = max_retries
        self.timeout     = timeout
        self.cache_dir   = Path(cache_dir) if cache_dir else _CACHE_DIR
        self.cache_dir.mkdir(exist_ok=True)

        self._client = OpenAI(
            api_key="no-key-needed",    # llama-server doesn't check keys
            base_url=base_url,
            timeout=timeout,
        )
        print(f"[LLM] Connected to llama-server at {base_url}")

    # ── Cache ────────────────────────────────────────────────────────────────
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.c"

    def _from_cache(self, key: str) -> Optional[str]:
        p = self._cache_path(key)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _to_cache(self, key: str, content: str) -> None:
        self._cache_path(key).write_text(content, encoding="utf-8")

    # ── Core call ────────────────────────────────────────────────────────────
    def _call(self, prompt: str) -> str:
        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=1024,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[LLM] Retry {attempt + 1}/{self.max_retries} in {wait}s ({exc})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"LLM call failed after {self.max_retries} retries: {exc}"
                    ) from exc
        return ""

    # ── Public API ───────────────────────────────────────────────────────────
    def generate_function(
        self,
        field: Field,
        fn_prefix: str,
        reg_name: str = "",
        reg_offset: int = 0,
        pfx_macro: str = "",
    ) -> str:
        """
        Query the model for C functions describing *field*.

        Caches by field content — re-runs never re-query for unchanged fields.
        Returns raw C source code string.
        """
        key    = _cache_key(field, reg_name)
        cached = self._from_cache(key)
        if cached is not None:
            print(f"[LLM] Cache hit: {reg_name}.{field.name}")
            return cached

        print(f"[LLM] Generating: {reg_name}.{field.name} …")
        prompt = _PROMPT.format(
            reg_name   = reg_name,
            offset     = reg_offset,
            field_name = field.name,
            msb        = field.msb,
            lsb        = field.lsb,
            access     = field.access,
            reset      = field.reset_value,
            description= field.description,
            fn_prefix  = fn_prefix,
            pfx_macro  = pfx_macro or reg_name.upper(),
        )
        code = _strip_fences(self._call(prompt))
        self._to_cache(key, code)
        return code

    def ping(self) -> bool:
        """Return True if the server responds correctly."""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Reply with the single word: OK"}],
                max_tokens=5,
            )
            return "ok" in (resp.choices[0].message.content or "").lower()
        except Exception as exc:
            print(f"[LLM] Ping failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------
def make_llm_fn(client: LLMClient, peripheral: str):
    """
    Returns a Callable(field, fn_prefix, reg=None) → str for use by
    generate_lld() and update_lld().
    """
    def llm_fn(field: Field, fn_prefix: str, reg=None) -> str:
        reg_name   = reg.name   if reg else ""
        reg_offset = reg.offset if reg else 0
        pfx_macro  = f"{peripheral.upper()}_{reg_name.upper()}" if reg_name else peripheral.upper()
        return client.generate_function(
            field, fn_prefix,
            reg_name=reg_name,
            reg_offset=reg_offset,
            pfx_macro=pfx_macro,
        )
    return llm_fn


def build_client_from_args(args, peripheral: str):
    """
    Build an LLMClient from parsed CLI args, or return None for stub mode.

    If --auto-start-server is set, also starts llama-server.exe and returns
    the (client, server) tuple. Otherwise returns (client, None).
    Caller is responsible for calling server.stop() if server is not None.
    """
    from llm.server_manager import LlamaCppServer

    if getattr(args, "no_llm", False):
        return None, None

    server_exe = Path(getattr(args, "server_exe",  str(_DEFAULT_SERVER_EXE)))
    model_path = Path(getattr(args, "model_path",  str(_DEFAULT_MODEL_GGUF)))
    port       = int(getattr(args, "server_port",  _DEFAULT_SERVER_PORT))
    base_url   = getattr(args, "llm_base_url",     None) or f"http://127.0.0.1:{port}/v1"
    auto_start = getattr(args, "auto_start_server", False)

    server = None

    if auto_start:
        server = LlamaCppServer(
            server_exe=server_exe,
            model_path=model_path,
            port=port,
        )
        try:
            server.start()
        except Exception as exc:
            print(f"[WARN] Server start failed ({exc}). Running in stub mode.")
            return None, None
    else:
        # Verify the server is reachable
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass
        except OSError:
            print(
                f"[WARN] No llama-server found on port {port}.\n"
                f"       Start it with:  .\\run_server.ps1\n"
                f"       Or use:         --auto-start-server\n"
                f"       Falling back to stub mode."
            )
            return None, None

    try:
        client = LLMClient(base_url=base_url)
        return client, server
    except Exception as exc:
        print(f"[WARN] LLM client init failed ({exc}). Stub mode.")
        if server:
            server.stop()
        return None, None
