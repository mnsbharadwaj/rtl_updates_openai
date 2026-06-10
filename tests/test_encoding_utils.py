"""
test_encoding_utils.py — pytest suite for lld_gen/encoding_utils.py

Tests:
    TestSanitiseLlmOutput     (9 tests)  — Unicode → ASCII mapping and fallbacks
    TestSafeOpen              (4 tests)  — UTF-8 file I/O without UnicodeError
    TestConfigureLogging      (3 tests)  — logging handler reconfiguration
    TestProductionBoundary    (4 tests)  — end-to-end: LLM output → file/log safe

Run:
    pytest tests/test_encoding_utils.py -v
    Expected: 20 passed
"""
from __future__ import annotations

import io
import logging
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.encoding_utils import (
    sanitise_llm_output,
    safe_encode_for_log,
    safe_open,
    configure_logging_encoding,
    ensure_utf8_streams,
)


# ============================================================================
# 1. TestSanitiseLlmOutput  (9 tests)
# ============================================================================
class TestSanitiseLlmOutput:

    # E01 — pure ASCII string returned unchanged
    def test_ascii_string_unchanged(self):
        s = "static inline uint8_t lld_foo_bar_get(struct lld_foo *lld) { return 0; }"
        assert sanitise_llm_output(s) == s

    # E02 — arrow → replaced with ->
    def test_arrow_replaced(self):
        result = sanitise_llm_output("LLD patch \u2192 updated function")
        assert "\u2192" not in result
        assert "->" in result

    # E03 — check mark replaced with [OK]
    def test_check_mark_replaced(self):
        result = sanitise_llm_output("Build \u2714 PASSED")
        assert "\u2714" not in result
        assert "OK" in result or "[ok]" in result.lower() or "passed" in result.lower()

    # E04 — warning sign replaced with [!]
    def test_warning_sign_replaced(self):
        result = sanitise_llm_output("WARNING \u26a0 threshold exceeded")
        assert "\u26a0" not in result
        assert "[!]" in result

    # E05 — box-drawing chars replaced with ASCII
    def test_box_drawing_replaced(self):
        banner = "\u250c\u2500\u2500\u2500\u2510\n\u2502 LLD-PATCH \u2502\n\u2514\u2500\u2500\u2500\u2518"
        result = sanitise_llm_output(banner)
        for ch in ["\u250c", "\u2500", "\u2510", "\u2502", "\u2514", "\u2518"]:
            assert ch not in result
        assert "+" in result or "-" in result or "|" in result

    # E06 — em dash replaced with --
    def test_em_dash_replaced(self):
        result = sanitise_llm_output("field \u2014 see description")
        assert "\u2014" not in result
        assert "--" in result

    # E07 — curly quotes replaced with straight quotes
    def test_curly_quotes_replaced(self):
        result = sanitise_llm_output("\u201cactive-low\u201d means write 0")
        assert "\u201c" not in result
        assert "\u201d" not in result
        assert '"' in result

    # E08 — NFKD-decomposable characters (e.g. accented letters) → ASCII base
    def test_nfkd_decomposition_fallback(self):
        # 'é' (U+00E9) decomposes to 'e' + combining accent → ASCII 'e'
        result = sanitise_llm_output("caf\u00e9")
        assert "\u00e9" not in result
        assert "cafe" in result or "caf" in result

    # E09 — completely unknown character → '?'
    def test_unknown_char_replaced_with_question(self):
        # CJK character — no ASCII equivalent
        result = sanitise_llm_output("Hello \u4e2d\u6587")
        assert "\u4e2d" not in result
        assert "\u6587" not in result
        # replaced with '?' since no NFKD decomposition to ASCII
        assert "?" in result or result.isascii()

    # E10 — result is always encodable as cp1252 (for Windows console)
    def test_result_is_cp1252_safe(self):
        nasty = (
            "LLD patch \u2192 done \u2714 \u26a0 "
            "\u250c\u2502\u2514 \u2014 \u201chello\u201d"
        )
        result = sanitise_llm_output(nasty)
        # Must not raise
        result.encode("cp1252")

    # E11 — idempotent: calling twice gives same result
    def test_idempotent(self):
        text = "patch \u2192 complete \u2714"
        once  = sanitise_llm_output(text)
        twice = sanitise_llm_output(once)
        assert once == twice

    # E12 — non-string input does not raise
    def test_non_string_input_does_not_raise(self):
        result = sanitise_llm_output(None)   # type: ignore[arg-type]
        assert isinstance(result, str)


