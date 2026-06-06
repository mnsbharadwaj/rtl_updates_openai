import tempfile
from pathlib import Path
import pytest

from lld_gen.sfr_diff_analyzer import ChangeRecord, ChangeType, RegisterIR, FieldIR
from lld_gen.ast_refactor import refactor_file, refactor_cross_references

def _make_reg_rename_cr():
    return ChangeRecord(
        change_type = ChangeType.REG_RENAMED,
        reg_name    = "PMU_CON",
        field_name  = None,
        old_reg     = RegisterIR("PMU_CON", 0, {}),
        new_reg     = RegisterIR("PMU_CTRL", 0, {}),
        needs_llm   = False,
        details     = []
    )

def _make_field_rename_cr():
    return ChangeRecord(
        change_type = ChangeType.FIELD_RENAMED,
        reg_name    = "STATUS_CON",
        field_name  = "DONE",
        old_field   = FieldIR("DONE", "STATUS_CON", 1, 0, 0, 0, "W1C", 0, ""),
        new_field   = FieldIR("COMPLETE", "STATUS_CON", 1, 0, 0, 0, "W1C", 0, ""),
        needs_llm   = False,
        details     = []
    )

def test_ast_register_rename():
    """Verify stPMU_CON is renamed to stPMU_CTRL but function names are frozen."""
    c_code = """#include <stdint.h>
#define REG_VAL 0x55

// Comment describing function
static inline void lld_pmu_pmu_con_dma_en_set(struct lld_pmu *lld, uint8_t val)
{
    /* Keep this spacing */
    lld->pSFR->stPMU_CON.stNative.DMA_EN = val;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "lld_pmu.c"
        file_path.write_text(c_code, encoding="utf-8")
        
        changes = [_make_reg_rename_cr()]
        applied = refactor_file(file_path, changes, {"uint8_t"})
        
        assert len(applied) == 1
        assert applied[0] == (8, "stPMU_CON", "stPMU_CTRL")
        
        updated_text = file_path.read_text(encoding="utf-8")
        assert "stPMU_CTRL" in updated_text
        assert "stPMU_CON" not in updated_text
        assert "lld_pmu_pmu_con_dma_en_set" in updated_text  # Frozen function name
        assert "/* Keep this spacing */" in updated_text      # Comments preserved
        assert "#include <stdint.h>" in updated_text          # Includes preserved
        assert "#define REG_VAL 0x55" in updated_text         # Macros preserved

def test_ast_field_rename():
    """Verify DONE is renamed to COMPLETE under stSTATUS_CON."""
    c_code = """
static inline void clear_done(struct lld_pmu *lld) {
    // Write 1 to clear DONE
    lld->pSFR->stSTATUS_CON.stNative.DONE = 1U;
    
    // This DONE is not under stSTATUS_CON and should NOT be renamed
    int DONE = 5;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "lld_pmu.c"
        file_path.write_text(c_code, encoding="utf-8")
        
        changes = [_make_field_rename_cr()]
        applied = refactor_file(file_path, changes, set())
        
        assert len(applied) == 1
        assert applied[0] == (4, "DONE", "COMPLETE")
        
        updated_text = file_path.read_text(encoding="utf-8")
        assert "stSTATUS_CON.stNative.COMPLETE" in updated_text
        assert "int DONE = 5;" in updated_text  # Local variable preserved

def test_ast_multi_replacements_on_same_line():
    """Verify multiple replacements on the same line work without offset corruption."""
    c_code = """
void sync_regs(struct lld_pmu *lld) {
    lld->pSFR->stPMU_CON.stNative.DMA_EN = lld->pSFR->stPMU_CON.stNative.DMA_PASS;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "lld_sync.c"
        file_path.write_text(c_code, encoding="utf-8")
        
        changes = [_make_reg_rename_cr()]
        applied = refactor_file(file_path, changes, set())
        
        # Should have found 2 references
        assert len(applied) == 2
        
        updated_text = file_path.read_text(encoding="utf-8")
        assert "stPMU_CTRL" in updated_text
        assert "stPMU_CON" not in updated_text
        assert "stPMU_CTRL.stNative.DMA_EN = lld->pSFR->stPMU_CTRL.stNative.DMA_PASS" in updated_text
