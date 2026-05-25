/*
 * sfr_new.h -- DMA Controller SFR Header  (UPDATED VERSION v2.0)
 *
 * IP       : DMA
 * Version  : v2.0  (updated from IP-XACT rev2)
 * Generated: 2026-05-24
 *
 * Changes vs sfr_old.h -- ALL 12 CHANGE TYPES:
 *
 *  Type 1  REG_RENAMED      DMA_CHAN (0x000C) -> DMA_CHANNEL (same offset)
 *  Type 2  REG_DELETED      DMA_DEBUG (0x0008) removed entirely
 *  Type 3  REG_ADDED        DMA_IRQ (0x0014) brand new register
 *  Type 4  FIELD_RENAMED    DMA_CTRL.EN -> DMA_CTRL.ENABLE (same [0:0] mask+shift)
 *  Type 5  FIELD_DELETED    DMA_STATUS.DONE removed
 *  Type 6  FIELD_ADDED      DMA_STATUS.BUSY [25:24] new field
 *  Type 7  BITWIDTH_CHANGED DMA_CTRL.BURST [3:1] 3-bit -> [4:1] 4-bit (popcount 3->4)
 *  Type 8  ACCESS_CHANGED   DMA_CTRL.MODE RW -> RO (access only, same bits+desc)
 *  Type 9  OFFSET_CHANGED   DMA_STATUS.THRESH [14:8] shift=8 -> [15:9] shift=9
 *                           (same 7-bit popcount, just position moved up by 1)
 *  Type 10 RESET_CHANGED    DMA_STATUS.LEVEL reset 0x00 -> 0x10
 *  Type 11 COMMENT_CHANGED  DMA_CTRL.TIMEOUT description updated (same bits+access)
 *  Type 12 MULTI_CHANGED    DMA_CTRL.PRIORITY access RW->RO AND desc changed
 */
#ifndef DMA_SFR_NEW_H
#define DMA_SFR_NEW_H

#include <stdint.h>

/* =========================================================================
 * Register: DMA_CTRL   offset=0x0000
 * DMA Control Register
 * ========================================================================= */
#define DMA_CTRL_OFFSET  0x0000U

/* ENABLE [0:0] RW -- Enable DMA transfer. Write 1 to start. */
/* Type 4 FIELD_RENAMED: EN -> ENABLE (mask 0x1, shift 0 unchanged) */
#define DMA_CTRL_ENABLE_MASK    0x00000001U
#define DMA_CTRL_ENABLE_SHIFT   0U
#define DMA_CTRL_ENABLE_RESET   0x0U

/* BURST [4:1] RW -- Burst length in beats (0=single, 1=4-beat, 2=8-beat). */
/* Type 7 BITWIDTH_CHANGED: [3:1] popcount=3 -> [4:1] popcount=4, desc unchanged */

#define DMA_CTRL_BURST_MASK    0x0000001EU
#define DMA_CTRL_BURST_SHIFT   1U
#define DMA_CTRL_BURST_RESET   0x0U

/* MODE [6:4] RO -- Operation mode: 0=normal, 1=loopback, 2=scatter-gather. */
/* Type 8 ACCESS_CHANGED: RW -> RO (same bits [6:4], same desc) */
#define DMA_CTRL_MODE_MASK    0x00000070U
#define DMA_CTRL_MODE_SHIFT   4U
#define DMA_CTRL_MODE_RESET   0x0U

/* TIMEOUT [15:8] RW -- Timeout in microseconds. Write 0 to disable watchdog. */
/* Type 11 COMMENT_CHANGED: description updated (same [15:8] RW bits) */
#define DMA_CTRL_TIMEOUT_MASK    0x0000FF00U
#define DMA_CTRL_TIMEOUT_SHIFT   8U
#define DMA_CTRL_TIMEOUT_RESET   0x0U

/* PRIORITY [17:16] RO -- Arbitration priority assigned by scheduler; read-only after init. */
/* Type 12 MULTI_CHANGED: access RW->RO AND description changed */
#define DMA_CTRL_PRIORITY_MASK    0x00030000U
#define DMA_CTRL_PRIORITY_SHIFT   16U
#define DMA_CTRL_PRIORITY_RESET   0x1U

/* =========================================================================
 * Register: DMA_STATUS   offset=0x0004
 * DMA Status Register
 * ========================================================================= */
#define DMA_STATUS_OFFSET  0x0004U

