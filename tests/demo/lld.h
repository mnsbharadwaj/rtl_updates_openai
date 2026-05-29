/*
 * lld.h — DMA Controller Low-Level Driver
 * AUTO-GENERATED from sfr_old.h by generate_demo_lld.py
 * DO NOT EDIT — regenerate with generate_demo_lld.py
 *
 * This file is the BASELINE lld.h for the patcher demo.
 * Apply sfr_new.h changes with:
 *   python -m lld_gen.main_sfr run \
 *       --old tests/demo/sfr_old.h \
 *       --new tests/demo/sfr_new.h \
 *       --lld tests/demo/lld.h     \
 *       --ip DMA --no-llm --no-git
 */
#ifndef DMA_LLD_H
#define DMA_LLD_H

#include <stdint.h>
#include "sfr_old.h"

/* Macro shims — allows tests to build without hardware */
/* Replace with real MMIO read/write in production       */

/* ═══════════════════════════════════════════════════════════════
 * REGISTER: CTRL                           offset=0x0000
 * 
 * SRC_SHA: 15404828c6bf57f2
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Burst length in beats (0=single, 1=4-beat, 2=8-beat). */
static inline uint8_t lld_dma_ctrl_burst_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.BURST);
}

/** @brief Burst length in beats (0=single, 1=4-beat, 2=8-beat). */
static inline void lld_dma_ctrl_burst_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.BURST = val;
}

/** @brief Enable DMA transfer. Write 1 to start. */
static inline uint8_t lld_dma_ctrl_en_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.EN);
}

/** @brief Enable DMA transfer. Write 1 to start. */
static inline void lld_dma_ctrl_en_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.EN = val;
}

/** @brief Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
static inline uint8_t lld_dma_ctrl_mode_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.MODE);
}

/** @brief Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
static inline void lld_dma_ctrl_mode_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.MODE = val;
}

/** @brief Transfer priority: 0=low, 1=normal, 2=high, 3=critical. */
static inline uint8_t lld_dma_ctrl_priority_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.PRIORITY);
}

/** @brief Transfer priority: 0=low, 1=normal, 2=high, 3=critical. */
static inline void lld_dma_ctrl_priority_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.PRIORITY = val;
}

/** @brief Timeout counter value in bus cycles. */
static inline uint8_t lld_dma_ctrl_timeout_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.TIMEOUT);
}

/** @brief Timeout counter value in bus cycles. */
static inline void lld_dma_ctrl_timeout_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.TIMEOUT = val;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: STATUS                         offset=0x0004
 * 
 * SRC_SHA: 1d34be3ed7760225
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Transfer complete flag. Write 1 to clear. */
static inline uint8_t lld_dma_status_done_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.DONE);
}

/** @brief Transfer complete flag. Write 1 to clear. */
static inline void lld_dma_status_done_clear(struct lld_dma *lld)
{
    lld->pSFR->stSTATUS.stNative.DONE = 1U; /* W1C: write 1 to clear */
}

/** @brief Error code: 0=none,1=bus_err,2=addr_err,3=timeout. */
static inline uint8_t lld_dma_status_error_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.ERROR);
}

/** @brief Current FIFO fill level in entries. */
static inline uint8_t lld_dma_status_level_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.LEVEL);
}

/** @brief FIFO watermark threshold. Triggers interrupt when level exceeds this. */
static inline uint8_t lld_dma_status_thresh_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stSTATUS.stNative.THRESH);
}

/** @brief FIFO watermark threshold. Triggers interrupt when level exceeds this. */
static inline void lld_dma_status_thresh_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stSTATUS.stNative.THRESH = val;
}

static inline void lld_dma_status_thresh_irq_enable(struct lld_dma *lld)  { lld->pSFR->stSTATUS.stNative.THRESH = 1U; }
static inline void lld_dma_status_thresh_irq_disable(struct lld_dma *lld) { lld->pSFR->stSTATUS.stNative.THRESH = 0U; }
static inline uint32_t lld_dma_status_thresh_irq_status(struct lld_dma *lld) { return (uint32_t)(lld->pSFR->stSTATUS.stNative.THRESH); }
static inline void lld_dma_status_thresh_irq_clear(struct lld_dma *lld)   { lld->pSFR->stSTATUS.stNative.THRESH = 1U; } /* W1C */


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: DEBUG                          offset=0x0008
 * 
 * SRC_SHA: c002449f25cc3930
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Enable debug mode. Freezes pipeline for inspection. */
static inline uint8_t lld_dma_debug_dbg_en_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stDEBUG.stNative.DBG_EN);
}

/** @brief Enable debug mode. Freezes pipeline for inspection. */
static inline void lld_dma_debug_dbg_en_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stDEBUG.stNative.DBG_EN = val;
}

/** @brief Debug mux select: 0=fifo, 1=arb, 2=axi, 3=wrap. */
static inline uint8_t lld_dma_debug_dbg_sel_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stDEBUG.stNative.DBG_SEL);
}

/** @brief Debug mux select: 0=fifo, 1=arb, 2=axi, 3=wrap. */
static inline void lld_dma_debug_dbg_sel_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stDEBUG.stNative.DBG_SEL = val;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: CHAN                           offset=0x000C
 * 
 * SRC_SHA: da8b685436d911a1
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Source start address (must be word-aligned). */
static inline uint32_t lld_dma_chan_src_addr_get(struct lld_dma *lld)
{
    return (uint32_t)(lld->pSFR->stCHAN.stNative.SRC_ADDR);
}

/** @brief Source start address (must be word-aligned). */
static inline void lld_dma_chan_src_addr_set(struct lld_dma *lld, uint32_t val)
{
    lld->pSFR->stCHAN.stNative.SRC_ADDR = val;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: FIFO                           offset=0x0010
 * 
 * SRC_SHA: 6058ef07e4b6bc7b
 * ═══════════════════════════════════════════════════════════════ */

/** @brief FIFO depth in entries (hardware constant). */
static inline uint8_t lld_dma_fifo_depth_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stFIFO.stNative.DEPTH);
}

/** @brief Write 1 to flush FIFO. Self-clearing. */
static inline void lld_dma_fifo_flush_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stFIFO.stNative.FLUSH = val;
}


#endif /* DMA_LLD_H */
