"""Orchestration: repo URL in, BootstrapResult out.

Wires the stages together: ingest -> Classification Agent -> CI-Authoring Agent
-> PR opener. Each stage's failure is turned into a structured result rather
than an exception crossing the service boundary.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import author as author_mod
from . import classify as classify_mod
from . import github as github_mod
from . import ingest as ingest_mod
from .contracts import BootstrapResult


def bootstrap(repo_url: str, *, open_pr: bool = True, token: str | None = None) -> BootstrapResult:
    """Run the full bootstrapping flow for a single repository."""
    token = token or github_mod.resolve_token()

    with tempfile.TemporaryDirectory(prefix="ci-agent-bootstrap-") as td:
        workdir = Path(td)
        try:
            snapshot = ingest_mod.ingest(repo_url, workdir, token=token)
        except ingest_mod.IngestError as exc:
            return BootstrapResult(repo_url=repo_url, status="error", message=str(exc))

        print(f"[bootstrap] {snapshot.owner}/{snapshot.name}: {len(snapshot.tree)} files, "
              f"{len(snapshot.manifests)} manifests, default branch {snapshot.default_branch}")

        # --- Classification Agent ---
        try:
            classification = classify_mod.classify(snapshot)
        except Exception as exc:
            return BootstrapResult(repo_url=repo_url, status="error", message=f"classification failed: {exc}")
        print(f"[bootstrap] classified: {classification.language}/{classification.ecosystem} "
              f"(confidence {classification.confidence:.2f}, via {classification.method})")

        # --- CI-Authoring Agent ---
        try:
            workflow = author_mod.author(snapshot, classification)
        except Exception as exc:
            return BootstrapResult(
                repo_url=repo_url, status="error",
                classification=classification, message=f"authoring failed: {exc}",
            )
        print(f"[bootstrap] authored {workflow.path}: valid={workflow.valid} "
              f"repaired={workflow.repaired}")

        if not workflow.valid:
            return BootstrapResult(
                repo_url=repo_url, status="error", classification=classification,
                workflow=workflow,
                message="authored workflow failed validation: " + "; ".join(workflow.validation_notes),
            )

        if not open_pr:
            return BootstrapResult(
                repo_url=repo_url, status="authored_only", classification=classification,
                workflow=workflow, message="workflow authored and validated; PR not opened (open_pr=False)",
            )

        if not token:
            return BootstrapResult(
                repo_url=repo_url, status="authored_only", classification=classification,
                workflow=workflow,
                message="workflow authored and validated, but no GitHub token available to open a PR "
                        "(set GITHUB_TOKEN or run `gh auth login`)",
            )

        # --- PR opener ---
        try:
            pr_number, pr_url, branch = github_mod.open_pr(snapshot, workflow, workdir / snapshot.name, token)
        except github_mod.PROpenError as exc:
            return BootstrapResult(
                repo_url=repo_url, status="error", classification=classification,
                workflow=workflow, message=f"PR creation failed: {exc}",
            )

        print(f"[bootstrap] opened PR #{pr_number}: {pr_url}")
        return BootstrapResult(
            repo_url=repo_url, status="opened", classification=classification, workflow=workflow,
            branch=branch, pr_number=pr_number, pr_url=pr_url,
            message=f"opened PR #{pr_number}",
        )
