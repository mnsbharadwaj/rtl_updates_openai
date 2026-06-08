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


def _make_clk_reg_rename_cr():
    return ChangeRecord(
        change_type = ChangeType.REG_RENAMED,
        reg_name    = "PLL_CON",
        field_name  = None,
        old_reg     = RegisterIR("PLL_CON", 0, {}),
        new_reg     = RegisterIR("PLL_CTRL", 0, {}),
        needs_llm   = False,
        details     = []
    )


def test_ast_multi_sfr_cross_lld_refactoring():
    """
    Verify a scenario where functions in LLD files reference multiple SFR registers
    belonging to different IPs. Renaming a register in one IP must refactor references
    in both its own LLD file and other IP LLD files.
    """
    # 1. PMU LLD contains a function accessing its own PMU_CON and CLK's PLL_CON
    pmu_lld_code = """
#include <stdint.h>
struct lld_pmu { pSFR_PMU pSFR; };
struct lld_clk { pSFR_CLK pSFR; };

static inline void lld_pmu_sync_with_clk(struct lld_pmu *lld, struct lld_clk *clk) {
    // 1. Access own register PMU_CON
    lld->pSFR->stPMU_CON.stNative.DMA_EN = 1;
    // 2. Access other IP's register PLL_CON
    clk->pSFR->stPLL_CON.stNative.PLL_EN = 1;
}
"""

    # 2. CLK LLD contains a function accessing its own PLL_CON and PMU's PMU_CON
    clk_lld_code = """
#include <stdint.h>
struct lld_clk { pSFR_CLK pSFR; };
struct lld_pmu { pSFR_PMU pSFR; };

static inline void lld_clk_check_pmu(struct lld_clk *clk, struct lld_pmu *lld) {
    // 1. Access own register PLL_CON
    clk->pSFR->stPLL_CON.stNative.PLL_EN = 0;
    // 2. Access other IP's register PMU_CON
    lld->pSFR->stPMU_CON.stNative.DMA_PASS = 1;
}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        pmu_path = Path(tmpdir) / "lld_pmu.c"
        clk_path = Path(tmpdir) / "lld_clk.c"
        
        pmu_path.write_text(pmu_lld_code, encoding="utf-8")
        clk_path.write_text(clk_lld_code, encoding="utf-8")
        
        # Define types to avoid parsing errors
        custom_types = {"struct lld_pmu", "struct lld_clk", "pSFR_PMU", "pSFR_CLK"}
        
        pmu_changes = [_make_reg_rename_cr()]
        clk_changes = [_make_clk_reg_rename_cr()]
        
        # Step A: Apply PMU changes (PMU_CON -> PMU_CTRL) to BOTH LLD files
        applied_pmu_own = refactor_file(pmu_path, pmu_changes, custom_types)
        applied_pmu_other = refactor_file(clk_path, pmu_changes, custom_types)
        
        # Verify PMU_CON was renamed in both files
        assert len(applied_pmu_own) == 1
        assert applied_pmu_own[0] == (8, "stPMU_CON", "stPMU_CTRL")
        
        assert len(applied_pmu_other) == 1
        assert applied_pmu_other[0] == (10, "stPMU_CON", "stPMU_CTRL")
        
        # Step B: Apply CLK changes (PLL_CON -> PLL_CTRL) to BOTH LLD files
        applied_clk_other = refactor_file(pmu_path, clk_changes, custom_types)
        applied_clk_own = refactor_file(clk_path, clk_changes, custom_types)
        
        # Verify PLL_CON was renamed in both files
        assert len(applied_clk_other) == 1
        assert applied_clk_other[0] == (10, "stPLL_CON", "stPLL_CTRL")
        
        assert len(applied_clk_own) == 1
        assert applied_clk_own[0] == (8, "stPLL_CON", "stPLL_CTRL")
        
        # Verify final contents
        pmu_text = pmu_path.read_text(encoding="utf-8")
        assert "stPMU_CTRL" in pmu_text
        assert "stPMU_CON" not in pmu_text
        assert "stPLL_CTRL" in pmu_text
        assert "stPLL_CON" not in pmu_text
        
        clk_text = clk_path.read_text(encoding="utf-8")
        assert "stPMU_CTRL" in clk_text
        assert "stPMU_CON" not in clk_text
        assert "stPLL_CTRL" in clk_text
        assert "stPLL_CON" not in clk_text


def test_ast_run_test_execution():
    """Verify that run_test_execution compiles and runs assertions successfully."""
    sfr_code = """
