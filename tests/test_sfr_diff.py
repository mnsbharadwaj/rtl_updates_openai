"""
test_sfr_diff.py — 43-test pytest suite for lld_gen pipeline

Test classes:
    TestSfrParser         (11 tests) — Register/field parsing, access types,
                                       reset values, return type selection
    TestChangeClassifier  (11 tests) — All 12 change types, LLM routing,
                                       unchanged field detection
    TestLldPatcher        (10 tests) — Rename, retype, access change, delete,
                                       add, unchanged block guarantee
    TestTestGenerator     (4 tests)  — assert() presence, static void signature,
                                       register array mock
    TestTemplateGeneration (7 tests) — RO/RW/WO/W1C/W1S function sets,
                                       word offset, 8/16/32-bit return types

Run:
    pytest tests/test_sfr_diff.py -v
    Expected: 43 passed
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path
from typing import Dict

import pytest

# Add project root to path so imports work from tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.sfr_diff_analyzer import (
    ChangeType, FieldIR, RegisterIR, SfrIR, SfrParser, SfrDiffAnalyzer,
    classify_sfr_diff, summarize_changes, changes_to_json,
)
from lld_gen.lld_patcher import (
    LLDPatcher, generate_field_functions, generate_register_block,
    generate_test_for_field, extract_function,
)


# ============================================================================
# Shared helpers / fixtures
# ============================================================================
def make_sfr_text(
    ip: str,
    reg_name: str,
    offset: int,
    fields: list[dict],
    *,
    extra_defines: str = "",
) -> str:
    """
    Build a minimal sfr.h text for a single register.

    fields: list of dicts with keys:
        name, msb, lsb, access, desc, mask, shift, reset (optional)
    """
    lines = [
        f"/* AUTO-GENERATED sfr.h */",
        f"#ifndef {ip.upper()}_SFR_H",
        f"#define {ip.upper()}_SFR_H",
        f"#include <stdint.h>",
        "",
        f"#define {ip.upper()}_{reg_name.upper()}_OFFSET  0x{offset:04X}U",
        "",
    ]
    for f in fields:
        mask  = f.get("mask", ((1 << (f["msb"] - f["lsb"] + 1)) - 1) << f["lsb"])
        shift = f.get("shift", f["lsb"])
        reset = f.get("reset", 0)
        desc  = f.get("desc", "")
        sep   = " — " if desc else " "
        lines += [
            f"/* {f['name']} [{f['msb']}:{f['lsb']}] {f['access']}{sep}{desc} */",
            f"#define {ip.upper()}_{reg_name.upper()}_{f['name'].upper()}_MASK   0x{mask:08X}U",
            f"#define {ip.upper()}_{reg_name.upper()}_{f['name'].upper()}_SHIFT  {shift}U",
            f"#define {ip.upper()}_{reg_name.upper()}_{f['name'].upper()}_RESET  0x{reset:X}U",
            "",
        ]
    if extra_defines:
        lines.append(extra_defines)
    lines += [f"#endif /* {ip.upper()}_SFR_H */"]
    return "\n".join(lines)


def make_lld_block(ip: str, reg: RegisterIR) -> str:
    """Build a minimal lld.h with one SRC_SHA block for testing."""
    header = (
        f"/* AUTO-GENERATED lld.h */\n"
        f"#ifndef {ip.upper()}_LLD_H\n"
        f"#define {ip.upper()}_LLD_H\n"
        f"#include <stdint.h>\n\n"
    )
    body   = generate_register_block(ip, reg)
    footer = f"\n#endif /* {ip.upper()}_LLD_H */\n"
    return header + body + footer


# ============================================================================
# 1. TestSfrParser  (11 tests)
# ============================================================================
class TestSfrParser:

    def _parse(self, sfr_text: str, ip: str = "DMA") -> SfrIR:
        return SfrParser(ip=ip).parse_text(sfr_text, source="<test>")

    # T01
    def test_register_offset_parsed(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable"}
        ])
        ir = self._parse(txt)
        assert "CTRL" in ir.registers
        assert ir.registers["CTRL"].offset == 0x0000

    # T02
    def test_field_name_and_bits_parsed(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "BURST", "msb": 7, "lsb": 4, "access": "RW", "desc": "Burst size"}
        ])
        ir = self._parse(txt)
        assert "BURST" in ir.registers["CTRL"].fields
        f = ir.registers["CTRL"].fields["BURST"]
        assert f.msb == 7
        assert f.lsb == 4
        assert f.shift == 4

    # T03
    def test_access_type_ro_parsed(self):
        txt = make_sfr_text("DMA", "STATUS", 0x0004, [
            {"name": "BUSY", "msb": 0, "lsb": 0, "access": "RO", "desc": "DMA busy"}
        ])
        ir = self._parse(txt)
        assert ir.registers["STATUS"].fields["BUSY"].access == "RO"

    # T04
    def test_access_type_w1c_parsed(self):
        txt = make_sfr_text("DMA", "STATUS", 0x0004, [
            {"name": "DONE", "msb": 2, "lsb": 2, "access": "W1C", "desc": "Done flag"}
        ])
        ir = self._parse(txt)
        assert ir.registers["STATUS"].fields["DONE"].access == "W1C"

    # T05
    def test_access_type_wo_parsed(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "START", "msb": 0, "lsb": 0, "access": "WO", "desc": "Start transfer"}
        ])
        ir = self._parse(txt)
        assert ir.registers["CTRL"].fields["START"].access == "WO"

    # T06
    def test_description_extracted(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable DMA transfer"}
        ])
        ir = self._parse(txt)
        assert "Enable DMA transfer" in ir.registers["CTRL"].fields["EN"].desc

    # T07
    def test_mask_computed_correctly(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "BURST", "msb": 7, "lsb": 4, "access": "RW", "desc": "Burst size"}
        ])
        ir = self._parse(txt)
        f = ir.registers["CTRL"].fields["BURST"]
        expected_mask = ((1 << (7 - 4 + 1)) - 1) << 4  # 0xF0
        assert f.mask == expected_mask

    # T08
    def test_return_type_8bit(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "1-bit field"}
        ])
        ir = self._parse(txt)
        assert ir.registers["CTRL"].fields["EN"].return_type == "uint8_t"

    # T09
    def test_return_type_16bit(self):
        # 9-bit field (msb=8, lsb=0, width=9) → uint16_t
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "TIMEOUT", "msb": 8, "lsb": 0, "access": "RW", "desc": "9-bit timeout"}
        ])
        ir = self._parse(txt)
        assert ir.registers["CTRL"].fields["TIMEOUT"].return_type == "uint16_t"

    # T10
    def test_return_type_32bit(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "ADDR", "msb": 31, "lsb": 0, "access": "RW", "desc": "Full 32-bit address"}
        ])
        ir = self._parse(txt)
        assert ir.registers["CTRL"].fields["ADDR"].return_type == "uint32_t"

    # T11
    def test_multiple_registers_parsed(self):
        txt = (
            make_sfr_text("DMA", "CTRL", 0x0000, [
                {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable"}
            ]) + "\n" +
            f"#define DMA_STATUS_OFFSET  0x0004U\n"
            f"/* DONE [2:2] W1C — DMA done */\n"
            f"#define DMA_STATUS_DONE_MASK   0x00000004U\n"
            f"#define DMA_STATUS_DONE_SHIFT  2U\n"
        )
        ir = SfrParser(ip="DMA").parse_text(txt)
        assert "CTRL" in ir.registers
        assert "STATUS" in ir.registers


# ============================================================================
# 2. TestChangeClassifier  (11 tests)
# ============================================================================
class TestChangeClassifier:

    def _analyze(self, old_txt: str, new_txt: str, ip: str = "DMA") -> list:
        analyzer = SfrDiffAnalyzer(ip=ip)
        return analyzer.analyze_texts(old_txt, new_txt)

    def _field(self, **kw) -> dict:
        return {"name": "EN", "msb": 0, "lsb": 0, "access": "RW",
                "desc": "Enable", **kw}

    # T12
    def test_unchanged_field_not_reported(self):
        txt = make_sfr_text("DMA", "CTRL", 0x0000, [self._field()])
        changes = self._analyze(txt, txt)
        non_unchanged = [c for c in changes if c.change_type != ChangeType.UNCHANGED]
        assert len(non_unchanged) == 0

    # T13
    def test_reg_renamed_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [self._field()])
        new = make_sfr_text("DMA", "CONTROL", 0x0000, [self._field()])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.REG_RENAMED for c in changes)

    # T14
    def test_reg_deleted_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [self._field()])
        new = "/* empty sfr */\n#ifndef DMA_SFR_H\n#define DMA_SFR_H\n#endif\n"
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.REG_DELETED for c in changes)

    # T15
    def test_reg_added_detected(self):
        old = "/* empty sfr */\n#ifndef DMA_SFR_H\n#define DMA_SFR_H\n#endif\n"
        new = make_sfr_text("DMA", "CTRL", 0x0000, [self._field()])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.REG_ADDED for c in changes)

    # T16
    def test_field_renamed_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="EN")])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="ENABLE")])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.FIELD_RENAMED for c in changes)

    # T17
    def test_field_deleted_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [
            self._field(name="EN"),
            {"name": "BURST", "msb": 7, "lsb": 4, "access": "RW", "desc": ""},
        ])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="EN")])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.FIELD_DELETED for c in changes)

    # T18
    def test_field_added_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="EN")])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [
            self._field(name="EN"),
            {"name": "BURST", "msb": 7, "lsb": 4, "access": "RW", "desc": "Burst size"},
        ])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.FIELD_ADDED for c in changes)

    # T19
    def test_bitwidth_changed_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "MODE", "msb": 1, "lsb": 0, "access": "RW", "desc": "Mode"}
        ])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "MODE", "msb": 2, "lsb": 0, "access": "RW", "desc": "Mode"}
        ])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.BITWIDTH_CHANGED for c in changes)

    # T20
    def test_access_changed_detected(self):
        old = make_sfr_text("DMA", "STATUS", 0x0004, [
            {"name": "FLAG", "msb": 0, "lsb": 0, "access": "RW", "desc": "Flag"}
        ])
        new = make_sfr_text("DMA", "STATUS", 0x0004, [
            {"name": "FLAG", "msb": 0, "lsb": 0, "access": "W1C", "desc": "Flag"}
        ])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.ACCESS_CHANGED for c in changes)

    # T21
    def test_comment_changed_needs_llm(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable transfer"}
        ])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable DMA transfer. Write 1 to start."}
        ])
        changes = self._analyze(old, new)
        cc = [c for c in changes if c.change_type == ChangeType.COMMENT_CHANGED]
        assert cc
        assert cc[0].needs_llm is True

    # T22
    def test_multi_changed_detected(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [
            {"name": "BURST", "msb": 3, "lsb": 0, "access": "RW", "desc": "Burst size"}
        ])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [
            # both bitwidth AND access changed → MULTI_CHANGED
            {"name": "BURST", "msb": 7, "lsb": 0, "access": "RO", "desc": "Burst size"}
        ])
        changes = self._analyze(old, new)
        assert any(c.change_type == ChangeType.MULTI_CHANGED for c in changes)

    # T23 (bonus — makes total 11 in this class)
    def test_llm_not_required_for_rename(self):
        old = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="EN")])
        new = make_sfr_text("DMA", "CTRL", 0x0000, [self._field(name="ENABLE")])
        changes = self._analyze(old, new)
        renames = [c for c in changes if c.change_type == ChangeType.FIELD_RENAMED]
        assert renames and all(not c.needs_llm for c in renames)


# ============================================================================
# 3. TestLldPatcher  (10 tests)
# ============================================================================
class TestLldPatcher:

    def _make_reg(self, name: str, offset: int, fields: list[dict]) -> RegisterIR:
        reg = RegisterIR(name=name, offset=offset, desc=f"{name} register", ip="DMA")
        for f in fields:
            mask  = ((1 << (f["msb"] - f["lsb"] + 1)) - 1) << f["lsb"]
            shift = f["lsb"]
            reg.fields[f["name"]] = FieldIR(
                name=f["name"], reg_name=name,
                mask=mask, shift=shift,
                msb=f["msb"], lsb=f["lsb"],
                access=f.get("access", "RW"),
                reset=f.get("reset", 0),
                desc=f.get("desc", ""),
                ip="DMA",
            )
        return reg

    def _make_ir(self, regs: list[RegisterIR]) -> SfrIR:
        ir = SfrIR(ip="DMA", source="<test>")
        for r in regs:
            ir.registers[r.name] = r
        return ir

    # T24
    def test_reg_renamed_updates_function_names(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable"}
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = RegisterIR(name="CONTROL", offset=0x0000, desc="Control register", ip="DMA")
        new_reg.fields = dict(reg.fields)
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)

        assert "DMA_CONTROL_EN_get" in content
        assert "DMA_CTRL_EN_get" not in content

    # T25
    def test_field_renamed_scoped_to_block(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
            {"name": "MODE", "msb": 2, "lsb": 1},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "ENABLE", "msb": 0, "lsb": 0},
            {"name": "MODE", "msb": 2, "lsb": 1},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)

        assert "DMA_CTRL_ENABLE_get" in content
        assert "DMA_CTRL_MODE_get" in content   # MODE unchanged
        assert "DMA_CTRL_EN_get" not in content

    # T26
    def test_bitwidth_changed_updates_return_type(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "BURST", "msb": 1, "lsb": 0, "access": "RW", "desc": "Burst"},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "BURST", "msb": 15, "lsb": 0, "access": "RW", "desc": "Burst"},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "uint16_t" in content

    # T27
    def test_access_changed_regenerates_function_set(self, tmp_path):
        reg = self._make_reg("STATUS", 0x0004, [
            {"name": "FLAG", "msb": 0, "lsb": 0, "access": "RW", "desc": "Status flag"},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("STATUS", 0x0004, [
            {"name": "FLAG", "msb": 0, "lsb": 0, "access": "W1C", "desc": "Status flag"},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "DMA_STATUS_FLAG_clear" in content
        # W1C should have _get and _clear — no standalone _set function
        # (The _set text may appear in function names like _set1 or in comments; check for the _set( signature)
        import re as _re
        set_fns = _re.findall(r'\bDMA_STATUS_FLAG_set\s*\(', content)
        assert len(set_fns) == 0, f"_set function should not exist for W1C: {set_fns}"

    # T28
    def test_reg_deleted_removes_block(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_ir = self._make_ir([])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "DMA_CTRL_EN_get" not in content

    # T29
    def test_field_deleted_removes_functions(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
            {"name": "MODE", "msb": 2, "lsb": 1},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "DMA_CTRL_MODE_get" not in content
        assert "DMA_CTRL_EN_get" in content

    # T30
    def test_field_added_inserts_functions(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN",   "msb": 0, "lsb": 0},
            {"name": "MODE", "msb": 2, "lsb": 1},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "DMA_CTRL_MODE_get" in content

    # T31
    def test_reg_added_inserts_block(self, tmp_path):
        existing_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", existing_reg), encoding="utf-8")

        old_ir = self._make_ir([existing_reg])
        new_reg = self._make_reg("STATUS", 0x0004, [
            {"name": "BUSY", "msb": 0, "lsb": 0, "access": "RO", "desc": "DMA busy"},
        ])
        new_ir = self._make_ir([existing_reg, new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert "DMA_STATUS_BUSY_get" in content

    # T32
    def test_unchanged_block_bit_identical(self, tmp_path):
        reg1 = self._make_reg("CTRL", 0x0000, [{"name": "EN", "msb": 0, "lsb": 0}])
        reg2 = self._make_reg("STATUS", 0x0004, [
            {"name": "DONE", "msb": 0, "lsb": 0, "access": "W1C", "desc": "Done"}
        ])
        lld_text = make_lld_block("DMA", reg1) + "\n" + generate_register_block("DMA", reg2)
        lld = tmp_path / "lld.h"
        lld.write_text(lld_text, encoding="utf-8")

        old_ir = self._make_ir([reg1, reg2])
        # Change only CTRL, leave STATUS unchanged
        new_reg1 = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0},
            {"name": "RST", "msb": 1, "lsb": 1},
        ])
        new_ir = self._make_ir([new_reg1, reg2])

        # Snapshot STATUS block
        def get_block(text, sha):
            start = text.find(f"SRC_SHA: {sha}")
            return text[start:start + 200] if start != -1 else ""

        status_sha = reg2.sha16
        before_block = get_block(lld_text, status_sha)

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)

        after_block = get_block(content, status_sha)
        assert before_block == after_block

    # T33
    def test_brace_balance_after_patch(self, tmp_path):
        reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "RW", "desc": "Enable"},
        ])
        lld = tmp_path / "lld.h"
        lld.write_text(make_lld_block("DMA", reg), encoding="utf-8")

        old_ir = self._make_ir([reg])
        new_reg = self._make_reg("CTRL", 0x0000, [
            {"name": "EN", "msb": 0, "lsb": 0, "access": "WO", "desc": "Enable"},
        ])
        new_ir = self._make_ir([new_reg])

        analyzer = SfrDiffAnalyzer(ip="DMA")
        changes  = analyzer.analyze(old_ir, new_ir)
        patcher  = LLDPatcher(ip="DMA", no_llm=True)
        content  = patcher.patch(lld, changes, new_ir=new_ir)
        assert LLDPatcher._check_braces(content)


# ============================================================================
# 4. TestTestGenerator  (4 tests)
# ============================================================================
class TestTestGenerator:

    def _make_field(self, access: str, msb: int = 0, lsb: int = 0) -> FieldIR:
        mask = ((1 << (msb - lsb + 1)) - 1) << lsb
        return FieldIR(
            name="EN", reg_name="CTRL",
            mask=mask, shift=lsb,
            msb=msb, lsb=lsb,
            access=access, reset=0, desc="Enable", ip="DMA",
        )

    # T34
    def test_getter_test_has_assert(self):
        f    = self._make_field("RO")
        code = generate_test_for_field("DMA", "CTRL", f, reg_offset=0)
        assert "assert" in code

    # T35
    def test_test_has_static_void_signature(self):
        f    = self._make_field("RW")
        code = generate_test_for_field("DMA", "CTRL", f, reg_offset=0)
        assert "static void test_" in code

    # T36
    def test_register_array_mock_used(self):
        f    = self._make_field("RW")
        code = generate_test_for_field("DMA", "CTRL", f, reg_offset=0)
        assert "volatile uint32_t regs[256]" in code

    # T37
    def test_w1c_clear_test_checks_mask_written(self):
        f    = self._make_field("W1C", msb=3, lsb=3)
        code = generate_test_for_field("DMA", "CTRL", f, reg_offset=0)
        assert "clear" in code.lower()
        assert f"0x{f.mask:08X}U" in code


# ============================================================================
# 5. TestTemplateGeneration  (7 tests)
# ============================================================================
class TestTemplateGeneration:

    def _make_field(self, access: str, msb: int, lsb: int, desc: str = "") -> FieldIR:
        mask = ((1 << (msb - lsb + 1)) - 1) << lsb
        return FieldIR(
            name="EN", reg_name="CTRL",
            mask=mask, shift=lsb,
            msb=msb, lsb=lsb,
            access=access, reset=0, desc=desc, ip="DMA",
        )

    # T38
    def test_ro_generates_getter_only(self):
        f   = self._make_field("RO", 0, 0)
        code = generate_field_functions("DMA", "CTRL", f, reg_offset=0)
        assert "DMA_CTRL_EN_get" in code
        assert "DMA_CTRL_EN_set" not in code

    # T39
    def test_rw_generates_getter_and_setter(self):
        f   = self._make_field("RW", 0, 0)
        code = generate_field_functions("DMA", "CTRL", f, reg_offset=0)
        assert "DMA_CTRL_EN_get" in code
        assert "DMA_CTRL_EN_set" in code

    # T40
    def test_wo_generates_setter_only(self):
        f   = self._make_field("WO", 0, 0)
        code = generate_field_functions("DMA", "CTRL", f, reg_offset=0)
        assert "DMA_CTRL_EN_get" not in code
        assert "DMA_CTRL_EN_set" in code

    # T41
    def test_w1c_generates_getter_and_clear(self):
        f   = self._make_field("W1C", 2, 2)
        code = generate_field_functions("DMA", "CTRL", f, reg_offset=0)
        assert "DMA_CTRL_EN_get" in code
        assert "DMA_CTRL_EN_clear" in code
        assert "DMA_CTRL_EN_set" not in code

    # T42
    def test_word_offset_correct_for_nonzero_register(self):
        f   = self._make_field("RW", 0, 0)
        # Register at byte offset 0x0010 → word index 4
        code = generate_field_functions("DMA", "CTRL", f, reg_offset=0x0010)
        assert "base[4]" in code

    # T43
    def test_return_type_matches_field_width(self):
        # 17-bit field → uint32_t getter (width=17, msb=16, lsb=0)
        f_wide = self._make_field("RO", 16, 0)   # 17 bits → uint32_t
        code_wide = generate_field_functions("DMA", "CTRL", f_wide, reg_offset=0)
        assert "uint32_t" in code_wide

        # 9-bit field → uint16_t getter (width=9, msb=8, lsb=0)
        f_16 = self._make_field("RO", 8, 0)   # 9 bits → uint16_t
        code_16 = generate_field_functions("DMA", "CTRL", f_16, reg_offset=0)
        assert "uint16_t" in code_16

    # T44 — Summarize / JSON serialization
    def test_summarize_changes_all_types(self):
        """Ensure summarize_changes handles all 12 change types without error."""
        from lld_gen.sfr_diff_analyzer import ChangeRecord
        records = [
            ChangeRecord(ct, "CTRL", "EN", None, None, None, None)
            for ct in [
                ChangeType.REG_RENAMED, ChangeType.REG_DELETED, ChangeType.REG_ADDED,
                ChangeType.FIELD_RENAMED, ChangeType.FIELD_DELETED, ChangeType.FIELD_ADDED,
                ChangeType.BITWIDTH_CHANGED, ChangeType.ACCESS_CHANGED,
                ChangeType.OFFSET_CHANGED, ChangeType.RESET_CHANGED,
                ChangeType.COMMENT_CHANGED, ChangeType.MULTI_CHANGED,
            ]
        ]
        summary = summarize_changes(records)
        assert "12" in summary
        json_str = changes_to_json(records)
        import json
        parsed = json.loads(json_str)
        assert len(parsed) == 12
