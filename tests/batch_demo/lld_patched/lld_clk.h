/*
 * lld_clk.h -- Low-Level Driver for CLK IP (OLD SFR revision)
 *
 * Auto-generated struct-based LLD for the OLD CLK SFR.
 * Generated from: tests/batch_demo/old_sfr/sfr_clk.h
 *
 * Convention:
 *   struct lld_clk { pSFR_CLK pSFR; };
 *
 * Getter+Setter for RW fields, Getter-only for RO fields.
 */
#ifndef LLD_CLK_H
#define LLD_CLK_H

#include <stdint.h>
#include "sfr_clk.h"

/* === BEGIN LLD_CLK_STRUCTS === */
/* SFR Aggregate Struct (auto-generated from new SFR — DO NOT EDIT) */
typedef volatile struct _SFR_CLK_S
{
    SFR_CLK_CLK_CON                      stCLK_CON;  /* offset 0x0000 */
    SFR_CLK_PLL_CTRL                     stPLL_CTRL;  /* offset 0x0004 */
    SFR_CLK_DIV_CON                      stDIV_CON;  /* offset 0x0008 */
    SFR_CLK_SPREAD_CON                   stSPREAD_CON;  /* offset 0x000C */
    SFR_CLK_RST_CON                      stRST_CON;  /* offset 0x0010 */
    SFR_CLK_IRQ_CON                      stIRQ_CON;  /* offset 0x0014 */
    SFR_CLK_CLK_STATUS                   stCLK_STATUS;  /* offset 0x0018 */
    SFR_CLK_RESET_CON                    stRESET_CON;  /* offset 0x001C */
} SFR_CLK, *pSFR_CLK;

/* LLD Driver Struct */
struct lld_clk {
    pSFR_CLK pSFR;  /* pointer to hardware register block */
};
/* === END LLD_CLK_STRUCTS === */


/* ── LLD handle ──────────────────────────────────────────────────────────── */
struct lld_clk {
    pSFR_CLK pSFR;
};

/* ══════════════════════════════════════════════════════
 * REGISTER: CLK_CON
 * OFFSET  : 0x0000
 * DESC    : Clock control register
 * SRC_SHA : abcd1234
 * ══════════════════════════════════════════════════════ */

/** @brief Get CLK_EN from CLK_CON */
static inline uint8_t lld_clk_clk_con_clk_en_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_CON.stNative.CLK_EN;
}
/** @brief Set CLK_EN in CLK_CON */
static inline void lld_clk_clk_con_clk_en_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stCLK_CON.stNative.CLK_EN = val;
}

/** @brief Get CLK_DIV from CLK_CON */
static inline uint8_t lld_clk_clk_con_clk_div_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_CON.stNative.CLK_DIV;
}
/** @brief Set CLK_DIV in CLK_CON */
static inline void lld_clk_clk_con_clk_div_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stCLK_CON.stNative.CLK_DIV = val;
}

/** @brief Get CLK_SRC from CLK_CON (RO field -- old revision) */
static inline uint8_t lld_clk_clk_con_clk_src_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_CON.stNative.CLK_SRC;
}

/** @brief Get CLK_GATE from CLK_CON */
static inline uint8_t lld_clk_clk_con_clk_gate_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_CON.stNative.CLK_GATE;
}
/** @brief Set CLK_GATE in CLK_CON */
static inline void lld_clk_clk_con_clk_gate_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stCLK_CON.stNative.CLK_GATE = val;
}

/** @brief Get CLK_MODE from CLK_CON */
static inline uint8_t lld_clk_clk_con_clk_mode_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_CON.stNative.CLK_MODE;
}
/** @brief Set CLK_MODE in CLK_CON */
static inline void lld_clk_clk_con_clk_mode_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stCLK_CON.stNative.CLK_MODE = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: PLL_CON
 * OFFSET  : 0x0004
 * DESC    : PLL control register (renamed to PLL_CTRL in new SFR)
 * SRC_SHA : ef012345
 * ══════════════════════════════════════════════════════ */

/** @brief Get PLL_EN from PLL_CON */
static inline uint8_t lld_clk_pll_ctrl_pll_en_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stPLL_CTRL.stNative.PLL_EN;
}
/** @brief Set PLL_EN in PLL_CON */
static inline void lld_clk_pll_ctrl_pll_en_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stPLL_CTRL.stNative.PLL_EN = val;
}

