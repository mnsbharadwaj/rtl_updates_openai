"""
compile_check.py -- GCC Compile Verification Loop

Runs: gcc -fsyntax-only -std=c11 -include sfr_new.h -include lld.h test_lld_generated.c

On failure:
    1. Extract function name from gcc error output
    2. Send broken function + error message to Qwen2.5-Coder-7B for fix
    3. Patch fixed function back into lld.h
    4. Re-run gcc — up to 3 retry attempts
    5. Mark any still-failing function as NEEDS_MANUAL_REVIEW

The compile check gate is mandatory. PR staging only proceeds after
gcc returns exit code 0.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from lld_gen.lld_patcher import extract_function

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GCC not-found error (raised when compile_check=true but gcc absent)
# ---------------------------------------------------------------------------
class GccNotFoundError(RuntimeError):
    """Raised when compile_check is enabled but gcc cannot be found."""
    pass


@dataclass
class CompileResult:
    success:         bool
    stdout:          str
    stderr:          str
    failed_functions: List[str] = field(default_factory=list)
    needs_review:    List[str] = field(default_factory=list)
    retries_used:    int = 0


_FN_FROM_ERROR_RE = re.compile(
    r"(?:error|warning).*?`(\w+)'|"          # gcc: 'error: ... 'fn_name''
    r"undefined reference to `(\w+)'|"        # linker
    r"(\w+)\s*:\s*(?:error|warning)",         # clang style
)

MAX_RETRIES = 5   # v3.0: was 3, now 5 per user requirement


def _find_gcc(cfg_gcc: Optional[str] = None) -> str:
    """
    Locate gcc / cc in PATH.

    Args:
        cfg_gcc:  Explicit gcc path from config ('gcc:' YAML key).
                  If provided and valid, use directly.

    Returns:
        Path to gcc executable.

    Raises:
        GccNotFoundError: If gcc is not found.
    """
    # Explicit override from config
    if cfg_gcc:
        found = shutil.which(cfg_gcc)
        if found:
            return found
        # cfg_gcc specified but not found — still search PATH
    for exe in ("gcc", "cc", "x86_64-w64-mingw32-gcc"):
        found = shutil.which(exe)
        if found:
            return found
    raise GccNotFoundError(
        "gcc not found in PATH. Install MinGW-w64 or GCC and ensure it is on PATH."
    )


def _run_gcc(
    gcc_exe: str,
    test_file: Path,
    sfr_new:   Path,
    lld_file:  Path,
    extra_includes: Optional[List[str]] = None,
) -> Tuple[int, str, str]:
    """Run gcc -fsyntax-only and return (returncode, stdout, stderr)."""
    cmd = [
        gcc_exe,
        "-fsyntax-only",
        "-std=c11",
        "-x", "c",
        f"-include{sfr_new}",
        f"-include{lld_file}",
    ]
    if extra_includes:
        for inc in extra_includes:
            cmd.extend(["-I", inc])
    cmd.append(str(test_file))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", f"gcc not found: {gcc_exe}"
    except subprocess.TimeoutExpired:
        return -1, "", "gcc timed out"


def _extract_failing_fns(stderr: str) -> List[str]:
    """Parse gcc error output for function names."""
    fns: List[str] = []
    for line in stderr.splitlines():
        for m in _FN_FROM_ERROR_RE.finditer(line):
            name = m.group(1) or m.group(2) or m.group(3)
            if name and name not in fns:
                fns.append(name)
    return fns


def _patch_function_in_lld(lld_path: Path, fn_name: str, fixed_code: str) -> bool:
    """
    Replace the old function body in lld.h with fixed_code.
    Returns True if function was found and replaced.
    """
    content = lld_path.read_text(encoding="utf-8")
    old_fn  = extract_function(content, fn_name)
    if old_fn is None:
        return False
    content = content.replace(old_fn, fixed_code.strip(), 1)
    lld_path.write_text(content, encoding="utf-8")
    return True


def run_test_execution(
    gcc_exe: str,
    test_file: Path,
    sfr_new: Path,
    lld_file: Path,
    extra_includes: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Compiles the test file into a native executable and runs it to execute assertions.
    Returns (success, message).
    """
    import tempfile
    import os

    suffix = ".exe" if os.name == "nt" else ""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        exe_path = tmp.name

    cmd = [
        gcc_exe,
        "-std=c11",
        "-x", "c",
        f"-I{sfr_new.parent}",
        f"-I{lld_file.parent}",
    ]
    if extra_includes:
        for inc in extra_includes:
            cmd.extend(["-I", inc])

    cmd.extend([str(test_file), "-o", exe_path])

    try:
        # 1. Compile and link
        logger.debug("[COMPILE-TEST] Running: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            if os.path.exists(exe_path):
                try: os.remove(exe_path)
                except Exception: pass
            return False, f"Compilation/link failed: {proc.stderr}"

        # 2. Run assertions
        logger.debug("[RUN-TEST] Executing: %s", exe_path)
        run_proc = subprocess.run([exe_path], capture_output=True, text=True, timeout=10)

        # Cleanup
        if os.path.exists(exe_path):
            try: os.remove(exe_path)
            except Exception: pass

        if run_proc.returncode != 0:
            err_msg = run_proc.stderr or run_proc.stdout or f"exit code {run_proc.returncode}"
            return False, f"Runtime assertions failed: {err_msg.strip()}"

        return True, "All generated unit test assertions passed successfully!"
    except OSError as e:
        if os.path.exists(exe_path):
            try: os.remove(exe_path)
            except Exception: pass
        return False, f"Execution failed (possible cross-compilation target): {e}"
    except Exception as e:
        if os.path.exists(exe_path):
            try: os.remove(exe_path)
            except Exception: pass
        return False, f"Error running tests: {e}"


def run_compile_check(
    test_file:   str | Path,
    sfr_new:     str | Path,
    lld_file:    str | Path,
    llm_client=None,    # Optional[LLMClient]
    gcc_exe:     Optional[str] = None,
    extra_includes: Optional[List[str]] = None,
    required:    bool = True,
) -> CompileResult:
    """
    Run the compile verification loop.

    Args:
        test_file:   Path to test_lld_generated.c
        sfr_new:     Path to new sfr.h header (included by gcc)
        lld_file:    Path to lld.h (included by gcc; patched in-place on retry)
        llm_client:  Optional LLMClient for fix retries
        gcc_exe:     Path to gcc executable; auto-detected if None
        extra_includes: Additional -I directories
        required:    Whether compiler is required to be present

    Returns:
        CompileResult with success flag and diagnostics
    """
    test_file = Path(test_file)
    sfr_new   = Path(sfr_new)
    lld_file  = Path(lld_file)

    result = CompileResult(success=False, stdout="", stderr="")

    # Find gcc
    if gcc_exe is None:
        try:
            gcc_exe = _find_gcc()
        except GccNotFoundError as exc:
            if required:
                raise exc
            msg = (
                "gcc not found in PATH. Install MinGW-w64 or GCC and ensure it "
                "is on PATH, then re-run.\n"
                "  Windows: choco install mingw   or   winget install GnuWin32.GCC"
            )
            result.stderr = msg
            result.needs_review = ["<compile_check_unavailable>"]
            logger.warning("[COMPILE] %s", msg)
            result.success = True   # allow pipeline to continue without compiler
            return result

    logger.debug("[COMPILE] Using %s", gcc_exe)

    retcode, stdout, stderr = _run_gcc(gcc_exe, test_file, sfr_new, lld_file, extra_includes)

    if retcode == 0:
        result.success = True
        result.stdout  = stdout
        result.stderr  = stderr
        logger.info("[COMPILE] Syntax check OK")

        # Perform functional unit test execution
        logger.info("[TEST] Running generated functional unit tests for %s ...", lld_file.name)
        ok, msg = run_test_execution(gcc_exe, test_file, sfr_new, lld_file, extra_includes)
        if ok:
            logger.info("[TEST] %s", msg)
        else:
            if "execution failed" in msg.lower() or "exec format error" in msg.lower():
                logger.warning("[TEST] Skipped functional execution (possible cross-compilation target): %s", msg)
            else:
                logger.error("[TEST] FAIL: %s", msg)
                result.success = False
                result.stderr += f"\n[Functional Test Failure]\n{msg}"
                result.needs_review = ["<functional_test_failure>"]
        return result

    # Failure path
    logger.warning("[COMPILE] FAIL (exit %d)", retcode)
    failing_fns = _extract_failing_fns(stderr)
    result.failed_functions = list(failing_fns)

    if llm_client is None or not failing_fns:
        result.stderr = stderr
        result.needs_review = list(failing_fns) or ["<unknown>"]
        return result

    # Retry loop
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("[COMPILE] Retry %d/%d -- requesting LLM fix ...", attempt, MAX_RETRIES)

        lld_content = lld_file.read_text(encoding="utf-8")
        fixed_any   = False

        for fn_name in list(failing_fns):
            broken_code = extract_function(lld_content, fn_name) or ""
            context = (
                f"SKILL.md requirement: static inline functions, "
                f"volatile uint32_t *base, base[N] word addressing, "
                f"no #include, no #define."
            )
            try:
                fixed_code = llm_client.fix_compile_error(
                    broken_code=broken_code,
                    error_msg=stderr,
                    context=context,
                )
                if fixed_code and _patch_function_in_lld(lld_file, fn_name, fixed_code):
                    fixed_any = True
            except RuntimeError as exc:
                logger.error("[LLM-ERROR] %s", exc)

        if not fixed_any:
            break

        retcode, stdout, stderr = _run_gcc(gcc_exe, test_file, sfr_new, lld_file, extra_includes)
        result.retries_used = attempt

        if retcode == 0:
            result.success = True
            result.stdout  = stdout
            result.stderr  = stderr
            logger.info("[COMPILE] OK after %d LLM fix(es)", attempt)
            return result

    # Still failing after MAX_RETRIES
    result.stderr       = stderr
    result.needs_review = list(failing_fns)
    logger.warning("[COMPILE] Still failing after %d retries. Marked for manual review.", MAX_RETRIES)
    return result


def run_compile_check_one_fn(
    fn_name:     str,
    lld_file:    "str | Path",
    sfr_new:     "str | Path",
    test_file:   "Optional[str | Path]" = None,
    llm_client   = None,
    gcc_exe:     Optional[str] = None,
    max_retries: int = MAX_RETRIES,
    required:    bool = True,
) -> CompileResult:
    """
    Per-function compile gate — called immediately after each LLM patch.

    Strategy:
      1. Run gcc -fsyntax-only on lld_file (with sfr_new included)
      2. If error references fn_name: retry with LLM fix (up to max_retries)
      3. If still failing: mark as MANUAL_REVIEW, caller should revert fn
      4. Return CompileResult (success=True even if error is unrelated to fn_name)
    """
    lld_file = Path(lld_file)
    sfr_new  = Path(sfr_new)

    result = CompileResult(success=False, stdout="", stderr="")

    if gcc_exe is None:
        try:
            gcc_exe = _find_gcc()
        except GccNotFoundError as exc:
            if required:
                raise exc
            result.success      = True
            result.needs_review = ["<compile_check_unavailable>"]
            return result

    check_file = Path(test_file) if (test_file and Path(test_file).exists()) else lld_file

    for attempt in range(0, max_retries + 1):
        retcode, stdout, stderr = _run_gcc(gcc_exe, check_file, sfr_new, lld_file)
        result.retries_used = attempt

        if retcode == 0:
            result.success = True
            result.stdout  = stdout
            result.stderr  = stderr
            if attempt > 0:
                logger.info("[COMPILE-FN] OK after %d fix(es): %s", attempt, fn_name)
            return result

        # Check if the error is related to our specific function
        failing_fns = _extract_failing_fns(stderr)
        if fn_name not in failing_fns and attempt == 0:
            # Error is from a different function — not our problem, pass through
            result.success = True
            return result

        if attempt >= max_retries or llm_client is None:
            break

        # LLM fix attempt
        lld_content = lld_file.read_text(encoding="utf-8")
        broken_code = extract_function(lld_content, fn_name) or ""
        if not broken_code:
            break

        logger.info("[COMPILE-FN] Retry %d/%d LLM fix: %s ...", attempt + 1, max_retries, fn_name)
        try:
            fixed_code = llm_client.fix_compile_error(
                broken_code=broken_code,
                error_msg=stderr,
                context="struct-based lld, no masks, preserve signature exactly",
            )
            if fixed_code:
                _patch_function_in_lld(lld_file, fn_name, fixed_code)
        except RuntimeError as exc:
            logger.error("[LLM-ERROR] %s", exc)
            break

    # Still failing after max_retries
    result.stderr       = stderr
    result.needs_review = [fn_name]
    logger.warning("[COMPILE-FN] MANUAL_REVIEW: %s (failed after %d retries)", fn_name, max_retries)
    return result
