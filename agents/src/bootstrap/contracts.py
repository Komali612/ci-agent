"""Contracts passed between the bootstrapping service's stages.

Unlike the in-Actions pipeline (which is constrained to Java/.NET), this
service must handle arbitrary repositories, so the language/ecosystem fields
are free-form strings the LLM fills in rather than closed enums.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RepoSnapshot(BaseModel):
    """A compact, LLM-friendly view of the repository under inspection."""

    repo_url: str
    owner: str
    name: str
    default_branch: str
    tree: list[str] = []  # repo-relative file paths (capped)
    manifests: dict[str, str] = {}  # path -> (truncated) contents of key manifest files


class LLMClassification(BaseModel):
    """Structured output the Classification Agent asks the LLM to produce."""

    language: str = Field(description="Primary language, lowercase, e.g. python, javascript, go, java")
    ecosystem: str = Field(description="Package manager / build tool, e.g. pip, poetry, npm, maven, cargo")
    test_command: str = Field(description="The shell command that runs this project's tests, e.g. 'pytest'")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(description="Files/facts that support the classification")


class RepoClassification(BaseModel):
    """Handoff contract: Classification Agent -> CI-Authoring Agent."""

    language: str
    ecosystem: str
    test_command: str
    confidence: float
    method: str  # "llm" | "heuristic-fallback"
    evidence: list[str] = []
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None


class LLMWorkflow(BaseModel):
    """Structured output the CI-Authoring Agent asks the LLM to produce."""

    filename: str = Field(default="ci.yml", description="Workflow file name, e.g. ci.yml")
    workflow_yaml: str = Field(description="The complete GitHub Actions workflow YAML")
    rationale: str = Field(description="One or two sentences on the choices made")


class AuthoredWorkflow(BaseModel):
    """The authored workflow after it has passed (or failed) the validation gate."""

    path: str  # e.g. ".github/workflows/ci.yml"
    content: str
    valid: bool
    validation_notes: list[str] = []
    repaired: bool = False
    rationale: str = ""
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None


class BootstrapResult(BaseModel):
    """The service's final answer, returned by the HTTP endpoint and the CLI."""

    repo_url: str
    status: str  # "opened" | "authored_only" | "error"
    classification: RepoClassification | None = None
    workflow: AuthoredWorkflow | None = None
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    message: str = ""