/** @brief Get PLL_LOCK from PLL_CON (RO field) */
static inline uint8_t lld_clk_pll_ctrl_pll_lock_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stPLL_CTRL.stNative.PLL_LOCK;
}

/** @brief Get PLL_DIV from PLL_CON */
static inline uint8_t lld_clk_pll_ctrl_pll_div_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stPLL_CTRL.stNative.PLL_DIV;
}
/** @brief Set PLL_DIV in PLL_CON */
static inline void lld_clk_pll_ctrl_pll_div_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stPLL_CTRL.stNative.PLL_DIV = val;
}

/** @brief Get PLL_SEL from PLL_CON */
static inline uint8_t lld_clk_pll_ctrl_pll_sel_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stPLL_CTRL.stNative.PLL_SEL;
}
/** @brief Set PLL_SEL in PLL_CON */
static inline void lld_clk_pll_ctrl_pll_sel_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stPLL_CTRL.stNative.PLL_SEL = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: DIV_CON
 * OFFSET  : 0x0008
 * DESC    : Divider control register
 * SRC_SHA : 67890abc
 * ══════════════════════════════════════════════════════ */

/** @brief Get DIV_PRE from DIV_CON (renamed to DIV_PREDIV in new SFR) */
static inline uint8_t lld_clk_div_con_div_prediv_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stDIV_CON.stNative.DIV_PREDIV;
}
/** @brief Set DIV_PRE in DIV_CON */
static inline void lld_clk_div_con_div_prediv_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stDIV_CON.stNative.DIV_PREDIV = val;
}

/** @brief Get DIV_POST from DIV_CON (deleted in new SFR) */
static inline uint8_t lld_clk_div_con_div_post_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stDIV_CON.stNative.DIV_POST;
}
/** @brief Set DIV_POST in DIV_CON */
static inline void lld_clk_div_con_div_post_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stDIV_CON.stNative.DIV_POST = val;
}

/** @brief Get DIV_MODE from DIV_CON */
static inline uint8_t lld_clk_div_con_div_mode_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stDIV_CON.stNative.DIV_MODE;
}
/** @brief Set DIV_MODE in DIV_CON */
static inline void lld_clk_div_con_div_mode_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stDIV_CON.stNative.DIV_MODE = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: SPREAD_CON
 * OFFSET  : 0x000C
 * DESC    : Spread spectrum control register
 * SRC_SHA : def56789
 * ══════════════════════════════════════════════════════ */

/** @brief Get SS_CTRL from SPREAD_CON (will split into SS_EN+SS_DEPTH in new SFR) */
static inline uint8_t lld_clk_spread_con_ss_ctrl_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stSPREAD_CON.stNative.SS_CTRL;
}
/** @brief Set SS_CTRL in SPREAD_CON */
static inline void lld_clk_spread_con_ss_ctrl_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stSPREAD_CON.stNative.SS_CTRL = val;
}

/** @brief Get SS_MERGE_A from SPREAD_CON (will merge with SS_MERGE_B in new SFR) */
static inline uint8_t lld_clk_spread_con_ss_merge_a_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stSPREAD_CON.stNative.SS_MERGE_A;
}
/** @brief Set SS_MERGE_A in SPREAD_CON */
static inline void lld_clk_spread_con_ss_merge_a_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stSPREAD_CON.stNative.SS_MERGE_A = val;
}

/** @brief Get SS_MERGE_B from SPREAD_CON (will merge with SS_MERGE_A in new SFR) */
static inline uint8_t lld_clk_spread_con_ss_merge_b_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stSPREAD_CON.stNative.SS_MERGE_B;
}
/** @brief Set SS_MERGE_B in SPREAD_CON */
static inline void lld_clk_spread_con_ss_merge_b_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stSPREAD_CON.stNative.SS_MERGE_B = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: RST_CON
 * OFFSET  : 0x0010
 * DESC    : Reset control register
 * SRC_SHA : 11223344
 * ══════════════════════════════════════════════════════ */

/** @brief Get RST_EN from RST_CON (moved to RESET_CON in new SFR) */
static inline uint8_t lld_clk_rst_con_rst_en_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stRST_CON.stNative.RST_EN;
}
/** @brief Set RST_EN in RST_CON */
static inline void lld_clk_rst_con_rst_en_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stRST_CON.stNative.RST_EN = val;
}

