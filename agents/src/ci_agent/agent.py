"""CI Agent: executes the four verification/delivery phases sequentially.

Pass/fail gating is deterministic -- exit codes and the Sonar quality gate --
never the LLM. The first failure stops the pipeline (later phases are marked
skipped), so `push` only runs when build, test, and sonar all succeeded.
"""

from __future__ import annotations

import os
from pathlib import Path

from contracts import ClassificationResult, PhaseResult, PhaseStatus, PipelineResult

from ci_agent.core import PhaseContext
from ci_agent.phases import build, push, sonar, test

PHASE_ORDER = [
    ("build", build.run),
    ("test", test.run),
    ("sonar", sonar.run),
    ("push", push.run),
]


def run_pipeline(
    classification: ClassificationResult, repo_root: Path, selected: set[str]
) -> PipelineResult:
    logs_dir = repo_root / ".ci-agent-logs"
    logs_dir.mkdir(exist_ok=True)
    ctx = PhaseContext(
        repo_root=repo_root,
        agent_root=Path(os.environ.get("CI_AGENT_ROOT", ".")).resolve(),
        classification=classification,
        logs_dir=logs_dir,
        env=dict(os.environ),
    )

    pipeline = PipelineResult(classification=classification)
    failed = False
    for name, phase_fn in PHASE_ORDER:
        if name not in selected:
            continue
        if failed:
            pipeline.phases.append(
                PhaseResult(
                    name=name,
                    status=PhaseStatus.SKIPPED,
                    detail="skipped: an earlier phase failed",
                )
            )
            continue
        print(
            f"\n[ci-agent] ===== phase: {name} "
            f"(playbook: {classification.language.value}) =====",
            flush=True,
        )
        result = phase_fn(ctx)
        pipeline.phases.append(result)
        if result.status is PhaseStatus.FAILURE:
            failed = True

    pipeline.image_ref = ctx.image_ref
    return pipeline
