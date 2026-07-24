import argparse
import sys
from pathlib import Path

import report
from contracts import ClassificationResult

from .agent import run_pipeline

ALL_PHASES = ["build", "test", "sonar", "push"]
BANNER = "=" * 21


def main() -> int:
    parser = argparse.ArgumentParser(prog="ci_agent")
    parser.add_argument(
        "--classification",
        required=True,
        help="Path to classification.json -- the handoff from the Classification Agent",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help="Comma-separated subset of build,test,sonar,push (useful while bootstrapping)",
    )
    args = parser.parse_args()

    classification = ClassificationResult.model_validate_json(
        Path(args.classification).read_text()
    )
    selected = {p.strip() for p in args.phases.split(",") if p.strip()}
    unknown = selected - set(ALL_PHASES)
    if unknown:
        print(f"[ci-agent] unknown phases: {sorted(unknown)}", file=sys.stderr)
        return 2

    print(f"{BANNER} CI Agent {BANNER}", flush=True)
    print(
        f"[ci-agent] handoff received: language={classification.language.value} "
        f"build_tool={classification.build_tool.value} "
        f"confidence={classification.confidence:.2f} method={classification.method}"
    )

    pipeline = run_pipeline(classification, Path(args.repo_root).resolve(), selected)
    report.publish(pipeline)

    print()
    for phase in pipeline.phases:
        print(f"[ci-agent] {phase.name}: {phase.status.value}")
    if pipeline.image_ref:
        print(f"[ci-agent] image pushed: {pipeline.image_ref}")

    return 1 if pipeline.failed else 0


if __name__ == "__main__":
    sys.exit(main())
