"""CI-bootstrapping service.

Point it at a repository URL; it clones the repo, classifies the language
(Classification Agent), authors a GitHub Actions workflow for it
(CI-Authoring Agent), opens a pull request adding that workflow, and returns
the PR number.

The two-agent separation mandated by the spec is preserved: classification
and authoring are distinct agents that communicate through the contracts in
this package -- the authoring agent never re-derives the language itself.
"""
