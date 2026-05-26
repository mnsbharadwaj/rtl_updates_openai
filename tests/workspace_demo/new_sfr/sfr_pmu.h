/*
 * sfr_pmu.h — PMU (Power Management Unit) SFR Header  [NEW / UPDATED]
 *
 * Format  : Samsung typedef volatile union bitfield
 * IP      : PMU  (auto-detected from filename: sfr_pmu.h → PMU)
 *
 * All 12 change types demonstrated versus old_sfr/sfr_pmu.h:
 *
 *   Type 1  REG_RENAMED      : PMU_CON      → PMU_CTRL  (fields identical)
 *   Type 2  REG_DELETED      : SWT_CON      completely removed
 *   Type 3  REG_ADDED        : IRQ_CON      brand new register
 *   Type 4  FIELD_RENAMED    : STATUS_CON.DONE      → COMPLETE
 *   Type 5  FIELD_DELETED    : STATUS_CON.FAIL      removed
 *   Type 6  FIELD_ADDED      : STATUS_CON.ABORT     added at bit[9]
 *   Type 7  BITWIDTH_CHANGED : STATUS_CON.THRESH    [6:3] 4-bit → [7:3] 5-bit
 *   Type 8  ACCESS_CHANGED   : STATUS_CON.ERR       RO → RW
 *   Type 9  OFFSET_CHANGED   : STATUS_CON.LEVEL     [8:7] → [10:9]
 *   Type 10 RESET_CHANGED    : CLK_CON reset        0x00000000 → 0x0000000E
 *   Type 11 COMMENT_CHANGED  : CLK_CON.CLK_SEL      description updated
 *   Type 12 MULTI_CHANGED    : CLK_CON.CLK_GATE     access RW→RO AND desc updated
 */
#ifndef SFR_PMU_H
#define SFR_PMU_H

#include <stdint.h>
typedef unsigned int UINT32;

/* =========================================================================
 * Register: PMU_CTRL  (Power Management Control)
 *   Type 1: REG_RENAMED — was PMU_CON; fields are IDENTICAL to old PMU_CON
 *           Diff tool detects same offset (0x0000) + different name → REG_RENAMED
 * ========================================================================= */
typedef volatile union _SFR_PMU_PMU_CTRL_U
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
} SFR_PMU_PMU_CTRL, *pSFR_PMU_PMU_CTRL;

/* =========================================================================
 * Register: STATUS_CON  (DMA Status Control)
 *   Six field-level changes vs old STATUS_CON:
 *   Type 4  FIELD_RENAMED    : DONE[0:0]W1C  → COMPLETE[0:0]W1C
 *   Type 5  FIELD_DELETED    : FAIL[1:1]RO   removed (bit 1 → RSVD)
 *   Type 6  FIELD_ADDED      : ABORT[9:9]RW  new field
 *   Type 7  BITWIDTH_CHANGED : THRESH 4-bit[6:3] → 5-bit[7:3]
 *   Type 8  ACCESS_CHANGED   : ERR[2:2]  RO → RW
 *   Type 9  OFFSET_CHANGED   : LEVEL 2-bit  [8:7] → [10:9]
 * ========================================================================= */
typedef volatile union _SFR_PMU_STATUS_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 COMPLETE     : 1;  // 0-0   [W1C] DMA done flag
        volatile UINT32 RSVD1        : 1;  // reserved
        volatile UINT32 ERR          : 1;  // 2-2   [RW]  DMA error flag
        volatile UINT32 THRESH       : 5;  // 3-7   [RW]  Threshold level 4-bit
        volatile UINT32 RSVD2        : 1;  // reserved
        volatile UINT32 ABORT        : 1;  // 9-9   [RW]  DMA abort status
        volatile UINT32 LEVEL        : 2;  // 9-10  [RW]  FIFO level indicator
        volatile UINT32 RSVD3        :20;  // reserved
    } stNative;
} SFR_PMU_STATUS_CON, *pSFR_PMU_STATUS_CON;

/* =========================================================================
 * Register: CLK_CON  (Clock Control)
 *   Type 10 RESET_CHANGED    : reset 0x00000000 → 0x0000000E
 *                              (CLK_DIV bits[4:1]=0x7 → 0xE>>1=7)
 *   Type 11 COMMENT_CHANGED  : CLK_SEL desc changed
 *   Type 12 MULTI_CHANGED    : CLK_GATE access RW→RO AND desc changed
 * ========================================================================= */
typedef volatile union _SFR_PMU_CLK_CON_U
{
    volatile UINT32 nValue _VALUE_(0x0000000E);
    struct
    {
        volatile UINT32 CLK_EN       : 1;  // 0-0   [RW]  Clock enable signal
        volatile UINT32 CLK_DIV      : 4;  // 1-4   [RW]  Clock divider ratio
        volatile UINT32 CLK_SEL      : 2;  // 5-6   [RW]  Clock source mux: 0=PLL0 1=PLL1 2=OSC 3=EXT
        volatile UINT32 CLK_GATE     : 1;  // 7-7   [RO]  Power gate status (read-only in v2)
        volatile UINT32 RSVD0        :24;  // reserved
    } stNative;
} SFR_PMU_CLK_CON, *pSFR_PMU_CLK_CON;

/* =========================================================================
 * Register: RST_CON  (Retention Control)
 *   UNCHANGED — identical to old SFR
 * ========================================================================= */
typedef volatile union _SFR_PMU_RST_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 RET_CON      :32;  // 0-31  [RW]  Retention control register
    } stNative;
} SFR_PMU_RST_CON, *pSFR_PMU_RST_CON;

/* =========================================================================
 * Register: IRQ_CON  (Interrupt Control)
 *   Type 3: REG_ADDED — brand new register not present in old SFR
 * ========================================================================= */
typedef volatile union _SFR_PMU_IRQ_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 IRQ_EN       : 1;  // 0-0   [RW]  IRQ enable
        volatile UINT32 IRQ_PEND     : 1;  // 1-1   [W1C] IRQ pending flag
        volatile UINT32 IRQ_MASK     : 2;  // 2-3   [RW]  IRQ mask bits
        volatile UINT32 RSVD0        :28;  // reserved
    } stNative;
} SFR_PMU_IRQ_CON, *pSFR_PMU_IRQ_CON;

#endif /* SFR_PMU_H */
