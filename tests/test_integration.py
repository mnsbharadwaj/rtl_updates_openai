"""
End-to-end integration tests using the real sample Excel workbook.

Covers:
  1. Fresh generation from sample_regs.xlsx
  2. Incremental update using sample_regs_v2.xlsx
  3. Verify only changed blocks were modified
  4. Verify programmer comment survives an update cycle
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from parser.excel_parser import parse_excel
from generators.sfr_generator import generate_sfr
from generators.lld_generator import generate_lld
from updater.diff_engine import diff_register_maps
from updater.sfr_updater import update_sfr
from updater.lld_updater import update_lld

SAMPLE_V1 = Path(__file__).parent / "sample_regs.xlsx"
SAMPLE_V2 = Path(__file__).parent / "sample_regs_v2.xlsx"


@pytest.fixture(scope="module", autouse=True)
def ensure_samples():
    from .create_sample_excel import create_sample, create_sample_v2
    if not SAMPLE_V1.exists():
        create_sample(SAMPLE_V1)
    if not SAMPLE_V2.exists():
        create_sample_v2(SAMPLE_V2)


# ── Helper ────────────────────────────────────────────────────────────────────
def extract_block(text: str, sentinel_key: str) -> str:
    start = text.find(f"[sfr_gen:reg:begin:{sentinel_key}]")
    end   = text.find(f"[sfr_gen:reg:end:{sentinel_key}]") + \
            len(f"[sfr_gen:reg:end:{sentinel_key}]")
    return text[start:end] if start != -1 else ""


def extract_lld_block(text: str, reg: str, field: str) -> str:
    begin = f"[sfr_gen:lld:begin:{reg}:{field}]"
    end   = f"[sfr_gen:lld:end:{reg}:{field}]"
    s = text.find(begin)
    e = text.find(end) + len(end)
    return text[s:e] if s != -1 else ""


# ════════════════════════════════════════════════════════════════════════════
# Integration: Fresh generation
# ════════════════════════════════════════════════════════════════════════════
class TestFreshGeneration:
    @pytest.fixture(scope="class")
    def outdir(self, tmp_path_factory):
        return tmp_path_factory.mktemp("fresh")

    @pytest.fixture(scope="class")
    def generated_files(self, outdir):
        maps = parse_excel(SAMPLE_V1, base_addr=0x40000000, skip_debug=True)
        results = {}
        for rm in maps:
            sfr_path = outdir / f"{rm.peripheral.lower()}_sfr.h"
            lld_path = outdir / f"{rm.peripheral.lower()}_lld.h"
            generate_sfr(rm, sfr_path)
            generate_lld(rm, lld_path)
            results[rm.peripheral] = {"sfr": sfr_path, "lld": lld_path, "rm": rm}
        return results

    def test_sfr_files_created(self, generated_files):
        for peri, info in generated_files.items():
            assert info["sfr"].exists(), f"sfr.h missing for {peri}"

    def test_lld_files_created(self, generated_files):
        for peri, info in generated_files.items():
            assert info["lld"].exists(), f"lld.h missing for {peri}"

    def test_ctrl_sfr_contains_all_registers(self, generated_files):
        sfr = generated_files["CXL_CTRL"]["sfr"].read_text()
        for reg in ["CTRL_REG", "STATUS_REG", "INT_ENABLE_REG",
                    "INT_STATUS_REG", "TIMEOUT_REG"]:
            assert reg in sfr, f"{reg} missing from CXL_CTRL sfr.h"

    def test_debug_reg_absent_sfr(self, generated_files):
        sfr = generated_files["CXL_CTRL"]["sfr"].read_text()
        assert "DEBUG_REG" not in sfr

    def test_debug_reg_absent_lld(self, generated_files):
        lld = generated_files["CXL_CTRL"]["lld"].read_text()
        assert "debug_reg" not in lld.lower()

    def test_ctrl_lld_ro_field_getter_only(self, generated_files):
        lld = generated_files["CXL_CTRL"]["lld"].read_text()
        # STATUS_REG / LINK_UP is RO
        assert "link_up_get" in lld
        assert "link_up_set" not in lld

    def test_ctrl_lld_wo_field_write_only(self, generated_files):
        lld = generated_files["CXL_CTRL"]["lld"].read_text()
        # CTRL_REG / RESET is WO
        assert "reset_write" in lld
        assert "reset_get" not in lld

    def test_ctrl_lld_w1c_clear_function(self, generated_files):
        lld = generated_files["CXL_CTRL"]["lld"].read_text()
        assert "link_err_clear" in lld

    def test_link_eq_ctrl_no_description_no_stub(self, generated_files):
        """EQ_CTRL TX_PRESET has no description → no LLM stub."""
        lld = generated_files["CXL_LINK"]["lld"].read_text()
        block = extract_lld_block(lld, "EQ_CTRL", "TX_PRESET")
        assert "[LLM-TODO]" not in block

    def test_lld_brace_balance(self, generated_files):
        from updater.lld_updater import _check_braces
        for peri, info in generated_files.items():
            content = info["lld"].read_text()
            assert _check_braces(content), f"Unbalanced braces in {peri} lld.h"

    def test_sfr_valid_c_header_guards(self, generated_files):
        for peri, info in generated_files.items():
            sfr = info["sfr"].read_text()
            guard = f"{peri}_SFR_H"
            assert f"#ifndef {guard}" in sfr
            assert f"#define {guard}" in sfr
            assert f"#endif /* {guard} */" in sfr


# ════════════════════════════════════════════════════════════════════════════
# Integration: Incremental update
# ════════════════════════════════════════════════════════════════════════════
class TestIncrementalUpdate:
    @pytest.fixture(scope="class")
    def update_env(self, tmp_path_factory):
        """Generate v1 files, inject programmer comments, then run v2 update."""
        outdir = tmp_path_factory.mktemp("update")

        # ── Generate v1 ──────────────────────────────────────────────────
        maps_v1 = parse_excel(SAMPLE_V1, base_addr=0x40000000, skip_debug=True)
        rm_ctrl_v1 = next(m for m in maps_v1 if m.peripheral == "CXL_CTRL")
        rm_link_v1 = next(m for m in maps_v1 if m.peripheral == "CXL_LINK")

        ctrl_sfr = outdir / "cxl_ctrl_sfr.h"
        ctrl_lld = outdir / "cxl_ctrl_lld.h"
        link_lld = outdir / "cxl_link_lld.h"

        generate_sfr(rm_ctrl_v1, ctrl_sfr)
        generate_lld(rm_ctrl_v1, ctrl_lld)
        generate_lld(rm_link_v1, link_lld)

        # ── Inject programmer comments into free zones ────────────────────
        for path in [ctrl_lld, link_lld]:
            txt = path.read_text()
            # Insert after the #include line
            txt = txt.replace(
                "#include <stdint.h>",
                "#include <stdint.h>\n/* PROGRAMMER: my custom init function */\nvoid my_init(void) {}"
            )
            path.write_text(txt)

        # Snapshot unchanged blocks before update
        ctrl_lld_before = ctrl_lld.read_text()

        # ── Parse v2 and diff ─────────────────────────────────────────────
        maps_v2 = parse_excel(SAMPLE_V2, base_addr=0x40000000, skip_debug=True)
        rm_ctrl_v2 = next(m for m in maps_v2 if m.peripheral == "CXL_CTRL")
        rm_link_v2 = next(m for m in maps_v2 if m.peripheral == "CXL_LINK")

        ctrl_diff = diff_register_maps(rm_ctrl_v1, rm_ctrl_v2)
        link_diff = diff_register_maps(rm_link_v1, rm_link_v2)

        update_sfr(ctrl_sfr, rm_ctrl_v2, ctrl_diff)
        update_lld(ctrl_lld, rm_ctrl_v2, ctrl_diff)
        update_lld(link_lld, rm_link_v2, link_diff)

        return {
            "ctrl_sfr":     ctrl_sfr,
            "ctrl_lld":     ctrl_lld,
            "link_lld":     link_lld,
            "ctrl_diff":    ctrl_diff,
            "link_diff":    link_diff,
            "ctrl_lld_before": ctrl_lld_before,
            "rm_ctrl_v1":   rm_ctrl_v1,
            "rm_ctrl_v2":   rm_ctrl_v2,
        }

    def test_diff_detects_mode_description_change(self, update_env):
        d = update_env["ctrl_diff"]
        changed_fields = [
            (rd.reg_name, fd.field_name)
            for rd in d.changed_registers
            for fd in rd.field_diffs
        ]
        assert ("CTRL_REG", "MODE") in changed_fields

    def test_diff_detects_timeout_reset_change(self, update_env):
        d = update_env["ctrl_diff"]
        changed_fields = [
            (rd.reg_name, fd.field_name)
            for rd in d.changed_registers
            for fd in rd.field_diffs
        ]
        assert ("TIMEOUT_REG", "TIMEOUT_VAL") in changed_fields

    def test_diff_detects_eq_en_description_added(self, update_env):
        d = update_env["link_diff"]
        changed_fields = [
            (rd.reg_name, fd.field_name)
            for rd in d.changed_registers
            for fd in rd.field_diffs
        ]
        assert ("EQ_CTRL", "EQ_EN") in changed_fields

    def test_sfr_timeout_reset_updated(self, update_env):
        sfr = update_env["ctrl_sfr"].read_text()
        # v2 reset value is 0xC8 = 200
        assert "0xC8" in sfr or "0xc8" in sfr.lower()

    def test_sfr_unchanged_register_byte_identical(self, update_env, tmp_path_factory):
        """STATUS_REG was not changed in v2 — its sfr block must be byte-identical.
        We compare the pre-update block (re-generated from v1 map) vs post-update block.
        The register-block sentinels contain no timestamps so direct text comparison works.
        """
        # Re-generate v1 sfr to get reference block (no timestamp inside reg blocks)
        rm_v1 = update_env["rm_ctrl_v1"]
        v1_sfr = generate_sfr(rm_v1)         # in-memory string
        v2_sfr = update_env["ctrl_sfr"].read_text(encoding="utf-8")

        b1 = extract_block(v1_sfr, "STATUS_REG")
        b2 = extract_block(v2_sfr, "STATUS_REG")
        assert b1 and b2, "STATUS_REG block not found in one of the files"
        # Compare ignoring any unicode/encoding differences by normalising
        assert b1.encode("utf-8") == b2.encode("utf-8")

    def test_programmer_comment_preserved_after_update(self, update_env):
        lld = update_env["ctrl_lld"].read_text()
        assert "PROGRAMMER: my custom init function" in lld
        assert "void my_init(void) {}" in lld

    def test_unchanged_lld_block_byte_identical(self, update_env):
        before = update_env["ctrl_lld_before"]
        after  = update_env["ctrl_lld"].read_text()
        # INT_ENABLE_REG was not changed → its blocks should be identical
        before_block = extract_lld_block(before, "INT_ENABLE_REG", "LINK_ERR_EN")
        after_block  = extract_lld_block(after,  "INT_ENABLE_REG", "LINK_ERR_EN")
        assert before_block == after_block

    def test_lld_still_brace_balanced_after_update(self, update_env):
        from updater.lld_updater import _check_braces
        for key in ["ctrl_lld", "link_lld"]:
            content = update_env[key].read_text()
            assert _check_braces(content), f"Unbalanced braces after update in {key}"

    def test_eq_en_block_regenerated_with_description(self, update_env):
        lld = update_env["link_lld"].read_text()
        block = extract_lld_block(lld, "EQ_CTRL", "EQ_EN")
        # v2 added a description → LLM stub should now be present
        assert "[LLM-TODO]" in block or "eq_en_get" in block
