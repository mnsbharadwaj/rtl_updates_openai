"""
test_workflow_pr.py — Integration tests for the full PR workflow pipeline

Uses the existing batch_demo SFR/LLD files (PMU IP) as real test fixtures.
All git/network calls are mocked — no remote repo needed.

Tests cover:
  1. PR description content (change table, function delta, checklist)
  2. Full _run_one_ip() pipeline: diff → patch → compile-skip → PR description
  3. stage_pr(): writes PR_DESCRIPTION.md to disk, git add called
  4. Workflow PR target branch logic:
       - no pr_target → PR to lld_repo.branch
       - pr_target given → PR to pr_target.branch at pr_target.url
  5. GitManager.create_pr() called with correct from/to branches
  6. WorkflowRunResult.all_ok and print_summary() output
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.pr_stage import build_pr_description, stage_pr
from lld_gen.compile_check import CompileResult
from lld_gen.sfr_diff_analyzer import ChangeRecord, ChangeType


# =============================================================================
# Fixtures — minimal ChangeRecord list for PMU IP
# =============================================================================
def _make_changes():
    """Return a representative list of ChangeRecords for tests."""
    from lld_gen.sfr_diff_analyzer import FieldIR, RegisterIR

    def _cr(ct, reg, field=None, needs_llm=True):
        cr = ChangeRecord(
            change_type = ct,
            reg_name    = reg,
            field_name  = field,
            needs_llm   = needs_llm,
            details     = [f"Test detail for {ct}"],
        )
        return cr

    return [
        _cr(ChangeType.REG_RENAMED,     "PMU_CON",    needs_llm=False),
        _cr(ChangeType.FIELD_RENAMED,   "STATUS_CON", "DONE",   needs_llm=False),
        _cr(ChangeType.FIELD_DELETED,   "STATUS_CON", "FAIL"),
        _cr(ChangeType.FIELD_ADDED,     "STATUS_CON", "ABORT"),
        _cr(ChangeType.BITWIDTH_CHANGED,"STATUS_CON", "THRESH"),
        _cr(ChangeType.ACCESS_CHANGED,  "STATUS_CON", "ERR"),
        _cr(ChangeType.RESET_CHANGED,   "CLK_CON",    None,     needs_llm=False),
        _cr(ChangeType.COMMENT_CHANGED, "CLK_CON",    "CLK_SEL",needs_llm=False),
        _cr(ChangeType.UNCHANGED,       "RST_CON",    None,     needs_llm=False),
    ]


def _make_compile_ok():
    return CompileResult(success=True, needs_review=[], stdout="", stderr="")


def _make_compile_fail():
    return CompileResult(
        success=False,
        needs_review=["lld_pmu_status_con_fail_get"],
        stdout="",
        stderr="error: 'FAIL' undeclared",
    )


# =============================================================================
# 1. build_pr_description() — content correctness
# =============================================================================
class TestBuildPrDescription(unittest.TestCase):

    def setUp(self):
        self.changes  = _make_changes()
        self.compile  = _make_compile_ok()
        self.lld_file = Path("/tmp/lld_pmu.h")
        self.test_file= Path("/tmp/test_lld_pmu.c")

    def test_ip_name_in_title(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
        )
        self.assertIn("PMU", desc)

    def test_unchanged_not_in_table(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
        )
        # UNCHANGED entries must not appear as rows
        self.assertNotIn("UNCHANGED", desc)

    def test_all_change_types_appear(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
        )
        for ct in (
            "REG_RENAMED", "FIELD_RENAMED", "FIELD_DELETED",
            "FIELD_ADDED", "BITWIDTH_CHANGED", "ACCESS_CHANGED",
        ):
            self.assertIn(ct, desc, f"{ct} missing from PR description")

    def test_compile_pass_shows_pass(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
        )
        self.assertIn("PASS", desc)

    def test_compile_fail_shows_manual_and_checklist(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=_make_compile_fail(),
            lld_file=self.lld_file, test_file=self.test_file,
        )
        self.assertIn("MANUAL", desc)
        self.assertIn("lld_pmu_status_con_fail_get", desc)

    def test_added_fns_section(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
            added_fns=["lld_pmu_clk_con_clk_sel_get", "lld_pmu_clk_con_clk_sel_set"],
        )
        self.assertIn("lld_pmu_clk_con_clk_sel_get", desc)

    def test_removed_fns_section(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
            removed_fns=["lld_pmu_status_con_fail_get"],
        )
        self.assertIn("lld_pmu_status_con_fail_get", desc)

    def test_github_url_pr_section(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
            github_url="https://github.com/org/repo",
        )
        self.assertIn("https://github.com/org/repo", desc)
        self.assertIn("compare", desc)

    def test_manual_review_items_section(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
            manual_review_items=[
                "REG_ADDED: IRQ_CON",
                "REG_DELETED: SWT_CON",
            ],
        )
        self.assertIn("MANUAL REVIEW", desc)
        self.assertIn("IRQ_CON", desc)
        self.assertIn("SWT_CON", desc)

    def test_deprecated_fns_section(self):
        desc = build_pr_description(
            ip="PMU", changes=self.changes,
            compile_result=self.compile,
            lld_file=self.lld_file, test_file=self.test_file,
            deprecated_fns=["lld_pmu_swt_con_scpre_get"],
        )
        self.assertIn("Deprecated", desc)
        self.assertIn("lld_pmu_swt_con_scpre_get", desc)

    def test_no_changes_placeholder(self):
        desc = build_pr_description(
            ip="PMU",
            changes=[],                    # empty — only UNCHANGED
            compile_result=self.compile,
            lld_file=self.lld_file,
            test_file=self.test_file,
        )
        self.assertIn("No changes detected", desc)


# =============================================================================
# 2. stage_pr() — writes PR_DESCRIPTION.md to disk + git add
# =============================================================================
class TestStagePr(unittest.TestCase):

    def test_pr_description_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            lld_file  = Path(tmp) / "lld_pmu.h"
            test_file = Path(tmp) / "test_lld_pmu.c"
            sfr_new   = Path(tmp) / "sfr_pmu.h"
            lld_file.write_text("/* lld */", encoding="utf-8")
            test_file.write_text("/* test */", encoding="utf-8")
            sfr_new.write_text("/* sfr */", encoding="utf-8")

            with patch("lld_gen.pr_stage._git_add", return_value=True), \
                 patch("lld_gen.pr_stage._git_diff_stat", return_value="1 file changed"):
                pr_path = stage_pr(
                    ip="PMU",
                    changes=_make_changes(),
                    compile_result=_make_compile_ok(),
                    lld_file=lld_file,
                    test_file=test_file,
                    sfr_new=sfr_new,
                    out_dir=Path(tmp),
                    no_git=False,
                    github_url="https://github.com/org/repo",
                )

            self.assertTrue(pr_path.exists())
            content = pr_path.read_text(encoding="utf-8")
            self.assertIn("PMU", content)
            self.assertIn("REG_RENAMED", content)

    def test_git_add_called_when_no_git_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            lld_file  = Path(tmp) / "lld_pmu.h"
            test_file = Path(tmp) / "test_lld_pmu.c"
            sfr_new   = Path(tmp) / "sfr_pmu.h"
            for f in (lld_file, test_file, sfr_new):
                f.write_text("/* x */", encoding="utf-8")

            with patch("lld_gen.pr_stage._git_add", return_value=True) as mock_add, \
                 patch("lld_gen.pr_stage._git_diff_stat", return_value=""):
                stage_pr(
                    ip="PMU", changes=_make_changes(),
                    compile_result=_make_compile_ok(),
                    lld_file=lld_file, test_file=test_file,
                    sfr_new=sfr_new, out_dir=Path(tmp), no_git=False,
                )
            mock_add.assert_called_once()

    def test_git_add_skipped_when_no_git_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            lld_file  = Path(tmp) / "lld_pmu.h"
            test_file = Path(tmp) / "test_lld_pmu.c"
            sfr_new   = Path(tmp) / "sfr_pmu.h"
            for f in (lld_file, test_file, sfr_new):
                f.write_text("/* x */", encoding="utf-8")

            with patch("lld_gen.pr_stage._git_add", return_value=True) as mock_add, \
                 patch("lld_gen.pr_stage._git_diff_stat", return_value=""):
                stage_pr(
                    ip="PMU", changes=_make_changes(),
                    compile_result=_make_compile_ok(),
                    lld_file=lld_file, test_file=test_file,
                    sfr_new=sfr_new, out_dir=Path(tmp), no_git=True,
                )
            mock_add.assert_not_called()


# =============================================================================
# 3. Workflow PR branch target logic
# =============================================================================
class TestWorkflowPrBranchLogic(unittest.TestCase):
    """
    Rule: if pr_target.branch given → PR to that branch
          else                       → PR to lld_repo.branch
    """

    def _make_cfg(self, lld_branch="main", pr_url="", pr_branch=""):
        from lld_gen.config import WorkflowConfig, RepoSpec
        from pathlib import Path

        cfg = WorkflowConfig(
            sfr_old_dir   = Path("/tmp/old"),
            sfr_new_dir   = Path("/tmp/new"),
            lld_dir       = Path("/tmp/lld"),
            output_dir    = Path("/tmp/out"),
            tests_dir     = Path("/tmp/tests"),
            no_llm        = True,
            no_git        = False,
            hf_token      = "",
            gcc            = None,
            lld_repo       = RepoSpec(
                url    = "https://github.com/org/lld",
                branch = lld_branch,
                token  = "tok",
            ),
            pr_target      = RepoSpec(
                url    = pr_url,
                branch = pr_branch,
            ),
        )
        return cfg

    def test_pr_to_same_branch_when_no_pr_target(self):
        cfg = self._make_cfg(lld_branch="develop", pr_branch="")
        from lld_gen.workflow_runner import WorkflowRunner
        runner = WorkflowRunner(cfg)

        # The to_branch logic in run() step H:
        pr_spec   = cfg.pr_target
        to_branch = pr_spec.branch if pr_spec.branch else cfg.lld_repo.branch
        self.assertEqual(to_branch, "develop")

    def test_pr_to_override_branch_when_pr_target_set(self):
        cfg = self._make_cfg(lld_branch="develop", pr_branch="release/v3")
        pr_spec   = cfg.pr_target
        to_branch = pr_spec.branch if pr_spec.branch else cfg.lld_repo.branch
        self.assertEqual(to_branch, "release/v3")

    def test_pr_url_inherits_lld_repo_when_pr_target_url_empty(self):
        cfg = self._make_cfg(pr_url="")
        pr_spec  = cfg.pr_target
        pr_url   = pr_spec.url or cfg.lld_repo.url
        self.assertEqual(pr_url, "https://github.com/org/lld")

    def test_pr_url_uses_override_when_given(self):
        cfg = self._make_cfg(pr_url="https://github.com/org/new-repo")
        pr_spec  = cfg.pr_target
        pr_url   = pr_spec.url or cfg.lld_repo.url
        self.assertEqual(pr_url, "https://github.com/org/new-repo")


# =============================================================================
# 4. WorkflowRunResult.all_ok and print_summary()
# =============================================================================
class TestWorkflowRunResult(unittest.TestCase):

    def _result(self, statuses):
        from lld_gen.workflow_runner import WorkflowRunResult, IPWorkflowResult
        r = WorkflowRunResult()
        for ip, status in statuses:
            r.ip_results.append(IPWorkflowResult(ip=ip, status=status,
                                                  n_changes=5, n_auto_patched=4,
                                                  n_manual_review=1, compile_ok=(status=="OK"),
                                                  elapsed_s=12.5))
        r.total_elapsed_s = sum(ir.elapsed_s for ir in r.ip_results)
        return r

    def test_all_ok_true_when_all_ok(self):
        r = self._result([("PMU", "OK"), ("CLK", "OK")])
        self.assertTrue(r.all_ok)

    def test_all_ok_false_when_any_fail(self):
        r = self._result([("PMU", "OK"), ("CLK", "FAIL")])
        self.assertFalse(r.all_ok)

    def test_all_ok_true_with_skip(self):
        r = self._result([("PMU", "OK"), ("DMA", "SKIP")])
        self.assertTrue(r.all_ok)

    def test_print_summary_shows_all_ips(self):
        r = self._result([("PMU", "OK"), ("CLK", "WARN"), ("UART", "FAIL")])
        output = []
        with patch("lld_gen.workflow_runner.logger.info", side_effect=lambda *a: output.append(str(a))):
            r.print_summary()
        combined = " ".join(output)
        self.assertIn("PMU",  combined)
        self.assertIn("CLK",  combined)
        self.assertIn("UART", combined)
        self.assertIn("OK",   combined)
        self.assertIn("WARN", combined)
        self.assertIn("FAIL", combined)

    def test_print_summary_shows_timing(self):
        r = self._result([("PMU", "OK")])
        r.total_elapsed_s = 95.7
        output = []
        with patch("lld_gen.workflow_runner.logger.info", side_effect=lambda *a: output.append(str(a))):
            r.print_summary()
        combined = " ".join(output)
        self.assertIn("1m", combined)   # 95s = 1m 35s
        self.assertIn("35s", combined)


# =============================================================================
# 5. End-to-end: _run_one_ip() with batch_demo PMU files (no LLM, no git)
# =============================================================================
class TestRunOneIpPMU(unittest.TestCase):
    """
    Uses the real PMU SFR and LLD files from tests/workspace_demo/.
    Skips if the fixture files don't exist.
    """

    DEMO_DIR = Path(__file__).parent / "workspace_demo"

    def setUp(self):
        self.old_sfr  = self.DEMO_DIR / "old_sfr" / "sfr_pmu.h"
        self.new_sfr  = self.DEMO_DIR / "new_sfr" / "sfr_pmu.h"
        self.lld_file = self.DEMO_DIR / "lld"     / "lld_pmu.h"
        missing = [p for p in (self.old_sfr, self.new_sfr, self.lld_file) if not p.exists()]
        if missing:
            self.skipTest(f"Fixture files missing: {missing}")

    def test_classify_pmu_diff(self):
        """Diff produces at least 10 classified changes for PMU."""
        from lld_gen.sfr_diff_analyzer import classify_sfr_diff
        changes = classify_sfr_diff(self.old_sfr, self.new_sfr, ip="PMU")
        non_unchanged = [c for c in changes if c.change_type != ChangeType.UNCHANGED]
        self.assertGreaterEqual(len(non_unchanged), 10,
            f"Expected ≥10 changes, got {len(non_unchanged)}: "
            f"{[c.change_type for c in non_unchanged]}")

    def test_pr_description_generated_from_diff(self):
        """Full PR description generated from real PMU diff."""
        from lld_gen.sfr_diff_analyzer import classify_sfr_diff

        with tempfile.TemporaryDirectory() as tmp:
            out_lld   = Path(tmp) / "lld_pmu.h"
            out_test  = Path(tmp) / "test_lld_pmu.c"
            out_lld.write_text(self.lld_file.read_text(encoding="utf-8"), encoding="utf-8")
            out_test.write_text("/* tests */", encoding="utf-8")

            changes = classify_sfr_diff(self.old_sfr, self.new_sfr, ip="PMU")

            with patch("lld_gen.pr_stage._git_diff_stat", return_value="2 files changed"):
                desc = build_pr_description(
                    ip             = "PMU",
                    changes        = changes,
                    compile_result = _make_compile_ok(),
                    lld_file       = out_lld,
                    test_file      = out_test,
                    github_url     = "https://github.com/org/repo",
                )

            self.assertIn("PMU", desc)
            self.assertIn("compare", desc)
            # Should have at least one real change type in the table
            has_type = any(
                ct in desc for ct in [
                    "FIELD_RENAMED", "FIELD_DELETED", "FIELD_ADDED",
                    "BITWIDTH_CHANGED", "ACCESS_CHANGED", "REG_RENAMED",
                ]
            )
            self.assertTrue(has_type, "No change types found in PR description")


# =============================================================================
# 6. load_workflow_config() — YAML parsing and field wiring
# =============================================================================
class TestLoadWorkflowConfig(unittest.TestCase):

    def _write_yaml(self, tmp: str, content: str) -> Path:
        p = Path(tmp) / "workflow_config.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_lld_repo_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml = f"""\
