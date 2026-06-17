#!/usr/bin/env python3
"""Interrogate a GHA deploy-log failure for the landing `doctor` agent.

Thin consumer of trailhead.vcs: bootstraps the shared library, then delegates to
``get_provider().deploy.logs(repo_path, job_id=...)`` — the failure-annotation
fetch that IS the doctor signal. A failing run surfaces its annotations; a clean
or not-found (404 → []) run yields no annotations and does NOT false-alarm.

Slice-2 C-1: a non-404 gh failure (auth / rate-limit / outage) makes
``deploy.logs()`` raise ``DeployError`` — the script surfaces that cause on
stderr and exits 1, so doctor never reads an *uncheckable* deploy as *healthy*.

Usage:
    diagnose_deploy.py <repo-path> --job-id <job-id>

Output JSON (stdout, on success):
    {"failed": <bool>, "annotations": [{path, start_line, message}, ...]}

Exit codes:
    0  the deploy log was readable (failed=true if failure annotations present)
    1  the deploy log could not be read (DeployError) — surface the cause
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider  # noqa: E402
from trailhead.vcs.github import DeployError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("--job-id", required=True, dest="job_id")
    args = ap.parse_args(argv)

    try:
        annotations = get_provider().deploy.logs(args.repo_path, job_id=args.job_id)
    except DeployError as e:
        print(f"diagnose_deploy: deploy log unreadable — {e}", file=sys.stderr)
        return 1

    print(json.dumps({"failed": bool(annotations), "annotations": annotations}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
