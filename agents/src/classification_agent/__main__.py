import argparse
import os
import sys
from pathlib import Path

from .agent import ClassificationError, classify

BANNER = "=" * 21


def main() -> int:
    parser = argparse.ArgumentParser(prog="classification_agent")
    parser.add_argument("--repo-root", default=".", help="Repository to classify")
    parser.add_argument("--output", default="classification.json", help="Where to write the handoff contract")
    args = parser.parse_args()

    print(f"{BANNER} Classification Agent {BANNER}", flush=True)
    try:
        result = classify(Path(args.repo_root).resolve())
    except ClassificationError as exc:
        print(f"[classification-agent] FATAL: {exc}", file=sys.stderr)
        return 1

    payload = result.model_dump_json(indent=2)
    Path(args.output).write_text(payload + "\n")
    print(payload)
    print(f"[classification-agent] handoff written to {args.output}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"language={result.language.value}\n")
            fh.write(f"confidence={result.confidence}\n")
            fh.write(f"method={result.method}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
