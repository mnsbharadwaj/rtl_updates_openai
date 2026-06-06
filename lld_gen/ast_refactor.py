"""
ast_refactor.py — C AST-Based Surgical Cross-Refactoring Engine

Uses pycparser to parse C source and header files, find struct member accesses to changed
SFR registers and fields, and surgically rename them in-place while preserving comments,
formatting, and preprocessor directives.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

from pycparser import c_parser, c_ast
from lld_gen.sfr_diff_analyzer import ChangeRecord, ChangeType

logger = logging.getLogger(__name__)

# Standard C types and keywords to register in lexer typedef lookup
STANDARD_TYPEDEFS = {
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "UINT8", "UINT16", "UINT32", "UINT64",
    "size_t", "bool", "boolean", "BOOL", "UINT", "USHORT", "ULONG"
}

class CustomCParser(c_parser.CParser):
    """
    Subclass of pycparser CParser that automatically registers a set of type names
    in the lexer symbol table upon reset, preventing syntax errors when parsing.
    """
    def __init__(self, typedefs: Set[str] | None = None):
        super().__init__()
        self.custom_typedefs = typedefs or set()

    def _reset(self, *args, **kwargs):
        super()._reset(*args, **kwargs)
        # Register standard typedefs
        for t in STANDARD_TYPEDEFS:
            self._add_typedef_name(t, None)
        # Register custom typedefs (e.g. SFR union types)
        for t in self.custom_typedefs:
            self._add_typedef_name(t, None)


def get_struct_ref_path(node: c_ast.StructRef) -> List[str]:
    """
    Decompose a StructRef chain (e.g. lld->pSFR->stPMU_CON.stNative.DMA_EN)
    into a list of member/variable names (e.g. ['lld', 'pSFR', 'stPMU_CON', 'stNative', 'DMA_EN']).
    """
    path = []
    curr = node
    while isinstance(curr, c_ast.StructRef):
        if isinstance(curr.field, c_ast.ID):
            path.append(curr.field.name)
        curr = curr.name
    if isinstance(curr, c_ast.ID):
        path.append(curr.name)
    else:
        path.append(type(curr).__name__)
    path.reverse()
    return path


class StructRefVisitor(c_ast.NodeVisitor):
    """
    Traverses AST to identify coordinates of struct member references
    that correspond to changed registers or fields.
    """
    def __init__(self, changes: List[ChangeRecord]):
        self.changes = changes
        # Gather search patterns
        self.reg_renames: Dict[str, str] = {}      # stOLD_REG -> stNEW_REG
        self.field_renames: Dict[str, Dict[str, str]] = {}  # REG_NAME -> {OLD_FLD: NEW_FLD}

        for cr in changes:
            # 1. Register Renames
            if cr.change_type == ChangeType.REG_RENAMED and cr.old_reg and cr.new_reg:
                old_m = "st" + cr.old_reg.name
                new_m = "st" + cr.new_reg.name
                self.reg_renames[old_m] = new_m
                self.reg_renames[old_m.upper()] = new_m
                self.reg_renames[old_m.lower()] = new_m.lower()

            # 2. Field Renames
            elif cr.change_type == ChangeType.FIELD_RENAMED and cr.old_field and cr.new_field:
                rname = cr.reg_name
                old_f = cr.old_field.name
                new_f = cr.new_field.name
                if rname not in self.field_renames:
                    self.field_renames[rname] = {}
                self.field_renames[rname][old_f] = new_f

        # list of tuples: (line, column, old_text, new_text)
        self.replacements: List[Tuple[int, int, str, str]] = []

    def visit_StructRef(self, node: c_ast.StructRef):
        # Visit children first (inner expressions first)
        self.generic_visit(node)

        if not isinstance(node.field, c_ast.ID) or not node.coord:
            return

        field_name = node.field.name
        path = get_struct_ref_path(node)

        # Case A: Register renamed (e.g. stPMU_CON -> stPMU_CTRL)
        if field_name in self.reg_renames:
            new_reg_m = self.reg_renames[field_name]
            self.replacements.append((node.coord.line, node.coord.column, field_name, new_reg_m))
            return

        # Case B: Field renamed (e.g. DONE -> COMPLETE inside stSTATUS_CON)
        # Check if field name matches any registered field rename
        for reg_name, f_renames in self.field_renames.items():
            if field_name in f_renames:
                # Confirm it is nested under 'st{REG_NAME}'
                target_reg_m = "st" + reg_name
                # Check path: field is at path[-1], stNative is at path[-2], st{REG} is at path[-3]
                if len(path) >= 3 and path[-2] == 'stNative' and path[-3].upper() == target_reg_m.upper():
                    new_field = f_renames[field_name]
                    self.replacements.append((node.coord.line, node.coord.column, field_name, new_field))


def remove_comments_keep_spacing(text: str) -> str:
    """
    Replaces C style comments (/*...*/ and //...) with spaces, preserving newlines
    so that line and column coordinates of the remaining C code remain intact.
    """
    out = list(text)
    n = len(out)
    i = 0
    while i < n:
        if i + 1 < n and out[i] == '/' and out[i+1] == '*':
            out[i] = ' '
            out[i+1] = ' '
            i += 2
            while i < n:
                if i + 1 < n and out[i] == '*' and out[i+1] == '/':
                    out[i] = ' '
                    out[i+1] = ' '
                    i += 2
                    break
                else:
                    if out[i] != '\n' and out[i] != '\r':
                        out[i] = ' '
                    i += 1
        elif i + 1 < n and out[i] == '/' and out[i+1] == '/':
            out[i] = ' '
            out[i+1] = ' '
            i += 2
            while i < n and out[i] != '\n' and out[i] != '\r':
                out[i] = ' '
                i += 1
        else:
            i += 1
    return "".join(out)


def refactor_file(
    file_path: Path,
    changes: List[ChangeRecord],
    custom_typedefs: Set[str]
) -> List[Tuple[int, str, str]]:
    """
    Parses a C source/header file, finds all register/field accesses that match the changes,
    applies updates in-place, and saves the file.

    Returns a list of applied patches: [(line_number, old_text, new_text)]
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.error("[AST] Failed to read %s: %s", file_path.name, e)
        return []

    # Comment out preprocessor lines to prevent parse errors while preserving line numbers
    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('__asm'):
            cleaned_lines.append("// " + line)
        else:
            cleaned_lines.append(line)
    cleaned_code = "\n".join(cleaned_lines)

    # Strip comments to prevent parse errors in pycparser while keeping line numbers
    code_for_parser = remove_comments_keep_spacing(cleaned_code)

    # Parse C code
    parser = CustomCParser(custom_typedefs)
    try:
        ast = parser.parse(code_for_parser, filename=str(file_path))
    except Exception as e:
        logger.warning("[AST] Skipping %s: parse error (%s)", file_path.name, str(e).strip())
        return []

    # Traverse AST and gather replacements
    visitor = StructRefVisitor(changes)
    visitor.visit(ast)

    if not visitor.replacements:
        return []

    # Apply replacements line-by-line (sorted in reverse order of line and column)
    # This prevents earlier offsets from shifting when text length changes.
    applied: List[Tuple[int, str, str]] = []
    lines = text.splitlines()

    # Sort: highest line number first, then highest column number first
    visitor.replacements.sort(key=lambda x: (x[0], x[1]), reverse=True)

    for line_no, col_start, old_text, new_text in visitor.replacements:
        line_idx = line_no - 1
        if line_idx < 0 or line_idx >= len(lines):
            continue
        line = lines[line_idx]
        
        # Search for old_text starting around col_start - 1
        start_idx = max(0, col_start - 1)
        idx = line.find(old_text, start_idx)
        if idx == -1:
            # Fallback search from start of line if column coordinate was slightly off
            idx = line.find(old_text)
            
        if idx != -1:
            lines[line_idx] = line[:idx] + new_text + line[idx + len(old_text):]
            applied.append((line_no, old_text, new_text))
            logger.debug("[AST] Patched %s:%d: %s -> %s", file_path.name, line_no, old_text, new_text)

    if applied:
        try:
            file_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("[AST] Patched %d reference(s) in %s", len(applied), file_path.name)
        except OSError as e:
            logger.error("[AST] Failed to write %s: %s", file_path.name, e)
            return []

    return applied


def refactor_cross_references(
    cfg: object,
    ip: str,
    new_ir: object,
    auto_crs: List[ChangeRecord],
    out_lld: Path,
    test_file: Path
) -> List[Path]:
    """
    Scans the output directory and LLD directory for other C source/header files,
    extracts custom types, and refactors them automatically.
    Returns the list of modified file paths.
    """
    # 1. Identify all directories to scan
    output_dir = getattr(cfg, "output_dir", None)
    lld_dir = getattr(cfg, "lld_dir", None)
    
    dirs_to_scan = set()
    if output_dir:
        p_out = Path(output_dir)
        if p_out.exists():
            dirs_to_scan.add(p_out)
    if lld_dir:
        p_lld = Path(lld_dir)
        if p_lld.exists():
            dirs_to_scan.add(p_lld)
        
    # 2. Find all C/C++ files (excluding the main patched LLD and the test suite)
    files_to_scan: List[Path] = []
    for d in dirs_to_scan:
        for ext in ("*.c", "*.h", "*.cpp"):
            for f in d.rglob(ext):
                resolved_f = f.resolve()
                if resolved_f == out_lld.resolve() or resolved_f == test_file.resolve():
                    continue
                if resolved_f not in files_to_scan:
                    files_to_scan.append(resolved_f)
                    
    if not files_to_scan:
        return []
        
    # 3. Dynamically collect hardware typedefs from new_ir and files
    custom_typedefs = set()
    if new_ir and hasattr(new_ir, "registers"):
        for rname in new_ir.registers.keys():
            custom_typedefs.add(f"SFR_{ip.upper()}_{rname.upper()}")
            custom_typedefs.add(f"pSFR_{ip.upper()}_{rname.upper()}")
            
    # Also extract any SFR_ or pSFR_ names found in the files' contents
    for file_path in files_to_scan:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'\b(SFR_[A-Za-z0-9_]+|pSFR_[A-Za-z0-9_]+)\b', text):
                custom_typedefs.add(m.group(1))
        except Exception:
            continue

    # 4. Run refactoring on each file
    patched_files = []
    for file_path in files_to_scan:
        applied = refactor_file(file_path, auto_crs, custom_typedefs)
        if applied:
            patched_files.append(file_path)
            
    return patched_files

