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

/* === BEGIN LLD_DMA_STRUCTS === */
/* SFR Aggregate Struct (auto-generated from new SFR — DO NOT EDIT) */
typedef volatile struct _SFR_DMA_S
{
    SFR_DMA_CTRL                         stCTRL;  /* offset 0x0000 */
    SFR_DMA_STATUS                       stSTATUS;  /* offset 0x0004 */
    SFR_DMA_CHANNEL                      stCHANNEL;  /* offset 0x000C */
    SFR_DMA_FIFO                         stFIFO;  /* offset 0x0010 */
    SFR_DMA_IRQ                          stIRQ;  /* offset 0x0014 */
} SFR_DMA, *pSFR_DMA;

/* LLD Driver Struct */
struct lld_dma {
    pSFR_DMA pSFR;  /* pointer to hardware register block */
};
/* === END LLD_DMA_STRUCTS === */


/* Macro shims — allows tests to build without hardware */
/* Replace with real MMIO read/write in production       */

/* ═══════════════════════════════════════════════════════════════
 * REGISTER: CTRL                           offset=0x0000
 * 
 * SRC_SHA: c7b7a79329a88d27
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
    return (uint8_t)(lld->pSFR->stCTRL.stNative.ENABLE);
}

/** @brief Enable DMA transfer. Write 1 to start. */
static inline void lld_dma_ctrl_en_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.ENABLE = val;
}

/** @brief Timeout in microseconds. Write 0 to disable watchdog. */
static inline uint8_t lld_dma_ctrl_timeout_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.TIMEOUT);
}

/** @brief Timeout in microseconds. Write 0 to disable watchdog. */
static inline void lld_dma_ctrl_timeout_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stCTRL.stNative.TIMEOUT = val;
}

/** @brief Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
static inline uint8_t lld_dma_ctrl_mode_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.MODE);
}

/** @brief Arbitration priority assigned by scheduler; read-only after init. */
static inline uint8_t lld_dma_ctrl_priority_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stCTRL.stNative.PRIORITY);
}
/* ═══════════════════════════════════════════════════════════════
 * REGISTER: STATUS                         offset=0x0004
 * 
 * SRC_SHA: 545ef759684a8c9c
 * ═══════════════════════════════════════════════════════════════ */

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



/* ⚠ DEPRECATED: SFR register DEBUG was DELETED in the new SFR version.
 * The functions below are no longer backed by hardware registers.
 * They are kept here to avoid compilation errors in IP emulation files.
 * Review the callers and MANUALLY DELETE these functions in a follow-up PR.
 * See PR_DESCRIPTION.md for the full list. */
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
 * REGISTER: CHANNEL                           offset=0x000C
 * 
 * SRC_SHA: 6425ab83b7515f0a
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Source start address (must be word-aligned). */
static inline uint32_t lld_dma_chan_src_addr_get(struct lld_dma *lld)
{
    return (uint32_t)(lld->pSFR->stCHANNEL.stNative.SRC_ADDR);
}

/** @brief Source start address (must be word-aligned). */
static inline void lld_dma_chan_src_addr_set(struct lld_dma *lld, uint32_t val)
{
    lld->pSFR->stCHANNEL.stNative.SRC_ADDR = val;
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



/* ═══════════════════════════════════════════════════════════════
 * REGISTER: IRQ                            offset=0x0014
 * 
 * SRC_SHA: 9bd012827363461c
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Master interrupt enable. Set before enabling channels. */
static inline uint8_t lld_dma_irq_irq_en_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stIRQ.stNative.IRQ_EN);
}

/** @brief Master interrupt enable. Set before enabling channels. */
static inline void lld_dma_irq_irq_en_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stIRQ.stNative.IRQ_EN = val;
}

static inline void lld_dma_irq_irq_en_irq_enable(struct lld_dma *lld)  { lld->pSFR->stIRQ.stNative.IRQ_EN = 1U; }
static inline void lld_dma_irq_irq_en_irq_disable(struct lld_dma *lld) { lld->pSFR->stIRQ.stNative.IRQ_EN = 0U; }
static inline uint32_t lld_dma_irq_irq_en_irq_status(struct lld_dma *lld) { return (uint32_t)(lld->pSFR->stIRQ.stNative.IRQ_EN); }
static inline void lld_dma_irq_irq_en_irq_clear(struct lld_dma *lld)   { lld->pSFR->stIRQ.stNative.IRQ_EN = 1U; } /* W1C */

/** @brief Interrupt mask: 0=masked, 1=unmasked. */
static inline uint8_t lld_dma_irq_irq_mask_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stIRQ.stNative.IRQ_MASK);
}

/** @brief Interrupt mask: 0=masked, 1=unmasked. */
static inline void lld_dma_irq_irq_mask_set(struct lld_dma *lld, uint8_t val)
{
    lld->pSFR->stIRQ.stNative.IRQ_MASK = val;
}

static inline void lld_dma_irq_irq_mask_irq_enable(struct lld_dma *lld)  { lld->pSFR->stIRQ.stNative.IRQ_MASK = 1U; }
static inline void lld_dma_irq_irq_mask_irq_disable(struct lld_dma *lld) { lld->pSFR->stIRQ.stNative.IRQ_MASK = 0U; }
static inline uint32_t lld_dma_irq_irq_mask_irq_status(struct lld_dma *lld) { return (uint32_t)(lld->pSFR->stIRQ.stNative.IRQ_MASK); }
static inline void lld_dma_irq_irq_mask_irq_clear(struct lld_dma *lld)   { lld->pSFR->stIRQ.stNative.IRQ_MASK = 1U; } /* W1C */

/** @brief Per-channel interrupt status. Write 1 to clear. */
static inline uint8_t lld_dma_irq_irq_status_get(struct lld_dma *lld)
{
    return (uint8_t)(lld->pSFR->stIRQ.stNative.IRQ_STATUS);
}

/** @brief Per-channel interrupt status. Write 1 to clear. */
static inline void lld_dma_irq_irq_status_clear(struct lld_dma *lld)
{
    lld->pSFR->stIRQ.stNative.IRQ_STATUS = 1U; /* W1C: write 1 to clear */
}

static inline void lld_dma_irq_irq_status_irq_enable(struct lld_dma *lld)  { lld->pSFR->stIRQ.stNative.IRQ_STATUS = 1U; }
static inline void lld_dma_irq_irq_status_irq_disable(struct lld_dma *lld) { lld->pSFR->stIRQ.stNative.IRQ_STATUS = 0U; }
static inline uint32_t lld_dma_irq_irq_status_irq_status(struct lld_dma *lld) { return (uint32_t)(lld->pSFR->stIRQ.stNative.IRQ_STATUS); }
static inline void lld_dma_irq_irq_status_irq_clear(struct lld_dma *lld)   { lld->pSFR->stIRQ.stNative.IRQ_STATUS = 1U; } /* W1C */

#endif /* DMA_LLD_H */
