"""Telemetry collector: aggregates per-run telemetry across consumer repos.

Pull-based by design -- GITHUB_TOKEN is repo-scoped, so consumer runs cannot
write here; instead this script (run by ci-agent's scheduled workflow) reads
each consumer repo's workflow runs via the Actions API and downloads the
`ci-agent-telemetry` artifact each run uploaded. Runs whose agent crashed
before emitting telemetry still get a run-level record (conclusion/duration)
so failures are never invisible.

Stdlib only. Output: <site>/data.json consumed by the dashboard.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
ARTIFACT_NAME = "ci-agent-telemetry"
MAX_RECORDS = 1000
RUNS_PER_REPO = 50


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api_get(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def download_artifact(url: str, token: str) -> bytes | None:
    """Artifact downloads 302-redirect to blob storage. The Authorization
    header must NOT be forwarded to the redirect target (the blob URL carries
    its own signature and rejects requests with two auth mechanisms), so the
    redirect is followed manually."""
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 302:
            location = err.headers["Location"]
            with urllib.request.urlopen(location, timeout=60) as resp:
                return resp.read()
        print(f"  warning: artifact download failed ({err.code})")
        return None


def telemetry_from_artifact(zip_bytes: bytes) -> dict | None:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("telemetry.json"):
                    return json.loads(zf.read(name))
    except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"  warning: bad telemetry artifact ({exc})")
    return None


def duration_seconds(run: dict) -> float | None:
    try:
        started = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        return max((updated - started).total_seconds(), 0.0)
    except (KeyError, ValueError):
        return None


def collect_repo(repo: str, token: str, known: set[str]) -> list[dict]:
    records = []
    try:
        runs = api_get(
            f"{API}/repos/{repo}/actions/runs?status=completed&per_page={RUNS_PER_REPO}",
            token,
        )["workflow_runs"]
    except urllib.error.HTTPError as err:
        print(f"warning: could not list runs for {repo} ({err.code}); skipping")
        return records

    for run in runs:
        key = f"{repo}#{run['id']}#{run.get('run_attempt', 1)}"
        if key in known:
            continue
        record = {
            "key": key,
            "repo": repo,
            "run_id": run["id"],
            "run_attempt": run.get("run_attempt", 1),
            "workflow": run.get("name"),
            "event": run.get("event"),
            "branch": run.get("head_branch"),
            "sha": run.get("head_sha"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("run_started_at") or run.get("created_at"),
            "run_duration_seconds": duration_seconds(run),
            "run_url": run.get("html_url"),
            "agent": None,
        }
        try:
            artifacts = api_get(
                f"{API}/repos/{repo}/actions/runs/{run['id']}/artifacts", token
            )["artifacts"]
        except urllib.error.HTTPError:
            artifacts = []
        for artifact in artifacts:
            if artifact["name"] == ARTIFACT_NAME and not artifact.get("expired"):
                zip_bytes = download_artifact(artifact["archive_download_url"], token)
                if zip_bytes:
                    record["agent"] = telemetry_from_artifact(zip_bytes)
                break
        records.append(record)
        print(f"  collected {key} conclusion={record['conclusion']} agent={'yes' if record['agent'] else 'no'}")
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, help="gh-pages checkout directory")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN not set", file=sys.stderr)
        return 1

    repos = json.loads((Path(__file__).parent / "repos.json").read_text())
    data_path = Path(args.site) / "data.json"
    if data_path.is_file():
        data = json.loads(data_path.read_text())
    else:
        data = {"records": []}

    known = {r["key"] for r in data["records"]}
    new_records: list[dict] = []
    for repo in repos:
        print(f"collecting {repo} ...")
        new_records.extend(collect_repo(repo, token, known))

    data["records"] = sorted(
        data["records"] + new_records, key=lambda r: r.get("created_at") or ""
    )[-MAX_RECORDS:]
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["repos"] = repos

    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {data_path}: {len(new_records)} new, {len(data['records'])} total records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
