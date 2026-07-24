"""Per-language phase commands. The Classification Agent's verdict selects
which playbook the CI Agent runs -- this is where classification becomes
consequential."""

from __future__ import annotations

from contracts import Language

BUILD: dict[Language, list[list[str]]] = {
    Language.JAVA: [["mvn", "-B", "-DskipTests", "package"]],
    Language.DOTNET: [["dotnet", "build", "--configuration", "Release"]],
}

# Java: `verify` runs the tests and writes the JaCoCo XML coverage report that
# the sonar phase later picks up from the shared workspace.
# .NET: coverlet's collector emits OpenCover-format coverage, the format the
# Sonar .NET scanner understands.
TEST: dict[Language, list[list[str]]] = {
    Language.JAVA: [["mvn", "-B", "verify"]],
    Language.DOTNET: [
        [
            "dotnet", "test",
            "--configuration", "Release",
            "--no-build",
            "--collect", "XPlat Code Coverage;Format=opencover",
            "--results-directory", "TestResults",
        ]
    ],
}
