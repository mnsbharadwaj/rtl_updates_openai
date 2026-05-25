"""
Structural diff engine for RegisterMap objects.

Compares two RegisterMap instances (old vs new) by walking the Python
dataclass tree — NO hashes stored in generated files.

Returns a DiffResult describing:
  - Registers added   (in new, not in old)
  - Registers removed (in old, not in new)
  - Registers changed (same name, but one or more fields differ)
    - Fields added / removed / changed inside those registers
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from models import Field, Register, RegisterMap


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass
class FieldDiff:
    field_name: str
    old_field: Optional[Field]   # None → added
    new_field: Optional[Field]   # None → removed

    @property
    def is_added(self) -> bool:   return self.old_field is None
    @property
    def is_removed(self) -> bool: return self.new_field is None
    @property
    def is_changed(self) -> bool: return (
        self.old_field is not None and self.new_field is not None
    )


@dataclass
class RegisterDiff:
    reg_name: str
    old_reg: Optional[Register]      # None → added register
    new_reg: Optional[Register]      # None → removed register
    field_diffs: List[FieldDiff] = field(default_factory=list)

    @property
    def is_added(self) -> bool:   return self.old_reg is None
    @property
    def is_removed(self) -> bool: return self.new_reg is None
    @property
    def is_changed(self) -> bool: return bool(self.field_diffs)


@dataclass
class DiffResult:
    peripheral: str
    register_diffs: List[RegisterDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.register_diffs)

    @property
    def added_registers(self) -> List[RegisterDiff]:
        return [d for d in self.register_diffs if d.is_added]

    @property
    def removed_registers(self) -> List[RegisterDiff]:
        return [d for d in self.register_diffs if d.is_removed]

    @property
    def changed_registers(self) -> List[RegisterDiff]:
        return [d for d in self.register_diffs if d.is_changed]

    def summary(self) -> str:
        lines = [f"Diff for peripheral: {self.peripheral}"]
        lines.append(f"  Added   registers : {len(self.added_registers)}")
        lines.append(f"  Removed registers : {len(self.removed_registers)}")
        lines.append(f"  Changed registers : {len(self.changed_registers)}")
        for rd in self.changed_registers:
            for fd in rd.field_diffs:
                tag = "ADDED" if fd.is_added else "REMOVED" if fd.is_removed else "CHANGED"
                lines.append(f"    [{tag}] {rd.reg_name}.{fd.field_name}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Field comparison
# ---------------------------------------------------------------------------
def _fields_equal(a: Field, b: Field) -> bool:
    """Two fields are equal when every attribute that affects code-gen matches."""
    return (
        a.name        == b.name
        and a.msb     == b.msb
        and a.lsb     == b.lsb
        and a.access  == b.access
        and a.reset_value == b.reset_value
        and a.description.strip() == b.description.strip()
    )


def _diff_fields(
    old_reg: Register,
    new_reg: Register,
) -> List[FieldDiff]:
    old_map: Dict[str, Field] = {f.name: f for f in old_reg.fields}
    new_map: Dict[str, Field] = {f.name: f for f in new_reg.fields}

    diffs: List[FieldDiff] = []

    # Changed or removed fields
    for name, old_f in old_map.items():
        if name not in new_map:
            diffs.append(FieldDiff(name, old_f, None))
        elif not _fields_equal(old_f, new_map[name]):
            diffs.append(FieldDiff(name, old_f, new_map[name]))

    # Added fields
    for name, new_f in new_map.items():
        if name not in old_map:
            diffs.append(FieldDiff(name, None, new_f))

    return diffs


# ---------------------------------------------------------------------------
# Register comparison
# ---------------------------------------------------------------------------
def _diff_registers(
    old_map: RegisterMap,
    new_map: RegisterMap,
) -> List[RegisterDiff]:
    old_regs: Dict[str, Register] = {r.name: r for r in old_map.registers}
    new_regs: Dict[str, Register] = {r.name: r for r in new_map.registers}

    diffs: List[RegisterDiff] = []

    for name, old_r in old_regs.items():
        if name not in new_regs:
            diffs.append(RegisterDiff(name, old_r, None))
        else:
            new_r = new_regs[name]
            # Quick offset / description check first
            field_diffs = _diff_fields(old_r, new_r)
            meta_changed = (
                old_r.offset != new_r.offset
                or old_r.description.strip() != new_r.description.strip()
            )
            if field_diffs or meta_changed:
                diffs.append(RegisterDiff(name, old_r, new_r, field_diffs))

    for name, new_r in new_regs.items():
        if name not in old_regs:
            diffs.append(RegisterDiff(name, None, new_r))

    return diffs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def diff_register_maps(old: RegisterMap, new: RegisterMap) -> DiffResult:
    """
    Structurally compare two RegisterMap objects.

    Returns a DiffResult with all added/removed/changed registers and fields.
    No files are read or written — pure in-memory comparison.
    """
    result = DiffResult(peripheral=new.peripheral)
    result.register_diffs = _diff_registers(old, new)
    return result
