"""FastAPI wrapper around the bootstrapping flow.

    POST /bootstrap  {"repo_url": "...", "open_pr": true}  -> BootstrapResult

Run with:  uvicorn bootstrap.service:app  (or `python -m bootstrap --serve`)
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .contracts import BootstrapResult
from .core import bootstrap

app = FastAPI(title="CI Bootstrapping Agent", version="0.1.0")


class BootstrapRequest(BaseModel):
    repo_url: str
    open_pr: bool = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/bootstrap", response_model=BootstrapResult)
def bootstrap_endpoint(req: BootstrapRequest) -> BootstrapResult:
    # bootstrap() converts stage failures into a structured result, so the
    # endpoint returns 200 with status="error" rather than raising -- callers
    # get the classification/workflow context even on failure.
    return bootstrap(req.repo_url, open_pr=req.open_pr)
