"""
git_manager.py — Git + Bitbucket Operations Manager

Responsibilities:
  1. Clone / shallow-fetch a repo at a specific branch/tag/commit
  2. Atomic git add + commit (one commit per SFR file changed)
  3. Push patched branch to remote
  4. Create a Bitbucket Server/Cloud PR via REST API

Bitbucket PR API endpoints:
  Cloud:  POST https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}/pullrequests
  Server: POST https://{host}/rest/api/1.0/projects/{proj}/repos/{repo}/pull-requests

Usage:
    gm = GitManager(lld_repo_cfg, token="xxx")
    gm.clone(dest_path)
    gm.commit(["lld_pmu.h", "test_lld_pmu.c"], message="feat(lld): patch PMU")
    gm.push()
    pr_url = gm.create_pr(title, description, from_branch, to_branch)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
try:
    import urllib.request as _req
    import urllib.error as _uerr
    import json as _json
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False


# ---------------------------------------------------------------------------
# Repo config dataclass
# ---------------------------------------------------------------------------
@dataclass
class RepoConfig:
    """Configuration for one Git repository."""
    url:    str
    branch: str = "main"
    tag:    str = ""
    commit: str = ""           # specific commit hash (overrides branch/tag)
    path:   str = ""           # sub-path inside repo (for sparse checkout)
    token:  str = ""           # Bitbucket/GitHub access token


@dataclass
class PRConfig:
    """Configuration for creating a pull-request."""
    url:          str = ""   # target repo URL (empty = same as lld_repo)
    branch:       str = ""   # target branch  (empty = same as lld_repo.branch)
    title_prefix: str = "feat(lld): SFR auto-patch"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess, raise on non-zero unless check=False."""
    env = os.environ.copy()
    # Prevent interactive git prompts
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, env=env,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )
    return result


def _inject_token(url: str, token: str) -> str:
    """Embed token into HTTPS URL:  https://token@host/path"""
    if not token or not url.startswith("https://"):
        return url
    # Remove existing credentials if present
    url = re.sub(r"https://[^@]+@", "https://", url)
    return url.replace("https://", f"https://{token}@", 1)


def _normalize_url(url: str) -> str:
    """Normalize SSH → HTTPS, strip trailing .git."""
    # git@bitbucket.org:org/repo.git → https://bitbucket.org/org/repo
    m = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return url.removesuffix(".git")


def _detect_bitbucket_server(url: str) -> bool:
    """Return True if URL looks like Bitbucket Server (not Cloud)."""
    return "api.bitbucket.org" not in url and "bitbucket.org" not in url


def _repo_slug(url: str) -> tuple[str, str]:
    """Extract (project_or_workspace, repo_slug) from URL."""
    url = _normalize_url(url)
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "unknown", "unknown"


