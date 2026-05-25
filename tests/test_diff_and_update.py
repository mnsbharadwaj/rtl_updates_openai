"""
Tests for the structural diff engine and surgical updaters.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Field, Register, RegisterMap
from generators.sfr_generator import generate_sfr
from generators.lld_generator import generate_lld
from updater.diff_engine import diff_register_maps, DiffResult
from updater.sfr_updater import update_sfr
from updater.lld_updater import update_lld, _check_braces


# ── Helpers ──────────────────────────────────────────────────────────────────
def make_field(name, msb, lsb, access="RW", reset=0, desc="") -> Field:
    return Field(name, msb, lsb, access, reset, desc)


def make_reg(name, offset, fields) -> Register:
    return Register(name, offset, fields)


def make_map(peripheral, *regs) -> RegisterMap:
    return RegisterMap(peripheral, 0x0, list(regs))


# ════════════════════════════════════════════════════════════════════════════
# Diff engine tests
# ════════════════════════════════════════════════════════════════════════════
class TestDiffEngine:
    def test_no_change(self):
        f = make_field("EN", 0, 0, "RW", 0, "Enable")
        r = make_reg("CTRL", 0, [f])
        old = make_map("P", r)
        new = make_map("P", r)
        diff = diff_register_maps(old, new)
        assert not diff.has_changes

    def test_field_description_change_detected(self):
        old_f = make_field("EN", 0, 0, "RW", 0, "Enable block.")
        new_f = make_field("EN", 0, 0, "RW", 0, "Enable block (updated).")
        old = make_map("P", make_reg("CTRL", 0, [old_f]))
        new = make_map("P", make_reg("CTRL", 0, [new_f]))
        diff = diff_register_maps(old, new)
        assert diff.has_changes
        assert len(diff.changed_registers) == 1
        reg_diff = diff.changed_registers[0]
        assert reg_diff.field_diffs[0].field_name == "EN"
        assert reg_diff.field_diffs[0].is_changed

    def test_field_msb_change_detected(self):
        old_f = make_field("MODE", 2, 1, "RW")
        new_f = make_field("MODE", 3, 1, "RW")   # MSB widened
        old = make_map("P", make_reg("CTRL", 0, [old_f]))
        new = make_map("P", make_reg("CTRL", 0, [new_f]))
        diff = diff_register_maps(old, new)
        assert diff.has_changes

    def test_field_access_change_detected(self):
        old_f = make_field("FLAG", 0, 0, "RW")
        new_f = make_field("FLAG", 0, 0, "W1C")
        old = make_map("P", make_reg("STATUS", 4, [old_f]))
        new = make_map("P", make_reg("STATUS", 4, [new_f]))
        diff = diff_register_maps(old, new)
        assert diff.has_changes

    def test_register_added(self):
        f = make_field("EN", 0, 0)
        old = make_map("P", make_reg("CTRL", 0, [f]))
        new = make_map("P", make_reg("CTRL", 0, [f]), make_reg("INT", 4, [f]))
        diff = diff_register_maps(old, new)
        assert len(diff.added_registers) == 1
        assert diff.added_registers[0].reg_name == "INT"

    def test_register_removed(self):
        f = make_field("EN", 0, 0)
        old = make_map("P", make_reg("CTRL", 0, [f]), make_reg("INT", 4, [f]))
        new = make_map("P", make_reg("CTRL", 0, [f]))
        diff = diff_register_maps(old, new)
        assert len(diff.removed_registers) == 1
        assert diff.removed_registers[0].reg_name == "INT"

    def test_field_added_within_register(self):
        f1 = make_field("EN", 0, 0)
        f2 = make_field("MODE", 2, 1)
        old = make_map("P", make_reg("CTRL", 0, [f1]))
        new = make_map("P", make_reg("CTRL", 0, [f1, f2]))
        diff = diff_register_maps(old, new)
        assert diff.has_changes
        added_fields = [fd for rd in diff.changed_registers for fd in rd.field_diffs if fd.is_added]
        assert any(fd.field_name == "MODE" for fd in added_fields)

    def test_field_removed_within_register(self):
        f1 = make_field("EN", 0, 0)
        f2 = make_field("MODE", 2, 1)
        old = make_map("P", make_reg("CTRL", 0, [f1, f2]))
        new = make_map("P", make_reg("CTRL", 0, [f1]))
        diff = diff_register_maps(old, new)
        removed_fields = [fd for rd in diff.changed_registers for fd in rd.field_diffs if fd.is_removed]
        assert any(fd.field_name == "MODE" for fd in removed_fields)

    def test_reset_value_change_detected(self):
        old_f = make_field("TIMEOUT", 15, 0, "RW", 0x64)
        new_f = make_field("TIMEOUT", 15, 0, "RW", 0xC8)
        old = make_map("P", make_reg("TIMEOUT_REG", 0x10, [old_f]))
        new = make_map("P", make_reg("TIMEOUT_REG", 0x10, [new_f]))
        diff = diff_register_maps(old, new)
        assert diff.has_changes

    def test_offset_change_in_register(self):
        f = make_field("EN", 0, 0)
        old = make_map("P", make_reg("CTRL", 0x00, [f]))
        new = make_map("P", make_reg("CTRL", 0x04, [f]))   # offset changed
        diff = diff_register_maps(old, new)
        assert diff.has_changes

    def test_diff_summary_string(self):
        f = make_field("EN", 0, 0, "RW", 0, "Old desc")
        g = make_field("EN", 0, 0, "RW", 0, "New desc")
        old = make_map("P", make_reg("CTRL", 0, [f]))
        new = make_map("P", make_reg("CTRL", 0, [g]))
        diff = diff_register_maps(old, new)
        summary = diff.summary()
        assert "P" in summary
        assert "CHANGED" in summary


# ════════════════════════════════════════════════════════════════════════════
# SFR updater tests
# ════════════════════════════════════════════════════════════════════════════
class TestSFRUpdater:
    @pytest.fixture
    def initial_map(self):
        f1 = make_field("EN",   0, 0, "RW", 0, "Enable")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [f1, f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link up")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        return make_map("PERIPH", r1, r2)

    @pytest.fixture
    def sfr_file(self, tmp_path, initial_map):
        p = tmp_path / "periph_sfr.h"
        generate_sfr(initial_map, p)
        return p

    def test_unchanged_register_byte_identical(self, sfr_file, initial_map):
        before = sfr_file.read_text()
        # Change only CTRL_REG (update EN description triggers offset change)
        new_f1 = make_field("EN", 0, 0, "RW", 0, "Enable (updated)")
        new_f2 = make_field("MODE", 2, 1, "RW", 0, "")
        new_r1 = make_reg("CTRL_REG", 0x0, [new_f1, new_f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link up")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", new_r1, r2)
        diff = diff_register_maps(initial_map, new_map)

        update_sfr(sfr_file, new_map, diff)
        after = sfr_file.read_text()

        # STATUS_REG block should be byte-identical
        def extract_block(text, name):
            start = text.find(f"[sfr_gen:reg:begin:{name}]")
            end   = text.find(f"[sfr_gen:reg:end:{name}]") + len(f"[sfr_gen:reg:end:{name}]")
            return text[start:end] if start != -1 else ""

        before_status = extract_block(before, "STATUS_REG")
        after_status  = extract_block(after,  "STATUS_REG")
        assert before_status == after_status

    def test_changed_register_updated(self, sfr_file, initial_map):
        new_f1 = make_field("EN",   0, 0, "RW", 0, "Enable (v2)")
        new_f2 = make_field("MODE", 3, 1, "RW", 0, "")   # MSB widened
        new_r1 = make_reg("CTRL_REG", 0x0, [new_f1, new_f2])
        f3     = make_field("LINK_UP", 0, 0, "RO", 0, "Link up")
        r2     = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", new_r1, r2)
        diff = diff_register_maps(initial_map, new_map)
        update_sfr(sfr_file, new_map, diff)
        content = sfr_file.read_text()
        # New MODE MSB should appear
        assert "MODE_MSB      3" in content

    def test_new_register_appended(self, sfr_file, initial_map):
        f_new = make_field("TIMEOUT", 15, 0, "RW", 100, "Timeout value")
        r_new = make_reg("TIMEOUT_REG", 0x8, [f_new])
        f1 = make_field("EN",   0, 0, "RW", 0, "Enable")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [f1, f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link up")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", r1, r2, r_new)
        diff = diff_register_maps(initial_map, new_map)
        update_sfr(sfr_file, new_map, diff)
        content = sfr_file.read_text()
        assert "TIMEOUT_REG" in content
        assert "[sfr_gen:reg:begin:TIMEOUT_REG]" in content

    def test_removed_register_absent(self, sfr_file, initial_map):
        f1 = make_field("EN", 0, 0, "RW", 0, "Enable")
        r1 = make_reg("CTRL_REG", 0x0, [f1])
        # STATUS_REG removed
        new_map = make_map("PERIPH", r1)
        diff = diff_register_maps(initial_map, new_map)
        update_sfr(sfr_file, new_map, diff)
        content = sfr_file.read_text()
        assert "[sfr_gen:reg:begin:STATUS_REG]" not in content


# ════════════════════════════════════════════════════════════════════════════
# LLD updater tests
# ════════════════════════════════════════════════════════════════════════════
class TestLLDUpdater:
    @pytest.fixture
    def initial_map(self):
        f1 = make_field("EN",   0, 0, "RW", 0, "Enable the block.")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [f1, f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active.")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        return make_map("PERIPH", r1, r2)

    @pytest.fixture
    def lld_file(self, tmp_path, initial_map):
        p = tmp_path / "periph_lld.h"
        generate_lld(initial_map, p)
        return p

    def test_programmer_comment_preserved(self, lld_file, initial_map):
        # Inject a programmer comment in the free zone between sentinels
        content = lld_file.read_text()
        marker = "/* [sfr_gen:lld:end:CTRL_REG:EN] */"
        programmer_comment = "\n/* PROGRAMMER NOTE: do not remove this */\n"
        content = content.replace(marker, marker + programmer_comment)
        lld_file.write_text(content)

        # Now update a different field (LINK_UP description change)
        new_f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active (updated).")
        r2_new = make_reg("STATUS_REG", 0x4, [new_f3])
        f1 = make_field("EN",   0, 0, "RW", 0, "Enable the block.")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [f1, f2])
        new_map = make_map("PERIPH", r1, r2_new)
        diff = diff_register_maps(initial_map, new_map)

        update_lld(lld_file, new_map, diff)
        updated = lld_file.read_text()
        assert "PROGRAMMER NOTE: do not remove this" in updated

    def test_changed_field_block_regenerated(self, lld_file, initial_map):
        before = lld_file.read_text()
        new_f2 = make_field("MODE", 3, 1, "RW", 0, "Mode select updated.")
        f1 = make_field("EN", 0, 0, "RW", 0, "Enable the block.")
        r1 = make_reg("CTRL_REG", 0x0, [f1, new_f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active.")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", r1, r2)
        diff = diff_register_maps(initial_map, new_map)
        update_lld(lld_file, new_map, diff)
        after = lld_file.read_text()
        # The block was regenerated; the sentinel is still present
        assert "[sfr_gen:lld:begin:CTRL_REG:MODE]" in after

    def test_unchanged_field_block_byte_identical(self, lld_file, initial_map):
        before = lld_file.read_text()

        def extract(text, reg, field):
            start = text.find(f"[sfr_gen:lld:begin:{reg}:{field}]")
            end   = text.find(f"[sfr_gen:lld:end:{reg}:{field}]") + len(f"[sfr_gen:lld:end:{reg}:{field}]")
            return text[start:end] if start != -1 else ""

        # Change only EN field
        new_f1 = make_field("EN", 0, 0, "RW", 0, "Enable (new desc).")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [new_f1, f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active.")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", r1, r2)
        diff = diff_register_maps(initial_map, new_map)
        update_lld(lld_file, new_map, diff)
        after = lld_file.read_text()

        # MODE and LINK_UP blocks should be identical
        assert extract(before, "CTRL_REG", "MODE")    == extract(after, "CTRL_REG", "MODE")
        assert extract(before, "STATUS_REG", "LINK_UP") == extract(after, "STATUS_REG", "LINK_UP")

    def test_brace_balance_validator(self):
        assert _check_braces("void f() { int x = 0; }")
        assert not _check_braces("void f() { int x = 0;")
        assert _check_braces("")
        assert _check_braces('char s[] = "not { a brace";')

    def test_removed_field_block_absent(self, lld_file, initial_map):
        # Remove MODE field
        f1 = make_field("EN", 0, 0, "RW", 0, "Enable the block.")
        r1 = make_reg("CTRL_REG", 0x0, [f1])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active.")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", r1, r2)
        diff = diff_register_maps(initial_map, new_map)
        update_lld(lld_file, new_map, diff)
        content = lld_file.read_text()
        assert "[sfr_gen:lld:begin:CTRL_REG:MODE]" not in content

    def test_original_restored_on_brace_error(self, lld_file, initial_map):
        """If LLM returns malformed code, original file must be restored."""
        original_content = lld_file.read_text()

        def bad_llm(field, fn_prefix):
            return "void bad_fn(uintptr_t base) { /* unclosed brace */"

        new_f1 = make_field("EN", 0, 0, "RW", 0, "Enable (trigger LLM).")
        f2 = make_field("MODE", 2, 1, "RW", 0, "")
        r1 = make_reg("CTRL_REG", 0x0, [new_f1, f2])
        f3 = make_field("LINK_UP", 0, 0, "RO", 0, "Link is active.")
        r2 = make_reg("STATUS_REG", 0x4, [f3])
        new_map = make_map("PERIPH", r1, r2)
        diff = diff_register_maps(initial_map, new_map)

        with pytest.raises(RuntimeError, match="brace-balance"):
            update_lld(lld_file, new_map, diff, llm_fn=bad_llm)

        # File must be restored to original
        assert lld_file.read_text() == original_content
