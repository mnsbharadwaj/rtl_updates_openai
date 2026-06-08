"""
llm_client.py  —  Universal Config-Driven LLM Client  (v3.0)

Backends supported (controlled 100% from config/yaml — zero hardcoding):
  1. ollama        — local Ollama server  (Qwen, Llama, Mistral, Phi, …)
  2. openai        — OpenAI API  (gpt-4o, gpt-3.5-turbo, …)
  3. azure_openai  — Azure OpenAI  (requires endpoint + deployment + api_version)
  4. anthropic     — Anthropic Claude  (claude-3-5-sonnet, claude-3-haiku, …)
  5. huggingface   — HuggingFace Inference API  (any HF model)
  6. openai_compat — Any OpenAI-compatible endpoint  (LM Studio, vLLM, Together,
                     Groq, Fireworks, Mistral, Cohere, etc.)

Config snippet (lld_patcher.yaml or workflow_config.yaml):
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ llm:                                                                    │
  │   backend: ollama            # ollama | openai | azure_openai |         │
  │                              # anthropic | huggingface | openai_compat  │
  │   model:   qwen2.5-coder:7b  # exact model tag / deployment name        │
  │   url:     http://localhost:11434  # override base URL (optional)       │
  │   api_key: ""               # or set env var per backend (see below)    │
  │   api_version: "2024-02-01" # Azure only                                │
  │   temperature: 0.1                                                      │
  │   max_tokens:  600                                                      │
  │   timeout:     120                                                      │
  │   max_retries: 3                                                        │
  │   context_limit: 4096       # truncate prompts to stay within limit     │
  └─────────────────────────────────────────────────────────────────────────┘

Environment variable fallbacks (per backend):
  OLLAMA_MODEL       — sets llm.model for ollama backend
  OPENAI_API_KEY     — api_key for openai / openai_compat
  ANTHROPIC_API_KEY  — api_key for anthropic
  AZURE_OPENAI_KEY   — api_key for azure_openai
  HF_TOKEN           — api_key for huggingface

Caching:
  Responses cached by SHA-16 of (reg|field|change_type|desc) in .lld_gen_cache/
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLD function generation system prompt (struct-based, backend-agnostic)
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

_TEST_SYSTEM_PROMPT = """\
You are an expert embedded C firmware test engineer for Samsung SFR drivers.
Generate ONLY raw C code — no prose, no markdown fences, no #include lines.
Write static void test functions using:
  SFR_{IP} sfr = {0};
  struct lld_{ip} lld = { .pSFR = &sfr };
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
  SFR_{IP} sfr = {{0}};
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


# ---------------------------------------------------------------------------
# LLM Config dataclass — populated from yaml or passed directly
# ---------------------------------------------------------------------------
@dataclass
class LLMConfig:
    """
    All settings for one LLM backend.
    Loaded from the 'llm:' section of lld_patcher.yaml or workflow_config.yaml.
    """
    backend:       str   = "ollama"         # ollama|openai|azure_openai|anthropic|huggingface|openai_compat
    model:         str   = ""               # model name/tag/deployment
    url:           str   = ""               # base URL override
    api_key:       str   = ""               # key (or from env var)
    api_version:   str   = "2024-02-01"    # Azure OpenAI only
    temperature:   float = 0.1
    max_tokens:    int   = 600
    timeout:       float = 120.0
    max_retries:   int   = 3
    context_limit: int   = 4096            # chars; truncate prompt body if exceeded
    location:      str   = "local"          # local|cloud

    # Default URLs per backend (can all be overridden via url:)
    _DEFAULT_URLS: Dict[str, str] = field(default_factory=lambda: {
        "ollama":        "http://localhost:11434",
        "openai":        "https://api.openai.com/v1",
        "azure_openai":  "",   # must be set via url: in config
        "anthropic":     "https://api.anthropic.com",
        "huggingface":   "https://api-inference.huggingface.co/models",
        "openai_compat": "",   # must be set via url: in config
    }, init=False, repr=False, compare=False)

    # Default env var names per backend
    _ENV_VARS: Dict[str, str] = field(default_factory=lambda: {
        "ollama":        "OLLAMA_MODEL",
        "openai":        "OPENAI_API_KEY",
        "azure_openai":  "AZURE_OPENAI_KEY",
        "anthropic":     "ANTHROPIC_API_KEY",
        "huggingface":   "HF_TOKEN",
        "openai_compat": "OPENAI_API_KEY",
    }, init=False, repr=False, compare=False)

    def resolve(self) -> "LLMConfig":
        """Fill defaults from environment variables if not set in config."""
        backend = self.backend.lower().replace("-", "_")
        self.backend = backend

        # Resolve model from env if not set
        if not self.model and backend == "ollama":
            self.model = os.environ.get("OLLAMA_MODEL", "")
        if not self.model and backend == "huggingface":
            self.model = os.environ.get("HF_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")

        # Resolve api_key from env if not set
        if not self.api_key:
            env_key = self._ENV_VARS.get(backend, "")
            if env_key:
                self.api_key = os.environ.get(env_key, "")

        # Resolve base URL
        if not self.url:
            self.url = self._DEFAULT_URLS.get(backend, "")

        return self

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