#ifndef SFR_PMU_H
#define SFR_PMU_H
#include <stdint.h>
typedef volatile union {
    struct {
        volatile uint32_t DMA_EN : 1;
        volatile uint32_t RSVD   : 31;
    } stNative;
    uint32_t u32Val;
} SFR_PMU_CON;

typedef struct {
    SFR_PMU_CON stPMU_CON;
} SFR_PMU;
#endif
"""

    lld_code = """
#ifndef LLD_PMU_H
#define LLD_PMU_H
#include "sfr_pmu.h"
struct lld_pmu { SFR_PMU *pSFR; };

static inline uint8_t lld_pmu_dma_en_get(struct lld_pmu *lld) {
    return (uint8_t)lld->pSFR->stPMU_CON.stNative.DMA_EN;
}
static inline void lld_pmu_dma_en_set(struct lld_pmu *lld, uint8_t val) {
    lld->pSFR->stPMU_CON.stNative.DMA_EN = val;
}
#endif
"""

    test_code = """
#include <assert.h>
#include "sfr_pmu.h"
#include "lld_pmu.h"

int main(void) {
    SFR_PMU sfr = {0};
    struct lld_pmu lld = { .pSFR = &sfr };
    
    // Initial value
    assert(lld_pmu_dma_en_get(&lld) == 0);
    
    // Set and check
    lld_pmu_dma_en_set(&lld, 1);
    assert(lld_pmu_dma_en_get(&lld) == 1);
    
    return 0;
}
"""
    from lld_gen.compile_check import _find_gcc, run_test_execution
    try:
        gcc_exe = _find_gcc()
    except Exception:
        pytest.skip("gcc not available for testing execution")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        sfr_path = Path(tmpdir) / "sfr_pmu.h"
        lld_path = Path(tmpdir) / "lld_pmu.h"
        test_path = Path(tmpdir) / "test_pmu.c"
        
        sfr_path.write_text(sfr_code, encoding="utf-8")
        lld_path.write_text(lld_code, encoding="utf-8")
        test_path.write_text(test_code, encoding="utf-8")
        
        ok, msg = run_test_execution(gcc_exe, test_path, sfr_path, lld_path)
        assert ok, f"Expected compilation/execution success, got: {msg}"
        assert "All generated unit test assertions passed" in msg


def test_lld_less_sfr_ast_refactoring():
    """Verify that an LLD-less SFR register change is refactored in other LLD files referencing it."""
    pmu_lld_code = """
#include "sfr_clock.h"
#include "sfr_pmu.h"

static inline void sync_pmu_with_clock(struct lld_pmu *lld, struct lld_clock *clk) {
    // Reference to CLOCK_CON under clk -> should rename to CLK_CTRL
    clk->pSFR->stCLOCK_CON.stNative.CLK_EN = 1;
}
"""
    clock_change = ChangeRecord(
        change_type = ChangeType.REG_RENAMED,
        reg_name    = "CLOCK_CON",
        field_name  = None,
        old_reg     = RegisterIR("CLOCK_CON", 0, {}),
        new_reg     = RegisterIR("CLK_CTRL", 0, {}),
        needs_llm   = False,
        details     = []
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "lld_pmu.h"
        file_path.write_text(pmu_lld_code, encoding="utf-8")
        
        applied = refactor_file(
            file_path = file_path,
            changes   = [clock_change],
            custom_typedefs = {"SFR_CLOCK_CLOCK_CON", "SFR_CLOCK_CLK_CTRL"},
            ip        = "CLOCK"
        )
        
        assert len(applied) == 1
        assert applied[0] == (7, "stCLOCK_CON", "stCLK_CTRL")
        
        updated_text = file_path.read_text(encoding="utf-8")
        assert "stCLK_CTRL" in updated_text
        assert "stCLOCK_CON" not in updated_text



