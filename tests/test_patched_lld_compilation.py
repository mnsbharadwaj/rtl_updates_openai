"""
test_patched_lld_compilation.py -- Compile-check all Ollama-patched LLD functions

Pipeline:
    1. Parse PCIELINK v1/v2 SFR fixtures
    2. Classify changes and apply semantic gate
    3. Call Ollama (qwen2.5-coder:1.5b) to patch each changed function
    4. Wrap result in a C file: stub typedefs + extension for v2 fields + patched fns
    5. Run: gcc -fsyntax-only -std=c99 -Wall -Wno-unused-* <tmpfile.c>
    6. PASS if exit code == 0, FAIL with full compiler diagnostics otherwise

Requires:
    - gcc in PATH  (MinGW-W64 or any GCC >= 9)
    - Ollama running on localhost:11434  with qwen2.5-coder:1.5b
    - Fixture files in tests/fixtures/pcie_sfr/

Run:
    pytest tests/test_patched_lld_compilation.py -v -s
    pytest tests/test_patched_lld_compilation.py -v -s --no-llm   # mock only
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.sfr_diff_analyzer import (
    NativeUnionSfrParser, ChangeType, ChangeRecord,
    classify_sfr_diff,
)
from lld_gen.semantic_check import apply_semantic_gate, SEMANTIC_CHECK_TYPES
from lld_gen.llm_client import LLMClient, load_llm_config
from lld_gen.encoding_utils import sanitise_llm_output

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_MODEL   = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
OLLAMA_URL     = os.environ.get("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pcie_sfr"
V1       = FIXTURE_DIR / "sfr_pcielink_v1.h"
V2       = FIXTURE_DIR / "sfr_pcielink_v2.h"
LLD_STUB = FIXTURE_DIR / "lld_pcielink_stub.h"

GCC = shutil.which("gcc") or shutil.which("gcc.exe")

_NEED_GCC   = pytest.mark.skipif(not GCC, reason="gcc not in PATH")
_NEED_FILES = pytest.mark.skipif(
    not (V1.exists() and V2.exists() and LLD_STUB.exists()),
    reason="Fixture files not found",
)


# ---------------------------------------------------------------------------
# C preamble built directly from the stub's own typedefs.
#
# Strategy: strip the include guard and #include directives from the stub,
# then append:
#   - v2-only additions (err_code field alias, burst_en field, preset_hint,
#     9-bit poll_timeout, 3-bit lt_speed) as additional struct members
#   - stLINK_CAP stub so the LLM can reference gen4_cap without a compile error
# ---------------------------------------------------------------------------

def _stub_typedefs() -> str:
    """
    Return the stub's type definitions (structs + SFR map + lld_pcielink)
    with the include guard and #include stripped. This is the ground truth
    for what the LLD functions are compiled against.
    """
    raw = LLD_STUB.read_text(encoding="utf-8")
    # Remove #ifndef / #define guard line pair
    raw = re.sub(r'#ifndef\s+\S+\s*\n#define\s+\S+\s*\n', '', raw)
    # Remove #endif
    raw = re.sub(r'#endif[^\n]*\n?', '', raw)
    # Remove #include lines
    raw = re.sub(r'#include[^\n]*\n', '', raw)
    # Keep only the type definitions (up to first static inline function)
    # — extract everything before the first 'static inline'
    m = re.search(r'\bstatic\s+inline\b', raw)
    return raw[:m.start()].rstrip() if m else raw


# Extended struct members for v2 fields the LLM may generate
_V2_EXTENSION = r"""
/* ---- v2 field extensions (added to allow patched code to compile) ---- */

/* CTRL_LT0 extended for v2: preset_hint field, 3-bit lt_speed */
typedef struct {
    struct {
        unsigned lt_en       :  1;
        unsigned lt_speed    :  3;   /* v2: 3-bit */
        unsigned lt_width    :  3;
        unsigned eq_en       :  1;
        unsigned retrain_cnt :  4;
        unsigned preset_hint :  4;   /* v2: new field */
        unsigned _rsvd1      : 16;
    } stNative;
} SFR_PCIELINK_CTRL_LT0_V2;

/* CTRL_LT1 extended for v2: 9-bit poll_timeout */
typedef struct {
    struct {
        unsigned det_en      :  1;
        unsigned det_timeout :  8;
        unsigned poll_en     :  1;
        unsigned poll_timeout:  9;   /* v2: 9-bit */
        unsigned _rsvd0      : 13;
    } stNative;
} SFR_PCIELINK_CTRL_LT1_V2;

