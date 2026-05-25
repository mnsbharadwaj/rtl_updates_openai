"""
Implementation Plan:
1. Auto-detect IP from filename: sfr_pmu.h → IP = PMU
2. New SFR parser for typedef volatile union bitfield format

New format:
  typedef volatile union _SFR_PMU_PMU_CON_U
  {
      volatile UINT32 nValue _VALUE_(0x10000000);
      struct
      {
          volatile UINT32 FIELD_NAME : bit_width; // lsb-msb [ACCESS] description
      } stNative;
  } SFR_PMU_PMU_CON, *pSFR_PMU_PMU_CON;

Extraction rules:
  - Register name : typedef name SFR_PMU_PMU_CON → strip SFR_ + IP prefix PMU_ → PMU_CON
  - Reset value   : _VALUE_(0x10000000)
  - Field name    : PMU_DMA_CON
  - Bit width     : :1 (accumulate for shift/mask)
  - Access        : [RW1S] [RW] [RO] [WO] from comment
  - Description   : text after access in comment
  - Shift/Mask    : computed by walking fields in order (accumulate bit positions)
"""
