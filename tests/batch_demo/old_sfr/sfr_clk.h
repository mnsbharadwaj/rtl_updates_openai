/*
 * sfr_clk.h -- CLK (Clock Control Unit) SFR Header (OLD)
 *
 * SFR_CLK (OLD) -- Covers all 28 change type test scenarios
 * Samsung typedef volatile union bitfield format.
 * IP auto-detected from filename: sfr_clk.h -> CLK
 */
#ifndef SFR_CLK_H
#define SFR_CLK_H

#include <stdint.h>
typedef unsigned int UINT32;

/* =========================================================================
 * Register: CLK_CON  (Clock Control Register)
 * Demonstrates: BITWIDTH_CHANGED(7), ACCESS_CHANGED(8), OFFSET_CHANGED(9),
 *               FIELD_POLARITY_CHANGED(27) [access+reset+desc all change]
 * ========================================================================= */
typedef volatile union _SFR_CLK_CLK_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 CLK_EN      : 1;  // 0-0    [RW]  Clock enable
        volatile UINT32 CLK_DIV     : 4;  // 1-4    [RW]  Clock divider ratio
        volatile UINT32 RSVD_G      : 1;  // 5-5    reserved
        volatile UINT32 CLK_SRC     : 2;  // 6-7    [RO]  Clock source select
        volatile UINT32 CLK_GATE    : 2;  // 8-9    [RW]  Power gate control
        volatile UINT32 CLK_MODE    : 2;  // 10-11  [RW]  Clock mode
        volatile UINT32 RSVD        :20;  // 12-31  reserved
    } stNative;
} SFR_CLK_CLK_CON, *pSFR_CLK_CLK_CON;

/* =========================================================================
 * Register: PLL_CON  (PLL Control) -- will be REG_RENAMED to PLL_CTRL (type 1)
 * ========================================================================= */
typedef volatile union _SFR_CLK_PLL_CON_U
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
} SFR_CLK_PLL_CON, *pSFR_CLK_PLL_CON;

/* =========================================================================
 * Register: DIV_CON  (Divider Control)
 * Demonstrates: FIELD_RENAMED(4) DIV_PRE->DIV_PREDIV,
 *               FIELD_DELETED(5) DIV_POST,
 *               FIELD_ADDED(6) DIV_FRAC,
 *               MULTI_CHANGED(12) DIV_MODE: access+desc both change
 * ========================================================================= */
typedef volatile union _SFR_CLK_DIV_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 DIV_PRE     : 4;  // 0-3    [RW]  Pre-divider value
        volatile UINT32 DIV_POST    : 4;  // 4-7    [RW]  Post-divider value
        volatile UINT32 DIV_MODE    : 2;  // 8-9    [RW]  Divider mode
        volatile UINT32 RSVD        :22;  // 10-31  reserved
    } stNative;
} SFR_CLK_DIV_CON, *pSFR_CLK_DIV_CON;

/* =========================================================================
 * Register: SPREAD_CON  (Spread Spectrum Control)
 * Demonstrates: FIELD_SPLIT(13) SS_CTRL splits into SS_EN+SS_DEPTH,
 *               FIELD_MERGED(14) SS_MERGE_A+SS_MERGE_B -> SS_COMBINED
 * ========================================================================= */
typedef volatile union _SFR_CLK_SPREAD_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 SS_CTRL     : 8;  // 0-7    [RW]  Spread spectrum control byte
        volatile UINT32 SS_MERGE_A  : 4;  // 8-11   [RW]  Merge field A
        volatile UINT32 SS_MERGE_B  : 4;  // 12-15  [RW]  Merge field B
        volatile UINT32 RSVD        :16;  // 16-31  reserved
    } stNative;
} SFR_CLK_SPREAD_CON, *pSFR_CLK_SPREAD_CON;

/* =========================================================================
 * Register: RST_CON  (Reset Control)
 * Demonstrates: FIELD_MOVED_CROSS_REG(19) RST_EN removed (moved to RESET_CON),
 *               RESERVED_PROMOTED(20) RSVD_A bit[5] becomes CLK_BYPASS,
 *               RESERVED_PARTIAL_ACTIVATED(21) RSVD_B [16-17] becomes RST_TYPE,
 *               RESET_CHANGED(10) RST_CNT reset value changes
 * ========================================================================= */
