"""EPHEMERAL assumption probe — NOT part of the permanent suite.

Resolves the unknown: "can a push rejected because the published history moved
be told apart from an unreachable forge WITHOUT parsing stderr?" via a re-fetch.

Delete this whole file before landing the publish-retry task. See handback
report for the exact cleanup instruction.
"""

import subprocess
from pathlib import Path

import pytest


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q")
    _run(path, "config", "user.email", "probe@example.com")
    _run(path, "config", "user.name", "Probe")


def _rev_parse(path: Path, ref: str) -> str | None:
    result = _run(path, "rev-parse", ref)
    return result.stdout.strip() if result.returncode == 0 else None


def _discriminator(clone: Path) -> tuple[bool, bool]:
    """Return (fetch_succeeded, remote_ref_advanced) — no stderr inspected."""
    before = _rev_parse(clone, "origin/main")
    fetch = _run(clone, "fetch", "origin")
    fetch_ok = fetch.returncode == 0
    after = _rev_parse(clone, "origin/main")
    advanced = fetch_ok and before != after
    return fetch_ok, advanced


@pytest.fixture
def forge(tmp_path):
    bare = tmp_path / "forge.git"
    _run(tmp_path, "init", "-q", "--bare", str(bare))
    return bare


def _clone(tmp_path: Path, name: str, bare: Path) -> Path:
    dest = tmp_path / name
    result = _run(tmp_path, "clone", "-q", str(bare), str(dest))
    assert result.returncode == 0, result.stderr
    _run(dest, "config", "user.email", "probe@example.com")
    _run(dest, "config", "user.name", "Probe")
    return dest


def _commit(repo: Path, filename: str, content: str) -> None:
    (repo / filename).write_text(content)
    _run(repo, "add", filename)
    _run(repo, "commit", "-q", "-m", f"add {filename}")


def test_moved_history_vs_unreachable_vs_hook_rejection(tmp_path):
    # --- Seed the forge with one commit so origin/main exists everywhere. ---
    bare = tmp_path / "forge.git"
    _run(tmp_path, "init", "-q", "--bare", str(bare))

    seed = _clone(tmp_path, "seed", bare)
    _commit(seed, "seed.txt", "seed\n")
    push = _run(seed, "push", "-q", "-u", "origin", "main")
    assert push.returncode == 0, push.stderr

    # =====================================================================
    # CASE 1: history moved underneath a stale clone (non-fast-forward).
    # =====================================================================
    clone_a = _clone(tmp_path, "clone_a", bare)
    clone_b = _clone(tmp_path, "clone_b", bare)

    # clone_b publishes first, moving the forge's history.
    _commit(clone_b, "from_b.txt", "b\n")
    push_b = _run(clone_b, "push", "-q", "origin", "main")
    assert push_b.returncode == 0, push_b.stderr

    # clone_a, unaware, commits a diverging change and tries to push.
    _commit(clone_a, "from_a.txt", "a\n")
    push_a = _run(clone_a, "push", "origin", "main")
    assert push_a.returncode != 0, "expected push to be rejected (non-fast-forward)"

    fetch_ok, advanced = _discriminator(clone_a)
    assert fetch_ok is True, "a forge whose history moved must still answer a fetch"
    assert advanced is True, "the remote-tracking ref must advance when history moved"

    # =====================================================================
    # CASE 2: forge unreachable (bad path) — fetch itself fails.
    # =====================================================================
    clone_c = _clone(tmp_path, "clone_c", bare)
    _run(clone_c, "remote", "set-url", "origin", str(tmp_path / "does_not_exist.git"))

    fetch_ok_unreachable, advanced_unreachable = _discriminator(clone_c)
    assert fetch_ok_unreachable is False, "an unreachable forge must fail the fetch itself"
    assert advanced_unreachable is False

    # CASE 2b: remote exists on disk but is not a valid git transport target
    # (a stand-in for a transport-level failure rather than a missing path).
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    clone_d = _clone(tmp_path, "clone_d", bare)
    _run(clone_d, "remote", "set-url", "origin", str(not_a_repo))
    fetch_ok_bad_transport, advanced_bad_transport = _discriminator(clone_d)
    assert fetch_ok_bad_transport is False
    assert advanced_bad_transport is False

    # =====================================================================
    # CASE 3: rejection that is NOT a moved history — a pre-receive hook
    # rejects every push (stand-in for protected-branch / permission refusal).
    # History on the forge does NOT move; the local branch is simply ahead.
    # =====================================================================
    hook_bare = tmp_path / "hook_forge.git"
    _run(tmp_path, "init", "-q", "--bare", str(hook_bare))
    hook_seed = _clone(tmp_path, "hook_seed", hook_bare)
    _commit(hook_seed, "seed.txt", "seed\n")
    push_seed = _run(hook_seed, "push", "-q", "-u", "origin", "main")
    assert push_seed.returncode == 0, push_seed.stderr

    hook_path = hook_bare / "hooks" / "pre-receive"
    hook_path.write_text("#!/bin/sh\nexit 1\n")
    hook_path.chmod(0o755)

    clone_e = _clone(tmp_path, "clone_e", hook_bare)
    _commit(clone_e, "from_e.txt", "e\n")
    push_e = _run(clone_e, "push", "origin", "main")
    assert push_e.returncode != 0, "expected the hook to reject the push"

    fetch_ok_hook, advanced_hook = _discriminator(clone_e)
    assert fetch_ok_hook is True, "the hook-rejecting forge is reachable and answers fetch"
    assert advanced_hook is False, (
        "history did NOT move on the forge — a naive (fetch-succeeded => retry) "
        "discriminator would misclassify this as 'moved' and retry forever up "
        "to the max; the correct discriminator is (fetch succeeded AND ref "
        "advanced), which correctly reports False here"
    )
