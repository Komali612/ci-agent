"""Sonar phase: run the SonarCloud scan and enforce the quality gate.

sonar.qualitygate.wait=true makes the scanner block until SonarCloud computes
the gate and exit non-zero when it fails -- so this phase's exit code *is* the
gate verdict, decided by deterministic code, not the LLM. The SonarCloud API
is additionally queried afterwards (best-effort) to pull the actual gate
conditions into the report.

The token is read by both scanners from the SONAR_TOKEN environment variable;
it is deliberately never placed in argv so it cannot leak into logs.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from contracts import Language, PhaseResult, PhaseStatus

from ci_agent.core import PhaseContext, pr_number, run_commands_phase

SONAR_HOST = "https://sonarcloud.io"


def run(ctx: PhaseContext) -> PhaseResult:
    token = ctx.env.get("SONAR_TOKEN", "")
    org = ctx.env.get("SONAR_ORGANIZATION", "")
    key = ctx.env.get("SONAR_PROJECT_KEY", "")
    if not (token and org and key):
        return PhaseResult(
            name="sonar",
            status=PhaseStatus.FAILURE,
            detail=(
                "SONAR_TOKEN / SONAR_ORGANIZATION / SONAR_PROJECT_KEY not configured. "
                "See SETUP.md -- or pass phases: build,test,push while bootstrapping."
            ),
        )

    result = run_commands_phase("sonar", _commands(ctx, org, key), ctx)
    gate = _quality_gate_summary(key, token, ctx)
    if gate:
        result.detail = gate
    return result


def _commands(ctx: PhaseContext, org: str, key: str) -> list[list[str]]:
    if ctx.classification.language is Language.JAVA:
        # Compiled classes and the JaCoCo report already exist from the
        # build/test phases -- all phases share one workspace.
        return [
            [
                "mvn", "-B",
                f"-Dsonar.host.url={SONAR_HOST}",
                f"-Dsonar.organization={org}",
                f"-Dsonar.projectKey={key}",
                "-Dsonar.qualitygate.wait=true",
                # Fully-qualified goal, not the short "sonar:sonar" prefix: the
                # pom doesn't declare the plugin, so prefix resolution depends on
                # fetching plugin-group metadata from Central, which is flaky.
                # Addressing the coordinates directly skips that lookup.
                "org.sonarsource.scanner.maven:sonar-maven-plugin:sonar",
            ]
        ]
    # The .NET scanner wraps the build (begin -> build -> test -> end); it
    # instruments MSBuild output, so this phase rebuilds. A known quirk.
    scanner = str(Path.home() / ".dotnet" / "tools" / "dotnet-sonarscanner")
    return [
        ["dotnet", "tool", "install", "--global", "dotnet-sonarscanner"],
        [
            scanner, "begin",
            f"/k:{key}",
            f"/o:{org}",
            f"/d:sonar.host.url={SONAR_HOST}",
            "/d:sonar.cs.opencover.reportsPaths=**/coverage.opencover.xml",
            "/d:sonar.qualitygate.wait=true",
        ],
        ["dotnet", "build", "--configuration", "Release"],
        [
            "dotnet", "test",
            "--configuration", "Release",
            "--no-build",
            "--collect", "XPlat Code Coverage;Format=opencover",
            "--results-directory", "TestResults",
        ],
        [scanner, "end"],
    ]


def _quality_gate_summary(key: str, token: str, ctx: PhaseContext) -> str:
    params = {"projectKey": key}
    pr = pr_number(ctx.env)
    if pr:
        params["pullRequest"] = pr
    try:
        resp = httpx.get(
            f"{SONAR_HOST}/api/qualitygates/project_status",
            params=params,
            auth=(token, ""),
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json()["projectStatus"]
        conditions = ", ".join(
            f"{c['metricKey']}={c.get('actualValue', '?')} ({c['status']})"
            for c in status.get("conditions", [])
        )
        summary = f"quality gate {status['status']}"
        return f"{summary}: {conditions}" if conditions else summary
    except Exception as exc:
        return f"quality gate detail unavailable ({exc})"
