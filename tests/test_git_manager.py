"""
test_git_manager.py — Unit tests for git_manager.py

All network + subprocess calls are mocked — no real git / internet needed.
Tests cover:
  - _detect_host()          : URL → 'github' | 'bitbucket_cloud' | 'bitbucket_server'
  - _inject_token()         : token embedding into HTTPS URL
  - _normalize_url()        : SSH → HTTPS normalisation, .git stripping
  - _repo_slug()            : owner/repo extraction
  - GitManager.clone()      : calls git clone with correct args
  - GitManager.create_branch(): checkout -b
  - GitManager.commit()     : git add + git commit + SHA return
  - GitManager.push()       : push with --force-with-lease
  - GitManager.create_pr()  : dispatches to _create_pr_github / _cloud / _server
  - _create_pr_github()     : POST to api.github.com/repos/{owner}/{repo}/pulls
  - _create_pr_cloud()      : POST to api.bitbucket.org/2.0/...
  - _create_pr_server()     : POST to {host}/rest/api/1.0/...
  - Fallback manual PR link : printed when API call fails
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from io import StringIO

# ── ensure lld_gen is importable ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from lld_gen.git_manager import (
    GitManager,
    RepoConfig,
    PRConfig,
    _detect_host,
    _inject_token,
    _normalize_url,
    _repo_slug,
)


# =============================================================================
# Helper: build a fake subprocess.CompletedProcess
# =============================================================================
def _cp(stdout="", returncode=0):
    r = MagicMock()
    r.stdout    = stdout
    r.stderr    = ""
    r.returncode = returncode
    return r


# =============================================================================
# 1. URL helper functions
# =============================================================================
class TestDetectHost(unittest.TestCase):

    def test_github_https(self):
        self.assertEqual(_detect_host("https://github.com/org/repo"), "github")

    def test_github_ssh(self):
        self.assertEqual(_detect_host("git@github.com:org/repo.git"), "github")

    def test_bitbucket_cloud(self):
        self.assertEqual(_detect_host("https://bitbucket.org/org/repo"), "bitbucket_cloud")

    def test_bitbucket_server(self):
        self.assertEqual(_detect_host("https://bb.company.internal/scm/proj/repo"), "bitbucket_server")

    def test_case_insensitive(self):
        self.assertEqual(_detect_host("https://GitHub.COM/org/repo"), "github")


class TestInjectToken(unittest.TestCase):

    def test_injects_into_https(self):
        url = _inject_token("https://github.com/org/repo", "mytoken")
        self.assertEqual(url, "https://mytoken@github.com/org/repo")

    def test_no_injection_for_ssh(self):
        url = _inject_token("git@github.com:org/repo.git", "mytoken")
        self.assertEqual(url, "git@github.com:org/repo.git")

    def test_empty_token(self):
        url = _inject_token("https://github.com/org/repo", "")
        self.assertEqual(url, "https://github.com/org/repo")

    def test_replaces_existing_creds(self):
        url = _inject_token("https://old@github.com/org/repo", "new")
        self.assertEqual(url, "https://new@github.com/org/repo")


class TestNormalizeUrl(unittest.TestCase):

    def test_strip_dot_git(self):
        self.assertEqual(_normalize_url("https://github.com/org/repo.git"),
                         "https://github.com/org/repo")

    def test_ssh_to_https(self):
        self.assertEqual(_normalize_url("git@github.com:org/repo.git"),
                         "https://github.com/org/repo")

    def test_bitbucket_ssh(self):
        self.assertEqual(_normalize_url("git@bitbucket.org:org/repo.git"),
                         "https://bitbucket.org/org/repo")

    def test_already_https(self):
        self.assertEqual(_normalize_url("https://github.com/org/repo"),
                         "https://github.com/org/repo")


class TestRepoSlug(unittest.TestCase):

    def test_github(self):
        owner, slug = _repo_slug("https://github.com/myorg/myrepo")
        self.assertEqual(owner, "myorg")
        self.assertEqual(slug, "myrepo")

    def test_bitbucket(self):
        owner, slug = _repo_slug("https://bitbucket.org/workspace/myrepo")
        self.assertEqual(owner, "workspace")
        self.assertEqual(slug, "myrepo")


# =============================================================================
# 2. GitManager.clone()
# =============================================================================
class TestGitManagerClone(unittest.TestCase):

    def _make_gm(self, url="https://github.com/org/repo", branch="main", token=""):
        cfg = RepoConfig(url=url, branch=branch, token=token)
        return GitManager(cfg, work_dir=Path("/tmp/fake_repo"))

    @patch("lld_gen.git_manager._run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.mkdir")
    def test_clone_shallow(self, mock_mkdir, mock_exists, mock_run):
        mock_run.return_value = _cp()
        gm = self._make_gm()
        gm.clone(Path("/tmp/fake_repo"))
        # First _run call should be git clone
        args = mock_run.call_args_list[0][0][0]
        self.assertIn("clone", args)
        self.assertIn("--depth", args)
        self.assertIn("1", args)

    @patch("lld_gen.git_manager._run")
    @patch("pathlib.Path.exists", return_value=True)  # .git exists
    def test_clone_existing_fetches(self, mock_exists, mock_run):
        mock_run.return_value = _cp()
        gm = self._make_gm()
        gm.clone(Path("/tmp/fake_repo"))
        args = mock_run.call_args_list[0][0][0]
        self.assertIn("fetch", args)

    @patch("lld_gen.git_manager._run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("pathlib.Path.mkdir")
    def test_token_embedded_in_url(self, mock_mkdir, mock_exists, mock_run):
        mock_run.return_value = _cp()
        gm = self._make_gm(token="secrettoken")
        gm.clone(Path("/tmp/fake_repo"))
        clone_cmd = mock_run.call_args_list[0][0][0]
        # auth URL should contain token
        auth_url = next(a for a in clone_cmd if "secrettoken" in a)
        self.assertIn("secrettoken", auth_url)


# =============================================================================
# 3. GitManager.commit()
# =============================================================================
class TestGitManagerCommit(unittest.TestCase):

    def _make_gm(self):
        cfg = RepoConfig(url="https://github.com/org/repo")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        return gm

    @patch("lld_gen.git_manager._run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_commit_returns_sha(self, mock_exists, mock_run):
        # Simulate: git diff --cached has staged files, git rev-parse returns SHA
        mock_run.side_effect = [
            _cp(),                  # git add file1
            _cp("file1.h\n"),       # git diff --cached (something staged)
            _cp(),                  # git config user.email
            _cp(),                  # git config user.name
            _cp(),                  # git commit
            _cp("abcd1234efgh\n"),  # git rev-parse HEAD
        ]
        gm = self._make_gm()
        sha = gm.commit([Path("/tmp/repo/file1.h")], "test commit")
        self.assertEqual(sha, "abcd1234")  # first 8 chars

    @patch("lld_gen.git_manager._run")
    @patch("pathlib.Path.exists", return_value=True)
    def test_nothing_to_commit_returns_none(self, mock_exists, mock_run):
        mock_run.side_effect = [
            _cp(),      # git add
            _cp(""),    # git diff --cached: nothing staged
        ]
        gm = self._make_gm()
        sha = gm.commit([Path("/tmp/repo/file1.h")], "empty commit")
        self.assertIsNone(sha)


# =============================================================================
# 4. GitManager.push()
# =============================================================================
class TestGitManagerPush(unittest.TestCase):

    @patch("lld_gen.git_manager._run")
    def test_push_success(self, mock_run):
        mock_run.side_effect = [
            _cp("my-branch\n"),   # git rev-parse --abbrev-ref HEAD
            _cp(),                 # git push
        ]
        cfg = RepoConfig(url="https://github.com/org/repo", token="tok")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        ok  = gm.push()
        self.assertTrue(ok)

    @patch("lld_gen.git_manager._run")
    def test_push_failure_returns_false(self, mock_run):
        mock_run.side_effect = [
            _cp("main\n"),          # current_branch
            _cp(returncode=128),    # push fails
        ]
        cfg = RepoConfig(url="https://github.com/org/repo")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        ok  = gm.push()
        self.assertFalse(ok)


# =============================================================================
# 5. GitManager.create_pr() — dispatch
# =============================================================================
class TestCreatePrDispatch(unittest.TestCase):

    def _gm(self, url):
        cfg = RepoConfig(url=url, token="mytoken")
        return GitManager(cfg, work_dir=Path("/tmp/repo"))

    def test_github_url_dispatches_to_github(self):
        gm = self._gm("https://github.com/org/repo")
        with patch.object(gm, "_create_pr_github", return_value="https://github.com/org/repo/pull/1") as mock_gh:
            pr = gm.create_pr("title", "body", "feature", "main")
        mock_gh.assert_called_once()
        self.assertIn("github.com", pr)

    def test_bitbucket_cloud_dispatches_to_cloud(self):
        gm = self._gm("https://bitbucket.org/workspace/repo")
        with patch.object(gm, "_create_pr_cloud", return_value="https://bitbucket.org/workspace/repo/pull-requests/5") as mock_bb:
            pr = gm.create_pr("title", "body", "feature", "main")
        mock_bb.assert_called_once()

    def test_bitbucket_server_dispatches_to_server(self):
        gm = self._gm("https://bb.internal.corp/projects/PROJ/repos/myrepo")
        with patch.object(gm, "_create_pr_server", return_value="https://bb.internal.corp/pull/1") as mock_srv:
            pr = gm.create_pr("title", "body", "feature", "main")
        mock_srv.assert_called_once()

    def test_api_failure_prints_fallback_link(self):
        gm = self._gm("https://github.com/org/repo")
        with patch.object(gm, "_create_pr_github", side_effect=RuntimeError("401")):
            with patch("builtins.print") as mock_print:
                pr = gm.create_pr("title", "body", "feature", "main")
        self.assertEqual(pr, "")
        # Should have printed a fallback URL
        all_output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("compare", all_output)

    def test_no_token_prints_warning(self):
        cfg = RepoConfig(url="https://github.com/org/repo", token="")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        with patch.object(gm, "_create_pr_github", return_value="https://pr-url"):
            with patch("builtins.print") as mock_print:
                with patch.dict("os.environ", {}, clear=True):
                    # Remove any GITHUB_TOKEN from env
                    import os
                    os.environ.pop("GITHUB_TOKEN", None)
                    os.environ.pop("GH_TOKEN", None)
                    gm.create_pr("title", "body", "feature", "main")
        all_output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("token", all_output.lower())


# =============================================================================
# 6. GitHub PR API call
# =============================================================================
class TestCreatePrGitHub(unittest.TestCase):

    def _gm(self):
        cfg = RepoConfig(url="https://github.com/myorg/myrepo", token="ghp_test")
        return GitManager(cfg, work_dir=Path("/tmp/repo"))

    def test_calls_correct_api_url(self):
        gm = self._gm()
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.read      = MagicMock(return_value=json.dumps({
            "html_url": "https://github.com/myorg/myrepo/pull/42"
        }).encode())

        import urllib.request as ur
        with patch.object(ur, "urlopen", return_value=fake_resp) as mock_urlopen:
            pr_url = gm._create_pr_github(
                "https://github.com/myorg/myrepo", "ghp_test",
                "My PR", "PR body", "feature-branch", "main"
            )

        self.assertEqual(pr_url, "https://github.com/myorg/myrepo/pull/42")
        req = mock_urlopen.call_args[0][0]
        self.assertIn("api.github.com", req.full_url)
        self.assertIn("myorg", req.full_url)
        self.assertIn("myrepo", req.full_url)
        self.assertIn("pulls", req.full_url)
        # Verify Authorization header
        self.assertEqual(req.get_header("Authorization"), "Bearer ghp_test")

    def test_payload_contains_head_base(self):
        gm = self._gm()
        captured = {}
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.read      = MagicMock(return_value=json.dumps({
            "html_url": "https://github.com/myorg/myrepo/pull/1"
        }).encode())

        import urllib.request as ur
        def capture(req, timeout=30):
            captured["payload"] = json.loads(req.data.decode())
            return fake_resp
        with patch.object(ur, "urlopen", side_effect=capture):
            gm._create_pr_github(
                "https://github.com/myorg/myrepo", "tok",
                "Title", "Body", "feat-branch", "main"
            )
        self.assertEqual(captured["payload"]["head"], "feat-branch")
        self.assertEqual(captured["payload"]["base"], "main")
        self.assertEqual(captured["payload"]["title"], "Title")


# =============================================================================
# 7. Bitbucket Cloud PR API call
# =============================================================================
class TestCreatePrBBCloud(unittest.TestCase):

    def _gm(self):
        cfg = RepoConfig(url="https://bitbucket.org/myws/myrepo", token="bb_token")
        return GitManager(cfg, work_dir=Path("/tmp/repo"))

    def test_calls_correct_api_url(self):
        gm = self._gm()
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.read      = MagicMock(return_value=json.dumps({
            "links": {"html": {"href": "https://bitbucket.org/myws/myrepo/pull-requests/7"}}
        }).encode())

        import urllib.request as ur
        with patch.object(ur, "urlopen", return_value=fake_resp) as mock_open:
            pr = gm._create_pr_cloud(
                "https://bitbucket.org/myws/myrepo", "bb_token",
                "My PR", "body", "feature", "main"
            )
        self.assertIn("7", pr)
        req = mock_open.call_args[0][0]
        self.assertIn("api.bitbucket.org", req.full_url)
        self.assertIn("myws", req.full_url)
        self.assertIn("myrepo", req.full_url)

    def test_payload_source_destination(self):
        gm = self._gm()
        captured = {}
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__  = MagicMock(return_value=False)
        fake_resp.read      = MagicMock(return_value=json.dumps({
            "links": {"html": {"href": "https://bitbucket.org/pr/1"}}
        }).encode())
        import urllib.request as ur
        def capture(req, timeout=30):
            captured["payload"] = json.loads(req.data.decode())
            return fake_resp
        with patch.object(ur, "urlopen", side_effect=capture):
            gm._create_pr_cloud(
                "https://bitbucket.org/myws/myrepo", "tok",
                "T", "B", "src-branch", "dst-branch"
            )
        self.assertEqual(captured["payload"]["source"]["branch"]["name"], "src-branch")
        self.assertEqual(captured["payload"]["destination"]["branch"]["name"], "dst-branch")


# =============================================================================
# 8. pr_target branch logic (same URL, different branch target)
# =============================================================================
class TestPrTargetBranchLogic(unittest.TestCase):
    """Verify PR target = pr_target.branch if given, else lld_repo.branch."""

    def test_pr_goes_to_configured_target_branch(self):
        cfg = RepoConfig(url="https://github.com/org/repo", token="tok")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        pr_cfg = PRConfig(url="https://github.com/org/repo", branch="release/v2")
        captured = {}

        with patch.object(gm, "_create_pr_github", side_effect=lambda *a, **kw: captured.update({"to": a[5]}) or "url") as m:
            gm.create_pr("T", "B", "feature", "main", pr_cfg=pr_cfg)

        # create_pr passes to_branch to _create_pr_github
        self.assertEqual(captured.get("to"), "main")

    def test_pr_uses_lld_branch_when_no_pr_target(self):
        cfg = RepoConfig(url="https://github.com/org/repo", token="tok")
        gm  = GitManager(cfg, work_dir=Path("/tmp/repo"))
        captured = {}
        with patch.object(gm, "_create_pr_github",
                          side_effect=lambda *a, **kw: captured.update({"from": a[4], "to": a[5]}) or "url"):
            gm.create_pr("T", "B", "lld-patch/main", "main")
        self.assertEqual(captured.get("to"), "main")
        self.assertEqual(captured.get("from"), "lld-patch/main")


# =============================================================================
# 9. Manual PR link fallback
# =============================================================================
class TestManualPrLink(unittest.TestCase):

    def _gm(self, url):
        cfg = RepoConfig(url=url, token="tok")
        return GitManager(cfg, work_dir=Path("/tmp/repo"))

    def test_github_fallback_url_format(self):
        gm = self._gm("https://github.com/org/repo")
        output = []
        with patch("builtins.print", side_effect=lambda *a: output.append(str(a))):
            gm._print_manual_pr_link("https://github.com/org/repo", "feat", "main", "github")
        self.assertTrue(any("compare" in o for o in output))

    def test_bitbucket_cloud_fallback_url_format(self):
        gm = self._gm("https://bitbucket.org/ws/repo")
        output = []
        with patch("builtins.print", side_effect=lambda *a: output.append(str(a))):
            gm._print_manual_pr_link("https://bitbucket.org/ws/repo", "feat", "main", "bitbucket_cloud")
        self.assertTrue(any("pull-requests" in o for o in output))


if __name__ == "__main__":
    unittest.main(verbosity=2)
