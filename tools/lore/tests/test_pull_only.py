"""``lore sync --pull-only`` and the freshness-window implicit pull on write paths.

Two behaviors, one seam:

``lore sync --pull-only`` is the NON-DESTRUCTIVE half of sync. It never stages,
never commits, never pushes. It integrates origin's commits only when the working
tree is clean; on a dirty tree it fetches and reports how far behind the vault is,
leaving the tree byte-identical. An integrating pull still refreshes the derived
search index, because pulled records have never been projected on this device.

Every write path (`record create`, `record update`, `session candidate`, `flush`)
runs that same pull IMPLICITLY, throttled to one fetch ATTEMPT per vault per
freshness window. The stamp records the attempt, success or failure, so an
offline session pays one network timeout per window rather than one per write.
The implicit pull is advisory in every direction: its whole output goes to
stderr (``record create``'s stdout stays exactly one ``RECORD_ID`` line), an
offline fetch soft-skips with a staleness notice, and a pull that WOULD conflict
neither integrates nor fails the write — it names ``lore resolve <vault>`` and
lets the write land.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import load_script, write_vault_config

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"

SID = "11111111-2222-4333-8444-555555555555"


# ── harness ────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def _make_vault(path: Path, *, dirty: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "a@e.st"), ("user.name", "DeviceA"),
                     ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    (path / "README.md").write_text("vault\n")
    (path / ".gitignore").write_text("*.lock\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    if dirty:
        (path / "dirt.md").write_text("# uncommitted\n")
    return path


def _make_bare_remote(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _wire_remote(vault: Path, remote: Path) -> None:
    _git(vault, "remote", "add", "origin", str(remote))
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(vault, "push", "-u", "origin", branch)


def _clone_as_second_device(remote: Path, path: Path) -> Path:
    subprocess.run(["git", "clone", str(remote), str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "b@e.st"), ("user.name", "DeviceB"),
                     ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    return path


def _push_from_device_b(other: Path, name: str, text: str) -> None:
    """Land one commit on the remote from the other device."""
    (other / name).write_text(text)
    _git(other, "add", "-A")
    _git(other, "commit", "-m", f"device B: {name}")
    _git(other, "push", "origin")


def _run_cli(args, *, config_home: Path, state_dir: Path, stdin_text=None):
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_STATE_HOME"] = str(state_dir)
    env["HOME"] = str(state_dir / "home")
    env["LORE_EMAIL"] = "tester@example.com"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=env, input=stdin_text,
    )


def _head(vault: Path) -> str:
    return _git(vault, "rev-parse", "HEAD").stdout.strip()


def _worktree_snapshot(vault: Path) -> dict[str, bytes]:
    """Every non-.git file's bytes — the evidence for "byte-identical after"."""
    snap: dict[str, bytes] = {}
    for p in sorted(vault.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        snap[str(p.relative_to(vault))] = p.read_bytes()
    return snap


def _one_vault(tmp_path: Path, *, dirty: bool = False):
    """Return ``(config_home, state_dir, vault, remote, device_b)``."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    vault = _make_vault(tmp_path / "v-default", dirty=dirty)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(vault, remote)
    other = _clone_as_second_device(remote, tmp_path / "device-b")
    write_vault_config(config_home, [("default", "default", vault)])
    return config_home, state_dir, vault, remote, other


def _fetch_stamp(state_dir: Path, vault: Path) -> Path:
    return state_dir / "lore" / "fetch" / vault.name


# ── lore sync --pull-only ──────────────────────────────────────────────────


def test_pull_only_integrates_on_a_clean_tree(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists(), "a clean tree must integrate origin's commits"


def test_pull_only_leaves_a_dirty_tree_byte_identical_and_reports_behind(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path, dirty=True)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    before_head = _head(vault)
    before_tree = _worktree_snapshot(vault)

    r = _run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _head(vault) == before_head, "a dirty tree must not be rebased"
    assert _worktree_snapshot(vault) == before_tree, "the working tree must be untouched"
    assert "1 commit(s) behind" in r.stderr, (
        f"the operator must be told how far behind they are; stderr={r.stderr!r}"
    )


def test_pull_only_never_commits_or_pushes(tmp_path):
    """The non-destructive half: local dirt stays local, local commits stay unpushed."""
    config_home, state_dir, vault, remote, _other = _one_vault(tmp_path)
    (vault / "ours.md").write_text("# device A\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "local only")
    (vault / "uncommitted.md").write_text("# still dirty\n")
    remote_head_before = _git(Path(remote), "rev-parse", "HEAD").stdout.strip()

    r = _run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _git(Path(remote), "rev-parse", "HEAD").stdout.strip() == remote_head_before, (
        "--pull-only must never push"
    )
    assert "uncommitted.md" in _git(vault, "status", "--porcelain").stdout, (
        "--pull-only must never commit"
    )


def test_pull_only_reindexes_what_it_integrated(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    (other / "decision").mkdir()
    (other / "decision" / "from-b.md").write_text("# Chose the quokka renderer\n")
    (other / "decision" / "from-b.json").write_text(json.dumps({
        "title": "Chose the quokka renderer",
        "status": "active",
        "created-at": "2026-07-29",
        "updated-at": "2026-07-29",
    }))
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B decision")
    _git(other, "push", "origin")

    r = _run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    s = _run_cli(["search", "quokka"], config_home=config_home, state_dir=state_dir)
    assert s.returncode == 0, s.stderr
    assert "from-b" in s.stdout, (
        f"an integrating pull must refresh the index; stdout={s.stdout!r}"
    )


# ── implicit pull on the write paths ───────────────────────────────────────


def _record_id_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.splitlines() if ln.strip()]


def test_record_create_pulls_implicitly(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Local call"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists(), "the write path must pull before writing"


def test_record_create_stdout_stays_exactly_one_record_id_line(tmp_path):
    """The pinned stdout contract: implicit-pull chatter is stderr-only."""
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Local call"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr
    lines = _record_id_lines(r.stdout)
    assert len(lines) == 1, f"stdout must be exactly one line; got {r.stdout!r}"
    assert re.fullmatch(r"\S+/\S+", lines[0].strip()), (
        f"the one stdout line must be the RECORD_ID; got {lines[0]!r}"
    )
    assert "Pulled 1 commit(s) from origin." in r.stderr, (
        f"the pull must be reported on stderr; stderr={r.stderr!r}"
    )


def test_second_write_inside_the_freshness_window_does_not_fetch(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "first.md", "# first\n")

    r1 = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "One"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r1.returncode == 0, r1.stderr
    assert (vault / "first.md").exists()

    # A second remote commit lands, then a second write inside the window.
    _push_from_device_b(other, "second.md", "# second\n")
    r2 = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Two"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r2.returncode == 0, r2.stderr
    assert not (vault / "second.md").exists(), (
        "a second write inside the freshness window must not fetch again"
    )


def test_a_failed_fetch_still_stamps_so_the_next_write_skips_the_retry(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    vault = _make_vault(tmp_path / "v-default")
    _git(vault, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    write_vault_config(config_home, [("default", "default", vault)])

    r1 = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "One"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r1.returncode == 0, f"an offline fetch must soft-skip: {r1.stderr}"
    assert "stale" in r1.stderr.lower(), (
        f"the staleness notice must be shown; stderr={r1.stderr!r}"
    )
    assert _fetch_stamp(state_dir, vault).exists(), (
        "a FAILED fetch attempt must still be stamped"
    )

    r2 = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Two"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r2.returncode == 0, r2.stderr
    assert "stale" not in r2.stderr.lower(), (
        f"the staleness notice is once per window, not once per write; stderr={r2.stderr!r}"
    )


def test_a_conflicting_implicit_pull_does_not_integrate_and_does_not_fail_the_write(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "README.md", "edited on device B\n")

    # A committed local edit to the same file — a true both-sides conflict.
    (vault / "README.md").write_text("edited on device A\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device A edit")
    before_head = _head(vault)

    r = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Local call"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert r.returncode == 0, f"a conflicting pull must not fail the write: {r.stderr}"
    assert len(_record_id_lines(r.stdout)) == 1, f"stdout must stay clean: {r.stdout!r}"
    # The remedy names the vault DIRECTORY, exactly as `resolve_state.resolve_remedy`
    # renders it everywhere else — one wording across every surface.
    assert f"run `lore resolve {vault.name}`" in r.stderr, (
        f"the remedy must name `lore resolve <vault>`; stderr={r.stderr!r}"
    )
    assert _head(vault) == before_head, "a conflicting pull must not integrate"
    assert (vault / "README.md").read_text() == "edited on device A\n"
    # The abort is verified, not assumed: the vault is usable afterwards.
    rebase_dir = _git(vault, "rev-parse", "--git-path", "rebase-merge").stdout.strip()
    assert not (vault / rebase_dir).exists(), "the vault must not be left mid-rebase"
    assert "<<<<<<<" not in (vault / "README.md").read_text()


def test_record_update_pulls_implicitly(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    created = _run_cli(
        ["record", "create", "--kind", "decision", "--title", "Local call"],
        config_home=config_home, state_dir=state_dir, stdin_text="body\n",
    )
    assert created.returncode == 0, created.stderr
    record_id = _record_id_lines(created.stdout)[0].strip()

    # Commit what the create wrote: a pull-only integration needs a clean tree,
    # and the create left the new record uncommitted.
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "the created record")
    # Clear the freshness stamp so the update's own pull is not throttled.
    _fetch_stamp(state_dir, vault).unlink(missing_ok=True)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(
        ["record", "update", record_id, "--status", "active"],
        config_home=config_home, state_dir=state_dir,
    )
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists(), "record update must pull before writing"


def test_session_candidate_pulls_implicitly(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(
        ["session", "candidate", "--session-id", SID, "--kind", "decision",
         "--phase", "Build"],
        config_home=config_home, state_dir=state_dir, stdin_text="a finding\n",
    )
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists(), "session candidate must pull before writing"


def test_flush_pulls_implicitly(tmp_path):
    config_home, state_dir, vault, _remote, other = _one_vault(tmp_path)
    seeded = _run_cli(
        ["session", "candidate", "--session-id", SID, "--kind", "decision",
         "--phase", "Build"],
        config_home=config_home, state_dir=state_dir, stdin_text="a finding\n",
    )
    assert seeded.returncode == 0, seeded.stderr
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "the seeded session")

    _fetch_stamp(state_dir, vault).unlink(missing_ok=True)
    _push_from_device_b(other, "theirs.md", "# device B\n")

    r = _run_cli(
        ["flush", "--session-id", SID], config_home=config_home, state_dir=state_dir
    )
    assert r.returncode == 0, r.stderr
    assert (vault / "theirs.md").exists(), "flush must pull before writing"


def test_implicit_pull_never_raises_even_when_the_freshness_check_itself_fails():
    """The advisory contract holds for the WHOLE function, not just the pull.

    ``fetch_stamp_path`` confines its candidate under the stamp root via
    ``layers.assert_within_root`` and raises ``LayerConfinementError`` — not
    ``OSError`` — when it doesn't fit. A vault root whose basename resolves
    outside the stamp root (``"/foo/.."`` -> basename ``".."``) triggers that
    raise from inside the freshness check, before any pull is attempted. A
    write path calling ``implicit_pull`` must never see that exception either.
    """
    sync = load_script("lore.cli.sync")

    sync.implicit_pull("/foo/..")
