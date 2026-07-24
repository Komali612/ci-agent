"""Classification Agent: determines a repository's language/ecosystem.

Decision policy (the LLM is advisory, code holds the floor):
1. If an Anthropic API key is configured, ask Claude for a structured
   classification.
2. If the call fails, or confidence is below CONFIDENCE_THRESHOLD, fall back
   to a deterministic manifest heuristic -- the LLM is a dependency that can
   be down, and CI must degrade gracefully.
3. If the heuristic is also ambiguous, fail fast. Never guess silently.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from contracts import BuildTool, ClassificationResult, Language, LLMClassification

from .prompts import SYSTEM_PROMPT, render_facts

CONFIDENCE_THRESHOLD = 0.8
DEFAULT_MODEL = "claude-haiku-4-5"  # classification is the deliberately cheap LLM use case
MAX_TREE_ENTRIES = 300
MAX_MANIFEST_CHARS = 3000
MAX_MANIFESTS = 5
MAVEN_MANIFESTS = ("pom.xml",)
GRADLE_MANIFESTS = ("build.gradle", "build.gradle.kts")
DOTNET_SUFFIXES = (".csproj", ".fsproj", ".sln")
EXCLUDED_DIRS = {".git", ".ci-agent", ".ci-agent-logs", "node_modules", "target", "bin", "obj"}


class ClassificationError(Exception):
    """Raised when the repository cannot be classified safely."""


def classify(repo_root: Path) -> ClassificationResult:
    tree, manifests = gather_facts(repo_root)
    if not tree:
        raise ClassificationError(f"no files found under {repo_root}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            llm = _classify_with_llm(tree, manifests)
            if llm.confidence >= CONFIDENCE_THRESHOLD:
                return ClassificationResult(
                    language=llm.language,
                    build_tool=llm.build_tool,
                    confidence=llm.confidence,
                    method="llm",
                    evidence=llm.evidence,
                )
            print(
                f"[classification-agent] LLM confidence {llm.confidence:.2f} is below "
                f"threshold {CONFIDENCE_THRESHOLD}; falling back to heuristic"
            )
        except Exception as exc:
            print(f"[classification-agent] LLM classification failed ({exc}); falling back to heuristic")
    else:
        print("[classification-agent] ANTHROPIC_API_KEY not set; using heuristic fallback")

    return _classify_with_heuristic(tree)


def gather_facts(repo_root: Path) -> tuple[list[str], dict[str, str]]:
    tree = _file_tree(repo_root)
    manifests: dict[str, str] = {}
    for rel in tree:
        name = Path(rel).name
        if name in MAVEN_MANIFESTS or name in GRADLE_MANIFESTS or name.endswith(DOTNET_SUFFIXES):
            try:
                manifests[rel] = (repo_root / rel).read_text(errors="replace")[:MAX_MANIFEST_CHARS]
            except OSError:
                continue
        if len(manifests) >= MAX_MANIFESTS:
            break
    return tree, manifests


def _file_tree(repo_root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        entries = [line for line in out.splitlines() if line.strip()]
        if entries:
            return entries[:MAX_TREE_ENTRIES]
    except (subprocess.SubprocessError, OSError):
        pass
    entries = []
    for path in sorted(repo_root.rglob("*")):
        rel = path.relative_to(repo_root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.is_file():
            entries.append(str(rel))
    return entries[:MAX_TREE_ENTRIES]


def _classify_with_llm(tree: list[str], manifests: dict[str, str]) -> LLMClassification:
    import anthropic

    client = anthropic.Anthropic().with_options(timeout=60.0, max_retries=1)
    response = client.messages.parse(
        model=os.environ.get("CLASSIFIER_MODEL", DEFAULT_MODEL),
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": render_facts(tree, manifests)}],
        output_format=LLMClassification,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise ClassificationError("LLM returned no parseable classification")
    return parsed


def _classify_with_heuristic(tree: list[str]) -> ClassificationResult:
    maven = [p for p in tree if Path(p).name in MAVEN_MANIFESTS]
    gradle = [p for p in tree if Path(p).name in GRADLE_MANIFESTS]
    dotnet = [p for p in tree if Path(p).name.endswith(DOTNET_SUFFIXES)]

    if maven and not dotnet:
        return ClassificationResult(
            language=Language.JAVA,
            build_tool=BuildTool.MAVEN,
            confidence=1.0,
            method="heuristic-fallback",
            evidence=maven[:5],
        )
    if dotnet and not maven and not gradle:
        return ClassificationResult(
            language=Language.DOTNET,
            build_tool=BuildTool.DOTNET,
            confidence=1.0,
            method="heuristic-fallback",
            evidence=dotnet[:5],
        )
    if gradle and not maven:
        raise ClassificationError(
            f"repository looks like Gradle ({gradle}); only Maven and dotnet playbooks are supported"
        )
    raise ClassificationError(
        f"heuristic could not classify the repository: maven={maven}, gradle={gradle}, dotnet={dotnet}"
    )
