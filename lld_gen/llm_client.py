"""
llm_client.py — Qwen2.5-Coder-7B Cloud LLM Client

Calls the HuggingFace Inference API with Qwen2.5-Coder-7B-Instruct
for AI-assisted LLD function generation.

Environment variables:
    HF_TOKEN     HuggingFace API token (required for cloud inference)

Parameters:
    max_new_tokens = 512    (token budget per call)
    temperature    = 0.1    (near-deterministic, reproducible output)
    do_sample      = False

Caching:
    Responses are cached by SHA-16 key of (reg + field + change_type + desc)
    in .lld_gen_cache/ to avoid redundant API calls.

Fallback:
    If HF_TOKEN is not set or the API is unreachable, the client
    falls back to template-only generation (no LLM).
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
# Prompt template (from SKILL.md / design doc section 6.3.1)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are an expert embedded C firmware engineer. Generate ONLY raw C code:
- No #include directives
- No #define directives
- No markdown fences
- No prose or explanations
- Exact function signatures per SKILL.md conventions
- static inline functions only
- Use volatile uint32_t *base parameter
- Use base[word_index] for register access (word_index = byte_offset / 4)
"""

_USER_PROMPT_TMPL = """\
IP: {ip}
Register: {reg_name} at byte offset 0x{offset:04X}
Register description: {reg_desc}
Field: {field_name}  bits [{msb}:{lsb}]  access={access}
Field description: {desc}
Bit mask: 0x{mask:08X}  shift: {shift}
Return type: {return_type}
Change: {change_type}
Generate: {functions_needed}

Old function text (for reference):
{old_code}

Generate the updated C function(s) following SKILL.md conventions exactly.
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


class LLMClient:
    """
    Qwen2.5-Coder-7B via HuggingFace Inference API.

    Falls back gracefully to None if HF_TOKEN is not available.
    Use `available` property to check before calling `generate()`.
    """

    HF_API_URL = (
        "https://api-inference.huggingface.co/models/"
        "Qwen/Qwen2.5-Coder-7B-Instruct"
    )

    def __init__(
        self,
        hf_token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ):
        self._token      = hf_token or os.environ.get("HF_TOKEN", "")
        self._cache_dir  = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._cache_dir.mkdir(exist_ok=True)
        self.max_retries = max_retries
        self.timeout     = timeout

        # Import requests lazily — not bundled in all envs
        try:
            import requests as _req
            self._requests = _req
        except ImportError:
            self._requests = None

    @property
    def available(self) -> bool:
        return bool(self._token) and self._requests is not None

    # ── Cache ────────────────────────────────────────────────────────────────
    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.c"

    def _from_cache(self, key: str) -> Optional[str]:
        p = self._cache_path(key)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _to_cache(self, key: str, content: str) -> None:
        self._cache_path(key).write_text(content, encoding="utf-8")

    # ── Core HTTP call ───────────────────────────────────────────────────────
    def _call_api(self, user_msg: str) -> str:
        if not self._requests:
            raise RuntimeError("requests package not available. Run: pip install requests")
        if not self._token:
            raise RuntimeError("HF_TOKEN not set. Export HF_TOKEN=hf_xxxx")

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }
        # HuggingFace Inference API chat-completion format
        payload = {
            "model": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "max_new_tokens": 512,
            "temperature":    0.1,
            "do_sample":      False,
        }

        for attempt in range(self.max_retries):
            try:
                resp = self._requests.post(
                    self.HF_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                # HF inference API returns generated_text or choices
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
                    print(f"[LLM] Retry {attempt + 1}/{self.max_retries} in {wait}s ({exc})")
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"LLM call failed after {self.max_retries} retries: {exc}"
                    ) from exc
        return ""

    # ── Public API ───────────────────────────────────────────────────────────
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
        Generate LLD C functions for one field change.

        Returns raw C source string (no fences, no includes).
        Raises RuntimeError if LLM is not available and no cache hit.
        """
        key    = _cache_key(reg_name, field_name, change_type, desc)
        cached = self._from_cache(key)
        if cached is not None:
            print(f"[LLM] Cache hit: {reg_name}.{field_name} ({change_type})")
            return cached

        if not self.available:
            raise RuntimeError(
                f"LLM not available (HF_TOKEN missing or requests not installed). "
                f"Cannot generate code for {reg_name}.{field_name} ({change_type}). "
                f"Set HF_TOKEN env var or use --no-llm."
            )

        print(f"[LLM] Generating: {reg_name}.{field_name} ({change_type}) …")
        if not functions_needed:
            fn_prefix = f"{ip}_{reg_name}_{field_name}"
            fns = []
            if access in {"RO", "RW", "W1C", "W1S"}:
                fns.append(f"{fn_prefix}_get")
            if access in {"RW"}:
                fns.append(f"{fn_prefix}_set")
            if access in {"WO"}:
                fns.append(f"{fn_prefix}_set")
            if access in {"W1C"}:
                fns.append(f"{fn_prefix}_clear")
            if access in {"W1S"}:
                fns.append(f"{fn_prefix}_set1")
            functions_needed = ", ".join(fns) or f"{fn_prefix}_get"

        user_msg = _USER_PROMPT_TMPL.format(
            ip=ip, reg_name=reg_name, offset=reg_offset, reg_desc=reg_desc or "",
            field_name=field_name, msb=msb, lsb=lsb, access=access,
            desc=desc, mask=mask, shift=shift, return_type=return_type,
            change_type=change_type, old_code=old_code or "(none)",
            functions_needed=functions_needed,
        )

        code = _strip_fences(self._call_api(user_msg))
        self._to_cache(key, code)
        return code

    def fix_compile_error(
        self,
        broken_code: str,
        error_msg:   str,
        context:     str = "",
    ) -> str:
        """
        Re-prompt the LLM with the broken function text and gcc error output
        to get a fixed version.
        """
        print(f"[LLM] Requesting compile-error fix …")
        user_msg = (
            f"The following C code failed gcc compilation:\n\n"
            f"```c\n{broken_code}\n```\n\n"
            f"GCC error:\n```\n{error_msg}\n```\n\n"
            f"{context}\n\n"
            f"Provide the corrected function(s) as raw C only."
        )
        if not self.available:
            raise RuntimeError("LLM not available for compile-error fix.")
        return _strip_fences(self._call_api(user_msg))
