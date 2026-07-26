"""Load configuration from a .env file so secrets are set once, not per run.

Looked for in the current directory and the ci-agent repo root. Real
environment variables always win over the file, so you can still override
per-invocation with `ANTHROPIC_API_KEY=... python -m bootstrap ...`.
"""

from __future__ import annotations

import os
from pathlib import Path


def _candidate_paths() -> list[Path]:
    # config.py -> bootstrap -> src -> agents -> <repo root>
    repo_root = Path(__file__).resolve().parents[3]
    return [Path.cwd() / ".env", repo_root / ".env"]


def load_dotenv() -> str | None:
    """Apply the first .env file found. Returns its path, or None if absent."""
    for path in _candidate_paths():
        if path.is_file():
            _apply(path)
            return str(path)
    return None


def _apply(path: Path) -> None:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:  # a real env var takes precedence
            os.environ[key] = val
