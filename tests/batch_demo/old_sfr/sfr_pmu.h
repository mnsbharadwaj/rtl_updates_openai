/*
 * sfr_pmu.h -- PMU (Power Management Unit) SFR Header
 *
 * Samsung typedef volatile union bitfield format.
 * IP auto-detected from filename: sfr_pmu.h → PMU
 */
#ifndef SFR_PMU_H
#define SFR_PMU_H

#include <stdint.h>
typedef unsigned int UINT32;

/* =========================================================================
 * Register: PMU_CON  (Power Management Control)
 * ========================================================================= */
typedef volatile union _SFR_PMU_PMU_CON_U
{
    volatile UINT32 nValue _VALUE_(0x10000000);
    struct
    {
        volatile UINT32 PMU_DMA_CON         : 1;  // 0 - 0  [RW1S] PMU_DMA_CON 0 : PMU DMA is in idle state
        volatile UINT32 PMU_DMA_READ_CHECK  : 1;  // 1 - 1  [RW]   PMU_DMA_READ_CHECK 0 : PMU DMA did completed
        volatile UINT32 PMU_DMA_PAT_TRG_EN  : 1;  // 2 - 2  [RW]   PMU_DMA_PAT_TRG_EN 0 : PMU DMA SEQ TRG
        volatile UINT32 PMU_DMA_DET_TRG_EN  : 1;  // 3 - 3  [RW]   PMU_DMA_DET_TRG_EN 0 : PMU DMA SEQ TRG
        volatile UINT32 PMU_DMA_PASS        : 1;  // 4 - 4  [RO]   PMU_DMA_PASS 0 : Last PMU DMA was not successful
        volatile UINT32 PMU_DMA_READ_FAIL   : 1;  // 5 - 5  [RO]   PMU_DMA_READ_FAIL 0 : Last PMU READ DMA failed
        volatile UINT32 RSVD2               : 2;  // 6 - 7  Do Not Write(or Set) Here
        volatile UINT32 PMU_DMA_DATASEQINDEX: 1;  // 8 - 8  [RW]   PMU_DMA_DATASEQINDEX 0 : Select PMU DMA Data
        volatile UINT32 PMU_DMA_ADDRSEQINDEX: 1;  // 9 - 9  [RW]   PMU_DMA_ADDRSEQINDEX 0 : Select PMU DMA Addr
        volatile UINT32 RSVD1               :17;  // 10-26  Do Not Write(or Set) Here
        volatile UINT32 HCPU1_RESET_CON     : 1;  // 27-27  [RW]   HCPU1_RESET_CON 0 : HCPU1 is under RESET/HALT control
        volatile UINT32 HCPU1_VINIT_CON     : 1;  // 28-28  [RW]   HCPU1_VINIT_CON 0 : After Reset Release, HCPU1 fetches from High-vector
        volatile UINT32 RSVDB               : 3;  // 29-31  Do Not Write(or Set) Here
    } stNative;
} SFR_PMU_PMU_CON, *pSFR_PMU_PMU_CON;

/* =========================================================================
 * Register: SWT_CON  (Software Timer Control)
 * ========================================================================= */
typedef volatile union _SFR_PMU_SWT_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 PG_SCPRE  :16;  // 0 -15  [RW]  PG_SCPRE 0 : Power-gating Disable(Power On)
        volatile UINT32 PG_SCALL  :16;  // 16-31  [RW]  PG_SCALL 0 : Power-gating Disable(Power On)
    } stNative;
} SFR_PMU_SWT_CON, *pSFR_PMU_SWT_CON;

/* =========================================================================
 * Register: RST_CON  (Retention Control)
 * ========================================================================= */
typedef volatile union _SFR_PMU_RST_CON_U
{
    volatile UINT32 nValue _VALUE_(0x00000000);
    struct
    {
        volatile UINT32 RET_CON   :32;  // 0 -31  [RW]  RET_CON 0 : Retention Disable
    } stNative;
} SFR_PMU_RST_CON, *pSFR_PMU_RST_CON;

#endif /* SFR_PMU_H */