# ---------------------------------------------------------------------------
# GitManager
# ---------------------------------------------------------------------------
class GitManager:
    """
    Manages git operations for one repository.
    Works with GitHub, Bitbucket Cloud, and Bitbucket Server.
    """

    def __init__(self, repo_cfg: RepoConfig, work_dir: Optional[Path] = None):
        self.cfg      = repo_cfg
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="lld_gen_"))
        self._cloned  = False

    @property
    def repo_path(self) -> Path:
        return self.work_dir

    # ── Clone ────────────────────────────────────────────────────────────────

    def clone(self, dest: Optional[Path] = None) -> Path:
        """
        Clone the repo into dest (defaults to self.work_dir).
        Uses shallow clone (depth=1) unless a specific commit is requested.
        If repo already cloned, does a fetch+reset instead.
        """
        dest = dest or self.work_dir
        dest = Path(dest)

        auth_url = _inject_token(self.cfg.url, self.cfg.token)

        if (dest / ".git").exists():
            print(f"  [GIT] Repo already present at {dest} — fetching …")
            _run(["git", "fetch", "--all"], cwd=dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
            ref = self.cfg.branch or "main"
            depth_args = ["--depth", "1"] if not self.cfg.commit else []
            print(f"  [GIT] Cloning {self.cfg.url} branch={ref} …")
            _run(
                ["git", "clone", auth_url, str(dest),
                 "--branch", ref, "--single-branch"] + depth_args
            )

        # Checkout specific commit or tag if requested
        if self.cfg.commit:
            print(f"  [GIT] Checking out commit {self.cfg.commit[:8]} …")
            _run(["git", "fetch", "origin", self.cfg.commit], cwd=dest, check=False)
            _run(["git", "checkout", self.cfg.commit], cwd=dest)
        elif self.cfg.tag:
            print(f"  [GIT] Checking out tag {self.cfg.tag} …")
            _run(["git", "fetch", "origin", f"refs/tags/{self.cfg.tag}"], cwd=dest)
            _run(["git", "checkout", f"tags/{self.cfg.tag}"], cwd=dest)

        self.work_dir = dest
        self._cloned  = True
        print(f"  [GIT] Ready at {dest}")
        return dest

    # ── Branch management ────────────────────────────────────────────────────

    def create_branch(self, branch_name: str) -> None:
        """Create and checkout a new branch (from current HEAD)."""
        # Check if branch already exists
        result = _run(
            ["git", "branch", "--list", branch_name],
            cwd=self.work_dir, check=False
        )
        if branch_name in result.stdout:
            _run(["git", "checkout", branch_name], cwd=self.work_dir)
        else:
            _run(["git", "checkout", "-b", branch_name], cwd=self.work_dir, check=False)
        print(f"  [GIT] On branch {branch_name}")

    def current_branch(self) -> str:
        """Return the current git branch name."""
        result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                      cwd=self.work_dir, check=False)
        return result.stdout.strip()

    # ── Atomic commit ────────────────────────────────────────────────────────

    def commit(self, files: List[Path], message: str) -> Optional[str]:
        """
        Stage files and create one atomic commit.
        Returns the new commit SHA, or None if nothing to commit.
        """
        if not files:
            return None

        # git add each file
        for f in files:
            p = Path(f)
            if p.exists():
                _run(["git", "add", str(p)], cwd=self.work_dir, check=False)

        # Check if there's anything staged
        status = _run(["git", "diff", "--cached", "--name-only"],
                      cwd=self.work_dir, check=False)
        if not status.stdout.strip():
            print(f"  [GIT] Nothing to commit for: {message}")
            return None

        # Set identity if not set (CI environments)
        _run(["git", "config", "user.email", "lld-gen@samsung.com"],
             cwd=self.work_dir, check=False)
        _run(["git", "config", "user.name", "LLD Auto-Patcher"],
             cwd=self.work_dir, check=False)

        _run(["git", "commit", "-m", message], cwd=self.work_dir)
        sha_result = _run(["git", "rev-parse", "HEAD"], cwd=self.work_dir, check=False)
        sha = sha_result.stdout.strip()[:8]
        print(f"  [GIT] Committed {sha}: {message[:60]}")
        return sha

    # ── Push ─────────────────────────────────────────────────────────────────

    def push(self, remote: str = "origin", branch: Optional[str] = None) -> bool:
        """Push current branch to remote."""
        branch = branch or self.current_branch()
        auth_url = _inject_token(self.cfg.url, self.cfg.token)

        print(f"  [GIT] Pushing {branch} → {remote} …")
        result = _run(
            ["git", "push", auth_url, f"HEAD:refs/heads/{branch}", "--force-with-lease"],
            cwd=self.work_dir, check=False,
        )
        if result.returncode != 0:
            print(f"  [GIT-WARN] Push failed: {result.stderr.strip()}")
            return False
        print(f"  [GIT] Pushed OK → {branch}")
        return True

    # ── Bitbucket PR ─────────────────────────────────────────────────────────

    def create_pr(
        self,
        title:       str,
        description: str,
        from_branch: str,
        to_branch:   str,
        pr_cfg:      Optional[PRConfig] = None,
    ) -> str:
        """
        Create a pull-request via Bitbucket REST API.
        Returns the PR URL on success, or empty string on failure.
        """
        if not _HAS_URLLIB:
            return ""

        target_url = pr_cfg.url if (pr_cfg and pr_cfg.url) else self.cfg.url
        target_url = _normalize_url(target_url)
        token      = self.cfg.token

        if not token:
            token = os.environ.get("BB_TOKEN", os.environ.get("BITBUCKET_TOKEN", ""))

        is_server = _detect_bitbucket_server(target_url)

        try:
            if is_server:
                return self._create_pr_server(
                    target_url, token, title, description, from_branch, to_branch
                )
            else:
                return self._create_pr_cloud(
                    target_url, token, title, description, from_branch, to_branch
                )
        except Exception as exc:
            print(f"  [PR-WARN] Could not create PR via API: {exc}")
            return ""

    def _create_pr_cloud(
        self, base_url: str, token: str,
        title: str, description: str,
        from_branch: str, to_branch: str,
    ) -> str:
        """Bitbucket Cloud REST API v2.0"""
        workspace, repo_slug = _repo_slug(base_url)
        api_url = (
            f"https://api.bitbucket.org/2.0/repositories/"
            f"{workspace}/{repo_slug}/pullrequests"
        )
        payload = _json.dumps({
            "title": title,
            "description": description,
            "source":      {"branch": {"name": from_branch}},
            "destination": {"branch": {"name": to_branch}},
            "close_source_branch": False,
        }).encode()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        req = _req.Request(api_url, data=payload, headers=headers, method="POST")
        try:
            with _req.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read())
                pr_url = data.get("links", {}).get("html", {}).get("href", "")
                print(f"  [PR] Created: {pr_url}")
                return pr_url
        except _uerr.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"BB Cloud PR API {e.code}: {body[:200]}")

    def _create_pr_server(
        self, base_url: str, token: str,
        title: str, description: str,
        from_branch: str, to_branch: str,
    ) -> str:
        """Bitbucket Server REST API 1.0"""
        # Detect project key + repo slug from URL
        # URL pattern: https://host/scm/PROJECT/repo  or  https://host/projects/PROJ/repos/REPO
        parts = base_url.rstrip("/").split("/")
        repo_slug = parts[-1]

        # Try /projects/KEY/repos/SLUG pattern
        if "projects" in parts:
            idx = parts.index("projects")
            project_key = parts[idx + 1] if idx + 1 < len(parts) else "PROJ"
        else:
            project_key = parts[-2].upper() if len(parts) >= 2 else "PROJ"

        # Determine host
        host_parts = base_url.split("/")
        host = "/".join(host_parts[:3])  # https://host

        api_url = (
            f"{host}/rest/api/1.0/projects/{project_key}"
            f"/repos/{repo_slug}/pull-requests"
        )
        payload = _json.dumps({
            "title":       title,
            "description": description,
            "fromRef":     {"id": f"refs/heads/{from_branch}"},
            "toRef":       {"id": f"refs/heads/{to_branch}"},
            "reviewers":   [],
        }).encode()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        req = _req.Request(api_url, data=payload, headers=headers, method="POST")
        try:
            with _req.urlopen(req, timeout=30) as resp:
                data = _json.loads(resp.read())
                pr_url = data.get("links", {}).get("self", [{}])[0].get("href", "")
                print(f"  [PR] Created: {pr_url}")
                return pr_url
        except _uerr.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"BB Server PR API {e.code}: {body[:200]}")

    # ── File discovery ────────────────────────────────────────────────────────

    def get_files(
        self,
        pattern: str = "SFR_*.h",
        sub_path: str = "",
    ) -> List[Path]:
        """List files matching pattern under sub_path in the cloned repo."""
        base = self.work_dir / sub_path if sub_path else self.work_dir
        if not base.exists():
            return []
        return sorted(base.rglob(pattern))

    def get_lld_files(self, lld_path: str = "") -> List[Path]:
        """List lld_*.h files under lld_path."""
        return self.get_files("lld_*.h", lld_path)

    def get_sfr_files(self, sfr_path: str = "") -> List[Path]:
        """List SFR_*.h (and sfr_*.h) files under sfr_path."""
        base = self.work_dir / sfr_path if sfr_path else self.work_dir
        if not base.exists():
            return []
        files: List[Path] = []
        for pat in ("SFR_*.h", "sfr_*.h"):
            files.extend(base.rglob(pat))
        return sorted(set(files))