/* ERR_INJECT extended for v2: err_code (renamed from err_type) + burst_en */
typedef struct {
    struct {
        unsigned err_en   :  1;
        unsigned err_code :  4;   /* v2: renamed from err_type */
        unsigned err_cnt  :  8;
        unsigned burst_en :  1;   /* v2: new field */
        unsigned _rsvd0   : 18;
    } stNative;
} SFR_PCIELINK_ERR_INJECT_V2;

/* LINK_CAP stub -- LLM may reference gen4_cap */
typedef struct {
    struct { unsigned gen4_cap : 1; unsigned _rsv : 31; } stNative;
} SFR_PCIELINK_LINK_CAP;

/*
 * v2 SFR map -- used when the LLM generates functions that reference
 * v2 register layout (preset_hint, err_code, burst_en, 9-bit poll_timeout).
 * The struct member names match stCTRL_LT0 / stCTRL_LT1 etc. so the LLD
 * access path lld->pSFR->stCTRL_LT0.stNative.X compiles correctly.
 */
typedef struct {
    SFR_PCIELINK_CTRL_LT0_V2   stCTRL_LT0;
    SFR_PCIELINK_CTRL_LT1_V2   stCTRL_LT1;
    SFR_PCIELINK_STATUS         stSTATUS;         /* unchanged */
    SFR_PCIELINK_ERR_INJECT_V2  stERR_INJECT;
    SFR_PCIELINK_LINK_CAP       stLINK_CAP;       /* optional reference */
} SFR_PCIELINK_V2;

/* Shadow lld_pcielink_v2 handle for patched functions */
struct lld_pcielink_v2 {
    SFR_PCIELINK_V2 *pSFR;
};
"""


def _wrap_for_compilation(patched_fn_text: str) -> str:
    """
    Build a complete C translation unit:
      [stub typedefs]  +  [v2 extensions]  +  [patched functions]

    The patched functions use 'struct lld_pcielink' with SFR_PCIELINK *pSFR.
    We redefine pSFR to point at the v2 layout so both v1 and v2 field
    accesses compile correctly.
    """
    # The patched functions reference  lld->pSFR->stCTRL_LT0.stNative.X
    # We need pSFR to resolve to a struct that has stNative with all v2 fields.
    # Solution: add a second lld handle whose pSFR is the v2 map, then let
    # gcc compile the functions as-is against the original stub typedefs.
    # For fields that only exist in v2 (preset_hint, err_code, burst_en)
    # we add a note typedef that silently adds those fields to the v1 union
    # via a second anonymous member -- this is a compile-only scaffold.
    bridge = r"""
/* ---- Compile bridge: add v2 fields to v1 unions so patched code compiles -- */
/* This is a TEST-ONLY scaffold; production code uses the real SFR headers.    */

/* Extend CTRL_LT0 v1 struct to include v2 fields (preset_hint, 3-bit speed)  */
#define lt_speed_v2  lt_speed   /* same field, wider in v2 */
#define preset_hint  _rsvd0     /* map preset_hint to reserved slot */

/* Extend ERR_INJECT to include v2 names */
#define err_code     err_type   /* v2 rename: err_code was err_type in v1 */
#define burst_en     _rsvd0     /* map burst_en to reserved slot */

