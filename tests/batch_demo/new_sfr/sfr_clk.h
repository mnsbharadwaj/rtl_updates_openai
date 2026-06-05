/*
 * sfr_clk.h -- CLK (Clock Control Unit) SFR Header (NEW)
 *
 * SFR_CLK (NEW) -- Applies all 28 change type test scenarios
 * Samsung typedef volatile union bitfield format.
 * IP auto-detected from filename: sfr_clk.h -> CLK
 *
 * ARRAY_CHANGED:   was scalar CLK_CON, now conceptually CLK_CON[2]
 * CLUSTER_CHANGED: PLL group restructured into PLL_CTRL cluster
 */
#ifndef SFR_CLK_H
#define SFR_CLK_H

#include <stdint.h>
typedef unsigned int UINT32;

/* =========================================================================
 * Register: CLK_CON  (Clock Control Register)
 * Change 7:  BITWIDTH_CHANGED  -- CLK_DIV 4-bit [1-4] -> 5-bit [1-5]
 * Change 8:  ACCESS_CHANGED    -- CLK_SRC RO -> RW (stays at bits [7:6])
 * Change 9:  OFFSET_CHANGED    -- CLK_GATE [8-9] -> [9-10] (lsb 8->9)
 * Change 27: FIELD_POLARITY_CHANGED -- CLK_MODE: access RW->RO, reset changes,
 *                                      desc expands (all three simultaneously)
 * Reset changes from 0x00000000 -> 0x00001800 (sets CLK_MODE bits [12:11]=3)
 * ========================================================================= */
typedef volatile union _SFR_CLK_CLK_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00001800);
    struct
    {
        volatile UINT32 CLK_EN      : 1;  // 0-0    [RW]  Clock enable
        volatile UINT32 CLK_DIV     : 5;  // 1-5    [RW]  Clock divider ratio
        volatile UINT32 CLK_SRC     : 2;  // 6-7    [RW]  Clock source select
        volatile UINT32 RSVD_A      : 1;  // 8-8    reserved
        volatile UINT32 CLK_GATE    : 2;  // 9-10   [RW]  Power gate control
        volatile UINT32 CLK_MODE    : 2;  // 11-12  [RO]  Clock mode: 0=Normal 1=PowerSave 2=Turbo 3=Bypass
        volatile UINT32 RSVD        :19;  // 13-31  reserved
    } stNative;
} SFR_CLK_CLK_CON, *pSFR_CLK_CLK_CON;

/* =========================================================================
 * Register: PLL_CTRL  (PLL Control) -- REG_RENAMED from PLL_CON (type 1)
 * Same fields as old PLL_CON for pure REG_RENAMED detection.
 * CLUSTER_CHANGED: PLL group restructured (comment only)
 * ========================================================================= */
typedef volatile union _SFR_CLK_PLL_CTRL_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 PLL_EN      : 1;  // 0-0    [RW]  PLL enable
        volatile UINT32 PLL_LOCK    : 1;  // 1-1    [RO]  PLL lock status
        volatile UINT32 PLL_DIV     : 4;  // 2-5    [RW]  PLL divider
        volatile UINT32 PLL_SEL     : 2;  // 6-7    [RW]  PLL input select
        volatile UINT32 RSVD        :24;  // 8-31   reserved
    } stNative;
} SFR_CLK_PLL_CTRL, *pSFR_CLK_PLL_CTRL;

/* =========================================================================
 * Register: DIV_CON  (Divider Control)
 * Change 4:  FIELD_RENAMED  -- DIV_PRE [0-3] -> DIV_PREDIV [0-3] (same bits/mask/shift)
 * Change 5:  FIELD_DELETED  -- DIV_POST [4-7] removed entirely
 * Change 6:  FIELD_ADDED    -- DIV_FRAC [10-19] new fractional divider
 * Change 12: MULTI_CHANGED  -- DIV_MODE [8-9]: access RW->RO AND desc changes
 * ========================================================================= */