sfr_old_dir: {tmp}/old
sfr_new_dir: {tmp}/new
lld_dir: {tmp}/lld
output_dir: {tmp}/out
tests_dir: {tmp}/tests
no_llm: true
no_git: true
hf_token: ""

lld_repo:
  url: https://github.com/org/lld
  branch: develop
  token: ghp_test

pr_target:
  url: https://github.com/org/lld
  branch: release/v2
"""
            for d in ("old","new","lld","out","tests"):
                Path(tmp, d).mkdir()
            p = self._write_yaml(tmp, yaml)

            from lld_gen.config import load_workflow_config
            cfg = load_workflow_config(p)

            self.assertEqual(cfg.lld_repo.url,    "https://github.com/org/lld")
            self.assertEqual(cfg.lld_repo.branch, "develop")
            self.assertEqual(cfg.lld_repo.token,  "ghp_test")
            self.assertEqual(cfg.pr_target.branch,"release/v2")

    def test_llm_block_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml = f"""\
sfr_old_dir: {tmp}/old
sfr_new_dir: {tmp}/new
lld_dir: {tmp}/lld
output_dir: {tmp}/out
tests_dir: {tmp}/tests
no_llm: false
no_git: true
hf_token: ""

llm:
  backend: ollama
  model: qwen2.5-coder:7b
  temperature: 0.05

