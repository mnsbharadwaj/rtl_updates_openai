"""
lld_gen -- LLD Auto-Patcher pipeline v3.1.

Automated Low-Level Driver Generation from SFR Diff.
Samsung Semiconductor Research India (SSRI)
CXL Architecture & Security IP Division -- June 2026

Pipeline (v3.1):
    [A] ipxact_pipeline      -- IP-XACT .xml -> SFR_*.h (via convert.py)
    [B] sfr_diff_analyzer    -- Old vs New SFR diff -> 28 ChangeRecord types
    [C] lld_patcher          -- Struct-based AST surgical LLD patcher
    [D] compile_check        -- Per-function gcc gate (5 retries + LLM fix)
    [E] lld_cross_ref        -- Cross-LLD caller scanner
    [F] pr_stage             -- PR_DESCRIPTION.md generator + git staging
    [G] git_manager          -- Clone / atomic commit / Bitbucket PR API
    [H] workflow_runner      -- End-to-end orchestrator (A->G)
"""
import logging

__version__ = "3.1.0"
__author__  = "M.N. Srivatsa Bharadwaj"


def setup_logging(level: int = logging.INFO, log_file: str = "") -> None:
    """
    Configure root logger for the lld_gen package.

    Call this once at the start of main() or before running any pipeline.
    All modules use ``logging.getLogger(__name__)`` which inherits this config.

    Args:
        level:    Logging level (logging.DEBUG, logging.INFO, etc.)
        log_file: Optional file path to write logs to (in addition to console).
    """
    fmt = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list = [
        logging.StreamHandler(),   # console
    ]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
    )
    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


# Core pipeline exports
from lld_gen.sfr_diff_analyzer import (
    classify_sfr_diff,
    summarize_changes,
    ChangeType,
    ChangeRecord,
    SfrParser,
    SfrDiffAnalyzer,
)
from lld_gen.lld_patcher import (
    LLDPatcher,
    generate_register_block,
    generate_field_functions,
    extract_function,
)
from lld_gen.config import (
    PatcherConfig,
    WorkflowConfig,
    RepoSpec,
    load_config,
    load_workflow_config,
    discover_jobs,
)
from lld_gen.compile_check import run_compile_check, run_compile_check_one_fn
from lld_gen.pr_stage import stage_pr, build_pr_description
from lld_gen.batch_runner import BatchRunner, run_from_config
from lld_gen.workflow_runner import WorkflowRunner, run_workflow

__all__ = [
    "classify_sfr_diff", "summarize_changes", "ChangeType", "ChangeRecord",
    "SfrParser", "SfrDiffAnalyzer",
    "LLDPatcher", "generate_register_block", "generate_field_functions",
    "extract_function",
    "PatcherConfig", "WorkflowConfig", "RepoSpec",
    "load_config", "load_workflow_config", "discover_jobs",
    "run_compile_check", "run_compile_check_one_fn",
    "stage_pr", "build_pr_description",
    "BatchRunner", "run_from_config",
    "WorkflowRunner", "run_workflow",
]
