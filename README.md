# ci-agent

Two cooperating agents that run inside GitHub Actions:

1. **Classification Agent** — uses an LLM (Claude, structured output) to determine a
   repository's language/ecosystem, with a deterministic heuristic fallback. Writes the
   handoff contract `classification.json`.
2. **CI Agent** — consumes the handoff and *executes* the four phases itself, gated
   sequentially: **build → test → sonar → push**. On success it builds a Docker image
   (template selected by the classification, per-repo `Dockerfile` override wins) and
   pushes it to GHCR tagged `pr-<num>-<sha7>`.

Pass/fail gating is deterministic (exit codes + Sonar quality gate) — the LLM never
holds the gate. The LLM's second job is interpretation: when a phase fails, the report
includes a plain-English diagnosis of the log tail.

## How service repos use it

```yaml
jobs:
  agent:
    uses: <owner>/ci-agent/.github/workflows/agent.yml@main
    with:
      sonar-project-key: <owner>_<repo>
      sonar-organization: <owner>
      # phases: build,test,push   # optional subset while bootstrapping Sonar
    secrets: inherit
```

The caller needs `permissions: contents: read, packages: write, pull-requests: write`
and the secrets `SONAR_TOKEN` (required) and `ANTHROPIC_API_KEY` (optional — without it
the Classification Agent uses its heuristic fallback and failure diagnosis is skipped).

## Layout

```
.github/workflows/agent.yml   reusable workflow (on: workflow_call) — the two agent steps
agents/src/contracts.py       ClassificationResult handoff + phase/pipeline models
agents/src/classification_agent/   LLM classify -> heuristic fallback -> fail fast
agents/src/ci_agent/          phase runner, per-language playbooks, phases/{build,test,sonar,push}
agents/src/report.py          job summary + upserted PR comment + LLM failure diagnosis
templates/                    java.Dockerfile, dotnet.Dockerfile
```

## Running locally

```bash
pip install ./agents
cd /path/to/some-service
python -m classification_agent --output classification.json
python -m ci_agent --classification classification.json --phases build,test
```

`sonar` needs `SONAR_TOKEN`/`SONAR_ORGANIZATION`/`SONAR_PROJECT_KEY`; `push` needs the
GitHub Actions environment (`GITHUB_TOKEN`, event payload) plus Docker.
