"""Classification Agent (bootstrapping variant).

Same policy as the in-Actions classifier -- LLM is advisory, deterministic code
holds the floor -- but the output space is open (any language/ecosystem) because
the service must handle arbitrary repositories, not just Java/.NET.
"""

from __future__ import annotations

import os
from pathlib import Path

from .contracts import LLMClassification, RepoClassification, RepoSnapshot

CONFIDENCE_THRESHOLD = 0.8
DEFAULT_MODEL = "claude-haiku-4-5"  # classification stays the cheap LLM use case

SYSTEM_PROMPT = """You are a build-systems expert classifying a source repository.

You are given the repository's file tree and the contents of its manifest
files. Determine:
- language: the primary programming language, lowercase (e.g. python, javascript, typescript, go, java, ruby, rust, csharp).
- ecosystem: the package manager or build tool actually used (e.g. pip, poetry, pipenv, npm, yarn, pnpm, go, maven, gradle, cargo, bundler, dotnet).
- test_command: the single shell command a contributor runs to execute this project's tests (e.g. "pytest", "npm test", "go test ./...", "mvn -B verify"). Infer it from the manifests and test layout; do not invent a framework that is not present.
- confidence: 0..1, honest about ambiguity.
- evidence: concrete files or facts that justify the call.

Prefer signals from manifest contents over file extensions. Base the test
command on what the repo actually configures."""


def _render(snapshot: RepoSnapshot) -> str:
    lines = ["# File tree", *snapshot.tree, "", "# Manifest files"]
    if snapshot.manifests:
        for path, content in snapshot.manifests.items():
            lines += [f"\n## {path}", "```", content, "```"]
    else:
        lines.append("(none found)")
    return "\n".join(lines)


def classify(snapshot: RepoSnapshot) -> RepoClassification:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            llm, usage = _classify_with_llm(snapshot)
            if llm.confidence >= CONFIDENCE_THRESHOLD:
                return RepoClassification(
                    language=llm.language.lower().strip(),
                    ecosystem=llm.ecosystem.lower().strip(),
                    test_command=llm.test_command.strip(),
                    confidence=llm.confidence,
                    method="llm",
                    evidence=llm.evidence,
                    llm_input_tokens=usage.get("input_tokens"),
                    llm_output_tokens=usage.get("output_tokens"),
                )
            print(
                f"[classification-agent] LLM confidence {llm.confidence:.2f} below "
                f"threshold {CONFIDENCE_THRESHOLD}; falling back to heuristic"
            )
        except Exception as exc:
            print(f"[classification-agent] LLM classification failed ({exc}); using heuristic")
    else:
        print("[classification-agent] ANTHROPIC_API_KEY not set; using heuristic fallback")

    return _classify_with_heuristic(snapshot)


def _classify_with_llm(snapshot: RepoSnapshot) -> tuple[LLMClassification, dict]:
    import anthropic

    client = anthropic.Anthropic().with_options(timeout=60.0, max_retries=1)
    response = client.messages.parse(
        model=os.environ.get("CLASSIFIER_MODEL", DEFAULT_MODEL),
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _render(snapshot)}],
        output_format=LLMClassification,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ValueError("LLM returned no parseable classification")
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return parsed, usage


# (manifest signal, language, ecosystem, test_command) in priority order.
_HEURISTICS: list[tuple[str, str, str, str]] = [
    ("pyproject.toml", "python", "pip", "pytest"),
    ("requirements.txt", "python", "pip", "pytest"),
    ("setup.py", "python", "pip", "pytest"),
    ("Pipfile", "python", "pipenv", "pytest"),
    ("package.json", "javascript", "npm", "npm test"),
    ("go.mod", "go", "go", "go test ./..."),
    ("Cargo.toml", "rust", "cargo", "cargo test"),
    ("pom.xml", "java", "maven", "mvn -B verify"),
    ("build.gradle", "java", "gradle", "./gradlew test"),
    ("Gemfile", "ruby", "bundler", "bundle exec rake test"),
    ("composer.json", "php", "composer", "composer test"),
]


def _classify_with_heuristic(snapshot: RepoSnapshot) -> RepoClassification:
    names = {Path(p).name for p in snapshot.tree}
    if any(Path(p).name.endswith((".csproj", ".fsproj", ".sln")) for p in snapshot.tree):
        return RepoClassification(
            language="csharp", ecosystem="dotnet", test_command="dotnet test",
            confidence=0.9, method="heuristic-fallback",
            evidence=[p for p in snapshot.tree if p.endswith((".csproj", ".sln"))][:5],
        )
    for signal, language, ecosystem, test_cmd in _HEURISTICS:
        if signal in names:
            return RepoClassification(
                language=language, ecosystem=ecosystem, test_command=test_cmd,
                confidence=0.9, method="heuristic-fallback", evidence=[signal],
            )
    raise ValueError(
        "heuristic could not classify the repository; no recognized manifest "
        f"among top-level files: {sorted(names)[:20]}"
    )
