"""Push phase: build the Docker image and push it to GHCR.

Only reached when build, test, and sonar all succeeded -- the CI Agent's
sequential gating guarantees that. Dockerfile selection follows the
buildpacks-style override rule: a Dockerfile at the service repo root wins;
otherwise the template matching the classification is used.
"""

from __future__ import annotations

import time
from pathlib import Path

from contracts import PhaseResult, PhaseStatus

from ci_agent.core import PhaseContext, head_sha, pr_number, run_command


def run(ctx: PhaseContext) -> PhaseResult:
    start = time.monotonic()
    env = ctx.env
    repository = env.get("GITHUB_REPOSITORY", "")
    token = env.get("GITHUB_TOKEN", "")
    actor = env.get("GITHUB_ACTOR", "")
    if not (repository and token and actor):
        return PhaseResult(
            name="push",
            status=PhaseStatus.FAILURE,
            detail="GITHUB_REPOSITORY/GITHUB_TOKEN/GITHUB_ACTOR missing -- push only runs inside GitHub Actions",
        )

    dockerfile = _select_dockerfile(ctx)
    sha = head_sha(env)
    pr = pr_number(env)
    tag = f"pr-{pr}-{sha[:7]}" if pr else f"manual-{sha[:7]}"
    image_ref = f"ghcr.io/{repository.lower()}:{tag}"

    def fail(step: str, outcome) -> PhaseResult:
        return PhaseResult(
            name="push",
            status=PhaseStatus.FAILURE,
            exit_code=outcome.exit_code,
            duration_seconds=time.monotonic() - start,
            log_tail=outcome.tail,
            detail=f"{step} failed for {image_ref}",
        )

    with open(ctx.logs_dir / "push.log", "w") as log_file:
        build = run_command(
            [
                "docker", "build",
                "--file", str(dockerfile),
                "--tag", image_ref,
                "--label", f"org.opencontainers.image.source=https://github.com/{repository}",
                "--label", f"org.opencontainers.image.revision={sha}",
                str(ctx.repo_root),
            ],
            cwd=ctx.repo_root,
            log_file=log_file,
        )
        if build.exit_code != 0:
            return fail("docker build", build)

        # GITHUB_TOKEN is run-scoped and passed on stdin, never in argv.
        login = run_command(
            ["docker", "login", "ghcr.io", "--username", actor, "--password-stdin"],
            cwd=ctx.repo_root,
            log_file=log_file,
            stdin_data=token,
        )
        if login.exit_code != 0:
            return fail("docker login", login)

        push = run_command(["docker", "push", image_ref], cwd=ctx.repo_root, log_file=log_file)
        if push.exit_code != 0:
            return fail("docker push", push)

    ctx.image_ref = image_ref
    return PhaseResult(
        name="push",
        status=PhaseStatus.SUCCESS,
        exit_code=0,
        duration_seconds=time.monotonic() - start,
        detail=image_ref,
    )


def _select_dockerfile(ctx: PhaseContext) -> Path:
    override = ctx.repo_root / "Dockerfile"
    if override.is_file():
        print("[ci-agent] using the service repo's own Dockerfile (override wins)")
        return override
    template = ctx.agent_root / "templates" / f"{ctx.classification.language.value}.Dockerfile"
    print(f"[ci-agent] using template {template.name} selected by the classification")
    return template
