"""
llm_client.py — Multi-Backend LLM Client (Ollama + HuggingFace)

Supports two backends:
  1. Ollama  (local, zero-cost) — auto-detected at http://localhost:11434
  2. HuggingFace Inference API  — requires HF_TOKEN env var

Backend priority:
  ollama_model configured  →  use Ollama
  HF_TOKEN set             →  use HuggingFace
  neither                  →  no LLM available

All prompts generate struct-based LLD functions:
  lld->pSFR->stREG.stNative.FIELD = val;

Caching:
  Responses cached by SHA-16 of (reg|field|change_type|desc) in .lld_gen_cache/

Struct-based system prompt:
  Instructs LLM to use `struct lld_{ip} *lld` parameter and
  `lld->pSFR->st{REG}.stNative.{FIELD}` access.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# LLD function generation system prompt (struct-based)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an expert embedded C firmware engineer for Samsung SFR register drivers.
Generate ONLY raw C code with these rules:
- No #include, no #define, no markdown fences, no prose
- static inline functions only
- Parameter: struct lld_{ip} *lld   (replace {ip} with actual IP name, lowercase)
- Access bitfields via: lld->pSFR->st{REG}.stNative.{FIELD}
  where st{REG} is the struct member (e.g. stSTATUS_CON) and {FIELD} is the bitfield
- Getter: return ({return_type})(lld->pSFR->stREG.stNative.FIELD);
- Setter: lld->pSFR->stREG.stNative.FIELD = val;
- W1C clear: lld->pSFR->stREG.stNative.FIELD = 1U; /* W1C */
- W1S set1:  lld->pSFR->stREG.stNative.FIELD = 1U; /* W1S */
- NO raw masks (0x...), NO bit shifts (>>), NO base[] arrays
- Function naming: lld_{ip}_{reg}_{field}_{verb}  all lowercase
- Add /** @brief {desc} */ doxygen comment before each function
"""

_USER_PROMPT_TMPL = """\
IP: {ip}  (lowercase struct: struct lld_{ip_lo} *lld)
Register: {reg_name}  struct member: st{reg_name}
Field: {field_name}  access={access}
Field description: {desc}
Return type: {return_type}
Change type: {change_type}

Required functions:
{functions_needed}

Access path: lld->pSFR->st{reg_name}.stNative.{field_name}

Old function text (for context/reference — rewrite using struct-based access):
{old_code}

Generate the updated C static inline function(s) — struct-based, no masks, no shifts.
"""

# ---------------------------------------------------------------------------
# Unit test generation system prompt (struct-based)
# ---------------------------------------------------------------------------
_TEST_SYSTEM_PROMPT = """\
You are an expert embedded C firmware test engineer for Samsung SFR drivers.
Generate ONLY raw C code — no prose, no markdown fences, no #include lines.
Write static void test functions using:
  SFR_{IP} sfr = {{0}};
  struct lld_{ip} lld = {{ .pSFR = &sfr }};
Rules:
  - Test via struct member:  sfr.st{REG}.stNative.{FIELD}
  - Test boundary values (0, max field value)
  - For RW: set via lld function, assert sfr member equals value
  - For RO: only getter test
  - For W1C: call clear(), assert member == 1U
  - For W1S: call set1(), assert member == 1U
  - One test function per behaviour: test_{fn_name}_{scenario}()
  - No malloc, no OS calls, no external dependencies
"""

_TEST_USER_PROMPT_TMPL = """\
IP: {ip}
Register: {reg_name}  struct member: st{reg_name}
Field: {field_name}  access={access}  width={width} bits
Description: {desc}
Reset value: 0x{reset_val:X}
Max value: 0x{max_val:X}

Test access pattern:
  SFR_{IP} sfr = {{{{0}}}};
  struct lld_{ip} lld = {{ .pSFR = &sfr }};
  sfr.st{reg_name}.stNative.{field_name} = VALUE;  // inject value
  assert(lld_{ip}_{reg_lo}_{field_lo}_get(&lld) == VALUE);  // getter

Functions to test:
{fn_list}

Reference implementation:
{impl}

Write thorough C unit tests covering:
1. get: inject 1U into sfr field, assert getter returns 1U
2. set (RW): call setter with 1U, assert sfr field == 1U
3. clear (W1C): call clear(), assert sfr field == 1U
4. boundary: test with max field value
"""

_CACHE_DIR = Path(".lld_gen_cache")


