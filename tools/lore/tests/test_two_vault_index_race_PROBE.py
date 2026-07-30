"""EPHEMERAL assumption-probe test — NOT part of the permanent suite.

Proves/disproves: two concurrent writers targeting DIFFERENT vaults, which
share ONE global SQLite index (state_dir("lore")/index.sqlite, WAL mode,
default 5s busy_timeout, no retry — see search/index.py:113-208), do not
surface `database is locked` (or any sqlite OperationalError) to the caller
under contention.

Delete this file after the unknown is resolved (see the assumption-prover
report for this run).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLI_PATH = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"

sys.path.insert(0, str(Path(__file__).parent))
from conftest import write_default_config  # noqa: E402

_WORKER = r"""
import sys, time, os
from pathlib import Path

vault = sys.argv[1]
state = sys.argv[2]
config_home = sys.argv[3]
barrier_file = Path(sys.argv[4])
n = int(sys.argv[5])
tag = sys.argv[6]
n_iters = int(sys.argv[7])

os.environ["XDG_STATE_HOME"] = state
os.environ["XDG_CONFIG_HOME"] = config_home
os.environ["LORE_EMAIL"] = "tester@example.com"

cli = {cli!r}
import subprocess

ready = barrier_file.parent / (barrier_file.name + ".ready." + tag)
ready.write_text("1")
deadline = time.time() + 10
while time.time() < deadline:
    markers = list(barrier_file.parent.glob(barrier_file.name + ".ready.*"))
    if len(markers) >= n:
        break
    time.sleep(0.0005)

for i in range(n_iters):
    r = subprocess.run(
        [sys.executable, cli, "record", "create", "--kind", "decision",
         "--title", f"race-{{tag}}-{{i}}"],
        input=f"body {{tag}} {{i}}\n", text=True, capture_output=True,
        env=dict(os.environ),
    )
    if r.returncode != 0:
        sys.stderr.write(f"[{{tag}}#{{i}}] rc={{r.returncode}}\nSTDOUT:{{r.stdout}}\nSTDERR:{{r.stderr}}\n")
        sys.exit(r.returncode)
"""


def test_two_vault_concurrent_index_writes_no_lock_errors(tmp_path):
    """4 workers x 5 writes each, split across 2 vaults sharing 1 state dir/index."""
    state = tmp_path / "state"
    state.mkdir()
    n_workers = 4
    n_iters = 5
    barrier_file = tmp_path / "_barrier"

    code = _WORKER.format(cli=str(CLI_PATH))
    procs = []
    for i in range(n_workers):
        tag = f"w{i}"
        vault_name = "a" if i % 2 == 0 else "b"
        vault = tmp_path / f"vault_{vault_name}"
        vault.mkdir(exist_ok=True)
        config_home = tmp_path / f"cfg_{tag}"
        write_default_config(config_home, vault)
        procs.append(subprocess.Popen(
            [sys.executable, "-c", code, str(vault), str(state),
             str(config_home), str(barrier_file), str(n_workers), tag, str(n_iters)],
            stderr=subprocess.PIPE, text=True,
        ))

    errs = []
    for i, p in enumerate(procs):
        _, err = p.communicate(timeout=90)
        if p.returncode != 0:
            errs.append(f"worker {i} exited {p.returncode}:\n{err}")

    assert not errs, (
        "Cross-vault concurrent writes surfaced errors "
        f"({len(errs)}/{n_workers} workers failed):\n" + "\n".join(errs)
    )

    # Every record landed on disk.
    for vault_name in ("a", "b"):
        vault = tmp_path / f"vault_{vault_name}"
        n_files = len(list((vault / "decision").glob("*.md"))) if (vault / "decision").exists() else 0
        # workers alternate a/b; each vault gets n_workers/2 workers x n_iters records
        expected = (n_workers // 2) * n_iters
        assert n_files == expected, (
            f"vault_{vault_name}: expected {expected} record files, found {n_files}"
        )

    # Index rows for both vaults present in the shared index.
    index_path = state / "lore" / "index.sqlite"
    assert index_path.exists(), "shared index.sqlite was never created"
    import sqlite3
    conn = sqlite3.connect(str(index_path))
    try:
        rows_a = conn.execute("SELECT COUNT(*) FROM records WHERE vault = 'vault_a'").fetchone()[0]
        rows_b = conn.execute("SELECT COUNT(*) FROM records WHERE vault = 'vault_b'").fetchone()[0]
    finally:
        conn.close()
    expected = (n_workers // 2) * n_iters
    assert rows_a == expected, f"index rows for vault_a: {rows_a}, expected {expected}"
    assert rows_b == expected, f"index rows for vault_b: {rows_b}, expected {expected}"