/** @brief Get RST_MODE from RST_CON */
static inline uint8_t lld_clk_rst_con_rst_mode_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stRST_CON.stNative.RST_MODE;
}
/** @brief Set RST_MODE in RST_CON */
static inline void lld_clk_rst_con_rst_mode_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stRST_CON.stNative.RST_MODE = val;
}

/** @brief Get RST_CNT from RST_CON */
static inline uint8_t lld_clk_rst_con_rst_cnt_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stRST_CON.stNative.RST_CNT;
}
/** @brief Set RST_CNT in RST_CON */
static inline void lld_clk_rst_con_rst_cnt_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stRST_CON.stNative.RST_CNT = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: IRQ_CON
 * OFFSET  : 0x0014
 * DESC    : Interrupt control register
 * SRC_SHA : aabb5566
 * ══════════════════════════════════════════════════════ */

/** @brief Get IRQ_EN from IRQ_CON */
static inline uint8_t lld_clk_irq_con_irq_en_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_EN;
}
/** @brief Set IRQ_EN in IRQ_CON */
static inline void lld_clk_irq_con_irq_en_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stIRQ_CON.stNative.IRQ_EN = val;
}

/** @brief Get IRQ_MASK from IRQ_CON */
static inline uint8_t lld_clk_irq_con_irq_mask_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_MASK;
}
/** @brief Set IRQ_MASK in IRQ_CON */
static inline void lld_clk_irq_con_irq_mask_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stIRQ_CON.stNative.IRQ_MASK = val;
}

/** @brief Get IRQ_TRIG from IRQ_CON */
static inline uint8_t lld_clk_irq_con_irq_trig_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_TRIG;
}
/** @brief Set IRQ_TRIG in IRQ_CON */
static inline void lld_clk_irq_con_irq_trig_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stIRQ_CON.stNative.IRQ_TRIG = val;
}

/** @brief Get IRQ_PENDING from IRQ_CON (RO field -- old revision) */
static inline uint8_t lld_clk_irq_con_irq_pending_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_PENDING;
}

/** @brief Get IRQ_CLR from IRQ_CON */
static inline uint8_t lld_clk_irq_con_irq_clr_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_CLR;
}
/** @brief Set IRQ_CLR in IRQ_CON */
static inline void lld_clk_irq_con_irq_clr_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stIRQ_CON.stNative.IRQ_CLR = val;
}

/** @brief Get IRQ_POL from IRQ_CON */
static inline uint8_t lld_clk_irq_con_irq_pol_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stIRQ_CON.stNative.IRQ_POL;
}
/** @brief Set IRQ_POL in IRQ_CON */
static inline void lld_clk_irq_con_irq_pol_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stIRQ_CON.stNative.IRQ_POL = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: TRIM_CON
 * OFFSET  : 0x0018
 * DESC    : Trim control register (deleted in new SFR -- REG_DELETED)
 * SRC_SHA : ccdd7788
 * ══════════════════════════════════════════════════════ */

/** @brief Get TRIM_COARSE from TRIM_CON */
static inline uint8_t lld_clk_trim_con_trim_coarse_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stTRIM_CON.stNative.TRIM_COARSE;
}
/** @brief Set TRIM_COARSE in TRIM_CON */
static inline void lld_clk_trim_con_trim_coarse_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stTRIM_CON.stNative.TRIM_COARSE = val;
}

/** @brief Get TRIM_FINE from TRIM_CON */
static inline uint8_t lld_clk_trim_con_trim_fine_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stTRIM_CON.stNative.TRIM_FINE;
}
/** @brief Set TRIM_FINE in TRIM_CON */
static inline void lld_clk_trim_con_trim_fine_set(struct lld_clk *lld, uint8_t val) {
    lld->pSFR->stTRIM_CON.stNative.TRIM_FINE = val;
}

/* ══════════════════════════════════════════════════════
 * REGISTER: CLK_STATUS
 * OFFSET  : 0x001C
 * DESC    : Clock status register (unchanged between revisions)
 * SRC_SHA : eeff99aa
 * ══════════════════════════════════════════════════════ */

/** @brief Get CLK_READY from CLK_STATUS (RO field) */
static inline uint8_t lld_clk_clk_status_clk_ready_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_STATUS.stNative.CLK_READY;
}

/** @brief Get CLK_STABLE from CLK_STATUS (RO field) */
static inline uint8_t lld_clk_clk_status_clk_stable_get(struct lld_clk *lld) {
    return (uint8_t)lld->pSFR->stCLK_STATUS.stNative.CLK_STABLE;
}

#endif /* LLD_CLK_H */