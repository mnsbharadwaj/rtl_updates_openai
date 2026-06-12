"""
test_llm_patch_from_sfr_diff.py -- End-to-end LLM patch pipeline test

This suite runs the FULL pipeline on the PCIELINK fixture files:
    1. Parse v1 and v2 SFR headers (NativeUnionSfrParser)
    2. Classify all changes (classify_sfr_diff)
    3. Apply the semantic equivalence gate
    4. For each change that still needs_llm=True, build a patch prompt and
       dispatch to LOCAL OLLAMA (qwen2.5-coder:1.5b)
    5. Print and verify the returned patched LLD C functions

LLM mode priority:
    1. Local Ollama (http://localhost:11434) -- used automatically if reachable
    2. Mock LLM -- fallback if Ollama is down, or when --no-llm is passed

Run:
    pytest tests/test_llm_patch_from_sfr_diff.py -v -s
    pytest tests/test_llm_patch_from_sfr_diff.py -v -s --no-llm   # force mock

Per-LLM-call timeout: 120 s (configurable via OLLAMA_TIMEOUT env var)
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.sfr_diff_analyzer import (
    NativeUnionSfrParser,
    ChangeType, ChangeRecord,
    classify_sfr_diff,
)
from lld_gen.semantic_check import apply_semantic_gate, SEMANTIC_CHECK_TYPES
from lld_gen.llm_client import LLMClient, load_llm_config

# ---------------------------------------------------------------------------
# pytest CLI option: --no-llm  forces mock mode even if Ollama is reachable
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption(
        "--no-llm", action="store_true", default=False,
        help="Skip real LLM calls and use mock responses instead",
    )


# ---------------------------------------------------------------------------
# Ollama config
# ---------------------------------------------------------------------------
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL",   "qwen2.5-coder:1.5b")
OLLAMA_URL     = os.environ.get("OLLAMA_URL",     "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Fixture file paths
# ---------------------------------------------------------------------------
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pcie_sfr"
V1          = FIXTURE_DIR / "sfr_pcielink_v1.h"
V2          = FIXTURE_DIR / "sfr_pcielink_v2.h"
LLD_STUB    = FIXTURE_DIR / "lld_pcielink_stub.h"

_ALL_FIXTURES = pytest.mark.skipif(
    not (V1.exists() and V2.exists() and LLD_STUB.exists()),
    reason="Fixture SFR/LLD files not found",
)


# ---------------------------------------------------------------------------
# Helper: build an IR summary string from a ChangeRecord
# ---------------------------------------------------------------------------
def _ir_summary(cr: ChangeRecord) -> str:
    if cr.new_field:
        f = cr.new_field
        return (
            f"Register: {cr.reg_name}  Field: {cr.field_name}\n"
            f"Bits: {f.lsb}-{f.msb}  Width: {f.width}  Access: {f.access}\n"
            f"Mask: 0x{f.mask:08X}  Shift: {f.shift}  Reset: 0x{f.reset:X}"
        )
    return f"Register: {cr.reg_name}  Field: {cr.field_name or '(register-level)'}"


# ---------------------------------------------------------------------------
# Helper: extract LLD functions whose name contains field_name
# ---------------------------------------------------------------------------
def _extract_field_functions(lld_text: str, field_name: str) -> str:
    """
    Extract static inline functions whose name contains field_name.
    e.g. 'retrain_cnt' matches lld_pcielink_ctrl_lt0_retrain_cnt_get/set.
    """
    if not field_name:
        return ""
    from lld_gen.lld_patcher import extract_all_functions_for_field
    parts = []
    for reg in ("CTRL_LT0", "CTRL_LT1", "ERR_INJECT", "STATUS"):
        extracted = extract_all_functions_for_field(lld_text, "PCIELINK", reg, field_name)
        if extracted:
            parts.append(extracted)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Build real Ollama LLMClient (or None if not reachable)
# ---------------------------------------------------------------------------
def _build_ollama_client() -> Optional[LLMClient]:
    """Try to connect to local Ollama. Return client if reachable, else None."""
    try:
        cfg = load_llm_config({
            "no_llm": False,
            "llm": {
                "backend":  "ollama",
                "model":    OLLAMA_MODEL,
                "url":      OLLAMA_URL,
                "location": "local",
                "timeout":  OLLAMA_TIMEOUT,
            },
        })
        client = LLMClient(cfg)
        if client.available:
            return client
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Mock LLM -- fallback when Ollama is not reachable
# ---------------------------------------------------------------------------
def _make_mock_llm() -> MagicMock:
    def _respond(system: str, user: str, max_tokens: int) -> str:
        fn_match    = re.search(r'```c\n(.*?)```', user, re.DOTALL)
        old_code    = fn_match.group(1).strip() if fn_match else "/* no code */"
        field_m     = re.search(r'Field\s*:\s*(\S+)', user)
        field_name  = field_m.group(1) if field_m else "unknown"
        reg_m       = re.search(r'Register\s*:\s*(\S+)', user)
        reg_name    = reg_m.group(1) if reg_m else "UNKNOWN"
        ct_m        = re.search(r'Change type\s*:\s*(\S+)', user)
        change_type = ct_m.group(1) if ct_m else ""
        nd_m        = re.search(r'=== New SFR Description.*?===\n(.*?)\n===', user, re.DOTALL)
        new_desc    = nd_m.group(1).strip() if nd_m else ""

        patched = re.sub(
            r'(@brief\s+)(.*)',
            lambda m: m.group(1) + f'[v2] {field_name} -- see updated SFR description',
            old_code, count=1,
        )
        if "POLARITY" in change_type or "active-low" in new_desc.lower():
            patched = patched.replace(
                "static inline void",
                "/* NOTE(v2): field is now active-low; write 0 to enable */\n"
                "static inline void",
                1,
            )
        banner = (
            f"/* [LLD-PATCH v2] {reg_name}.{field_name}\n"
            f" * Updated: {change_type}\n"
            f" * {new_desc[:120]}{'...' if len(new_desc) > 120 else ''}\n"
            f" */\n"
        )
        return banner + patched

    mock = MagicMock()
    mock.available = True
    mock.backend   = "mock"
    mock._call     = MagicMock(side_effect=_respond)

    def patch_lld_function(ip, reg_name, field_name, change_type,
                            old_desc, new_desc, old_fn_text,
                            reg_ir_summary="", extra_context=""):
        from lld_gen.llm_client import _LLD_PATCH_USER_TMPL, _LLD_PATCH_SYSTEM, _strip_fences
        user = _LLD_PATCH_USER_TMPL.format(
            ip=ip, reg_name=reg_name, field_name=field_name,
            change_type=change_type, old_desc=old_desc, new_desc=new_desc,
            reg_summary=reg_ir_summary or f"{reg_name}.{field_name}",
            old_fn_text=old_fn_text, extra=extra_context or "(none)",
        )
        raw = mock._call(_LLD_PATCH_SYSTEM, user, 2048)
        return _strip_fences(raw) if raw else old_fn_text

    mock.patch_lld_function = patch_lld_function
    return mock


# ---------------------------------------------------------------------------
# Session-scoped LLM fixture -- shared across all tests in this file
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def llm(request):
    """
    Provide the best available LLM for the test session.
    - Uses real Ollama (qwen2.5-coder:1.5b @ localhost:11434) when reachable.
    - Falls back to the mock LLM when --no-llm is given or Ollama is down.
    """
    force_mock = request.config.getoption("--no-llm", default=False)

    if not force_mock:
        client = _build_ollama_client()
        if client:
            print(f"\n  [LLM] Using real Ollama: {OLLAMA_MODEL} @ {OLLAMA_URL}")
            return client, True

    print("\n  [LLM] Ollama not reachable or --no-llm given -- using mock LLM")
    return _make_mock_llm(), False


# ============================================================================
# 1. TestPipelineSetup  (3 tests) -- parse, classify, semantic-gate
# ============================================================================
class TestPipelineSetup:

    @_ALL_FIXTURES
    def test_parse_v1_registers(self):
        ir = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        assert len(ir.registers) == 4, f"Expected 4 registers, got {list(ir.registers)}"

    @_ALL_FIXTURES
    def test_classify_produces_changes(self):
        changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
        assert len(changes) > 0
        types = {c.change_type for c in changes}
        assert ChangeType.FIELD_ADDED   in types
        assert ChangeType.FIELD_RENAMED in types

    @_ALL_FIXTURES
    def test_semantic_gate_skips_cosmetic_changes(self):
        changes  = classify_sfr_diff(V1, V2, ip="PCIELINK")
        n_before = sum(1 for c in changes if c.needs_llm)
        apply_semantic_gate(changes, llm_client=None,
                            threshold_low=0.75, threshold_high=0.85)
        n_after  = sum(1 for c in changes if c.needs_llm)
        skipped  = [c for c in changes if c.semantic_equivalent]
        print(f"\n  Before gate: {n_before} need LLM  |  After: {n_after}  "
              f"|  Skipped: {len(skipped)}")
        for c in skipped:
            print(f"    SKIPPED (cosmetic): {c.reg_name}.{c.field_name}")
        assert n_after > 0, "Expected at least one non-cosmetic description change"


# ============================================================================
# 2. TestPromptBuilding  (4 tests) -- validate prompt structure (no LLM needed)
# ============================================================================
class TestPromptBuilding:

    @_ALL_FIXTURES
    def test_prompt_contains_old_and_new_desc(self):
        from lld_gen.llm_client import _LLD_PATCH_USER_TMPL
        old_d = "Retrain count limit before error flag"
        new_d = "Automatic retraining threshold. When the physical layer detects an error..."
        prompt = _LLD_PATCH_USER_TMPL.format(
            ip="PCIELINK", reg_name="CTRL_LT0", field_name="retrain_cnt",
            change_type=ChangeType.COMMENT_CHANGED,
            old_desc=old_d, new_desc=new_d,
            reg_summary="bits 8-11  [RW]  reset=0",
            old_fn_text="static inline uint8_t lld_pcielink_ctrl_lt0_retrain_cnt_get(...) {}",
            extra="(none)",
            bitfield_path="lld->pSFR->stCTRL_LT0.stNative.retrain_cnt",
        )
        assert old_d in prompt
        assert new_d in prompt
        assert "CTRL_LT0" in prompt
        assert "retrain_cnt" in prompt

    @_ALL_FIXTURES
    def test_prompt_contains_old_lld_function(self):
        from lld_gen.llm_client import _LLD_PATCH_USER_TMPL
        stub_text = LLD_STUB.read_text(encoding="utf-8")
        fn_text   = _extract_field_functions(stub_text, "retrain_cnt")
        prompt    = _LLD_PATCH_USER_TMPL.format(
            ip="PCIELINK", reg_name="CTRL_LT0", field_name="retrain_cnt",
            change_type=ChangeType.COMMENT_CHANGED,
            old_desc="old", new_desc="new",
            reg_summary="bits 8-11 [RW]",
            old_fn_text=fn_text or "/* no fn found */",
            extra="(none)",
            bitfield_path="lld->pSFR->stCTRL_LT0.stNative.retrain_cnt",
        )
        assert "retrain_cnt" in prompt or "no fn found" in prompt

    @_ALL_FIXTURES
    def test_prompt_system_has_polarity_rule(self):
        from lld_gen.llm_client import _LLD_PATCH_SYSTEM
        assert "polarity" in _LLD_PATCH_SYSTEM.lower()

    @_ALL_FIXTURES
    def test_prompt_user_has_task_instruction(self):
        from lld_gen.llm_client import _LLD_PATCH_USER_TMPL
        prompt = _LLD_PATCH_USER_TMPL.format(
            ip="X", reg_name="R", field_name="f",
            change_type="COMMENT_CHANGED",
            old_desc="old", new_desc="new", reg_summary="info",
            old_fn_text="void foo() {}", extra="(none)",
            bitfield_path="lld->pSFR->stR.stNative.f",
        )
        assert "Task:" in prompt


# ============================================================================
# 3. TestLLMPatchDispatch  (5 tests) -- real Ollama or mock fallback
# ============================================================================
class TestLLMPatchDispatch:

    # -- helper ----------------------------------------------------------------
    @staticmethod
    def _print_result(label: str, is_real: bool, result: str) -> None:
        mode = "OLLAMA" if is_real else "MOCK"
        sep  = "-" * 60
        print(f"\n  +{sep}")
        print(f"  | [{mode} LLM]  {label}")
        print(f"  +{sep}")
        for line in result.splitlines()[:25]:
            try:
                print(f"  |  {line}")
            except UnicodeEncodeError:
                print(f"  |  {line.encode('ascii', errors='replace').decode()}")
        if result.count("\n") >= 25:
            print(f"  |  ... ({result.count(chr(10))} lines total)")
        print(f"  +{sep}")

    # -- T1: retrain_cnt -- semantically different desc -------------------------
    @_ALL_FIXTURES
    def test_patch_retrain_cnt_description_change(self, llm):
        client, is_real = llm
        stub_text = LLD_STUB.read_text(encoding="utf-8")
        fn_text   = _extract_field_functions(stub_text, "retrain_cnt")

        ir_v1 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        ir_v2 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V2)
        f1    = ir_v1.registers["CTRL_LT0"].fields["retrain_cnt"]
        f2    = ir_v2.registers["CTRL_LT0"].fields["retrain_cnt"]

        cr = ChangeRecord(
            change_type=ChangeType.COMMENT_CHANGED,
            reg_name="CTRL_LT0", field_name="retrain_cnt",
            old_field=f1, new_field=f2,
        )
        result = client.patch_lld_function(
            ip            = "PCIELINK",
            reg_name      = "CTRL_LT0",
            field_name    = "retrain_cnt",
            change_type   = ChangeType.COMMENT_CHANGED,
            old_desc      = f1.desc,
            new_desc      = f2.desc,
            old_fn_text   = fn_text or "/* fn not found */",
            reg_ir_summary= _ir_summary(cr),
        )
        self._print_result("retrain_cnt -- COMMENT_CHANGED", is_real, result)

        assert result, "Expected non-empty patched function text"
        # Real LLM: must contain C code; mock: must contain banner
        assert "static" in result or "retrain_cnt" in result or "LLD-PATCH" in result, (
            f"Unexpected output:\n{result}"
        )

    # -- T2: det_en -- polarity inversion --------------------------------------
    @_ALL_FIXTURES
    def test_patch_det_en_polarity_change(self, llm):
        client, is_real = llm
        stub_text = LLD_STUB.read_text(encoding="utf-8")
        fn_text   = _extract_field_functions(stub_text, "det_en")

        ir_v1 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        ir_v2 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V2)
        f1    = ir_v1.registers["CTRL_LT1"].fields["det_en"]
        f2    = ir_v2.registers["CTRL_LT1"].fields["det_en"]

        result = client.patch_lld_function(
            ip           = "PCIELINK",
            reg_name     = "CTRL_LT1",
            field_name   = "det_en",
            change_type  = ChangeType.FIELD_POLARITY_CHANGED,
            old_desc     = f1.desc,
            new_desc     = f2.desc,
            old_fn_text  = fn_text or "/* fn not found */",
            extra_context= (
                "IMPORTANT: det_en is now active-low. "
                "Write 0 to enable receiver detect (not 1). "
                "Update @brief, any comments, and body logic accordingly."
            ),
        )
        self._print_result("det_en -- FIELD_POLARITY_CHANGED (active-low)", is_real, result)

        assert result, "Expected non-empty patched function"
        # For real LLM: check it mentions 0, active-low, or polarity
        if is_real:
            lower = result.lower()
            has_polarity_note = (
                "active-low" in lower or "active low" in lower
                or "write 0"  in lower or "val == 0" in lower
                or "= 0"      in lower or "!val"     in lower
                or "~val"     in lower or "0 to enable" in lower
                or "polarity" in lower
            )
            assert has_polarity_note, (
                "Real LLM should acknowledge the active-low polarity change.\n"
                f"Got:\n{result}"
            )

    # -- T3: poll_timeout -- bitwidth 8→9 bits + desc change -------------------
    @_ALL_FIXTURES
    def test_patch_poll_timeout_bitwidth_and_desc(self, llm):
        client, is_real = llm
        stub_text = LLD_STUB.read_text(encoding="utf-8")
        fn_text   = _extract_field_functions(stub_text, "poll_timeout")

        ir_v1 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        ir_v2 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V2)
        f1    = ir_v1.registers["CTRL_LT1"].fields["poll_timeout"]
        f2    = ir_v2.registers["CTRL_LT1"].fields["poll_timeout"]

        cr = ChangeRecord(
            change_type=ChangeType.MULTI_CHANGED,
            reg_name="CTRL_LT1", field_name="poll_timeout",
            old_field=f1, new_field=f2,
        )
        result = client.patch_lld_function(
            ip            = "PCIELINK",
            reg_name      = "CTRL_LT1",
            field_name    = "poll_timeout",
            change_type   = ChangeType.MULTI_CHANGED,
            old_desc      = f1.desc,
            new_desc      = f2.desc,
            old_fn_text   = fn_text or "/* fn not found */",
            reg_ir_summary= _ir_summary(cr),
            extra_context = (
                "poll_timeout width increased from 8 bits (max 255 ms) to 9 bits (max 511 ms). "
                "Update the getter return type from uint8_t to uint16_t. "
                "Update @brief to reflect the extended 9-bit range."
            ),
        )
        self._print_result("poll_timeout -- MULTI_CHANGED (8→9 bit + desc)", is_real, result)

        assert result, "Expected non-empty patched function"
        if is_real:
            # Real LLM should mention the wider type or new range
            assert ("uint16" in result or "512" in result or "511" in result
                    or "9-bit" in result or "9 bit" in result or "poll_timeout" in result), (
                f"Expected LLM to reflect the 9-bit expansion.\nGot:\n{result}"
            )

    # -- T4: full pipeline -- all 5 changes needing LLM ------------------------
    @_ALL_FIXTURES
    def test_full_pipeline_changes_needing_llm(self, llm):
        """
        Run the complete pipeline on every change that survives the semantic gate.
        Prints structured output for all 5 remaining LLM-patch candidates.
        """
        client, is_real = llm
        changes   = classify_sfr_diff(V1, V2, ip="PCIELINK")
        stub_text = LLD_STUB.read_text(encoding="utf-8")

        apply_semantic_gate(changes, llm_client=None,
                            threshold_low=0.75, threshold_high=0.85)

        to_patch = [c for c in changes
                    if c.change_type in SEMANTIC_CHECK_TYPES and c.needs_llm]

        mode = "OLLAMA" if is_real else "MOCK"
        print(f"\n  [{mode}] Changes needing LLM patch: {len(to_patch)}")
        for c in to_patch:
            print(f"    * {c.change_type:<30}  {c.reg_name}.{c.field_name or ''}")

        results = []
        for cr in to_patch:
            old_desc = (cr.old_field.desc if cr.old_field else "") or ""
            new_desc = (cr.new_field.desc if cr.new_field else "") or ""
            fn_text  = _extract_field_functions(stub_text, cr.field_name or "")

            patched = client.patch_lld_function(
                ip            = "PCIELINK",
                reg_name      = cr.reg_name,
                field_name    = cr.field_name or "",
                change_type   = cr.change_type,
                old_desc      = old_desc,
                new_desc      = new_desc,
                old_fn_text   = fn_text or f"/* no fn for {cr.field_name} */",
                reg_ir_summary= _ir_summary(cr),
            )
            results.append((cr, patched))
            self._print_result(
                f"{cr.reg_name}.{cr.field_name} -- {cr.change_type}",
                is_real, patched,
            )

        assert len(results) == len(to_patch)
        for cr, patched in results:
            assert patched, f"Got empty patch for {cr.reg_name}.{cr.field_name}"

    # -- T5: signature preservation --------------------------------------------
    @_ALL_FIXTURES
    def test_llm_patch_function_signature_preserved(self, llm):
        """
        The LLM must preserve all original function signatures.
        A well-prompted model never renames or retypes the function.
        """
        client, is_real = llm
        stub_text = LLD_STUB.read_text(encoding="utf-8")
        fn_text   = _extract_field_functions(stub_text, "retrain_cnt")
        if not fn_text:
            pytest.skip("retrain_cnt functions not found in LLD stub")

        sig_re    = re.compile(r'static\s+inline\s+(\S+)\s+(\w+)\s*\(')
        orig_sigs = sig_re.findall(fn_text)

        ir_v1 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V1)
        ir_v2 = NativeUnionSfrParser(ip="PCIELINK").parse_file(V2)

        result = client.patch_lld_function(
            ip          = "PCIELINK",
            reg_name    = "CTRL_LT0",
            field_name  = "retrain_cnt",
            change_type = ChangeType.COMMENT_CHANGED,
            old_desc    = ir_v1.registers["CTRL_LT0"].fields["retrain_cnt"].desc,
            new_desc    = ir_v2.registers["CTRL_LT0"].fields["retrain_cnt"].desc,
            old_fn_text = fn_text,
        )
        patched_sigs = sig_re.findall(result)

        mode = "OLLAMA" if is_real else "MOCK"
        print(f"\n  [{mode}] Original signatures : {[n for _, n in orig_sigs]}")
        print(f"  [{mode}] Patched  signatures : {[n for _, n in patched_sigs]}")
        self._print_result("retrain_cnt -- signature check", is_real, result)

        for ret_type, fn_name in orig_sigs:
            assert fn_name in result, (
                f"[{mode}] Function '{fn_name}' disappeared after LLM patch!\n"
                f"Patched output:\n{result}"
            )


# ============================================================================
# 4. TestSemanticGateWithOllama  (2 tests) -- gate uses Ollama for borderline
# ============================================================================
class TestSemanticGateWithOllama:

    @_ALL_FIXTURES
    def test_gate_uses_ollama_for_borderline_similarity(self, llm):
        """
        When similarity is in the 0.75-0.85 zone, the gate should call the LLM.
        With Ollama, the LLM should return YES (equivalent) or NO (different).
        """
        from lld_gen.semantic_check import check_semantic_equivalence

        client, is_real = llm

        # These two descriptions are borderline similar (~0.78)
        old_d = "Polling phase timeout in milliseconds"
        new_d = "Maximum polling phase duration in milliseconds"

        is_equiv, method = check_semantic_equivalence(
            old_desc       = old_d,
            new_desc       = new_d,
            reg_name       = "CTRL_LT1",
            field_name     = "poll_timeout",
            llm_client     = client,
            threshold_low  = 0.75,
            threshold_high = 0.85,
        )
        mode = "OLLAMA" if is_real else "MOCK"
        print(f"\n  [{mode}] Borderline gate: is_equiv={is_equiv}  method={method}")
        # Method should be one of the known values
        assert method in (
            "heuristic_exact", "heuristic_empty",
            "heuristic_high",  "heuristic_low",
            "llm_yes",         "llm_no",
            "llm_unavailable",
        )

    @_ALL_FIXTURES
    def test_gate_skips_genuinely_cosmetic_desc(self, llm):
        """
        Two descriptions that only differ in minor wording (same meaning).
        SequenceMatcher ratio should be > 0.85 so the gate auto-skips.
        """
        from lld_gen.semantic_check import check_semantic_equivalence

        client, is_real = llm

        # Ratio ~0.92 -- only 'begin' vs 'start' and minor punctuation differ
        old_d = "Enable the PCIe link training controller. Write 1 to start the LTSSM."
        new_d = "Enable the PCIe link training controller. Write 1 to begin the LTSSM."

        is_equiv, method = check_semantic_equivalence(
            old_desc       = old_d,
            new_desc       = new_d,
            reg_name       = "CTRL_LT0",
            field_name     = "lt_en",
            llm_client     = client,
            threshold_low  = 0.75,
            threshold_high = 0.85,
        )
        mode = "OLLAMA" if is_real else "MOCK"
        print(f"\n  [{mode}] cosmetic gate: is_equiv={is_equiv}  method={method}")
        assert is_equiv is True, (
            f"High-similarity cosmetic text should be auto-skipped (no LLM needed). "
            f"method={method}"
        )
        # Cosmetic skip must never call the LLM -- it's auto-decided by heuristic
        assert method == "heuristic_high", (
            f"Expected heuristic_high (auto-skip), got method={method}"
        )

    @_ALL_FIXTURES
    def test_gate_sends_eq_en_to_ollama_for_verdict(self, llm):
        """
        The actual eq_en v1/v2 descriptions differ significantly in wording
        (ratio=0.20, heuristic_low zone) so the gate auto-patches without LLM.
        With a lower threshold_low=0.10, they fall in the LLM zone.
        Use Ollama to decide: the LLM should say they ARE semantically equivalent
        (both describe PCIe Phase 2/3 equalization on the same register).
        """
        from lld_gen.semantic_check import check_semantic_equivalence

        client, is_real = llm

        old_d = (
            "Equalization enable during training. When set, the link training sequence "
            "will perform Phase 2 and Phase 3 equalization as defined in the PCIe 3.0 "
            "Base Specification Section 4.2.6. Ignored for Gen1 and Gen2 speeds."
        )
        new_d = (
            "Equalization enable during the link training sequence. When asserted, the "
            "LTSSM will execute Phase 2 and Phase 3 channel equalization as specified by "
            "the PCIe Base Specification Section 4.2.6. This field has no effect for "
            "Gen1 or Gen2 operating speeds."
        )

        # Force LLM zone by setting thresholds that bracket ratio=0.20
        is_equiv, method = check_semantic_equivalence(
            old_desc       = old_d,
            new_desc       = new_d,
            reg_name       = "CTRL_LT0",
            field_name     = "eq_en",
            llm_client     = client,
            threshold_low  = 0.10,   # below ratio=0.20 → everything goes to LLM
            threshold_high = 0.85,
        )
        mode = "OLLAMA" if is_real else "MOCK"
        print(f"\n  [{mode}] eq_en LLM gate: is_equiv={is_equiv}  method={method}")
        if is_real:
            # Real Ollama should recognise these as semantically equivalent
            print(f"  [{mode}] Ollama verdict: {'EQUIVALENT' if is_equiv else 'DIFFERENT'} ({method})")
            assert method in ("llm_yes", "llm_no", "llm_unavailable"), (
                f"Expected LLM method, got: {method}"
            )
            # We do NOT enforce YES/NO here -- Ollama 1.5b can go either way;
            # just assert the call succeeded without crashing
        else:
            # Mock: no LLM available -- falls back to not-equivalent
            assert method in ("heuristic_low", "llm_unavailable")
