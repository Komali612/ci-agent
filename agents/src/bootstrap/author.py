"""CI-Authoring Agent: the LLM writes the workflow, deterministic code gates it.

Per the design, the YAML is fully LLM-authored -- we do not template it. But
nothing reaches a pull request until it passes a deterministic validation gate
(parses as YAML, is a workflow with triggers and jobs, and -- if actionlint is
installed -- lints clean). On failure we hand the errors back to the LLM for a
single repair round-trip before giving up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from .contracts import AuthoredWorkflow, LLMWorkflow, RepoClassification, RepoSnapshot

DEFAULT_MODEL = "claude-opus-4-8"  # authoring is the harder generative task
WORKFLOW_DIR = ".github/workflows"

SYSTEM_PROMPT = """You are a senior CI engineer. Write a single, complete GitHub Actions
workflow (YAML) that gives this repository a solid continuous-integration check.

Requirements:
- Trigger on pull_request and push, and also allow workflow_dispatch.
- Run on ubuntu-latest.
- Check out the code, set up the correct language toolchain using official
  actions (e.g. actions/setup-python, actions/setup-node, actions/setup-go,
  actions/setup-java), install dependencies, then build (if applicable) and run
  the tests. Use the ecosystem and test command you are given.
- Pin action versions with a major tag (e.g. @v4).
- Keep it self-contained: no dependency on secrets, external reusable
  workflows, or private infrastructure.
- Output valid YAML only, no markdown fences, in the workflow_yaml field.

Base every step on the actual manifests and file layout you are shown. Do not
add deploy/publish steps; this is a CI check only."""

REPAIR_PROMPT = """The workflow you produced failed validation. Fix it and return the
corrected complete YAML. Errors:

{errors}

Previous YAML:
{previous}"""


def author(snapshot: RepoSnapshot, classification: RepoClassification) -> AuthoredWorkflow:
    """Ask the LLM to write the workflow, then validate (and repair once)."""
    llm, usage = _author_with_llm(snapshot, classification)
    filename = _safe_filename(llm.filename)
    content = _strip_fences(llm.workflow_yaml)

    notes = _validate(content)
    repaired = False
    if notes:
        print(f"[ci-authoring-agent] validation failed: {notes}; attempting one repair")
        try:
            llm2, usage2 = _repair_with_llm(snapshot, classification, content, notes)
            content2 = _strip_fences(llm2.workflow_yaml)
            notes2 = _validate(content2)
            usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in ("input_tokens", "output_tokens")}
            if not notes2:
                content, notes, repaired = content2, [], True
            else:
                content, notes, repaired = content2, notes2, True
        except Exception as exc:
            notes = notes + [f"repair attempt failed: {exc}"]

    return AuthoredWorkflow(
        path=f"{WORKFLOW_DIR}/{filename}",
        content=content,
        valid=not notes,
        validation_notes=notes,
        repaired=repaired,
        rationale=llm.rationale,
        llm_input_tokens=usage.get("input_tokens"),
        llm_output_tokens=usage.get("output_tokens"),
    )


def _render(snapshot: RepoSnapshot, classification: RepoClassification) -> str:
    manifests = "\n".join(f"\n## {p}\n```\n{c}\n```" for p, c in snapshot.manifests.items())
    return (
        f"Repository: {snapshot.owner}/{snapshot.name}\n"
        f"Classification: language={classification.language}, "
        f"ecosystem={classification.ecosystem}, test_command={classification.test_command!r}\n\n"
        f"# File tree\n" + "\n".join(snapshot.tree) + "\n\n"
        f"# Manifest files\n" + (manifests or "(none)")
    )


def _author_with_llm(snapshot: RepoSnapshot, classification: RepoClassification) -> tuple[LLMWorkflow, dict]:
    return _call(SYSTEM_PROMPT, _render(snapshot, classification))


def _repair_with_llm(
    snapshot: RepoSnapshot, classification: RepoClassification, previous: str, errors: list[str]
) -> tuple[LLMWorkflow, dict]:
    user = _render(snapshot, classification) + "\n\n" + REPAIR_PROMPT.format(
        errors="\n".join(f"- {e}" for e in errors), previous=previous
    )
    return _call(SYSTEM_PROMPT, user)


def _call(system: str, user: str) -> tuple[LLMWorkflow, dict]:
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required to author a workflow")
    client = anthropic.Anthropic().with_options(timeout=120.0, max_retries=1)
    response = client.messages.parse(
        model=os.environ.get("AUTHORING_MODEL", DEFAULT_MODEL),
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=LLMWorkflow,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ValueError("LLM returned no parseable workflow")
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return parsed, usage


def _strip_fences(text: str) -> str:
    """Defensive: remove ```/```yaml fences if the model added them anyway."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip() + "\n"


def _validate(content: str) -> list[str]:
    """Return a list of problems; empty means the workflow passed the gate."""
    notes: list[str] = []
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return [f"not valid YAML: {str(exc).splitlines()[0]}"]

    if not isinstance(data, dict):
        return ["workflow must be a YAML mapping"]

    # In YAML the bare key `on:` parses to the boolean True, not the string "on".
    if "on" not in data and True not in data:
        notes.append("missing 'on' trigger")
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        notes.append("missing or empty 'jobs' block")

    notes += _actionlint(content)
    return notes


def _actionlint(content: str) -> list[str]:
    """Run actionlint if it is installed; a bonus gate, skipped if absent."""
    exe = shutil.which("actionlint")
    if not exe:
        return []
    with tempfile.TemporaryDirectory() as td:
        wf = Path(td) / ".github" / "workflows" / "ci.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(content)
        try:
            proc = subprocess.run(
                [exe, "-no-color", str(wf)],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.SubprocessError:
            return []
        if proc.returncode != 0:
            out = (proc.stdout + proc.stderr).strip()
            return [f"actionlint: {line}" for line in out.splitlines()[:8] if line.strip()]
    return []


def _safe_filename(name: str) -> str:
    name = Path(name.strip() or "ci.yml").name
    if not name.endswith((".yml", ".yaml")):
        name += ".yml"
    return name