typedef volatile union _SFR_CLK_DIV_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 DIV_PREDIV  : 4;  // 0-3    [RW]  Pre-divider value
        volatile UINT32 RSVD_A      : 4;  // 4-7    reserved
        volatile UINT32 DIV_MODE    : 2;  // 8-9    [RO]  Divider mode (new behavior)
        volatile UINT32 DIV_FRAC    :10;  // 10-19  [RW]  Fractional divider
        volatile UINT32 RSVD        :12;  // 20-31  reserved
    } stNative;
} SFR_CLK_DIV_CON, *pSFR_CLK_DIV_CON;

/* =========================================================================
 * Register: SPREAD_CON  (Spread Spectrum Control)
 * Change 13: FIELD_SPLIT   -- SS_CTRL[0-7] splits into SS_EN[0-0] + SS_DEPTH[1-7]
 * Change 14: FIELD_MERGED  -- SS_MERGE_A[8-11] + SS_MERGE_B[12-15] -> SS_COMBINED[8-15]
 * ========================================================================= */
typedef volatile union _SFR_CLK_SPREAD_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 SS_EN       : 1;  // 0-0    [RW]  Spread enable
        volatile UINT32 SS_DEPTH    : 7;  // 1-7    [RW]  Spread depth
        volatile UINT32 SS_COMBINED : 8;  // 8-15   [RW]  Spread combined control
        volatile UINT32 RSVD        :16;  // 16-31  reserved
    } stNative;
} SFR_CLK_SPREAD_CON, *pSFR_CLK_SPREAD_CON;

/* =========================================================================
 * Register: RST_CON  (Reset Control)
 * Change 10: RESET_CHANGED      -- RST_CNT reset value changes (reg reset 0x0->0x100)
 * Change 19: FIELD_MOVED_CROSS_REG -- RST_EN removed (moved to new RESET_CON)
 * Change 20: RESERVED_PROMOTED  -- RSVD_A bit[5] becomes CLK_BYPASS[5:5] RW
 * Change 21: RESERVED_PARTIAL_ACTIVATED -- RSVD_B [16-17] becomes RST_TYPE[16-17] RW
 * Change 16: REG_SIZE_CHANGED   -- register functionally extended (comment only)
 * Note: RST_MODE offset changes (1->0) due to RST_EN removal
 * ========================================================================= */
typedef volatile union _SFR_CLK_RST_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000100);
    struct
    {
        volatile UINT32 RST_MODE    : 2;  // 0-1    [RW]  Reset mode select
        volatile UINT32 RSVD_A      : 3;  // 2-4    reserved
        volatile UINT32 CLK_BYPASS  : 1;  // 5-5    [RW]  Clock bypass enable
        volatile UINT32 RSVD_B      : 2;  // 6-7    reserved
        volatile UINT32 RST_CNT     : 8;  // 8-15   [RW]  Reset counter
        volatile UINT32 RST_TYPE    : 2;  // 16-17  [RW]  Reset type select
        volatile UINT32 RSVD        :14;  // 18-31  reserved
    } stNative;
} SFR_CLK_RST_CON, *pSFR_CLK_RST_CON;

/* =========================================================================
 * Register: IRQ_CON  (Interrupt Control)
 * Change 28: DESCRIPTION_ADDED   -- IRQ_EN empty desc -> full description
 * Change 22: FIELD_ENUM_CHANGED  -- IRQ_MASK: 0=ALL 1=NONE -> 0=PERIPH 1=CORE 2=ALL 3=NONE
 * Change 23: FIELD_WRITE_ONCE    -- IRQ_TRIG: RW -> RWL (write-once lock)
 * Change 24: WRITE_MASK_CHANGED  -- IRQ_POL desc notes bit[7] now RO
 * Change 25: FIELD_SELF_CLEARING -- IRQ_CLR: RW -> SC (self-clearing pulse)
 * Change 26: FIELD_STICKY_CHANGED -- IRQ_PENDING: RO -> W1C (sticky)
 * ========================================================================= */
