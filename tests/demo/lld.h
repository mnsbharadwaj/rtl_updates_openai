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
static inline uint8_t DMA_CTRL_BURST_get(volatile uint32_t *base)
{
    return (uint8_t)((base[0] & 0x0000000EU) >> 1U);
}

/** @brief Burst length in beats (0=single, 1=4-beat, 2=8-beat). */
static inline void DMA_CTRL_BURST_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[0];
    r &= ~0x0000000EU;
    r |= ((uint32_t)val << 1U) & 0x0000000EU;
    base[0] = r;
}

/** @brief Enable DMA transfer. Write 1 to start. */
static inline uint8_t DMA_CTRL_EN_get(volatile uint32_t *base)
{
    return (uint8_t)((base[0] & 0x00000001U) >> 0U);
}

/** @brief Enable DMA transfer. Write 1 to start. */
static inline void DMA_CTRL_EN_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[0];
    r &= ~0x00000001U;
    r |= ((uint32_t)val << 0U) & 0x00000001U;
    base[0] = r;
}

/** @brief Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
static inline uint8_t DMA_CTRL_MODE_get(volatile uint32_t *base)
{
    return (uint8_t)((base[0] & 0x00000070U) >> 4U);
}

/** @brief Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
static inline void DMA_CTRL_MODE_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[0];
    r &= ~0x00000070U;
    r |= ((uint32_t)val << 4U) & 0x00000070U;
    base[0] = r;
}

/** @brief Transfer priority: 0=low, 1=normal, 2=high, 3=critical. */
static inline uint8_t DMA_CTRL_PRIORITY_get(volatile uint32_t *base)
{
    return (uint8_t)((base[0] & 0x00030000U) >> 16U);
}

/** @brief Transfer priority: 0=low, 1=normal, 2=high, 3=critical. */
static inline void DMA_CTRL_PRIORITY_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[0];
    r &= ~0x00030000U;
    r |= ((uint32_t)val << 16U) & 0x00030000U;
    base[0] = r;
}

/** @brief Timeout counter value in bus cycles. */
static inline uint8_t DMA_CTRL_TIMEOUT_get(volatile uint32_t *base)
{
    return (uint8_t)((base[0] & 0x0000FF00U) >> 8U);
}

/** @brief Timeout counter value in bus cycles. */
static inline void DMA_CTRL_TIMEOUT_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[0];
    r &= ~0x0000FF00U;
    r |= ((uint32_t)val << 8U) & 0x0000FF00U;
    base[0] = r;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: STATUS                         offset=0x0004
 * 
 * SRC_SHA: 1d34be3ed7760225
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Transfer complete flag. Write 1 to clear. */
static inline uint8_t DMA_STATUS_DONE_get(volatile uint32_t *base)
{
    return (uint8_t)((base[1] & 0x00000001U) >> 0U);
}

/** @brief Transfer complete flag. Write 1 to clear. */
static inline void DMA_STATUS_DONE_clear(volatile uint32_t *base)
{
    base[1] = 0x00000001U; /* W1C: write 1 to clear */
}

/** @brief Error code: 0=none,1=bus_err,2=addr_err,3=timeout. */
static inline uint8_t DMA_STATUS_ERROR_get(volatile uint32_t *base)
{
    return (uint8_t)((base[1] & 0x0000001CU) >> 2U);
}

/** @brief Current FIFO fill level in entries. */
static inline uint8_t DMA_STATUS_LEVEL_get(volatile uint32_t *base)
{
    return (uint8_t)((base[1] & 0x00FF0000U) >> 16U);
}

/** @brief FIFO watermark threshold. Triggers interrupt when level exceeds this. */
static inline uint8_t DMA_STATUS_THRESH_get(volatile uint32_t *base)
{
    return (uint8_t)((base[1] & 0x00007F00U) >> 8U);
}

/** @brief FIFO watermark threshold. Triggers interrupt when level exceeds this. */
static inline void DMA_STATUS_THRESH_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[1];
    r &= ~0x00007F00U;
    r |= ((uint32_t)val << 8U) & 0x00007F00U;
    base[1] = r;
}

static inline void DMA_STATUS_THRESH_IRQ_enable(volatile uint32_t *base)  { base[1] |=  0x00007F00U; }
static inline void DMA_STATUS_THRESH_IRQ_disable(volatile uint32_t *base) { base[1] &= ~0x00007F00U; }
static inline uint32_t DMA_STATUS_THRESH_IRQ_status(volatile uint32_t *base) { return (base[1] & 0x00007F00U); }
static inline void DMA_STATUS_THRESH_IRQ_clear(volatile uint32_t *base)   { base[1] = 0x00007F00U; }


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: DEBUG                          offset=0x0008
 * 
 * SRC_SHA: c002449f25cc3930
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Enable debug mode. Freezes pipeline for inspection. */
static inline uint8_t DMA_DEBUG_DBG_EN_get(volatile uint32_t *base)
{
    return (uint8_t)((base[2] & 0x00000001U) >> 0U);
}

/** @brief Enable debug mode. Freezes pipeline for inspection. */
static inline void DMA_DEBUG_DBG_EN_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[2];
    r &= ~0x00000001U;
    r |= ((uint32_t)val << 0U) & 0x00000001U;
    base[2] = r;
}

/** @brief Debug mux select: 0=fifo, 1=arb, 2=axi, 3=wrap. */
static inline uint8_t DMA_DEBUG_DBG_SEL_get(volatile uint32_t *base)
{
    return (uint8_t)((base[2] & 0x0000000EU) >> 1U);
}

/** @brief Debug mux select: 0=fifo, 1=arb, 2=axi, 3=wrap. */
static inline void DMA_DEBUG_DBG_SEL_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[2];
    r &= ~0x0000000EU;
    r |= ((uint32_t)val << 1U) & 0x0000000EU;
    base[2] = r;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: CHAN                           offset=0x000C
 * 
 * SRC_SHA: da8b685436d911a1
 * ═══════════════════════════════════════════════════════════════ */

/** @brief Source start address (must be word-aligned). */
static inline uint32_t DMA_CHAN_SRC_ADDR_get(volatile uint32_t *base)
{
    return (uint32_t)((base[3] & 0xFFFFFFFFU) >> 0U);
}

/** @brief Source start address (must be word-aligned). */
static inline void DMA_CHAN_SRC_ADDR_set(volatile uint32_t *base, uint32_t val)
{
    uint32_t r = base[3];
    r &= ~0xFFFFFFFFU;
    r |= ((uint32_t)val << 0U) & 0xFFFFFFFFU;
    base[3] = r;
}


/* ═══════════════════════════════════════════════════════════════
 * REGISTER: FIFO                           offset=0x0010
 * 
 * SRC_SHA: 6058ef07e4b6bc7b
 * ═══════════════════════════════════════════════════════════════ */

/** @brief FIFO depth in entries (hardware constant). */
static inline uint8_t DMA_FIFO_DEPTH_get(volatile uint32_t *base)
{
    return (uint8_t)((base[4] & 0x000000FFU) >> 0U);
}

/** @brief Write 1 to flush FIFO. Self-clearing. */
static inline void DMA_FIFO_FLUSH_set(volatile uint32_t *base, uint32_t val)
{
    base[4] = ((uint32_t)val << 8U) & 0x00000100U;
}


#endif /* DMA_LLD_H */
