"""
lld_gen — LLD Auto-Patcher pipeline.

Automated Low-Level Driver Generation from SFR Diff.
Samsung Semiconductor Research India (SSRI)
CXL Architecture & Security IP Division — May 2026

Pipeline:
    sfr_diff_analyzer → lld_patcher → compile_check → pr_stage
"""
__version__ = "1.0.0"
__author__  = "M.N. Srivatsa Bharadwaj"
