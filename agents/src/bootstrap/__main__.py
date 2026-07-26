"""CLI for the bootstrapping agent.

    python -m bootstrap <repo_url>            # classify, author, open PR
    python -m bootstrap <repo_url> --no-pr    # stop after authoring (dry run)
    python -m bootstrap --serve [--port 8080] # run the HTTP service
"""

from __future__ import annotations

import argparse
import sys

from .core import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap", description="CI-bootstrapping agent")
    parser.add_argument("repo_url", nargs="?", help="GitHub repository URL")
    parser.add_argument("--no-pr", action="store_true", help="author + validate only; do not open a PR")
    parser.add_argument("--serve", action="store_true", help="run the FastAPI service instead")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    if args.serve:
        import uvicorn

        uvicorn.run("bootstrap.service:app", host=args.host, port=args.port)
        return 0

    if not args.repo_url:
        parser.error("repo_url is required unless --serve is given")

    result = bootstrap(args.repo_url, open_pr=not args.no_pr)
    print()
    print(result.model_dump_json(indent=2))

    if result.status == "opened":
        print(f"\n✅ PR #{result.pr_number}: {result.pr_url}")
        return 0
    if result.status == "authored_only":
        print("\nℹ️  workflow authored and validated; no PR opened")
        return 0
    print(f"\n❌ {result.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