def load_llm_config(data: dict) -> LLMConfig:
    """
    Parse the 'llm:' section of a YAML config dict into an LLMConfig.

    Supports both flat and nested forms:
      Flat:   ollama_model: qwen2.5-coder:7b
      Nested: llm:
                backend: ollama
                model:   qwen2.5-coder:7b

    Legacy flat fields (still honoured for backward compat):
      ollama_model -> llm.backend=ollama, llm.model=<value>
      hf_token     -> llm.backend=huggingface, llm.api_key=<value>
      no_llm: true -> returns None (caller skips LLM entirely)
    """
    if data.get("no_llm"):
        return LLMConfig(backend="none")

    llm_section = data.get("llm", {}) or {}

    # Resolve location
    location = "local"
    for k in ("location", "cloud_or_local", "mode"):
        if k in llm_section:
            location = str(llm_section[k]).strip().lower()
            break
    else:
        for k in ("llm_location", "cloud_or_local", "llm_mode"):
            if k in data:
                location = str(data[k]).strip().lower()
                break

    cfg = LLMConfig(
        backend       = llm_section.get("backend", ""),
        model         = llm_section.get("model", ""),
        url           = llm_section.get("url", ""),
        api_key       = llm_section.get("api_key", ""),
        api_version   = llm_section.get("api_version", "2024-02-01"),
        temperature   = float(llm_section.get("temperature", 0.1)),
        max_tokens    = int(llm_section.get("max_tokens", 600)),
        timeout       = float(llm_section.get("timeout", 120.0)),
        max_retries   = int(llm_section.get("max_retries", 3)),
        context_limit = int(llm_section.get("context_limit", 4096)),
        location      = location,
    )

    # ── Legacy flat field support ────────────────────────────────────────────
    if not cfg.backend:
        if data.get("ollama_model"):
            cfg.backend = "ollama"
            cfg.model   = cfg.model or data["ollama_model"]
        elif data.get("hf_token"):
            cfg.backend = "huggingface"
            cfg.api_key = cfg.api_key or data["hf_token"]
            cfg.model   = cfg.model or data.get("hf_model", "Qwen/Qwen2.5-Coder-7B-Instruct")
        else:
            cfg.backend = "ollama"  # default attempt

    return cfg.resolve()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cache_key(reg: str, field_name: str, change_type: str, desc: str) -> str:
    raw = f"{reg}|{field_name}|{change_type}|{desc}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that some models add despite instructions."""
    text = re.sub(r"```(?:c|cpp|C)?\s*\n", "", text)
    text = re.sub(r"\n?```", "", text)
    return text.strip()


