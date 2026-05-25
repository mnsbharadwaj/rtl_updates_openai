"""
Tests for sfr_generator and lld_generator.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Field, Register, RegisterMap
from generators.sfr_generator import generate_sfr, _REG_BEGIN, _REG_END
from generators.lld_generator import generate_lld


# ── Minimal fixture register map ─────────────────────────────────────────────
@pytest.fixture
def simple_map() -> RegisterMap:
    fields = [
        Field("EN",    0, 0, "RW",  0, "Enables the block."),
        Field("MODE",  2, 1, "RW",  0, ""),
        Field("RESET", 3, 3, "WO",  0, "Soft reset."),
        Field("RSVD", 31, 4, "RO",  0, ""),
    ]
    reg = Register("CTRL_REG", 0x0000, fields, "Main control register")
    status_fields = [
        Field("LINK_UP", 0, 0, "RO", 0, "Link is up."),
        Field("SPEED",   2, 1, "RO", 0, ""),
    ]
    reg2 = Register("STATUS_REG", 0x0004, status_fields, "Link status")
    return RegisterMap("MY_PERIPH", 0x40000000, [reg, reg2])


# ════════════════════════════════════════════════════════════════════════════
# SFR generator tests
# ════════════════════════════════════════════════════════════════════════════
class TestSFRGenerator:
    def test_header_guard_present(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "#ifndef MY_PERIPH_SFR_H" in sfr
        assert "#define MY_PERIPH_SFR_H" in sfr
        assert "#endif /* MY_PERIPH_SFR_H */" in sfr

    def test_base_address_macro(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "MY_PERIPH_BASE" in sfr
        assert "0x40000000" in sfr

    def test_register_offset_macro(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "#define MY_PERIPH_CTRL_REG_OFFSET" in sfr

    def test_field_mask_macro(self, simple_map):
        sfr = generate_sfr(simple_map)
        # EN [0:0] → mask = 0x00000001
        assert "0x00000001U" in sfr

    def test_field_shift_macro(self, simple_map):
        sfr = generate_sfr(simple_map)
        # MODE [2:1] → shift = 1
        assert "#define MY_PERIPH_CTRL_REG_MODE_SHIFT" in sfr

    def test_sentinel_begin_present(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "[sfr_gen:reg:begin:CTRL_REG]" in sfr

    def test_sentinel_end_present(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "[sfr_gen:reg:end:CTRL_REG]" in sfr

    def test_all_registers_present(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "CTRL_REG" in sfr
        assert "STATUS_REG" in sfr

    def test_auto_generated_banner(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "AUTO-GENERATED" in sfr

    def test_write_to_file(self, simple_map, tmp_path):
        out = tmp_path / "test_sfr.h"
        generate_sfr(simple_map, out)
        assert out.exists()
        content = out.read_text()
        assert "MY_PERIPH_SFR_H" in content

    def test_reset_value_hex(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "RESET" in sfr   # RESET field macro present

    def test_rsvd_field_included(self, simple_map):
        sfr = generate_sfr(simple_map)
        assert "RSVD" in sfr

    def test_mode_mask_correct(self, simple_map):
        sfr = generate_sfr(simple_map)
        # MODE [2:1] → mask = ((1<<2)-1)<<1 = 3<<1 = 6 → 0x00000006
        assert "0x00000006U" in sfr


# ════════════════════════════════════════════════════════════════════════════
# LLD generator tests
# ════════════════════════════════════════════════════════════════════════════
class TestLLDGenerator:
    def test_header_guard(self, simple_map):
        lld = generate_lld(simple_map)
        assert "#ifndef MY_PERIPH_LLD_H" in lld
        assert "#define MY_PERIPH_LLD_H" in lld

    def test_include_sfr_header(self, simple_map):
        lld = generate_lld(simple_map)
        assert 'my_periph_sfr.h' in lld

    def test_ro_field_getter_only(self, simple_map):
        lld = generate_lld(simple_map)
        # LINK_UP is RO → should have getter, no setter
        assert "link_up_get" in lld
        assert "link_up_set" not in lld

    def test_wo_field_write_only(self, simple_map):
        lld = generate_lld(simple_map)
        # RESET is WO → write only, no getter
        assert "reset_write" in lld
        assert "reset_get" not in lld

    def test_rw_field_getter_and_setter(self, simple_map):
        lld = generate_lld(simple_map)
        assert "en_get" in lld
        assert "en_set" in lld

    def test_sentinel_begin_end(self, simple_map):
        lld = generate_lld(simple_map)
        assert "[sfr_gen:lld:begin:CTRL_REG:EN]" in lld
        assert "[sfr_gen:lld:end:CTRL_REG:EN]" in lld

    def test_no_description_no_stub_semantic(self, simple_map):
        lld = generate_lld(simple_map)
        # MODE has no description → no LLM stub comment about it
        # (stub appears only when description is non-empty)
        # The stub marker is [LLM-TODO]
        # We verify no [LLM-TODO] block for MODE
        assert "my_periph_ctrl_reg_mode_get" in lld   # getter present
        # LLM-TODO for MODE should NOT be there (no description)
        lld_lines = lld.split("\n")
        in_mode_block = False
        has_llm_todo_in_mode = False
        for line in lld_lines:
            if "[sfr_gen:lld:begin:CTRL_REG:MODE]" in line:
                in_mode_block = True
            if "[sfr_gen:lld:end:CTRL_REG:MODE]" in line:
                in_mode_block = False
            if in_mode_block and "[LLM-TODO]" in line:
                has_llm_todo_in_mode = True
        assert not has_llm_todo_in_mode

    def test_with_description_llm_stub_present(self, simple_map):
        lld = generate_lld(simple_map)
        # EN has a description → stub comment expected (no llm_fn passed)
        assert "[LLM-TODO]" in lld

    def test_llm_fn_called_for_described_field(self, simple_map):
        called = []
        def fake_llm(field, fn_prefix):
            called.append((field.name, fn_prefix))
            return f"/* fake LLM output for {field.name} */"

        lld = generate_lld(simple_map, llm_fn=fake_llm)
        # EN and RESET and LINK_UP have descriptions → LLM should be called
        described_fields = {f.name for r in simple_map.registers for f in r.fields if f.has_description}
        called_fields = {name for name, _ in called}
        assert described_fields == called_fields

    def test_llm_output_embedded(self, simple_map):
        def fake_llm(field, fn_prefix):
            return f"void {fn_prefix}_semantic_action(uintptr_t base) {{ /* {field.name} */ }}"

        lld = generate_lld(simple_map, llm_fn=fake_llm)
        assert "semantic_action" in lld

    def test_write_to_file(self, simple_map, tmp_path):
        out = tmp_path / "test_lld.h"
        generate_lld(simple_map, out)
        assert out.exists()
        assert "MY_PERIPH_LLD_H" in out.read_text()

    def test_doxygen_comments_present(self, simple_map):
        lld = generate_lld(simple_map)
        assert "/** @brief" in lld

    def test_brace_balance(self, simple_map):
        """Generated lld.h must have balanced braces."""
        lld = generate_lld(simple_map)
        assert lld.count("{") == lld.count("}")