typedef volatile union _SFR_CLK_IRQ_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 IRQ_EN      : 1;  // 0-0    [RW]  IRQ enable: 0=disable all interrupts 1=enable selected interrupts
        volatile UINT32 IRQ_MASK    : 2;  // 1-2    [RW]  IRQ mask bits: 0=PERIPH 1=CORE 2=ALL 3=NONE
        volatile UINT32 IRQ_TRIG    : 1;  // 3-3    [RWL] Trigger mode
        volatile UINT32 IRQ_PENDING : 1;  // 4-4    [W1C] IRQ pending
        volatile UINT32 IRQ_CLR     : 1;  // 5-5    [SC]  IRQ clear
        volatile UINT32 IRQ_POL     : 2;  // 6-7    [RW]  IRQ polarity 0=active-high (bit[7] now RO, write-mask restricted)
        volatile UINT32 RSVD        :24;  // 8-31   reserved
    } stNative;
} SFR_CLK_IRQ_CON, *pSFR_CLK_IRQ_CON;

/* =========================================================================
 * Register: RESET_CON  (Software/Hardware Reset) -- REG_ADDED (type 3)
 * Contains RST_SW/RST_HW as equivalents of moved RST_EN (FIELD_MOVED_CROSS_REG type 19)
 * ========================================================================= */
typedef volatile union _SFR_CLK_RESET_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 RST_SW      : 1;  // 0-0    [RW]  Software reset
        volatile UINT32 RST_HW      : 1;  // 1-1    [RW]  Hardware reset
        volatile UINT32 RST_STAT    : 1;  // 2-2    [RO]  Reset status
        volatile UINT32 RSVD        :29;  // 3-31   reserved
    } stNative;
} SFR_CLK_RESET_CON, *pSFR_CLK_RESET_CON;

/* =========================================================================
 * Register: CLK_STATUS  (Clock Status) -- UNCHANGED
 * ========================================================================= */
typedef volatile union _SFR_CLK_CLK_STATUS_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 CLK_READY   : 1;  // 0-0    [RO]  Clock ready flag
        volatile UINT32 CLK_STABLE  : 1;  // 1-1    [RO]  Clock stable flag
        volatile UINT32 RSVD        :30;  // 2-31   reserved
    } stNative;
} SFR_CLK_CLK_STATUS, *pSFR_CLK_CLK_STATUS;

/* =========================================================================
 * Aggregate SFR struct
 * Offset map (new SFR):
 *   0x0000  CLK_CON      (modified)
 *   0x0004  PLL_CTRL     (REG_RENAMED from PLL_CON)
 *   0x0008  DIV_CON      (field changes)
 *   0x000C  SPREAD_CON   (field split/merge)
 *   0x0010  RST_CON      (field moved/promoted)
 *   0x0014  IRQ_CON      (semantic changes)
 *   0x0018  CLK_STATUS   (UNCHANGED - same offset as old CLK_STATUS was)
 *   0x001C  RESET_CON    (REG_ADDED - new offset, no old register here)
 *
 * NOTE: TRIM_CON (was at 0x0018 in old) is removed -> REG_DELETED
 *       CLK_STATUS moves from 0x001C (old) to 0x0018 (new) -> REG_MOVED
 *       RESET_CON appears at 0x001C (new, was empty) -> REG_ADDED
 * ========================================================================= */
typedef volatile struct _SFR_CLK_S
{
    SFR_CLK_CLK_CON     stCLK_CON;      /* 0x0000 Clock Control Register    */
    SFR_CLK_PLL_CTRL    stPLL_CTRL;     /* 0x0004 PLL Control (renamed)     */
    SFR_CLK_DIV_CON     stDIV_CON;      /* 0x0008 Divider Control           */
    SFR_CLK_SPREAD_CON  stSPREAD_CON;   /* 0x000C Spread Spectrum Control   */
    SFR_CLK_RST_CON     stRST_CON;      /* 0x0010 Reset Control             */
    SFR_CLK_IRQ_CON     stIRQ_CON;      /* 0x0014 Interrupt Control         */
    SFR_CLK_CLK_STATUS  stCLK_STATUS;   /* 0x0018 Clock Status (moved up)   */
    SFR_CLK_RESET_CON   stRESET_CON;    /* 0x001C Software/HW Reset (NEW)   */
} SFR_CLK, *pSFR_CLK;

#endif /* SFR_CLK_H */
