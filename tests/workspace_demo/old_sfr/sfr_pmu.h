/*
 * sfr_pmu.h — PMU (Power Management Unit) SFR Header  [OLD / BASELINE]
 *
 * Format  : Samsung typedef volatile union bitfield
 * IP      : PMU  (auto-detected from filename: sfr_pmu.h → PMU)
 *
 * Change-type demo map (old → new):
 *   PMU_CON     → REG_RENAMED  (renamed to PMU_CTRL, fields identical)
 *   STATUS_CON  → 6 field changes (FIELD_RENAMED, FIELD_DELETED, FIELD_ADDED,
 *                                   BITWIDTH_CHANGED, ACCESS_CHANGED, OFFSET_CHANGED)
 *   CLK_CON     → 3 field changes (RESET_CHANGED, COMMENT_CHANGED, MULTI_CHANGED)
 *   SWT_CON     → REG_DELETED   (completely removed in new)
 *   RST_CON     → UNCHANGED
 */
#ifndef SFR_PMU_H
#define SFR_PMU_H

#include <stdint.h>
typedef unsigned int UINT32;

/* =========================================================================
 * Register: PMU_CON  (Power Management Control)
 *   → Will be RENAMED to PMU_CTRL in new SFR (Type 1: REG_RENAMED)
 *     Fields stay IDENTICAL so the diff tool recognises it as a rename.
 * ========================================================================= */
typedef volatile union _SFR_PMU_PMU_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 DMA_EN       : 1;  // 0-0   [RW]  DMA enable
        volatile UINT32 DMA_PASS     : 1;  // 1-1   [RO]  DMA pass status
        volatile UINT32 HCPU_RST     : 1;  // 2-2   [RW]  HCPU reset control
        volatile UINT32 HCPU_VINIT   : 1;  // 3-3   [RW]  HCPU vector init
        volatile UINT32 RSVD0        :28;  // reserved
    } stNative;
} SFR_PMU_PMU_CON, *pSFR_PMU_PMU_CON;

/* =========================================================================
 * Register: STATUS_CON  (DMA Status Control)
 *   → Multiple field changes in new SFR:
 *     Type 4  FIELD_RENAMED  : DONE  → COMPLETE  (same bit[0], same W1C)
 *     Type 5  FIELD_DELETED  : FAIL  removed
 *     Type 6  FIELD_ADDED    : ABORT added at bit[9]
 *     Type 7  BITWIDTH_CHANGED : THRESH  [6:3] 4-bit  → [7:3] 5-bit
 *     Type 8  ACCESS_CHANGED  : ERR  RO → RW
 *     Type 9  OFFSET_CHANGED  : LEVEL  [8:7] → [10:9]
 * ========================================================================= */
typedef volatile union _SFR_PMU_STATUS_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 DONE         : 1;  // 0-0   [W1C] DMA done flag
        volatile UINT32 FAIL         : 1;  // 1-1   [RO]  DMA fail flag
        volatile UINT32 ERR          : 1;  // 2-2   [RO]  DMA error flag
        volatile UINT32 THRESH       : 4;  // 3-6   [RW]  Threshold level 4-bit
        volatile UINT32 LEVEL        : 2;  // 7-8   [RW]  FIFO level indicator
        volatile UINT32 RSVD0        :23;  // reserved
    } stNative;
} SFR_PMU_STATUS_CON, *pSFR_PMU_STATUS_CON;

/* =========================================================================
 * Register: CLK_CON  (Clock Control)
 *   → 3 field changes in new SFR:
 *     Type 10 RESET_CHANGED   : reset 0x00000000 → 0x0000000E
 *     Type 11 COMMENT_CHANGED : CLK_SEL desc updated
 *     Type 12 MULTI_CHANGED   : CLK_GATE access RW→RO AND desc updated
 * ========================================================================= */
typedef volatile union _SFR_PMU_CLK_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 CLK_EN       : 1;  // 0-0   [RW]  Clock enable signal
        volatile UINT32 CLK_DIV      : 4;  // 1-4   [RW]  Clock divider ratio
        volatile UINT32 CLK_SEL      : 2;  // 5-6   [RW]  Clock source select signal
        volatile UINT32 CLK_GATE     : 1;  // 7-7   [RW]  Power gate control
        volatile UINT32 RSVD0        :24;  // reserved
    } stNative;
} SFR_PMU_CLK_CON, *pSFR_PMU_CLK_CON;

/* =========================================================================
 * Register: SWT_CON  (Software Timer Control)
 *   → Will be DELETED in new SFR (Type 2: REG_DELETED)
 * ========================================================================= */
typedef volatile union _SFR_PMU_SWT_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 SCPRE        :16;  // 0-15  [RW]  Software timer prescaler
        volatile UINT32 SCALL        :16;  // 16-31 [RW]  Software timer reload value
    } stNative;
} SFR_PMU_SWT_CON, *pSFR_PMU_SWT_CON;

/* =========================================================================
 * Register: RST_CON  (Retention Control)
 *   → UNCHANGED in new SFR
 * ========================================================================= */
typedef volatile union _SFR_PMU_RST_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 RET_CON      :32;  // 0-31  [RW]  Retention control register
    } stNative;
} SFR_PMU_RST_CON, *pSFR_PMU_RST_CON;

#endif /* SFR_PMU_H */