typedef volatile union _SFR_CLK_RST_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 RST_EN      : 1;  // 0-0    [RW]  Reset enable
        volatile UINT32 RST_MODE    : 2;  // 1-2    [RW]  Reset mode select
        volatile UINT32 RSVD_A      : 5;  // 3-7    reserved
        volatile UINT32 RST_CNT     : 8;  // 8-15   [RW]  Reset counter
        volatile UINT32 RSVD_B      :16;  // 16-31  reserved
    } stNative;
} SFR_CLK_RST_CON, *pSFR_CLK_RST_CON;

/* =========================================================================
 * Register: IRQ_CON  (Interrupt Control)
 * Demonstrates: DESCRIPTION_ADDED(28) IRQ_EN empty->full desc,
 *               FIELD_ENUM_CHANGED(22) IRQ_MASK enum values change,
 *               FIELD_WRITE_ONCE(23) IRQ_TRIG gains RWL,
 *               WRITE_MASK_CHANGED(24) IRQ_POL desc notes partial RO mask,
 *               FIELD_SELF_CLEARING(25) IRQ_CLR becomes SC,
 *               FIELD_STICKY_CHANGED(26) IRQ_PENDING RO->W1C
 * ========================================================================= */
typedef volatile union _SFR_CLK_IRQ_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 IRQ_EN      : 1;  // 0-0    [RW]
        volatile UINT32 IRQ_MASK    : 2;  // 1-2    [RW]  IRQ mask bits: 0=ALL 1=NONE
        volatile UINT32 IRQ_TRIG    : 1;  // 3-3    [RW]  Trigger mode
        volatile UINT32 IRQ_PENDING : 1;  // 4-4    [RO]  IRQ pending
        volatile UINT32 IRQ_CLR     : 1;  // 5-5    [RW]  IRQ clear
        volatile UINT32 IRQ_POL     : 2;  // 6-7    [RW]  IRQ polarity 0=active-high
        volatile UINT32 RSVD        :24;  // 8-31   reserved
    } stNative;
} SFR_CLK_IRQ_CON, *pSFR_CLK_IRQ_CON;

/* =========================================================================
 * Register: TRIM_CON  (Trim Control) -- will be REG_DELETED (type 2)
 * ========================================================================= */
typedef volatile union _SFR_CLK_TRIM_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 TRIM_COARSE : 8;  // 0-7    [RW]  Coarse trim value
        volatile UINT32 TRIM_FINE   : 8;  // 8-15   [RW]  Fine trim value
        volatile UINT32 RSVD        :16;  // 16-31  reserved
    } stNative;
} SFR_CLK_TRIM_CON, *pSFR_CLK_TRIM_CON;

/* =========================================================================
 * Register: CLK_STATUS  (Clock Status) -- UNCHANGED in both versions
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
 * ========================================================================= */
typedef volatile struct _SFR_CLK_S
{
    SFR_CLK_CLK_CON     stCLK_CON;      /* 0x0000 Clock Control Register    */
    SFR_CLK_PLL_CON     stPLL_CON;      /* 0x0004 PLL Control               */
    SFR_CLK_DIV_CON     stDIV_CON;      /* 0x0008 Divider Control           */
    SFR_CLK_SPREAD_CON  stSPREAD_CON;   /* 0x000C Spread Spectrum Control   */
    SFR_CLK_RST_CON     stRST_CON;      /* 0x0010 Reset Control             */
    SFR_CLK_IRQ_CON     stIRQ_CON;      /* 0x0014 Interrupt Control         */
    SFR_CLK_TRIM_CON    stTRIM_CON;     /* 0x0018 Trim Control              */
    SFR_CLK_CLK_STATUS  stCLK_STATUS;   /* 0x001C Clock Status              */
} SFR_CLK, *pSFR_CLK;

#endif /* SFR_CLK_H */
