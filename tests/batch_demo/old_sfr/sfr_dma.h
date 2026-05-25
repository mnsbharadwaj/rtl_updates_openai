/*
 * sfr_old.h — DMA Controller SFR Header  (BASELINE / OLD VERSION)
 *
 * IP       : DMA
 * Version  : v1.0  (git HEAD baseline)
 * Generated: 2026-05-01
 *
 * Register map:
 *   0x0000  DMA_CTRL    — Control register
 *   0x0004  DMA_STATUS  — Status register
 *   0x0008  DMA_DEBUG   — Debug register  [will be REG_DELETED]
 *   0x000C  DMA_CHAN    — Channel config   [will be REG_RENAMED -> DMA_CHANNEL]
 *   0x0010  DMA_FIFO    — FIFO control     [unchanged]
 *                        (DMA_IRQ at 0x0014 REG_ADDED in new)
 *
 * Change coverage vs sfr_new.h (12 types):
 *   Type 1  REG_RENAMED      DMA_CHAN -> DMA_CHANNEL (same offset 0x000C)
 *   Type 2  REG_DELETED      DMA_DEBUG removed entirely
 *   Type 3  REG_ADDED        DMA_IRQ appears at 0x0014
 *   Type 4  FIELD_RENAMED    DMA_CTRL.EN -> DMA_CTRL.ENABLE (same mask+shift)
 *   Type 5  FIELD_DELETED    DMA_STATUS.DONE removed
 *   Type 6  FIELD_ADDED      DMA_STATUS.BUSY added
 *   Type 7  BITWIDTH_CHANGED DMA_CTRL.BURST [3:1] 3-bit -> [4:1] 4-bit
 *   Type 8  ACCESS_CHANGED   DMA_CTRL.MODE RW -> RO
 *   Type 9  OFFSET_CHANGED   DMA_STATUS.THRESH [14:8] shift=8 -> [15:9] shift=9
 *                            (same 7-bit popcount, only position moved)
 *   Type 10 RESET_CHANGED    DMA_STATUS.LEVEL reset 0x00 -> 0x10
 *   Type 11 COMMENT_CHANGED  DMA_CTRL.TIMEOUT description updated
 *   Type 12 MULTI_CHANGED    DMA_CTRL.PRIORITY access RW->RO + desc changed
 */
#ifndef DMA_SFR_OLD_H
#define DMA_SFR_OLD_H

#include <stdint.h>

/* =========================================================================
 * Register: DMA_CTRL   offset=0x0000
 * DMA Control Register
 * ========================================================================= */
#define DMA_CTRL_OFFSET  0x0000U

/* EN [0:0] RW — Enable DMA transfer. Write 1 to start. */
#define DMA_CTRL_EN_MASK    0x00000001U
#define DMA_CTRL_EN_SHIFT   0U
#define DMA_CTRL_EN_RESET   0x0U

/* BURST [3:1] RW — Burst length in beats (0=single, 1=4-beat, 2=8-beat). */
#define DMA_CTRL_BURST_MASK    0x0000000EU
#define DMA_CTRL_BURST_SHIFT   1U
#define DMA_CTRL_BURST_RESET   0x0U

/* MODE [6:4] RW — Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
#define DMA_CTRL_MODE_MASK    0x00000070U
#define DMA_CTRL_MODE_SHIFT   4U
#define DMA_CTRL_MODE_RESET   0x0U

/* TIMEOUT [15:8] RW — Timeout counter value in bus cycles. */
#define DMA_CTRL_TIMEOUT_MASK    0x0000FF00U
#define DMA_CTRL_TIMEOUT_SHIFT   8U
#define DMA_CTRL_TIMEOUT_RESET   0x0U

/* PRIORITY [17:16] RW — Transfer priority: 0=low, 1=normal, 2=high, 3=critical. */
#define DMA_CTRL_PRIORITY_MASK    0x00030000U
#define DMA_CTRL_PRIORITY_SHIFT   16U
#define DMA_CTRL_PRIORITY_RESET   0x1U

/* =========================================================================
 * Register: DMA_STATUS   offset=0x0004
 * DMA Status Register
 * ========================================================================= */
#define DMA_STATUS_OFFSET  0x0004U

/* DONE [0:0] W1C — Transfer complete flag. Write 1 to clear. */
#define DMA_STATUS_DONE_MASK    0x00000001U
#define DMA_STATUS_DONE_SHIFT   0U
#define DMA_STATUS_DONE_RESET   0x0U

/* ERROR [4:2] RO — Error code: 0=none,1=bus_err,2=addr_err,3=timeout. */
#define DMA_STATUS_ERROR_MASK    0x0000001CU
#define DMA_STATUS_ERROR_SHIFT   2U
#define DMA_STATUS_ERROR_RESET   0x0U

/* THRESH [14:8] RW -- FIFO watermark threshold. Triggers interrupt when level exceeds this. */
#define DMA_STATUS_THRESH_MASK    0x00007F00U
#define DMA_STATUS_THRESH_SHIFT   8U
#define DMA_STATUS_THRESH_RESET   0x0U

/* LEVEL [23:16] RO -- Current FIFO fill level in entries. */
#define DMA_STATUS_LEVEL_MASK    0x00FF0000U
#define DMA_STATUS_LEVEL_SHIFT   16U
#define DMA_STATUS_LEVEL_RESET   0x00U

/* =========================================================================
 * Register: DMA_DEBUG   offset=0x0008
 * DMA Debug Register [will be REG_DELETED in new version]
 * ========================================================================= */
#define DMA_DEBUG_OFFSET  0x0008U

/* DBG_EN [0:0] RW — Enable debug mode. Freezes pipeline for inspection. */
#define DMA_DEBUG_DBG_EN_MASK    0x00000001U
#define DMA_DEBUG_DBG_EN_SHIFT   0U
#define DMA_DEBUG_DBG_EN_RESET   0x0U

/* DBG_SEL [3:1] RW — Debug mux select: 0=fifo, 1=arb, 2=axi, 3=wrap. */
#define DMA_DEBUG_DBG_SEL_MASK   0x0000000EU
#define DMA_DEBUG_DBG_SEL_SHIFT  1U
#define DMA_DEBUG_DBG_SEL_RESET  0x0U

/* =========================================================================
 * Register: DMA_CHAN   offset=0x000C
 * Channel Config [will be REG_RENAMED to DMA_CHANNEL in new version]
 * ========================================================================= */
#define DMA_CHAN_OFFSET  0x000CU

/* SRC_ADDR [31:0] RW — Source start address (must be word-aligned). */
#define DMA_CHAN_SRC_ADDR_MASK    0xFFFFFFFFU
#define DMA_CHAN_SRC_ADDR_SHIFT   0U
#define DMA_CHAN_SRC_ADDR_RESET   0x0U

/* =========================================================================
 * Register: DMA_FIFO   offset=0x0010
 * FIFO Control [UNCHANGED between versions]
 * ========================================================================= */
#define DMA_FIFO_OFFSET  0x0010U

/* DEPTH [7:0] RO — FIFO depth in entries (hardware constant). */
#define DMA_FIFO_DEPTH_MASK    0x000000FFU
#define DMA_FIFO_DEPTH_SHIFT   0U
#define DMA_FIFO_DEPTH_RESET   0x40U

/* FLUSH [8:8] WO — Write 1 to flush FIFO. Self-clearing. */
#define DMA_FIFO_FLUSH_MASK    0x00000100U
#define DMA_FIFO_FLUSH_SHIFT   8U
#define DMA_FIFO_FLUSH_RESET   0x0U

#endif /* DMA_SFR_OLD_H */
