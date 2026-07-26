"""Offline unit tests for the bootstrapping agent.

These cover the deterministic machinery -- URL parsing, the validation gate,
fence stripping, filename safety, and the heuristic classifier -- none of which
touch the network or the LLM.
"""

from __future__ import annotations

import pytest

from bootstrap.author import _safe_filename, _strip_fences, _validate
from bootstrap.classify import _classify_with_heuristic
from bootstrap.contracts import RepoSnapshot
from bootstrap.ingest import IngestError, parse_repo_url

VALID_WORKFLOW = """
name: CI
on:
  push:
  pull_request:
permissions:
  contents: read
  packages: write
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: echo build
      - name: Test
        run: pytest
      - name: Sonar
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        if: ${{ env.SONAR_TOKEN != '' }}
        run: echo sonar
      - name: Push image
        if: github.event_name == 'push'
        run: echo push
"""


# --- URL parsing ---------------------------------------------------------

@pytest.mark.parametrize(
    "url,owner,name",
    [
        ("https://github.com/Komali612/python-service", "Komali612", "python-service"),
        ("https://github.com/Komali612/python-service.git", "Komali612", "python-service"),
        ("https://github.com/Komali612/python-service/", "Komali612", "python-service"),
        ("git@github.com:acme/widget.git", "acme", "widget"),
    ],
)
def test_parse_repo_url_ok(url, owner, name):
    assert parse_repo_url(url) == (owner, name)


@pytest.mark.parametrize("url", ["https://gitlab.com/a/b", "not-a-url", "https://github.com/onlyowner"])
def test_parse_repo_url_rejects(url):
    with pytest.raises(IngestError):
        parse_repo_url(url)


# --- validation gate -----------------------------------------------------

def test_validate_accepts_good_workflow():
    assert _validate(VALID_WORKFLOW) == []


def test_validate_handles_on_yaml_footgun():
    # Bare `on:` parses to the boolean True, not "on"; the gate must not flag it.
    wf = "on:\n  push:\njobs:\n  t:\n    runs-on: ubuntu-latest\n"
    assert not any("'on'" in n for n in _validate(wf))


def test_validate_flags_missing_phases():
    # A build+test-only workflow is missing the Sonar and Push phases.
    wf = (
        "on: [push]\njobs:\n  ci:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: Build\n        run: echo build\n"
        "      - name: Test\n        run: echo test\n"
    )
    notes = _validate(wf)
    assert any("sonar" in n and "push" in n for n in notes)


def test_validate_rejects_broken_yaml():
    notes = _validate("name: CI\n  bad: : :")
    assert notes and "not valid YAML" in notes[0]


def test_validate_flags_missing_on():
    notes = _validate("jobs:\n  t:\n    runs-on: ubuntu-latest\n")
    assert any("'on'" in n for n in notes)


def test_validate_flags_missing_jobs():
    notes = _validate("on: [push]\n")
    assert any("jobs" in n for n in notes)


# --- fence stripping & filename safety -----------------------------------

def test_strip_fences_removes_code_block():
    fenced = "```yaml\nname: CI\non: [push]\n```"
    assert _strip_fences(fenced).strip() == "name: CI\non: [push]"


def test_strip_fences_passthrough_when_unfenced():
    assert _strip_fences("name: CI\n").strip() == "name: CI"


@pytest.mark.parametrize(
    "given,expected",
    [("ci.yml", "ci.yml"), ("ci", "ci.yml"), ("build.yaml", "build.yaml"), ("../evil", "evil.yml")],
)
def test_safe_filename(given, expected):
    assert _safe_filename(given) == expected


# --- heuristic classifier ------------------------------------------------

def _snap(*paths: str) -> RepoSnapshot:
    return RepoSnapshot(
        repo_url="https://github.com/x/y", owner="x", name="y",
        default_branch="main", tree=list(paths),
    )


@pytest.mark.parametrize(
    "paths,language,ecosystem",
    [
        (["pyproject.toml", "app/main.py"], "python", "pip"),
        (["requirements.txt"], "python", "pip"),
        (["package.json", "src/index.js"], "javascript", "npm"),
        (["go.mod", "main.go"], "go", "go"),
        (["Cargo.toml"], "rust", "cargo"),
        (["pom.xml"], "java", "maven"),
        (["src/App.csproj", "App.sln"], "csharp", "dotnet"),
    ],
)
def test_heuristic_classifies(paths, language, ecosystem):
    result = _classify_with_heuristic(_snap(*paths))
    assert (result.language, result.ecosystem) == (language, ecosystem)
    assert result.method == "heuristic-fallback"


def test_heuristic_raises_on_unknown():
    with pytest.raises(ValueError):
        _classify_with_heuristic(_snap("README.md", "LICENSE"))
