"""
encoding_utils.py  --  Safe Unicode handling for cross-platform production use.

Problem
-------
On Windows the default console/file encoding is often cp1252 (or cp949, GBK …).
LLM responses, SFR descriptions, and log messages can contain Unicode characters
(arrows →, check marks ✔, box-drawing ┌─, CJK, etc.) that are not representable
in cp1252.  Writing them to a logger, a file opened without encoding=, or stdout
causes::

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192' …

This module provides three defence layers that should be applied at every
boundary where text crosses from Python into the OS:

    Layer 1 — sanitise_llm_output()
        Call this IMMEDIATELY after receiving text from the LLM backend.
        Replaces non-ASCII chars that are not printable-ASCII with their closest
        ASCII approximation (or '?' if none exists).  This keeps the text
        human-readable while eliminating the crash risk.

    Layer 2 — safe_open()
        A drop-in replacement for open() that always specifies encoding='utf-8'
        with errors='replace'.  Use for all LLD / SFR / report file I/O.

    Layer 3 — configure_logging_encoding()
        Call once at process startup.  Reconfigures every StreamHandler attached
        to the root logger so it uses UTF-8 with errors='replace', regardless of
        the platform locale.

Usage in production code
------------------------
    from lld_gen.encoding_utils import (
        sanitise_llm_output,
        safe_open,
        configure_logging_encoding,
    )

    # At process startup (batch_runner.py / workflow_runner.py main())
    configure_logging_encoding()

    # After every LLM call
    raw = llm_backend.generate(...)
    text = sanitise_llm_output(raw)

    # Every file write
    with safe_open(output_path, 'w') as fh:
        fh.write(text)
"""
from __future__ import annotations

import codecs
import io
import logging
import os
import sys
import unicodedata
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unicode → ASCII approximation table  (hand-picked for LLM/SFR output)
# ---------------------------------------------------------------------------
_UNICODE_ASCII_MAP: dict[int, str] = {
    # Arrows
    0x2192: "->",   # →
    0x2190: "<-",   # ←
    0x21D2: "=>",   # ⇒
    0x2194: "<->",  # ↔
    # Check marks / crosses
    0x2714: "[OK]",   # ✔
    0x2713: "[ok]",   # ✓
    0x2717: "[X]",    # ✗
    0x2718: "[X]",    # ✘
    0x274C: "[X]",    # ❌
    # Warning / info
    0x26A0: "[!]",    # ⚠
    0x24D8: "(i)",    # ⓘ
    # Box-drawing (used in verbose log banners)
    0x2502: "|",      # │
    0x2500: "-",      # ─
    0x250C: "+",      # ┌
    0x2510: "+",      # ┐
    0x2514: "+",      # └
    0x2518: "+",      # ┘
    0x251C: "+",      # ├
    0x2524: "+",      # ┤
    0x252C: "+",      # ┬
    0x2534: "+",      # ┴
    0x253C: "+",      # ┼
    # Bullets / markers
    0x2022: "*",      # •
    0x25CF: "*",      # ●
    0x25CB: "o",      # ○
    0x25A0: "#",      # ■
    # Typography
    0x2014: "--",     # — (em dash)
    0x2013: "-",      # – (en dash)
    0x2018: "'",      # ' (left single quote)
    0x2019: "'",      # ' (right single quote)
    0x201C: '"',      # " (left double quote)
    0x201D: '"',      # " (right double quote)
    0x2026: "...",    # … (ellipsis)
    0x00B7: ".",      # · (middle dot)
    0x00D7: "x",      # × (multiplication sign)
    0x00F7: "/",      # ÷ (division sign)
    0x2248: "~=",     # ≈
    0x2260: "!=",     # ≠
    0x2264: "<=",     # ≤
    0x2265: ">=",     # ≥
}


def sanitise_llm_output(text: str, replacement: str = "?") -> str:
    """
    Replace non-ASCII Unicode characters with safe ASCII equivalents.

    Characters are handled in priority order:
    1. Characters in _UNICODE_ASCII_MAP  → mapped to ASCII string
    2. Characters with a Unicode 'NFKD' decomposition that is ASCII → use it
    3. Everything else → ``replacement`` (default '?')

    The function is **idempotent** and never raises.

    Args:
        text:        Raw string from the LLM (or any external source).
        replacement: Fallback for characters with no known ASCII equivalent.

    Returns:
        A string containing only printable ASCII characters (0x20–0x7E) plus
        whitespace (\\t, \\n, \\r).
    """
    if not isinstance(text, str):
        return str(text)

    # Fast path: already ASCII-safe
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        pass

    result: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            result.append(ch)
            continue
        # Table lookup
        if code in _UNICODE_ASCII_MAP:
            result.append(_UNICODE_ASCII_MAP[code])
            continue
        # NFKD decomposition — e.g. é → e + combining-accent → 'e'
        decomposed = unicodedata.normalize("NFKD", ch)
        ascii_only = decomposed.encode("ascii", errors="ignore").decode()
        if ascii_only:
            result.append(ascii_only)
        else:
            result.append(replacement)

    return "".join(result)


def safe_encode_for_log(text: str) -> str:
    """
    Lightweight version of sanitise_llm_output for use in logger format strings.
    Only replaces characters that would crash cp1252; leaves everything else alone.
    This is cheaper than full sanitisation and preserves UTF-8 content when
    the logging handler is already UTF-8 capable.
    """
    try:
        text.encode("cp1252")
        return text
    except (UnicodeEncodeError, LookupError):
        return sanitise_llm_output(text)


def safe_open(
    path: str | Path,
    mode: str = "r",
    encoding: str = "utf-8",
    errors: str = "replace",
    **kwargs,
):
    """
    Drop-in replacement for open() that always specifies encoding='utf-8'
    with errors='replace', preventing UnicodeDecodeError / UnicodeEncodeError
    on Windows regardless of the system locale.

    Usage:
        with safe_open(output_path, 'w') as fh:
            fh.write(llm_output)
    """
    # Binary modes don't take encoding
    if "b" in mode:
        return open(path, mode, **kwargs)
    return open(path, mode, encoding=encoding, errors=errors, **kwargs)


def configure_logging_encoding() -> None:
    """
    Reconfigure every StreamHandler on the root logger to use UTF-8 with
    errors='replace'.  Call once at process startup (before any log messages).

    This prevents UnicodeEncodeError when logger.info() emits Unicode text
    to a Windows console that uses cp1252 (or another narrow encoding).

    Safe to call multiple times — already-patched handlers are skipped.
    """
    root = logging.getLogger()
    _patched = 0
    for handler in root.handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue
        stream = handler.stream
        if not hasattr(stream, "reconfigure"):
            # Wrap the stream in a TextIOWrapper with UTF-8 + replace
            try:
                raw = stream.buffer if hasattr(stream, "buffer") else None
                if raw is not None:
                    handler.stream = io.TextIOWrapper(
                        raw,
                        encoding="utf-8",
                        errors="replace",
                        line_buffering=True,
                    )
                    _patched += 1
            except Exception:
                pass
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                _patched += 1
            except Exception:
                pass
    if _patched:
        logger.debug(
            "[encoding_utils] Reconfigured %d logging handler(s) to utf-8/replace",
            _patched,
        )


def ensure_utf8_streams() -> None:
    """
    Reconfigure sys.stdout and sys.stderr to UTF-8 with errors='replace'.
    Call at process entry point to make print() Unicode-safe on Windows.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        elif hasattr(stream, "buffer"):
            try:
                wrapped = io.TextIOWrapper(
                    stream.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
                setattr(sys, name, wrapped)
            except Exception:
                pass
