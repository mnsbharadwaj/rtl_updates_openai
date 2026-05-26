lld/
===

Place your existing LLD header files here (lld_pmu.h, lld_uart.h ...)
These files will NEVER be modified. Patched copies go to lld_patched/.

File naming convention:
  SFR files : sfr_<ipname>.h   (e.g. sfr_pmu.h, sfr_uart.h)
  LLD files : lld_<ipname>.h   (e.g. lld_pmu.h, lld_uart.h)
              OR <ipname>_lld.h (e.g. pmu_lld.h)