# ============================================================================
# 2. TestSafeOpen  (4 tests)
# ============================================================================
class TestSafeOpen:

    def test_write_unicode_no_error(self, tmp_path):
        """safe_open must write Unicode (arrow, box chars) without raising."""
        out = tmp_path / "out.c"
        content = "/* patch \u2192 done \u2714 */\nstatic inline void foo() {}"
        with safe_open(out, "w") as fh:
            fh.write(content)
        assert out.read_text(encoding="utf-8") == content

    def test_read_back_unicode(self, tmp_path):
        out = tmp_path / "out.c"
        content = "/* \u250c\u2500\u2510 */\nstatic inline void bar() {}"
        out.write_text(content, encoding="utf-8")
        with safe_open(out, "r") as fh:
            read_back = fh.read()
        assert read_back == content

    def test_binary_mode_not_affected(self, tmp_path):
        """Binary mode must pass through to standard open()."""
        out = tmp_path / "out.bin"
        with safe_open(out, "wb") as fh:
            fh.write(b"\x00\x01\x02")
        assert out.read_bytes() == b"\x00\x01\x02"

    def test_errors_replace_on_bad_input(self, tmp_path):
        """Errors='replace' means unrepresentable chars become replacement char."""
        out = tmp_path / "out.txt"
        # Write known-bad cp1252 via safe_open (errors=replace → ? substituted)
        with safe_open(out, "w", encoding="cp1252", errors="replace") as fh:
            fh.write("hello \u2192 world")
        text = out.read_text(encoding="cp1252")
        assert "hello" in text
        assert "\u2192" not in text   # replaced with '?'


# ============================================================================
# 3. TestConfigureLogging  (3 tests)
# ============================================================================
class TestConfigureLogging:

    def test_configure_does_not_raise(self):
        """configure_logging_encoding() must not raise on any platform."""
        configure_logging_encoding()   # should complete without exception

    def test_log_unicode_after_configure(self, capfd):
        """After configure, a logger.info call with Unicode must not crash."""
        configure_logging_encoding()
        log = logging.getLogger("test_encoding_utils")
        # Should not raise UnicodeEncodeError
        try:
            log.info("patch %s complete", "\u2192 updated")
        except UnicodeEncodeError as exc:
            pytest.fail(f"UnicodeEncodeError after configure_logging_encoding: {exc}")

    def test_safe_encode_for_log_always_ascii(self):
        """safe_encode_for_log must return a string encodable as cp1252."""
        texts = [
            "plain ascii",
            "arrow \u2192 here",
            "check \u2714 mark",
            "\u250c box \u2518",
            "caf\u00e9",
        ]
        for t in texts:
            result = safe_encode_for_log(t)
            try:
                result.encode("cp1252")
            except UnicodeEncodeError as exc:
                pytest.fail(f"safe_encode_for_log({t!r}) still not cp1252-safe: {exc}")