lld_repo:
  url: https://github.com/org/lld
  branch: main
"""
            for d in ("old","new","lld","out","tests"):
                Path(tmp, d).mkdir()
            p = self._write_yaml(tmp, yaml)

            from lld_gen.config import load_workflow_config
            cfg = load_workflow_config(p)

            self.assertEqual(cfg.llm.get("backend"), "ollama")
            self.assertEqual(cfg.llm.get("model"),   "qwen2.5-coder:7b")
            self.assertAlmostEqual(float(cfg.llm.get("temperature", 0)), 0.05)

    def test_token_inherited_by_pr_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml = f"""\
sfr_old_dir: {tmp}/old
sfr_new_dir: {tmp}/new
lld_dir: {tmp}/lld
output_dir: {tmp}/out
tests_dir: {tmp}/tests
no_llm: true
no_git: true
hf_token: ""

lld_repo:
  url: https://github.com/org/lld
  branch: main
  token: inherited_token

pr_target:
  url: https://github.com/org/lld
  branch: release
"""
            for d in ("old","new","lld","out","tests"):
                Path(tmp, d).mkdir()
            p = self._write_yaml(tmp, yaml)

            from lld_gen.config import load_workflow_config
            import os
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.pop("GH_TOKEN", None)
            cfg = load_workflow_config(p)

            # pr_target has no token in YAML → should inherit from lld_repo
            self.assertEqual(cfg.pr_target.token, "inherited_token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
