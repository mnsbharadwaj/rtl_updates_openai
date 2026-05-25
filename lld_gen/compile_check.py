"""
compile_check.py — GCC Compile Verification Loop

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

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from lld_gen.lld_patcher import extract_function


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

MAX_RETRIES = 3


def _find_gcc() -> Optional[str]:
    """Locate gcc / cc in PATH."""
    for exe in ("gcc", "cc", "x86_64-w64-mingw32-gcc"):
        found = shutil.which(exe)
        if found:
            return found
    return None


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


def run_compile_check(
    test_file:   str | Path,
    sfr_new:     str | Path,
    lld_file:    str | Path,
    llm_client=None,    # Optional[LLMClient]
    gcc_exe:     Optional[str] = None,
    extra_includes: Optional[List[str]] = None,
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

    Returns:
        CompileResult with success flag and diagnostics
    """
    test_file = Path(test_file)
    sfr_new   = Path(sfr_new)
    lld_file  = Path(lld_file)

    result = CompileResult(success=False, stdout="", stderr="")

    # Find gcc
    if gcc_exe is None:
        gcc_exe = _find_gcc()
    if gcc_exe is None:
        msg = (
            "gcc not found in PATH. Install MinGW-w64 or GCC and ensure it "
            "is on PATH, then re-run.\n"
            "  Windows: choco install mingw   or   winget install GnuWin32.GCC"
        )
        result.stderr = msg
        result.needs_review = ["<compile_check_unavailable>"]
        print(f"  [COMPILE] WARNING: {msg}")
        result.success = True   # allow pipeline to continue without compiler
        return result

    print(f"  [COMPILE] Using {gcc_exe}")

    retcode, stdout, stderr = _run_gcc(gcc_exe, test_file, sfr_new, lld_file, extra_includes)

    if retcode == 0:
        result.success = True
        result.stdout  = stdout
        result.stderr  = stderr
        print("  [COMPILE] OK")
        return result

    # Failure path
    print(f"  [COMPILE] FAIL (exit {retcode})")
    failing_fns = _extract_failing_fns(stderr)
    result.failed_functions = list(failing_fns)

    if llm_client is None or not failing_fns:
        result.stderr = stderr
        result.needs_review = list(failing_fns) or ["<unknown>"]
        return result

    # Retry loop
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  [COMPILE] Retry {attempt}/{MAX_RETRIES} — requesting LLM fix …")

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
                print(f"  [LLM-ERROR] {exc}")

        if not fixed_any:
            break

        retcode, stdout, stderr = _run_gcc(gcc_exe, test_file, sfr_new, lld_file, extra_includes)
        result.retries_used = attempt

        if retcode == 0:
            result.success = True
            result.stdout  = stdout
            result.stderr  = stderr
            print(f"  [COMPILE] OK after {attempt} LLM fix(es)")
            return result

    # Still failing after MAX_RETRIES
    result.stderr       = stderr
    result.needs_review = list(failing_fns)
    print(f"  [COMPILE] Still failing after {MAX_RETRIES} retries. Marked for manual review.")
    return result
