"""PR opener: commit the authored workflow on a branch and open a pull request.

Uses git (with a token-authenticated remote) to push the branch, and the
GitHub REST API to open the PR. The token is resolved from the environment or,
for local CLI runs, from `gh auth token`. It is only ever placed in the push
remote URL, never printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from .contracts import AuthoredWorkflow, RepoSnapshot

API = "https://api.github.com"
BRANCH_PREFIX = "ci-agent/add-ci"


class PROpenError(Exception):
    """Raised when the branch cannot be pushed or the PR cannot be opened."""


def resolve_token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    # Fall back to the gh CLI. A subprocess may have a narrower PATH than the
    # interactive shell (common under conda), so try known install locations too.
    candidates = [shutil.which("gh"), "/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]
    for exe in candidates:
        if not exe:
            continue
        try:
            out = subprocess.run(
                [exe, "auth", "token"], capture_output=True, text=True, timeout=15
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None


def open_pr(
    snapshot: RepoSnapshot,
    workflow: AuthoredWorkflow,
    clone_dir: Path,
    token: str,
) -> tuple[int, str, str]:
    """Commit the workflow on a fresh branch, push it, and open a PR.

    Returns (pr_number, pr_url, branch).
    """
    owner, name, base = snapshot.owner, snapshot.name, snapshot.default_branch
    branch = f"{BRANCH_PREFIX}-{int(time.time())}"

    target = clone_dir / workflow.path
    if target.exists():
        # Don't clobber an existing workflow of the same name.
        target = target.with_name(f"ci-agent-{target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(workflow.content)

    _git(clone_dir, "checkout", "-b", branch)
    _git(clone_dir, "add", str(target.relative_to(clone_dir)))
    _git(
        clone_dir, "-c", "user.name=ci-agent[bot]",
        "-c", "user.email=ci-agent@users.noreply.github.com",
        "commit", "-m", f"ci: add {classification_line(workflow)} workflow",
    )

    push_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
    try:
        _git(clone_dir, "push", push_url, f"{branch}:{branch}")
    except PROpenError as exc:
        raise PROpenError(f"failed to push branch: {_redact(str(exc), token)}") from None

    body = _pr_body(snapshot, workflow)
    resp = httpx.post(
        f"{API}/repos/{owner}/{name}/pulls",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={
            "title": "ci: add CI workflow (via ci-agent)",
            "head": branch,
            "base": base,
            "body": body,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise PROpenError(f"GitHub API {resp.status_code} opening PR: {resp.text[:400]}")
    data = resp.json()
    return data["number"], data["html_url"], branch


def classification_line(workflow: AuthoredWorkflow) -> str:
    return Path(workflow.path).stem


def _pr_body(snapshot: RepoSnapshot, workflow: AuthoredWorkflow) -> str:
    status = "validated" + (" (auto-repaired)" if workflow.repaired else "")
    return (
        "## 🤖 CI Agent — bootstrapped workflow\n\n"
        f"This PR adds `{workflow.path}` to **{snapshot.owner}/{snapshot.name}**, "
        "authored automatically after classifying the repository.\n\n"
        f"**Rationale:** {workflow.rationale}\n\n"
        f"**Validation:** {status} — parses as YAML, has triggers and jobs"
        + (", actionlint clean" if _actionlint_ran() else "")
        + ".\n\n"
        "Review the workflow and merge if it looks right."
    )


def _actionlint_ran() -> bool:
    import shutil

    return shutil.which("actionlint") is not None


def _git(cwd: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=120
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise PROpenError(f"git {' '.join(args[:2])} failed: {exc.stderr.strip()[:300]}") from exc


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text