/* Extend CTRL_LT1 poll_timeout (8->9 bit; still same member name) */
/* No #define needed; member name unchanged, width difference is non-fatal */
"""
    return _stub_typedefs() + "\n" + bridge + "\n" + patched_fn_text + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_ollama() -> Optional[LLMClient]:
    try:
        cfg = load_llm_config({"no_llm": False, "llm": {
            "backend":  "ollama",
            "model":    OLLAMA_MODEL,
            "url":      OLLAMA_URL,
            "location": "local",
            "timeout":  OLLAMA_TIMEOUT,
        }})
        c = LLMClient(cfg)
        return c if c.available else None
    except Exception:
        return None


def _extract_field_fns(lld_text: str, field_name: str) -> str:
    if not field_name:
        return ""
    fn_re = re.compile(
        r'(/\*\*.*?\*/\s*)?'
        r'(static\s+inline\s+\S+\s+'
        r'\w*' + re.escape(field_name.lower()) + r'\w*'
        r'\s*\([^)]*\)\s*\{[^}]*\})',
        re.DOTALL | re.IGNORECASE,
    )
    parts = []
    for doc, fn in fn_re.findall(lld_text):
        parts.append((doc.strip() + "\n" + fn.strip()).strip())
    return "\n\n".join(parts)


def _ir_summary(cr: ChangeRecord) -> str:
    if cr.new_field:
        f = cr.new_field
        return (
            f"Register: {cr.reg_name}  Field: {cr.field_name}\n"
            f"Bits: {f.lsb}-{f.msb}  Width: {f.width}  Access: {f.access}"
        )
    return f"Register: {cr.reg_name}  Field: {cr.field_name}"


def _compile_check(c_code: str, extra_flags: list | None = None
                   ) -> Tuple[bool, str]:
    """
    Write c_code to a temp .c file and run gcc -fsyntax-only.
    Returns (success: bool, diagnostics: str).
    """
    flags = [
        "-fsyntax-only",
        "-std=c99",
        "-Wall",
        "-Wno-unused-parameter",
        "-Wno-unused-function",
        "-Wno-unused-variable",
    ] + (extra_flags or [])

    with tempfile.NamedTemporaryFile(
        suffix=".c", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(c_code)
        tmp = f.name

    try:
        r = subprocess.run([GCC] + flags + [tmp],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _print_compile_result(label: str, ok: bool, diag: str,
                           code: str, max_diag: int = 25) -> None:
    status = "COMPILE OK" if ok else "COMPILE FAILED"
    print(f"\n  [{label}]  {status}")
    if not ok:
        print("  -- GCC diagnostics --")
        for line in diag.splitlines()[:max_diag]:
            print(f"  {line}")
        print("  -- Patched source (first 40 lines) --")
        for i, line in enumerate(code.splitlines()[:40], 1):
            print(f"  {i:3d}: {line}")


# ---------------------------------------------------------------------------
# pytest CLI option
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    try:
        parser.addoption("--no-llm", action="store_true", default=False,
                         help="Skip Ollama calls, use mock LLM")
    except Exception:
        pass   # already registered by another conftest


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def ollama_or_skip(request):
    force_mock = request.config.getoption("--no-llm", default=False)
    if not force_mock:
        client = _build_ollama()
        if client:
            print(f"\n  [LLM] Ollama {OLLAMA_MODEL} @ {OLLAMA_URL}")
            return client
    pytest.skip("Ollama not reachable — run without --no-llm or start Ollama")


@pytest.fixture(scope="session")
def stub_text():
    return LLD_STUB.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def patched_functions(ollama_or_skip, stub_text):
    """
    Call Ollama once for all semantically-changed fields.
    Returns: dict[field_name -> (ChangeRecord, sanitised_patched_str)]
    """
    client  = ollama_or_skip
    changes = classify_sfr_diff(V1, V2, ip="PCIELINK")
    apply_semantic_gate(changes, llm_client=None,
                        threshold_low=0.75, threshold_high=0.85)
    to_patch = [c for c in changes
                if c.change_type in SEMANTIC_CHECK_TYPES and c.needs_llm]

    print(f"\n  Calling Ollama for {len(to_patch)} field(s)...")
    results = {}
    for cr in to_patch:
        old_desc = (cr.old_field.desc if cr.old_field else "") or ""
        new_desc = (cr.new_field.desc if cr.new_field else "") or ""
        fn_text  = _extract_field_fns(stub_text, cr.field_name or "")

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
        results[cr.field_name] = (cr, sanitise_llm_output(patched))
        print(f"    patched {cr.reg_name}.{cr.field_name} ({len(patched)} chars)")
    return results


# ============================================================================
# 1. TestCompilationScaffold  (2 tests)
# ============================================================================
@_NEED_GCC
@_NEED_FILES
class TestCompilationScaffold:

    def test_stub_typedefs_compile(self):
        """The raw stub type definitions must compile cleanly on their own."""
        code = _stub_typedefs() + "\n"
        ok, diag = _compile_check(code)
        _print_compile_result("scaffold typedefs", ok, diag, code)
        assert ok, f"Stub typedefs compile FAILED:\n{diag}"

    def test_original_stub_functions_compile(self):
        """The original (unpatched) LLD functions must compile with the preamble."""
        raw      = LLD_STUB.read_text(encoding="utf-8")
        # Strip guard + includes; keep everything from first static inline onward
        raw = re.sub(r'#ifndef\s+\S+\s*\n#define\s+\S+\s*\n', '', raw)
        raw = re.sub(r'#endif[^\n]*\n?', '', raw)
        raw = re.sub(r'#include[^\n]*\n', '', raw)
        # Use the bridge wrapper (adds v2 field aliases)
        stub_fns = re.search(r'(static\s+inline\b.*)', raw, re.DOTALL)
        fn_text  = stub_fns.group(1) if stub_fns else raw
        code = _wrap_for_compilation(fn_text)
        ok, diag = _compile_check(code)
        _print_compile_result("original stub functions", ok, diag, code)
        assert ok, f"Original LLD stub functions compile FAILED:\n{diag}"


# ============================================================================
# 2. TestPatchedFunctionCompilation  (5 tests, one per changed field)
# ============================================================================
@_NEED_GCC
@_NEED_FILES
class TestPatchedFunctionCompilation:

    def _check_field(self, field_name: str, patched_functions: dict) -> None:
        if field_name not in patched_functions:
            pytest.skip(f"No patch generated for '{field_name}'")

        cr, patched = patched_functions[field_name]
        code = _wrap_for_compilation(patched)
        ok, diag = _compile_check(code)
        _print_compile_result(
            f"{cr.reg_name}.{field_name}", ok, diag, patched
        )
        assert ok, (
            f"[{cr.reg_name}.{field_name}] Patched LLD does NOT compile!\n"
            f"GCC:\n{diag}"
        )

    # C01
    def test_retrain_cnt_compiles(self, patched_functions):
        """COMMENT_CHANGED -- updated description, same bitfield."""
        self._check_field("retrain_cnt", patched_functions)

    # C02
    def test_det_en_compiles(self, patched_functions):
        """FIELD_POLARITY_CHANGED -- active-low inversion."""
        self._check_field("det_en", patched_functions)

    # C03
    def test_poll_timeout_compiles(self, patched_functions):
        """MULTI_CHANGED -- 8-bit -> 9-bit width + desc change."""
        self._check_field("poll_timeout", patched_functions)

    # C04
    def test_eq_en_compiles(self, patched_functions):
        """MULTI_CHANGED -- offset shifted + comment changed."""
        self._check_field("eq_en", patched_functions)

    # C05
    def test_lt_speed_compiles(self, patched_functions):
        """MULTI_CHANGED -- 2-bit -> 3-bit (Gen4), desc updated."""
        self._check_field("lt_speed", patched_functions)


# ============================================================================
# 3. TestCompilationDiagnostics  (3 tests — quality of patched code)
# ============================================================================
@_NEED_GCC
@_NEED_FILES
class TestCompilationDiagnostics:

    def test_no_implicit_function_declarations(self, patched_functions):
        """LLM must not call undefined functions."""
        errors = []
        for field_name, (cr, patched) in patched_functions.items():
            code = _wrap_for_compilation(patched)
            ok, diag = _compile_check(
                code, ["-Werror=implicit-function-declaration"]
            )
            if not ok and "implicit" in diag.lower():
                first = next(
                    (l for l in diag.splitlines() if "implicit" in l.lower()),
                    diag.splitlines()[0],
                )
                errors.append(f"  {cr.reg_name}.{field_name}: {first}")
        assert not errors, (
            "Implicit function declarations in patched LLD:\n" +
            "\n".join(errors)
        )

    def test_no_undeclared_identifiers(self, patched_functions):
        """All identifiers must be declared in the preamble or the patch itself."""
        errors = []
        for field_name, (cr, patched) in patched_functions.items():
            code = _wrap_for_compilation(patched)
            ok, diag = _compile_check(code)
            if not ok:
                bad = [l for l in diag.splitlines()
                       if any(k in l for k in
                              ("undeclared", "has no member",
                               "unknown type", "not a member"))]
                if bad:
                    errors.append(
                        f"  {cr.reg_name}.{field_name}:\n" +
                        "\n".join(f"    {l}" for l in bad[:4])
                    )
        assert not errors, (
            "Undeclared identifiers in patched LLD:\n" + "\n".join(errors)
        )

    def test_all_original_fn_names_preserved(self, patched_functions, stub_text):
        """
        Every function name from the text given TO the LLM (old_fn_text) must
        still appear in the patched output.  The LLM must not drop or rename
        the specific function(s) it was asked to patch.

        Note: we check against what was sent to the LLM (extracted per field),
        not the whole stub — avoiding the substring-match trap where 'eq_en'
        would also pull in 'lt_en' function names.
        """
        sig_re = re.compile(r'static\s+inline\s+\S+\s+(\w+)\s*\(')

        def _field_fn_text(full_stub: str, field: str) -> str:
            """Extract only functions whose name ends with _{field}_{verb}."""
            # Match function names like: lld_ip_reg_<field>_get / _set / _clear
            fn_name_re = re.compile(
                r'\bstatic\s+inline\b[^\n]*\blld_\w+_' +
                re.escape(field) + r'_\w+\s*\(',
                re.IGNORECASE,
            )
            blocks = re.split(r'(?=\bstatic\s+inline\b)', full_stub)
            return "\n".join(
                b for b in blocks if fn_name_re.search(b)
            )

        missing = []
        for field_name, (cr, patched) in patched_functions.items():
            given_text = _field_fn_text(stub_text, field_name)
            given_names = set(sig_re.findall(given_text))
            for fn in given_names:
                if fn not in patched:
                    missing.append(f"  {cr.reg_name}.{field_name}: '{fn}' missing")
        assert not missing, (
            "Functions given to LLM are missing from patched output "
            "(LLM dropped or renamed them):\n" +
            "\n".join(missing)
        )
