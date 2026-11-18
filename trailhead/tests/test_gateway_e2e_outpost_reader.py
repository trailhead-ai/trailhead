"""Outpost's own workflow reader run against the gateway e2e fixture repo.

Opt-in with the rest of the gateway end-to-end tier (``TRAILHEAD_GATEWAY_E2E=1``, see the root
conftest): it needs a real Outpost checkout with its node_modules, which CI's default areas do not
have and may not skip for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


from ._gateway_e2e_support import outpost_checkout
from .test_gateway_e2e_run_support import JOBS, fixture_repo  # noqa: F401  (fixture_repo is a pytest fixture)


def _node_reads(workflow: Path) -> dict:
    checkout = outpost_checkout()
    if not (checkout / "node_modules" / "tsx").is_dir() or shutil.which("node") is None:
        raise AssertionError("needs node and the Outpost checkout's node_modules")
    code = (
        "import { readFileSync } from 'node:fs';"
        f"const w = await import({json.dumps(str(checkout / 'server/runs/workflow.ts'))});"
        "const wf = w.parseWorkflow(readFileSync(process.argv[1], 'utf8'));"
        "const out = {};"
        "for (const id of w.jobIds(wf)) out[id] = w.planJob(wf, id);"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(
        ["node", "--import", "tsx", "--input-type=module", "-e", code, str(workflow)],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout)


def test_outposts_reader_finds_the_jobs_their_labels_and_the_matrix_first_values(fixture_repo):  # noqa: F811
    repo, _, _ = fixture_repo
    plans = _node_reads(repo / ".github/workflows/ci.yml")
    assert set(plans) == JOBS
    assert plans["matrix"]["matrix"]["chosen"] == {"color": "red", "size": "small"}
    assert plans["matrix"]["matrix"]["notRun"] == 3
    assert plans["pass"]["matrix"] is None
    assert plans["unmapped"]["runsOn"] == ["windows-latest"]
    assert all(plans[j]["runsOn"] == ["ubuntu-latest"] for j in JOBS - {"unmapped"})
