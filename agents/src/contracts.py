"""Data contracts shared between the Classification Agent and the CI Agent.

The handoff between the two agents is a ClassificationResult serialized to
classification.json. The CI Agent consumes only this contract and never
re-inspects the repository to decide language -- that is what makes the
two-agent separation real rather than decorative.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field


class Language(str, enum.Enum):
    JAVA = "java"
    DOTNET = "dotnet"


class BuildTool(str, enum.Enum):
    MAVEN = "maven"
    DOTNET = "dotnet"


class LLMClassification(BaseModel):
    """The structured output the LLM is asked to produce."""

    language: Language
    build_tool: BuildTool
    confidence: float = Field(
        ge=0.0, le=1.0, description="How confident the classification is, from 0 to 1"
    )
    evidence: list[str] = Field(
        description="File names or facts from the repository that support the classification"
    )


class ClassificationResult(BaseModel):
    """The handoff contract written to classification.json."""

    language: Language
    build_tool: BuildTool
    confidence: float
    method: str  # "llm" or "heuristic-fallback"
    evidence: list[str] = []
    # Telemetry metadata (None on the heuristic path): what the LLM call cost.
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None


class PhaseStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


class PhaseResult(BaseModel):
    name: str
    status: PhaseStatus
    exit_code: int | None = None
    duration_seconds: float = 0.0
    log_tail: str = ""
    detail: str = ""


class PipelineResult(BaseModel):
    classification: ClassificationResult
    phases: list[PhaseResult] = []
    image_ref: str | None = None

    @property
    def failed(self) -> bool:
        return any(p.status is PhaseStatus.FAILURE for p in self.phases)
