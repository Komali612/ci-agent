"""Ingest stage: clone a repository and reduce it to a RepoSnapshot.

The snapshot is what both agents reason over. We keep it small and textual so
it fits comfortably in an LLM context: the full file list (capped) plus the
contents of a handful of manifest files that reveal the ecosystem.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .contracts import RepoSnapshot

MAX_TREE_ENTRIES = 400
MAX_MANIFEST_CHARS = 4000
MAX_MANIFESTS = 10

# Manifest files that strongly signal an ecosystem. Read in full (truncated) so
# the agents can see declared dependencies, scripts, and test config.
MANIFEST_NAMES = {
    # Python
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "tox.ini", "noxfile.py", "conftest.py",
    # JavaScript / TypeScript
    "package.json", "tsconfig.json",
    # Go
    "go.mod",
    # Rust
    "Cargo.toml",
    # JVM
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    # Ruby / PHP
    "Gemfile", "composer.json",
    # Generic
    "Makefile", "Dockerfile",
}
MANIFEST_SUFFIXES = (".csproj", ".fsproj", ".sln")

EXCLUDED_DIRS = {".git", "node_modules", "target", "bin", "obj", "dist", "build", ".venv"}

_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


class IngestError(Exception):
    """Raised when the repository cannot be fetched or parsed."""


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Extract (owner, name) from a GitHub URL. Raises on anything else."""
    m = _URL_RE.match(repo_url.strip())
    if not m:
        raise IngestError(
            f"could not parse GitHub owner/name from {repo_url!r}; "
            "expected e.g. https://github.com/owner/repo"
        )
    return m.group("owner"), m.group("name")


def ingest(repo_url: str, workdir: Path, token: str | None = None) -> RepoSnapshot:
    """Shallow-clone `repo_url` into `workdir` and build a RepoSnapshot."""
    owner, name = parse_repo_url(repo_url)
    clone_dir = workdir / name

    clone_url = f"https://github.com/{owner}/{name}.git"
    if token:
        # Auth lets us reach private repos and lifts unauthenticated rate limits.
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(clone_dir)],
            capture_output=True, text=True, check=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        # Never surface the token-bearing URL in an error message.
        raise IngestError(f"git clone failed for {owner}/{name}: {exc.stderr.strip()[:400]}") from exc
    except subprocess.SubprocessError as exc:
        raise IngestError(f"git clone failed for {owner}/{name}: {exc}") from exc

    default_branch = _current_branch(clone_dir)
    tree = _file_tree(clone_dir)
    if not tree:
        raise IngestError(f"repository {owner}/{name} appears to be empty")
    manifests = _read_manifests(clone_dir, tree)

    return RepoSnapshot(
        repo_url=repo_url,
        owner=owner,
        name=name,
        default_branch=default_branch,
        tree=tree,
        manifests=manifests,
    )


def _current_branch(clone_dir: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=clone_dir, capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
        return out or "main"
    except subprocess.SubprocessError:
        return "main"


def _file_tree(clone_dir: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=clone_dir, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        entries = [line for line in out.splitlines() if line.strip()]
        return entries[:MAX_TREE_ENTRIES]
    except subprocess.SubprocessError:
        entries = [
            str(p.relative_to(clone_dir))
            for p in sorted(clone_dir.rglob("*"))
            if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.relative_to(clone_dir).parts)
        ]
        return entries[:MAX_TREE_ENTRIES]


def _read_manifests(clone_dir: Path, tree: list[str]) -> dict[str, str]:
    manifests: dict[str, str] = {}
    for rel in tree:
        name = Path(rel).name
        if name in MANIFEST_NAMES or name.endswith(MANIFEST_SUFFIXES):
            try:
                manifests[rel] = (clone_dir / rel).read_text(errors="replace")[:MAX_MANIFEST_CHARS]
            except OSError:
                continue
        if len(manifests) >= MAX_MANIFESTS:
            break
    return manifests
