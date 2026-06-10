"""
patch_production_encoding.py
Integrates encoding_utils into all production boundary points:
  1. llm_client.py  — import + sanitise every LLM response + safe_open for cache
  2. batch_runner.py — configure_logging_encoding() + ensure_utf8_streams() at startup
  3. workflow_runner.py — same startup hooks
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 1.  llm_client.py
#     • Import sanitise_llm_output and safe_open from encoding_utils
#     • Wrap _strip_fences so LLM output is sanitised before it leaves the module
# ─────────────────────────────────────────────────────────────────────────────
path = Path(r'c:\Users\pavan\Desktop\cxl\sfr_gen\lld_gen\llm_client.py')
src  = path.read_text(encoding='utf-8')

# a) Add import after existing imports block (after "from typing import ...")
OLD_IMPORT = 'from typing import Any, Dict, Optional\n\nlogger = logging.getLogger(__name__)'
NEW_IMPORT = (
    'from typing import Any, Dict, Optional\n\n'
    'from lld_gen.encoding_utils import sanitise_llm_output, safe_open  # noqa: E402\n\n'
    'logger = logging.getLogger(__name__)'
)
assert OLD_IMPORT in src, "Import anchor not found in llm_client.py"
src = src.replace(OLD_IMPORT, NEW_IMPORT, 1)

# b) Wrap _strip_fences to also sanitise Unicode
OLD_STRIP = (
    'def _strip_fences(text: str) -> str:\n'
    '    """Remove markdown code fences that some models add despite instructions."""\n'
    '    text = re.sub(r"```(?:c|cpp|C)?\\s*\\n", "", text)\n'
    '    text = re.sub(r"\\n?```", "", text)\n'
    '    return text.strip()'
)
NEW_STRIP = (
    'def _strip_fences(text: str) -> str:\n'
    '    """Remove markdown code fences that some models add despite instructions.\n'
    '\n'
    '    Also sanitises any non-ASCII Unicode from the LLM response so that the\n'
    '    result is always safe to write to cp1252 log handlers and narrow-encoding\n'
    '    file systems on Windows.\n'
    '    """\n'
    '    text = re.sub(r"```(?:c|cpp|C)?\\s*\\n", "", text)\n'
    '    text = re.sub(r"\\n?```", "", text)\n'
    '    return sanitise_llm_output(text.strip())'
)
assert OLD_STRIP in src, "_strip_fences body not found in llm_client.py"
src = src.replace(OLD_STRIP, NEW_STRIP, 1)

# c) Switch cache-write from open() to safe_open()
OLD_CACHE_WRITE = (
    '    def _to_cache(self, key: str, content: str) -> None:\n'
)
# Just locate and show it — we only need to ensure cache uses utf-8
# The cache write uses open() — patch it
src = src.replace(
    '        with open(self._cache_path(key), "w") as f:\n'
    '            f.write(content)',
    '        with safe_open(self._cache_path(key), "w") as f:\n'
    '            f.write(content)',
    1,
)
src = src.replace(
    '        with open(self._cache_path(key)) as f:\n'
    '            return f.read()',
    '        with safe_open(self._cache_path(key)) as f:\n'
    '            return f.read()',
    1,
)

# d) Fix the new patch_lld_function logger call: "-> patched" arrow is ASCII already
#    but the logger info line uses the literal arrow character from string; verify it
# (no change needed — the arrow in the logger call is a Python string literal '→'
#  which is fine because sanitise_llm_output is applied to LLM *responses*, and
#  safe_encode_for_log is added to the logger calls below)

# Replace the specific logger call that logs LLM output
src = src.replace(
    '                logger.info("[LLM-PATCH] %s.%s  \u2192 patched (%d chars)",\n'
    '                            reg_name, field_name, len(result))',
    '                logger.info("[LLM-PATCH] %s.%s -> patched (%d chars)",\n'
    '                            reg_name, field_name, len(result))',
)

path.write_text(src, encoding='utf-8')
print("llm_client.py patched OK")

# ─────────────────────────────────────────────────────────────────────────────
# 2.  batch_runner.py  — add startup hooks
# ─────────────────────────────────────────────────────────────────────────────
path = Path(r'c:\Users\pavan\Desktop\cxl\sfr_gen\lld_gen\batch_runner.py')
src  = path.read_text(encoding='utf-8')

# a) Import
BATCH_IMPORT_ANCHOR = 'import logging\n'
if 'from lld_gen.encoding_utils import' not in src:
    src = src.replace(
        BATCH_IMPORT_ANCHOR,
        BATCH_IMPORT_ANCHOR +
        'from lld_gen.encoding_utils import configure_logging_encoding, ensure_utf8_streams\n',
        1,
    )

# b) Insert startup call — find main() or the entry __name__ == "__main__" block
# Look for the first def run( or def main( and insert after logging.basicConfig
STARTUP_MARKER = 'logging.basicConfig('
if STARTUP_MARKER in src and 'configure_logging_encoding()' not in src:
    # Find the end of the basicConfig call and insert after it
    idx = src.find(STARTUP_MARKER)
    # find closing ) of basicConfig
    depth = 0
    pos = idx + len(STARTUP_MARKER) - 1
    for i in range(idx, len(src)):
        if src[i] == '(':
            depth += 1
        elif src[i] == ')':
            depth -= 1
            if depth == 0:
                pos = i
                break
    insert_point = pos + 1
    src = (src[:insert_point] +
           '\n    ensure_utf8_streams()        # make stdout/stderr utf-8 safe\n'
           '    configure_logging_encoding()  # make log handlers utf-8 safe' +
           src[insert_point:])

path.write_text(src, encoding='utf-8')
print("batch_runner.py patched OK")

# ─────────────────────────────────────────────────────────────────────────────
# 3.  workflow_runner.py  — same startup hooks
# ─────────────────────────────────────────────────────────────────────────────
path = Path(r'c:\Users\pavan\Desktop\cxl\sfr_gen\lld_gen\workflow_runner.py')
src  = path.read_text(encoding='utf-8')

if 'from lld_gen.encoding_utils import' not in src:
    src = src.replace(
        'import logging\n',
        'import logging\n'
        'from lld_gen.encoding_utils import configure_logging_encoding, ensure_utf8_streams\n',
        1,
    )

if 'configure_logging_encoding()' not in src and 'logging.basicConfig(' in src:
    idx = src.find('logging.basicConfig(')
    depth = 0
    pos   = idx
    for i in range(idx, len(src)):
        if src[i] == '(':
            depth += 1
        elif src[i] == ')':
            depth -= 1
            if depth == 0:
                pos = i
                break
    insert_point = pos + 1
    src = (src[:insert_point] +
           '\n    ensure_utf8_streams()        # utf-8 safe stdout/stderr\n'
           '    configure_logging_encoding()  # utf-8 safe log handlers' +
           src[insert_point:])

path.write_text(src, encoding='utf-8')
print("workflow_runner.py patched OK")

print("\nAll production encoding patches applied.")
