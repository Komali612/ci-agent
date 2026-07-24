"""Reporting: GitHub job summary, upserted PR comment, and (when a phase
failed and an API key is available) an LLM-written plain-English diagnosis.

The diagnosis is interpretation only -- by the time it runs, the deterministic
gate has already decided the outcome. Every reporting step is best-effort: a
reporting failure must never change the pipeline verdict.
"""

from __future__ import annotations

import os

import httpx

from contracts import PhaseStatus, PipelineResult

from ci_agent.core import github_event

COMMENT_MARKER = "<!-- ci-agent-report -->"
STATUS_ICONS = {
    PhaseStatus.SUCCESS: "✅",
    PhaseStatus.FAILURE: "❌",
    PhaseStatus.SKIPPED: "⏭️",
}
DEFAULT_DIAGNOSIS_MODEL = "claude-opus-4-8"


def publish(pipeline: PipelineResult) -> None:
    diagnosis = _diagnose_failure(pipeline) if pipeline.failed else ""
    markdown = _build_markdown(pipeline, diagnosis)
    _write_job_summary(markdown)
    _upsert_pr_comment(markdown)


def _build_markdown(pipeline: PipelineResult, diagnosis: str) -> str:
    c = pipeline.classification
    lines = [
        COMMENT_MARKER,
        "## 🤖 CI Agent report",
        "",
        f"**Classification Agent** → `{c.language.value}` (build tool `{c.build_tool.value}`, "
        f"confidence {c.confidence:.2f}, via {c.method})",
    ]
    if c.evidence:
        lines.append(f"Evidence: {', '.join(f'`{e}`' for e in c.evidence[:5])}")
    lines += ["", "| Phase | Status | Duration | Detail |", "|---|---|---|---|"]
    for p in pipeline.phases:
        icon = STATUS_ICONS.get(p.status, "")
        duration = f"{p.duration_seconds:.0f}s" if p.duration_seconds else "–"
        detail = p.detail.replace("|", "\\|") or "–"
        lines.append(f"| {p.name} | {icon} {p.status.value} | {duration} | {detail} |")
    if pipeline.image_ref:
        lines += ["", f"**Image published:** `{pipeline.image_ref}`"]
    if diagnosis:
        lines += ["", "### What broke (Claude's read)", "", diagnosis]
    return "\n".join(lines) + "\n"


def _write_job_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(markdown)
    except OSError as exc:
        print(f"[ci-agent] warning: could not write job summary ({exc})")


def _upsert_pr_comment(markdown: str) -> None:
    env = os.environ
    token = env.get("GITHUB_TOKEN")
    repo = env.get("GITHUB_REPOSITORY")
    number = github_event(dict(env)).get("pull_request", {}).get("number")
    if not (token and repo and number):
        print("[ci-agent] no PR context; skipping PR comment")
        return

    base = env.get("GITHUB_API_URL", "https://api.github.com")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        existing = httpx.get(
            f"{base}/repos/{repo}/issues/{number}/comments",
            headers=headers,
            params={"per_page": 100},
            timeout=30,
        )
        existing.raise_for_status()
        comment_id = next(
            (c["id"] for c in existing.json() if COMMENT_MARKER in (c.get("body") or "")),
            None,
        )
        if comment_id:
            resp = httpx.patch(
                f"{base}/repos/{repo}/issues/comments/{comment_id}",
                headers=headers,
                json={"body": markdown},
                timeout=30,
            )
        else:
            resp = httpx.post(
                f"{base}/repos/{repo}/issues/{number}/comments",
                headers=headers,
                json={"body": markdown},
                timeout=30,
            )
        resp.raise_for_status()
        print("[ci-agent] PR comment updated" if comment_id else "[ci-agent] PR comment posted")
    except Exception as exc:
        print(f"[ci-agent] warning: could not post PR comment ({exc})")


def _diagnose_failure(pipeline: PipelineResult) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    failed = next((p for p in pipeline.phases if p.status is PhaseStatus.FAILURE), None)
    if failed is None or not (failed.log_tail.strip() or failed.detail):
        return ""
    try:
        import anthropic

        client = anthropic.Anthropic().with_options(timeout=90.0, max_retries=1)
        response = client.messages.create(
            model=os.environ.get("DIAGNOSIS_MODEL", DEFAULT_DIAGNOSIS_MODEL),
            max_tokens=1000,
            system=(
                "You are the CI Agent's failure analyst. Given the tail of a failed "
                "CI phase's log, explain in plain English what broke and the most "
                "likely fix. Be specific and brief: 2-4 sentences, no headings."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Phase `{failed.name}` failed with exit code {failed.exit_code} "
                        f"in a {pipeline.classification.language.value} repository.\n"
                        f"Detail: {failed.detail or 'n/a'}\n\n"
                        f"Log tail:\n```\n{failed.log_tail[-6000:]}\n```"
                    ),
                }
            ],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception as exc:
        print(f"[ci-agent] warning: failure diagnosis unavailable ({exc})")
        return ""