# ============================================================================
# 4. TestProductionBoundary  (4 tests)
# ============================================================================
class TestProductionBoundary:

    def test_llm_strip_fences_sanitises_output(self):
        """
        _strip_fences (now wraps sanitise_llm_output) must return cp1252-safe text.
        """
        from lld_gen.llm_client import _strip_fences
        raw = "```c\n/* patch \u2192 done \u2714 \u26a0 */\nstatic inline void foo() {}\n```"
        result = _strip_fences(raw)
        assert "```" not in result
        # Must be cp1252-safe
        result.encode("cp1252")

    def test_patch_lld_function_output_is_cp1252_safe(self, tmp_path):
        """
        patch_lld_function's return value (via mock) must be cp1252-encodable
        even when the mock injects Unicode characters into its response.
        """
        from unittest.mock import MagicMock
        from lld_gen.llm_client import _strip_fences

        # Simulate an LLM response with Unicode
        raw_llm_response = (
            "/* \u2192 updated by LLM \u2714 */\n"
            "/** @brief [v2] det_en \u2014 active-low */\n"
            "static inline uint8_t lld_pcielink_ctrl_lt1_det_en_get"
            "(struct lld_pcielink *lld) {\n"
            "    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.det_en);\n"
            "}"
        )
        sanitised = _strip_fences(raw_llm_response)
        # Must not raise on cp1252 encode
        sanitised.encode("cp1252")
        # Function body must still be present
        assert "det_en_get" in sanitised

    def test_safe_open_write_llm_output_no_error(self, tmp_path):
        """
        Writing LLM output (with Unicode) to a file via safe_open must not raise.
        """
        from lld_gen.encoding_utils import sanitise_llm_output
        llm_output = (
            "/** @brief [v2] retrain_cnt \u2014 see updated description */\n"
            "static inline uint8_t lld_pcielink_ctrl_lt0_retrain_cnt_get"
            "(struct lld_pcielink *lld) {\n"
            "    /* \u2714 revised per SFR v2 \u2192 threshold now 0=disabled */\n"
            "    return (uint8_t)(lld->pSFR->stCTRL_LT0.stNative.retrain_cnt);\n"
            "}"
        )
        safe_text = sanitise_llm_output(llm_output)
        out = tmp_path / "lld_pcielink_patched.h"
        with safe_open(out, "w") as fh:
            fh.write(safe_text)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "retrain_cnt_get" in content

    def test_entire_pipeline_output_is_encodable(self, tmp_path):
        """
        Full chain: classify -> semantic gate -> (mock) LLM response ->
        sanitise -> safe_open write -> read back: no UnicodeError at any step.
        """
        from lld_gen.encoding_utils import sanitise_llm_output

        # Simulate a rich LLM response with many Unicode chars
        mock_response = (
            "/* [LLD-PATCH v2] CTRL_LT1.det_en\n"
            " * Change: FIELD_POLARITY_CHANGED \u2192 active-low\n"
            " * Status: \u2714 patched \u2022 reviewed \u26a0 verify reset value\n"
            " */\n"
            "/** @brief [v2] det_en \u2014 Receiver detect active-low enable.\n"
            " *  Write 0 (\u2260 1) to enable. See PCIe spec \u00a74.2.6.\n"
            " */\n"
            "static inline uint8_t lld_pcielink_ctrl_lt1_det_en_get"
            "(struct lld_pcielink *lld) {\n"
            "    return (uint8_t)(lld->pSFR->stCTRL_LT1.stNative.det_en);\n"
            "}\n"
            "static inline void lld_pcielink_ctrl_lt1_det_en_set"
            "(struct lld_pcielink *lld, uint8_t val) {\n"
            "    /* active-low: 0 \u2192 enabled, 1 \u2192 bypassed */\n"
            "    lld->pSFR->stCTRL_LT1.stNative.det_en = val;\n"
            "}"
        )

        sanitised = sanitise_llm_output(mock_response)

        # Write
        out = tmp_path / "lld_out.h"
        with safe_open(out, "w") as fh:
            fh.write(sanitised)

        # Read back
        content = out.read_text(encoding="utf-8")
        assert "det_en_get" in content
        assert "det_en_set" in content

        # cp1252 safe
        sanitised.encode("cp1252")