def _cache_key(reg: str, field: str, change_type: str, desc: str) -> str:
    raw = f"{reg}|{field}|{change_type}|{desc}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models add despite instructions."""
    text = re.sub(r"```(?:c|cpp|C)?\s*\n", "", text)
    text = re.sub(r"\n?```", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# LLM Client (Ollama + HuggingFace)
# ---------------------------------------------------------------------------
class LLMClient:
    """
    Multi-backend LLM client.

    Priority:
      1. Ollama (local) — if ollama_model is set and server is reachable
      2. HuggingFace   — if HF_TOKEN is set
      3. No LLM        — available=False

    All generated functions use struct lld_*lld parameter (struct-based LLD).
    """

    OLLAMA_BASE      = "http://localhost:11434"
    OLLAMA_CHAT_URL  = f"{OLLAMA_BASE}/api/chat"
    HF_API_URL       = (
        "https://api-inference.huggingface.co/models/"
        "Qwen/Qwen2.5-Coder-7B-Instruct"
    )

    def __init__(
        self,
        hf_token:     Optional[str]  = None,
        ollama_model: Optional[str]  = None,
        cache_dir:    Optional[Path] = None,
        max_retries:  int   = 3,
        timeout:      float = 120.0,
    ):
        self._hf_token    = hf_token or os.environ.get("HF_TOKEN", "")
        self._ollama_model = ollama_model or os.environ.get("OLLAMA_MODEL", "")
        self._cache_dir   = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._cache_dir.mkdir(exist_ok=True)
        self.max_retries  = max_retries
        self.timeout      = timeout
        self._backend     = "none"

        try:
            import requests as _req
            self._requests = _req
        except ImportError:
            self._requests = None

        # Auto-detect backend
        self._detect_backend()

    def _detect_backend(self) -> None:
        """Probe Ollama then HuggingFace to determine which backend is live."""
        if self._ollama_model and self._requests:
            try:
                r = self._requests.get(
                    f"{self.OLLAMA_BASE}/api/tags", timeout=3
                )
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    # Accept partial match (e.g. "qwen2.5-coder" matches "qwen2.5-coder:1.5b")
                    matched = next(
                        (m for m in models if m.startswith(self._ollama_model.split(":")[0])),
                        None
                    )
                    if matched:
                        self._ollama_model = matched   # use exact tag
                        self._backend = "ollama"
                        print(f"[LLM] Backend: Ollama ({matched})")
                        return
                    else:
                        print(f"[LLM] Ollama running but model '{self._ollama_model}' not found."
                              f" Available: {models}")
            except Exception as e:
                print(f"[LLM] Ollama probe failed: {e}")

        if self._hf_token and self._requests:
            self._backend = "huggingface"
            print(f"[LLM] Backend: HuggingFace (Qwen2.5-Coder-7B)")

    @property
    def available(self) -> bool:
        return self._backend in {"ollama", "huggingface"}

    @property
    def backend(self) -> str:
        return self._backend

    # ── Cache ────────────────────────────────────────────────────────────────
    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.c"

    def _from_cache(self, key: str) -> Optional[str]:
        p = self._cache_path(key)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _to_cache(self, key: str, content: str) -> None:
        self._cache_path(key).write_text(content, encoding="utf-8")

    # ── Ollama backend ───────────────────────────────────────────────────────
    def _call_ollama(self, system: str, user: str, max_tokens: int = 512) -> str:
        if not self._requests:
            raise RuntimeError("requests package not available")
        payload = {
            "model": self._ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {
                "temperature":  0.1,
                "num_predict":  max_tokens,
            },
        }
        for attempt in range(self.max_retries):
            try:
                resp = self._requests.post(
                    self.OLLAMA_CHAT_URL, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "")
            except Exception as exc:
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[LLM-Ollama] Retry {attempt+1}/{self.max_retries} in {wait}s ({exc})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Ollama call failed: {exc}") from exc
        return ""

    # ── HuggingFace backend ──────────────────────────────────────────────────
    def _call_hf(self, system: str, user: str, max_tokens: int = 512) -> str:
        if not self._requests or not self._hf_token:
            raise RuntimeError("HuggingFace: requests or HF_TOKEN missing")
        headers = {
            "Authorization": f"Bearer {self._hf_token}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_new_tokens": max_tokens,
            "temperature":    0.1,
            "do_sample":      False,
        }
        for attempt in range(self.max_retries):
            try:
                resp = self._requests.post(
                    self.HF_API_URL, headers=headers, json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data:
                    return data[0].get("generated_text", "")
                if isinstance(data, dict):
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return data.get("generated_text", "")
                return str(data)
            except Exception as exc:
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[LLM-HF] Retry {attempt+1}/{self.max_retries} in {wait}s ({exc})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"HF call failed: {exc}") from exc
        return ""

    # ── Dispatch ─────────────────────────────────────────────────────────────
    def _call(self, system: str, user: str, max_tokens: int = 512) -> str:
        if self._backend == "ollama":
            return self._call_ollama(system, user, max_tokens)
        elif self._backend == "huggingface":
            return self._call_hf(system, user, max_tokens)
        else:
            raise RuntimeError("No LLM backend available")

    # ── Public: LLD function generation ─────────────────────────────────────
    def generate(
        self,
        ip:          str,
        reg_name:    str,
        reg_offset:  int,
        reg_desc:    str,
        field_name:  str,
        msb:         int,
        lsb:         int,
        access:      str,
        desc:        str,
        mask:        int,
        shift:       int,
        return_type: str,
        change_type: str,
        old_code:    str = "",
        functions_needed: str = "",
    ) -> str:
        """
        Generate struct-based LLD C functions for one field change.
        Returns raw C source string.
        Raises RuntimeError if LLM is unavailable and no cache hit.
        """
        key    = _cache_key(reg_name, field_name, change_type, desc)
        cached = self._from_cache(key)
        if cached is not None:
            print(f"[LLM] Cache hit: {reg_name}.{field_name} ({change_type})")
            return cached

        if not self.available:
            raise RuntimeError(
                f"LLM not available. Set OLLAMA_MODEL or HF_TOKEN. "
                f"Field: {reg_name}.{field_name} ({change_type})"
            )

        ip_lo   = ip.lower()
        reg_lo  = reg_name.lower()
        field_lo = field_name.lower()

        if not functions_needed:
            fn_prefix = f"lld_{ip_lo}_{reg_lo}_{field_lo}"
            fns = []
            if access in {"RO", "RW", "W1C", "W1S"}:
                fns.append(f"{fn_prefix}_get")
            if access == "RW":
                fns.append(f"{fn_prefix}_set")
            if access == "WO":
                fns.append(f"{fn_prefix}_set")
            if access == "W1C":
                fns.append(f"{fn_prefix}_clear")
            if access == "W1S":
                fns.append(f"{fn_prefix}_set1")
            functions_needed = "\n".join(f"  - {f}" for f in fns) or f"  - {fn_prefix}_get"

        # Build struct-based system prompt substituting IP name
        system = _SYSTEM_PROMPT.replace("{ip}", ip_lo).replace("{return_type}", return_type)

        user = _USER_PROMPT_TMPL.format(
            ip=ip, ip_lo=ip_lo,
            reg_name=reg_name, field_name=field_name,
            access=access, desc=desc, return_type=return_type,
            change_type=change_type,
            old_code=old_code or "(none — generate fresh)",
            functions_needed=functions_needed,
        )

        print(f"[LLM-{self._backend.upper()}] Generating: {reg_name}.{field_name} ({change_type}) …")
        code = _strip_fences(self._call(system, user, max_tokens=600))
        if code:
            self._to_cache(key, code)
        return code

    # ── Public: compile-error fix ────────────────────────────────────────────
    def fix_compile_error(
        self,
        broken_code: str,
        error_msg:   str,
        context:     str = "",
    ) -> str:
        if not self.available:
            raise RuntimeError("LLM not available for compile-error fix.")
        system = "You are an expert C firmware engineer. Fix the compile error. Return raw C only."
        user = (
            f"Broken code:\n```c\n{broken_code}\n```\n\n"
            f"GCC error:\n```\n{error_msg}\n```\n\n"
            f"{context}\n\nReturn the fixed C function(s) only."
        )
        return _strip_fences(self._call(system, user, max_tokens=512))

    # ── Public: unit test generation ─────────────────────────────────────────
    def generate_test(
        self,
        ip:          str,
        reg_name:    str,
        reg_offset:  int,
        field_name:  str,
        msb:         int,
        lsb:         int,
        access:      str,
        desc:        str,
        mask:        int,
        shift:       int,
        width:       int,
        reset_val:   int,
        fn_list:     str,
        impl:        str,
    ) -> str:
        """
        Generate rich struct-based unit tests for one LLD field.
        Returns empty string if LLM unavailable — caller uses template fallback.
        """
        key    = _cache_key(reg_name, field_name, f"TEST_{access}", desc)
        cached = self._from_cache(key)
        if cached is not None:
            print(f"[LLM-TEST] Cache hit: {reg_name}.{field_name}")
            return cached

        if not self.available:
            return ""  # caller uses template fallback

        ip_lo    = ip.lower()
        reg_lo   = reg_name.lower()
        field_lo = field_name.lower()
        max_val  = (1 << width) - 1

        system = _TEST_SYSTEM_PROMPT
        user   = _TEST_USER_PROMPT_TMPL.format(
            ip=ip, IP=ip.upper(), ip_lo=ip_lo,
            reg_name=reg_name, reg_lo=reg_lo,
            field_name=field_name, field_lo=field_lo,
            access=access, desc=desc, width=width,
            reset_val=reset_val, max_val=max_val,
            fn_list=fn_list,
            impl=impl or "(not available — generate from description)",
        )

        print(f"[LLM-TEST-{self._backend.upper()}] Generating tests: {reg_name}.{field_name} ({access}) …")
        try:
            code = _strip_fences(self._call(system, user, max_tokens=768))
            if code:
                self._to_cache(key, code)
            return code
        except Exception as exc:
            print(f"[LLM-TEST] Failed: {exc} — using template")
            return ""