/* Type 5 FIELD_DELETED: DMA_STATUS.DONE removed (interrupt routed to DMA_IRQ) */

/* ERROR [4:2] RO -- Error code: 0=none,1=bus_err,2=addr_err,3=timeout. */
/* UNCHANGED -- bit-identical to sfr_old.h */
#define DMA_STATUS_ERROR_MASK    0x0000001CU
#define DMA_STATUS_ERROR_SHIFT   2U
#define DMA_STATUS_ERROR_RESET   0x0U

/* THRESH [15:9] RW -- FIFO watermark threshold. Triggers interrupt when level exceeds this. */
/* Type 9 OFFSET_CHANGED: same 7-bit mask width, shift moved from 8 to 9 */

#define DMA_STATUS_THRESH_MASK    0x0000FE00U
#define DMA_STATUS_THRESH_SHIFT   9U
#define DMA_STATUS_THRESH_RESET   0x0U

/* LEVEL [23:16] RO -- Current FIFO fill level in entries. */
/* Type 10 RESET_CHANGED: reset 0x00 -> 0x10 */
#define DMA_STATUS_LEVEL_MASK    0x00FF0000U
#define DMA_STATUS_LEVEL_SHIFT   16U
#define DMA_STATUS_LEVEL_RESET   0x10U

/* BUSY [25:24] RO -- DMA busy: 0=idle,1=read,2=write,3=flush. */
/* Type 6 FIELD_ADDED: new field not present in sfr_old.h */
#define DMA_STATUS_BUSY_MASK    0x03000000U
#define DMA_STATUS_BUSY_SHIFT   24U
#define DMA_STATUS_BUSY_RESET   0x0U

/* Type 2 REG_DELETED: DMA_DEBUG (0x0008) entirely removed */

/* =========================================================================
 * Register: DMA_CHANNEL   offset=0x000C
 * Channel Config [Type 1 REG_RENAMED from DMA_CHAN]
 * ========================================================================= */
#define DMA_CHANNEL_OFFSET  0x000CU

/* SRC_ADDR [31:0] RW -- Source start address (must be word-aligned). */
#define DMA_CHANNEL_SRC_ADDR_MASK    0xFFFFFFFFU
#define DMA_CHANNEL_SRC_ADDR_SHIFT   0U
#define DMA_CHANNEL_SRC_ADDR_RESET   0x0U

/* =========================================================================
 * Register: DMA_FIFO   offset=0x0010
 * FIFO Control [UNCHANGED between versions]
 * ========================================================================= */
#define DMA_FIFO_OFFSET  0x0010U

/* DEPTH [7:0] RO -- FIFO depth in entries (hardware constant). */
#define DMA_FIFO_DEPTH_MASK    0x000000FFU
#define DMA_FIFO_DEPTH_SHIFT   0U
#define DMA_FIFO_DEPTH_RESET   0x40U

/* FLUSH [8:8] WO -- Write 1 to flush FIFO. Self-clearing. */
#define DMA_FIFO_FLUSH_MASK    0x00000100U
#define DMA_FIFO_FLUSH_SHIFT   8U
#define DMA_FIFO_FLUSH_RESET   0x0U

/* =========================================================================
 * Register: DMA_IRQ   offset=0x0014
 * DMA Interrupt Control [Type 3 REG_ADDED: brand new in v2.0]
 * ========================================================================= */
#define DMA_IRQ_OFFSET  0x0014U

/* IRQ_EN [0:0] RW -- Master interrupt enable. Set before enabling channels. */
#define DMA_IRQ_IRQ_EN_MASK    0x00000001U
#define DMA_IRQ_IRQ_EN_SHIFT   0U
#define DMA_IRQ_IRQ_EN_RESET   0x0U

/* IRQ_STATUS [7:1] W1C -- Per-channel interrupt status. Write 1 to clear. */
#define DMA_IRQ_IRQ_STATUS_MASK    0x000000FEU
#define DMA_IRQ_IRQ_STATUS_SHIFT   1U
#define DMA_IRQ_IRQ_STATUS_RESET   0x0U

/* IRQ_MASK [15:8] RW -- Interrupt mask: 0=masked, 1=unmasked. */
#define DMA_IRQ_IRQ_MASK_MASK    0x0000FF00U
#define DMA_IRQ_IRQ_MASK_SHIFT   8U
#define DMA_IRQ_IRQ_MASK_RESET   0x0U

#endif /* DMA_SFR_NEW_H */
