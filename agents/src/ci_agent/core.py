"""Shared plumbing for CI Agent phases: subprocess runner, phase context,
and GitHub event helpers."""

from __future__ import annotations

import collections
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from contracts import ClassificationResult, PhaseResult, PhaseStatus

TAIL_LINES = 120


@dataclass
class PhaseContext:
    repo_root: Path
    agent_root: Path  # ci-agent checkout; where Dockerfile templates live
    classification: ClassificationResult
    logs_dir: Path
    env: dict[str, str] = field(default_factory=dict)
    image_ref: str | None = None  # set by the push phase


class CommandOutcome:
    def __init__(self, exit_code: int, tail: str):
        self.exit_code = exit_code
        self.tail = tail


def run_command(
    argv: list[str],
    cwd: Path,
    log_file,
    stdin_data: str | None = None,
) -> CommandOutcome:
    """Run one command, streaming output to both the console and a log file.

    Secrets must never appear in argv -- they are passed via environment
    variables (Sonar) or stdin (docker login) so this echo stays safe.
    """
    print(f"[ci-agent] $ {' '.join(argv)}", flush=True)
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError as exc:
        message = f"[ci-agent] command not found: {exc}\n"
        log_file.write(message)
        return CommandOutcome(127, message)

    if stdin_data is not None and proc.stdin is not None:
        proc.stdin.write(stdin_data)
        proc.stdin.close()
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        log_file.write(line)
        tail.append(line)
    return CommandOutcome(proc.wait(), "".join(tail))


def run_commands_phase(name: str, commands: list[list[str]], ctx: PhaseContext) -> PhaseResult:
    """Run a phase made of shell commands; the first non-zero exit fails it."""
    start = time.monotonic()
    log_path = ctx.logs_dir / f"{name}.log"
    tail = ""
    with open(log_path, "w") as log_file:
        for argv in commands:
            outcome = run_command(argv, cwd=ctx.repo_root, log_file=log_file)
            tail = outcome.tail
            if outcome.exit_code != 0:
                return PhaseResult(
                    name=name,
                    status=PhaseStatus.FAILURE,
                    exit_code=outcome.exit_code,
                    duration_seconds=time.monotonic() - start,
                    log_tail=tail,
                )
    return PhaseResult(
        name=name,
        status=PhaseStatus.SUCCESS,
        exit_code=0,
        duration_seconds=time.monotonic() - start,
        log_tail=tail,
    )


def github_event(env: dict[str, str]) -> dict:
    path = env.get("GITHUB_EVENT_PATH")
    if path and Path(path).is_file():
        try:
            return json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def pr_number(env: dict[str, str]) -> str | None:
    number = github_event(env).get("pull_request", {}).get("number")
    return str(number) if number else None


def head_sha(env: dict[str, str]) -> str:
    """The PR head commit -- more traceable than GITHUB_SHA, which is the
    synthetic merge commit on pull_request events."""
    sha = github_event(env).get("pull_request", {}).get("head", {}).get("sha")
    return sha or env.get("GITHUB_SHA", "unknown")
