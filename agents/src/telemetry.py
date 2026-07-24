"""Telemetry: one structured record per pipeline run, uploaded as a workflow
artifact and later aggregated by ci-agent's collector workflow.

This is instrumentation at the source -- the record is a serialization of the
PipelineResult the CI Agent already computed, plus run metadata from the
Actions environment. Emission is best-effort and must never change the
pipeline verdict.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from contracts import PipelineResult

from ci_agent.core import head_sha, pr_number

SCHEMA_VERSION = 1


def build_record(pipeline: PipelineResult) -> dict:
    env = dict(os.environ)
    c = pipeline.classification
    return {
        "schema": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": env.get("GITHUB_REPOSITORY"),
        "run_id": env.get("GITHUB_RUN_ID"),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "event": env.get("GITHUB_EVENT_NAME"),
        "pr": pr_number(env),
        "sha": head_sha(env),
        "agent_ref": env.get("CI_AGENT_REF"),
        "conclusion": "failure" if pipeline.failed else "success",
        "classification": {
            "language": c.language.value,
            "build_tool": c.build_tool.value,
            "confidence": c.confidence,
            "method": c.method,
            "llm_input_tokens": c.llm_input_tokens,
            "llm_output_tokens": c.llm_output_tokens,
        },
        "phases": [
            {
                "name": p.name,
                "status": p.status.value,
                "duration_seconds": round(p.duration_seconds, 1),
                "exit_code": p.exit_code,
            }
            for p in pipeline.phases
        ],
        "image_ref": pipeline.image_ref,
    }


def write(pipeline: PipelineResult, path: Path) -> None:
    path.write_text(json.dumps(build_record(pipeline), indent=2) + "\n")
    print(f"[ci-agent] telemetry written to {path}")