def _truncate(text: str, limit: int) -> str:
    """Truncate to char limit, appending '...[truncated]'."""
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "\n...[truncated for context window]"


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------
class LLMClient:
    """
    Universal config-driven LLM client.

    Instantiate with an LLMConfig object (loaded from yaml).
    All backend URLs, model names, API keys come from config — nothing hardcoded.

    Example backends:
      # Local Ollama (Qwen 7B, Llama 3, Mistral, etc.)
      llm: {backend: ollama, model: qwen2.5-coder:7b}

      # OpenAI cloud
      llm: {backend: openai, model: gpt-4o-mini, api_key: sk-...}

      # Azure OpenAI
      llm: {backend: azure_openai, model: gpt-4o,
            url: https://myres.openai.azure.com/openai/deployments/gpt-4o,
            api_key: ..., api_version: 2024-02-01}

      # Anthropic Claude
      llm: {backend: anthropic, model: claude-3-haiku-20240307, api_key: sk-ant-...}

      # HuggingFace (Qwen, Mistral, CodeLlama, etc.)
      llm: {backend: huggingface, model: Qwen/Qwen2.5-Coder-7B-Instruct, api_key: hf_...}

      # Any OpenAI-compatible (LM Studio, vLLM, Together, Groq, Fireworks, Mistral)
      llm: {backend: openai_compat, model: mixtral-8x7b-32768,
            url: https://api.groq.com/openai/v1, api_key: gsk_...}
    """

    def __init__(self, cfg: LLMConfig, cache_dir: Optional[Path] = None):
        self.cfg = cfg
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._cache_dir.mkdir(exist_ok=True)

        try:
            import requests as _req
            self._requests = _req
        except ImportError:
            self._requests = None

        self._backend_fn = self._resolve_backend()

        if self.available:
            logger.info("[LLM] Backend : %s", cfg.backend.upper())
            logger.info("[LLM] Model   : %s", cfg.model or '(auto)')
            logger.info("[LLM] Endpoint: %s", cfg.base_url or '(default)')
        else:
            logger.info("[LLM] No LLM backend active (backend='%s', no_llm or missing key)", cfg.backend)

    # ── Backend resolution ────────────────────────────────────────────────────
    def _resolve_backend(self):
        """Return the bound call method for the configured backend."""
        b = self.cfg.backend
        if b == "none" or not b:
            return None
        if getattr(self.cfg, "location", "local") == "cloud":
            if not self._requests:
                return None
            return self._call_cloud_ollama
        if b == "ollama":
            return self._verify_ollama()
        if b == "openai":
            return self._call_openai if self.cfg.api_key else None
        if b == "azure_openai":
            return self._call_azure if (self.cfg.api_key and self.cfg.base_url) else None
        if b == "anthropic":
            return self._call_anthropic if self.cfg.api_key else None
        if b == "huggingface":
            return self._call_hf if self.cfg.api_key else None
        if b == "openai_compat":
            return self._call_openai_compat if self.cfg.base_url else None
        logger.warning("[LLM] Unknown backend '%s' -- LLM disabled", b)
        return None

    def _verify_ollama(self):
        """Probe local Ollama and verify the requested model is loaded."""
        if not self._requests:
            return None
        try:
            r = self._requests.get(
                f"{self.cfg.base_url}/api/tags", timeout=3
            )
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                matched = next(
                    (m for m in models
                     if m.startswith(self.cfg.model.split(":")[0])),
                    None
                )
                if matched:
                    self.cfg.model = matched
                    return self._call_ollama
                logger.warning("[LLM] Ollama: model '%s' not found. Available: %s",
                               self.cfg.model, models)
        except Exception as exc:
            logger.warning("[LLM] Ollama probe failed: %s", exc)
        return None

    @property
    def available(self) -> bool:
        return self._backend_fn is not None

    @property
    def backend(self) -> str:
        return self.cfg.backend

    # ── Cache ─────────────────────────────────────────────────────────────────
    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.c"

    def _from_cache(self, key: str) -> Optional[str]:
        p = self._cache_path(key)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _to_cache(self, key: str, content: str) -> None:
        self._cache_path(key).write_text(content, encoding="utf-8")

    # ── Retry wrapper ─────────────────────────────────────────────────────────
    def _with_retry(self, fn, *args, **kwargs) -> str:
        last_exc = None
        for attempt in range(self.cfg.max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < self.cfg.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("[LLM] Retry %d/%d in %ds (%s)",
                                   attempt + 1, self.cfg.max_retries, wait, exc)
                    time.sleep(wait)
        raise RuntimeError(f"LLM call failed after {self.cfg.max_retries} retries: {last_exc}")

    # ── Backend: Cloud Ollama ─────────────────────────────────────────────────
    def _call_cloud_ollama(self, system: str, user: str, max_tokens: int) -> str:
        url = "http://107.99.41.85/ollama/srv1/api/generate"
        prompt = (
            f"System Instruction:\n{system}\n\n"
            f"User Context and Request:\n{user}\n\n"
            "Please generate the C code containing only the static inline function(s) or the fixed code. "
            "Return only the C code, without any markdown code fences, explanation, or prose."
        )
        payload = {
            "model": "gpt-oss",
            "prompt": prompt,
            "stream": False,
        }
        resp = self._requests.post(
            url, json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    # ── Backend: Ollama ───────────────────────────────────────────────────────
    def _call_ollama(self, system: str, user: str, max_tokens: int) -> str:
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {
                "temperature":  self.cfg.temperature,
                "num_predict":  max_tokens,
            },
        }
        resp = self._requests.post(
            f"{self.cfg.base_url}/api/chat",
            json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")

    # ── Backend: OpenAI ───────────────────────────────────────────────────────
    def _call_openai(self, system: str, user: str, max_tokens: int) -> str:
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model":       self.cfg.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": self.cfg.temperature,
        }
        resp = self._requests.post(
            f"{self.cfg.base_url}/chat/completions",
            headers=headers, json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── Backend: Azure OpenAI ─────────────────────────────────────────────────
    def _call_azure(self, system: str, user: str, max_tokens: int) -> str:
        headers = {
            "api-key":      self.cfg.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": self.cfg.temperature,
        }
        # Azure URL format: {endpoint}/chat/completions?api-version={version}
        url = f"{self.cfg.base_url}/chat/completions?api-version={self.cfg.api_version}"
        resp = self._requests.post(
            url, headers=headers, json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── Backend: Anthropic ────────────────────────────────────────────────────
    def _call_anthropic(self, system: str, user: str, max_tokens: int) -> str:
        headers = {
            "x-api-key":         self.cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        payload = {
            "model":      self.cfg.model or "claude-3-haiku-20240307",
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        resp = self._requests.post(
            f"{self.cfg.base_url}/v1/messages",
            headers=headers, json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("content", [{}])[0].get("text", "")

    # ── Backend: HuggingFace Inference API ────────────────────────────────────
    def _call_hf(self, system: str, user: str, max_tokens: int) -> str:
        model = self.cfg.model or "Qwen/Qwen2.5-Coder-7B-Instruct"
        url   = f"{self.cfg.base_url}/{model}"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type":  "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_new_tokens": max_tokens,
            "temperature":    self.cfg.temperature,
            "do_sample":      False,
        }
        resp = self._requests.post(
            url, headers=headers, json=payload, timeout=self.cfg.timeout
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

    # ── Backend: OpenAI-compatible (LM Studio, vLLM, Groq, Together, etc.) ───
    def _call_openai_compat(self, system: str, user: str, max_tokens: int) -> str:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        payload: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "max_tokens":  max_tokens,
            "temperature": self.cfg.temperature,
        }
        if self.cfg.model:
            payload["model"] = self.cfg.model
        resp = self._requests.post(
            f"{self.cfg.base_url}/chat/completions",
            headers=headers, json=payload, timeout=self.cfg.timeout
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── Dispatch ──────────────────────────────────────────────────────────────
    def _call(self, system: str, user: str, max_tokens: int) -> str:
        if not self._backend_fn:
            raise RuntimeError(
                f"No LLM backend active. Configure 'llm:' section in your yaml. "
                f"Current backend='{self.cfg.backend}'"
            )
        # Context-window guard: truncate user prompt body if needed
        if len(user) > self.cfg.context_limit:
            user = _truncate(user, self.cfg.context_limit)
        return self._with_retry(self._backend_fn, system, user, max_tokens)

    # ── Public: LLD function generation ──────────────────────────────────────
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
        Raises RuntimeError if backend unavailable and no cache hit.
        """
        key    = _cache_key(reg_name, field_name, change_type, desc)
        cached = self._from_cache(key)
        if cached is not None:
            logger.debug("[LLM] Cache hit: %s.%s (%s)", reg_name, field_name, change_type)
            return cached

        if not self.available:
            raise RuntimeError(
                f"LLM not available. Configure llm: backend in yaml. "
                f"Field: {reg_name}.{field_name} ({change_type})"
            )

        ip_lo    = ip.lower()
        reg_lo   = reg_name.lower()
        field_lo = field_name.lower()

        if not functions_needed:
            fn_prefix = f"lld_{ip_lo}_{reg_lo}_{field_lo}"
            fns = []
            if access in {"RO", "RW", "W1C", "W1S"}:
                fns.append(f"{fn_prefix}_get")
            if access in {"RW", "WO"}:
                fns.append(f"{fn_prefix}_set")
            if access == "W1C":
                fns.append(f"{fn_prefix}_clear")
            if access == "W1S":
                fns.append(f"{fn_prefix}_set1")
            functions_needed = "\n".join(f"  - {f}" for f in fns) or f"  - {fn_prefix}_get"

        system = _SYSTEM_PROMPT.replace("{ip}", ip_lo).replace("{return_type}", return_type)
        user = _USER_PROMPT_TMPL.format(
            ip=ip, ip_lo=ip_lo,
            reg_name=reg_name, field_name=field_name,
            access=access, desc=desc, return_type=return_type,
            change_type=change_type,
            old_code=old_code or "(none — generate fresh)",
            functions_needed=functions_needed,
        )

        logger.info("[LLM-%s] %s.%s (%s) ...", self.cfg.backend.upper(), reg_name, field_name, change_type)
        code = _strip_fences(self._call(system, user, self.cfg.max_tokens))
        if code:
            self._to_cache(key, code)
        return code

    # ── Public: compile-error fix ─────────────────────────────────────────────
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
        return _strip_fences(self._call(system, user, self.cfg.max_tokens))

    # ── Public: unit test generation ──────────────────────────────────────────
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
            logger.debug("[LLM-TEST] Cache hit: %s.%s", reg_name, field_name)
            return cached

        if not self.available:
            return ""

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

        logger.info("[LLM-TEST-%s] %s.%s (%s) ...", self.cfg.backend.upper(), reg_name, field_name, access)
        try:
            code = _strip_fences(self._call(system, user, self.cfg.max_tokens))
            if code:
                self._to_cache(key, code)
            return code
        except Exception as exc:
            logger.warning("[LLM-TEST] Failed: %s -- using template", exc)
            return ""


# ---------------------------------------------------------------------------
# Factory: build LLMClient from yaml config dict (used by batch_runner/workflow)
# ---------------------------------------------------------------------------
def make_llm_client(
    config_data: dict,
    cache_dir:   Optional[Path] = None,
) -> Optional[LLMClient]:
    """
    Build an LLMClient from a parsed yaml config dict.
    Returns None if no_llm=true or backend=none.

    Usage:
        data = yaml.safe_load(open("lld_patcher.yaml"))
        llm  = make_llm_client(data)
    """
    cfg = load_llm_config(config_data)
    if cfg.backend in ("none", ""):
        return None
    return LLMClient(cfg, cache_dir=cache_dir)
