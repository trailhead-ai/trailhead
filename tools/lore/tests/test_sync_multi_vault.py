"""Whole-install vault coverage: multi-vault ``lore sync``, drift reporting.

The behavior under test exists because record writes route **by scope** while the
old ``lore sync`` resolved the ``default``-scope vault alone. A product-scope vault
could therefore accumulate every record a session created and never be committed,
while ``lore sync`` still printed "Committed / Pushed to origin". Every test here
uses a config with MORE THAN ONE vault, because a single-vault config cannot tell
"covered every vault" apart from "covered the default one".

Covers:

``lore sync``:
  - commits every configured vault, not just ``default``
  - labels each line with the vault it describes
  - ``--vault <name>`` narrows to one and leaves the others untouched
  - an unknown ``--vault`` name is refused (exit 1) and commits nothing
  - a malformed config is refused (exit 1) rather than degrading to one vault
  - one broken vault does not strand the others: they commit, exit code is 1
  - a clean but unpushed vault is still pushed
  - a clean, in-sync vault is not pushed (no needless round-trip)
  - commits landed on the remote by another device are pulled down (rebase)
  - a diverged vault is rebased onto origin and then pushed
  - a rebase conflict aborts cleanly: no mid-rebase state, exit 1, `lore resolve` named
  - a conflicted vault does not strand the others (per-vault isolation holds)
  - an unreachable remote with nothing committed this run stays soft (converged);
    with an unpublished commit it is `holding`, exit non-zero — a host must
    never report hoarded work as `converged`
  - an unborn (`git init` + `remote add`) vault adopts the remote branch
  - an unborn vault with no matching remote branch is reported, not silent
  - a pulled record becomes visible to `lore search` (reindex is not vacuous)
  - `--pull-only` covers every vault without committing or pushing any of them
  - `--pull-only --vault <name>` narrows to one, same as a full sync
  - a vault whose write lock is held by a GENUINE second process ends the sync
    immediately (in-progress, exit 0), never a partial commit; the uncontended
    vaults in the same run still complete their whole commit -> pull -> push

``lore status``:
  - flags never-committed / uncommitted / remote-less vaults, per vault
  - reports a fully-synced vault as synced

``lore flush``:
  - the sync tail: the full commit → pull → push flow over every writable vault
  - a tail conflict exits 0 and names `lore resolve <vault>`; offline with an
    unpublished commit is `holding`, exit non-zero (same rule as full sync)
  - shared vaults are outside the tail (the shared-vault write gate)
  - ``--no-sync`` opts out: session-record commit only, and names what it left
"""

from __future__ import annotations

import importlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from conftest import make_bare_remote, write_vault_config
from test_vault_write_lock import _spawn_holder

sync_mod = importlib.import_module("lore.cli.sync")
resolve_state_mod = importlib.import_module("lore.cli.resolve_state")
resolve_mod = importlib.import_module("lore.cli.resolve")

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


# ── harness ────────────────────────────────────────────────────────────────


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _make_vault(path: Path, *, commit: bool = True, dirty: bool = True) -> Path:
    """Create a git vault at ``path``; optionally give it a commit and dirt.

    ``commit=False`` reproduces the never-committed vault — the state the product
    vault was actually found in (git-init'd, zero commits, records untracked).
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    # Record content lives under a kind directory — a real vault has no loose
    # file at its root besides `.gitignore`, and `lore sync`'s commit scope
    # only ever stages a kind directory, `sites/`, or that one root file.
    (path / "task").mkdir(parents=True, exist_ok=True)
    (path / "task" / "README.md").write_text("vault\n")
    # Mirrors what `config.installer` scaffolds into every real vault. Lore's
    # write locks are `*.lock` sidecars living inside the vault, so a fixture
    # without this would test a vault shape no install ever has.
    (path / ".gitignore").write_text("*.lock\n")
    if commit:
        _git(path, "add", "-A")
        _git(path, "commit", "-m", "init")
    if dirty:
        (path / "task" / "record.md").write_text("# a record\n")
    return path


def _make_bare_remote(path: Path) -> Path:
    return make_bare_remote(path)


def _wire_remote(vault: Path, remote: Path, *, track: bool = True) -> None:
    """Attach ``remote`` as origin.

    ``track=True`` pushes with ``-u`` so the branch has an upstream. ``track=False``
    attaches the remote and stops — the origin-without-upstream state, which a bare
    ``git push origin`` refuses outright (exit 128) rather than treating as a first
    push. Tests must be able to construct it: it is the state a freshly wired vault
    is in, and covering only the tracked case is what let that bug ship.
    """
    _git(vault, "remote", "add", "origin", str(remote))
    if track:
        branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        _git(vault, "push", "-u", "origin", branch)


def _commit_count(vault: Path) -> int:
    r = _git(vault, "rev-list", "--count", "HEAD")
    return int(r.stdout.strip()) if r.returncode == 0 else 0


def run_cli(args, *, config_home: Path, state_dir: Path, cwd=None):
    """Run the lore CLI with XDG fenced to tmp so the real vaults are never touched."""
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_STATE_HOME"] = str(state_dir)
    env["HOME"] = str(state_dir / "home")
    env["LORE_EMAIL"] = "tester@example.com"
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def _three_vaults(tmp_path: Path):
    """Return ``(config_home, state_dir, {name: path})`` for a 3-vault install.

    Mirrors the real shape that exposed the bug: a ``default`` vault plus a
    product-scope and a repo-scope vault, all dirty.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    vaults = {
        "default": _make_vault(tmp_path / "v-default"),
        "trailhead": _make_vault(tmp_path / "v-trailhead"),
        "home-manager": _make_vault(tmp_path / "v-home-manager"),
    }
    write_vault_config(
        config_home,
        [
            ("default", "default", vaults["default"]),
            ("trailhead", "product", vaults["trailhead"]),
            ("home-manager", "repo", vaults["home-manager"]),
        ],
    )
    return config_home, state_dir, vaults


# ── lore sync: covers every vault ──────────────────────────────────────────


def test_sync_commits_every_configured_vault(tmp_path):
    """The core regression: a product/repo vault must not be left uncommitted."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    for name, vault in vaults.items():
        assert _commit_count(vault) == 2, f"{name} did not receive the sync commit"
        assert _git(vault, "status", "--porcelain").stdout.strip() == "", (
            f"{name} still has uncommitted changes"
        )


def test_sync_commits_a_never_committed_vault(tmp_path):
    """A vault with ZERO commits (the state the product vault was found in)."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    virgin = _make_vault(tmp_path / "v-virgin", commit=False)
    write_vault_config(
        config_home, [("default", "default", default), ("trailhead", "product", virgin)]
    )

    assert _commit_count(virgin) == 0

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _commit_count(virgin) == 1


def test_sync_labels_output_with_the_vault_name(tmp_path):
    """Every outcome line names its vault, so a mismatch is visible at the call site."""
    config_home, state_dir, _ = _three_vaults(tmp_path)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    for name in ("default:", "trailhead:", "home-manager:"):
        assert name in r.stdout, f"output does not name {name!r}: {r.stdout!r}"

    # Not just "appears somewhere" — the commit-phase line AND the pull/push-
    # phase line for each vault must each carry the label. A regression that
    # reused one vault's emitter closure across both phases (see
    # ``cmd_sync``'s "fresh emitters for the pull/push phase" comment) prints
    # the second phase's line with a blank, un-labeled prefix instead — this
    # is the case that slipped through when this test only checked "the name
    # appears anywhere in stdout".
    for name in ("default:", "trailhead:", "home-manager:"):
        labeled_lines = [line for line in r.stdout.splitlines() if line.startswith(f"  {name}")]
        assert len(labeled_lines) >= 2, (
            f"expected at least 2 labeled lines for {name!r} (one per phase), "
            f"got {labeled_lines!r} in {r.stdout!r}"
        )


# ── lore sync --vault ──────────────────────────────────────────────────────


def test_sync_vault_filter_narrows_to_one(tmp_path):
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync", "--vault", "trailhead"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    assert _commit_count(vaults["trailhead"]) == 2
    for name in ("default", "home-manager"):
        assert _commit_count(vaults[name]) == 1, f"{name} should have been left alone"


def test_sync_unknown_vault_is_refused_and_commits_nothing(tmp_path):
    """An unknown name must not silently fall back to syncing default."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync", "--vault", "nope"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "unknown vault" in r.stderr.lower()
    # The diagnostic lists what IS configured, so the operator can self-correct.
    assert "trailhead" in r.stderr
    for name, vault in vaults.items():
        assert _commit_count(vault) == 1, f"{name} must not have been committed"


def test_sync_vault_filter_accepts_the_unnormalized_name(tmp_path):
    """A repo-scope vault is configured as ``org/repo`` but stored ``org_repo``."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    repo_vault = _make_vault(tmp_path / "v-repo")
    write_vault_config(
        config_home,
        [("default", "default", default), ("trailhead-ai/trailhead", "repo", repo_vault)],
    )

    r = run_cli(
        ["sync", "--vault", "trailhead-ai/trailhead"],
        config_home=config_home,
        state_dir=state_dir,
    )
    assert r.returncode == 0, r.stderr
    assert _commit_count(repo_vault) == 2
    assert _commit_count(default) == 1


# ── lore sync: partial failure ─────────────────────────────────────────────


def test_sync_broken_vault_does_not_strand_the_others(tmp_path):
    """One unusable vault is skipped; the rest still commit; exit code is 1."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    good = _make_vault(tmp_path / "v-good")
    missing = tmp_path / "v-missing"  # never created
    write_vault_config(
        config_home,
        [
            ("default", "default", default),
            ("broken", "product", missing),
            ("good", "repo", good),
        ],
    )

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1, "a hard per-vault failure must surface in the exit code"
    assert "broken" in r.stderr

    # The healthy vaults committed anyway — including `good`, which is ordered
    # AFTER the broken one, so a failure cannot abort the remaining work.
    assert _commit_count(default) == 2
    assert _commit_count(good) == 2


def test_sync_refuses_a_malformed_config(tmp_path):
    """A broken config must abort, not degrade to syncing the floor vault alone."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text("{not json")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "partial vault set" in r.stderr.lower()


# ── lore sync: push behavior ───────────────────────────────────────────────


def test_sync_pushes_a_clean_but_unpushed_vault(tmp_path):
    """A clean tree with local-only commits must still reach the remote.

    The pre-fix command returned early on a clean tree, so commits made outside
    ``lore sync`` were never pushed and the vault silently stayed local-only.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    # A commit made outside lore sync, leaving a CLEAN tree that is ahead of origin.
    (default / "extra.md").write_text("later\n")
    _git(default, "add", "-A")
    _git(default, "commit", "-m", "out-of-band")
    assert _git(default, "status", "--porcelain").stdout.strip() == ""

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pushed to origin." in r.stdout

    assert _commit_count(Path(remote)) == _commit_count(default)


def test_sync_sets_upstream_on_a_remote_without_a_tracking_branch(tmp_path):
    """A wired-but-never-pushed vault must converge, not fail forever.

    A bare ``git push origin`` refuses with exit 128 when the branch has no
    upstream, AND does not set one — so without ``--set-upstream`` this vault fails
    identically on every future sync while the error text blames the network.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote, track=False)

    assert _git(default, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode != 0

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pushed to origin." in r.stdout, (
        f"push must succeed on a first push; stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert _commit_count(Path(remote)) == _commit_count(default)

    # Upstream is now set, so the condition has actually cleared — the second sync
    # is a silent no-op rather than a repeat of the same doomed push.
    assert _git(default, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0
    r2 = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r2.returncode == 0, r2.stderr
    assert "Pushed to origin." not in r2.stdout


def test_status_flags_a_remote_without_an_upstream_as_never_pushed(tmp_path):
    """The no-upstream state is its own finding, distinct from 'unpushed commits'."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"), track=False)
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "never pushed — no upstream branch set" in r.stdout
    # Sync-fixable, so the remedy must be the one that clears it.
    assert "lore sync --vault default" in r.stdout


def test_sync_soft_network_failure_does_not_strand_other_vaults(tmp_path):
    """An unreachable remote holds ITS OWN vault (offline with a just-committed,
    unpublished change is `holding`, not `converged` — see the module's
    `_pull_and_push_one` docstring) but never strands a later vault: the commit
    lands before the network is touched, and `later` still commits and the run
    still attempts it.

    The fetch is the first network probe, so it is the one that reports; the
    push is then skipped rather than double-reporting the same dead remote.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    broken = _make_vault(tmp_path / "v-broken")
    _git(broken, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    later = _make_vault(tmp_path / "v-later")
    write_vault_config(
        config_home, [("default", "default", broken), ("later", "product", later)]
    )

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode != 0, (
        f"the broken vault is holding an unpublished commit — the run must "
        f"exit non-zero; stderr={r.stderr!r}"
    )
    assert "fetch failed" in r.stderr
    assert _commit_count(broken) == 2, "the commit must land before the network is touched"
    assert _commit_count(later) == 2, "a network failure must not skip later vaults"


def test_sync_message_applies_to_every_vault(tmp_path):
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(
        ["sync", "--message", "custom msg"], config_home=config_home, state_dir=state_dir
    )
    assert r.returncode == 0, r.stderr
    for name, vault in vaults.items():
        subject = _git(vault, "log", "-1", "--pretty=%s").stdout.strip()
        assert subject == "custom msg", f"{name} got {subject!r}"


# ── lore sync: generated commit message (no --message) ─────────────────────


def _one_vault_config(tmp_path: Path, name: str = "v") -> tuple[Path, Path, Path]:
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    vault = tmp_path / name
    vault.mkdir()
    _git(vault, "init")
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(vault, "config", key, val)
    (vault / "task").mkdir()
    # A tracked file in `task/` already, like a real vault's scaffolded
    # README — otherwise `task/` is a wholly untracked directory and `git
    # status --porcelain` collapses a later untracked addition inside it to
    # the bare directory line (`?? task/`) instead of naming the file.
    (vault / "task" / "README.md").write_text("vault\n")
    (vault / "spec").mkdir()
    (vault / "spec" / "existing-spec.md").write_text("old\n")
    (vault / "sites").mkdir()
    (vault / "sites" / ".gitkeep").write_text("")
    (vault / ".gitignore").write_text("*.lock\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "init")
    write_vault_config(config_home, [(name, "default", vault)])
    return config_home, state_dir, vault


def test_sync_no_message_names_host_and_lists_staged_record_ids(tmp_path):
    """A message-less sync over one new task record and one changed spec
    record produces a commit whose subject names the host and whose body
    lists both record ids."""
    config_home, state_dir, vault = _one_vault_config(tmp_path)
    (vault / "task" / "new-task.md").write_text("# a new task\n")
    (vault / "spec" / "existing-spec.md").write_text("changed\n")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    subject = _git(vault, "log", "-1", "--pretty=%s").stdout.strip()
    body = _git(vault, "log", "-1", "--pretty=%b").stdout
    assert socket.gethostname() in subject, subject
    # Record ids, not raw filenames — the `.md` extension must be stripped,
    # so match the id at a line boundary rather than as a loose substring
    # (which "task/new-task.md" would also satisfy).
    assert "- task/new-task\n" in body, body
    assert "- spec/existing-spec\n" in body, body
    assert ".md" not in body, body


def test_sync_no_message_uses_camps_declared_self_name(tmp_path):
    """When camp declares this host's self name, the generated subject uses
    it instead of the OS hostname."""
    config_home, state_dir, vault = _one_vault_config(tmp_path)
    (vault / "task" / "new-task.md").write_text("# a new task\n")
    camp_dir = config_home / "camp"
    camp_dir.mkdir(parents=True)
    (camp_dir / "hosts.toml").write_text('self_name = "camp-quokka"\n')

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    subject = _git(vault, "log", "-1", "--pretty=%s").stdout.strip()
    assert "camp-quokka" in subject, subject
    assert socket.gethostname() not in subject, subject


def test_sync_no_message_names_a_host_even_when_camp_is_unimportable(tmp_path, monkeypatch):
    """Camp unimportable never blocks the sync, and the fallback still names
    a host (the OS hostname) rather than omitting one."""
    monkeypatch.setitem(sys.modules, "camp", None)
    monkeypatch.setitem(sys.modules, "camp.host", None)
    monkeypatch.setitem(sys.modules, "camp.host.config", None)

    tmp_path2 = tmp_path / "in-process"
    tmp_path2.mkdir()
    config_home, state_dir, vault = _one_vault_config(tmp_path2)
    (vault / "task" / "new-task.md").write_text("# a new task\n")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.setenv("HOME", str(state_dir / "home"))
    monkeypatch.setenv("LORE_EMAIL", "tester@example.com")

    class _Args:
        vault = None
        message = None
        pull_only = False
        blocking = False

    rc = sync_mod.cmd_sync(_Args())
    assert rc == 0
    subject = _git(vault, "log", "-1", "--pretty=%s").stdout.strip()
    assert socket.gethostname() in subject, subject


def test_sync_no_message_summarizes_a_large_staged_set_by_kind(tmp_path):
    """A staged set past the summarisation threshold collapses to per-kind
    counts rather than listing every record id."""
    config_home, state_dir, vault = _one_vault_config(tmp_path)
    for i in range(sync_mod._SYNC_MESSAGE_SUMMARY_THRESHOLD + 3):
        (vault / "task" / f"task-{i}.md").write_text(f"# task {i}\n")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    body = _git(vault, "log", "-1", "--pretty=%b").stdout
    assert "task/task-0" not in body, body
    assert "task:" in body, body


def test_sync_no_message_names_a_sites_change_as_a_site_path(tmp_path):
    """A new file inside an ALREADY-TRACKED site directory is named as its
    own site path — a wholly new site directory instead collapses to the
    directory line in `git status --porcelain` itself (real git behavior,
    not this feature's concern), so the fixture tracks the site directory
    first, the way a site that already has one page does."""
    config_home, state_dir, vault = _one_vault_config(tmp_path)
    (vault / "sites" / "my-site").mkdir(parents=True)
    (vault / "sites" / "my-site" / "index.html").write_text("<html></html>\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "seed site")
    (vault / "sites" / "my-site" / "page2.html").write_text("<html>2</html>\n")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    body = _git(vault, "log", "-1", "--pretty=%b").stdout
    assert "sites/my-site/page2.html" in body, body


def test_sync_no_message_strips_control_characters_from_staged_paths(tmp_path):
    """`git status --porcelain` itself always C-escapes a control byte in a
    path (e.g. BEL/ESC become the literal two-character sequences ``\\a`` /
    ``\\033``, wrapped in quotes) — there is no git config that turns this
    off, so a raw control byte can never actually reach
    :func:`sync_mod._staged_items_for_message` by staging a real file with
    one in its name. This calls the message-building path directly with a
    synthetic porcelain line carrying a raw control byte, the input shape
    :func:`sync_mod._sanitize_message_path` exists to defend against even
    though git's own escaping means that defense is never reached from the
    CLI in practice."""
    raw_line = "?? task/weird\x07\x1btask.md"
    items = sync_mod._staged_items_for_message([raw_line])
    joined = "".join(items)
    assert "\x07" not in joined
    assert "\x1b" not in joined

    config_home, state_dir, vault = _one_vault_config(tmp_path)
    weird_name = "task-x\x07\x1b[31m.md"
    (vault / "task" / weird_name).write_text("# weird\n")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    subject = _git(vault, "log", "-1", "--pretty=%s").stdout
    body = _git(vault, "log", "-1", "--pretty=%b").stdout
    for ch in "\x07\x1b":
        assert ch not in subject
        assert ch not in body


def test_sync_no_message_bounds_body_length(tmp_path):
    """A body assembled from many long paths is truncated rather than
    growing without bound, while staying under the summarisation threshold
    so this test isolates the length bound from the count-based collapse."""
    config_home, state_dir, vault = _one_vault_config(tmp_path)
    long_name = "x" * 200
    count = sync_mod._SYNC_MESSAGE_SUMMARY_THRESHOLD - 1
    for i in range(count):
        (vault / "task" / f"{long_name}-{i}.md").write_text("# long\n")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    body = _git(vault, "log", "-1", "--pretty=%b").stdout
    assert len(body) <= sync_mod._SYNC_MESSAGE_BODY_MAX_CHARS + 16, len(body)


def test_sync_skips_push_when_clean_and_in_sync(tmp_path):
    """Nothing to commit and nothing ahead → no push, no round-trip."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"))

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Nothing to commit" in r.stdout
    assert "Pushed to origin." not in r.stdout


# ── lore sync: pull behavior ───────────────────────────────────────────────


def _clone_as_second_device(remote: Path, path: Path) -> Path:
    """Clone ``remote`` to ``path`` — the same vault as seen from another device."""
    subprocess.run(
        ["git", "clone", str(remote), str(path)], check=True, capture_output=True
    )
    for key, val in (("user.email", "b@e.st"), ("user.name", "DeviceB"), ("commit.gpgsign", "false")):
        _git(path, "config", key, val)
    return path


def test_sync_pulls_commits_made_on_another_device(tmp_path):
    """The cross-device case: a record captured elsewhere must land here on sync."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "elsewhere.md").write_text("# captured on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pulled 1 commit(s) from origin." in r.stdout
    assert (default / "elsewhere.md").exists(), "the remote record must land locally"


def test_sync_diverged_vault_rebases_then_pushes(tmp_path):
    """Local dirt + a remote commit: sync must integrate BOTH, not fail the push.

    The pre-pull command soft-failed the push on every run once the devices
    diverged — the exact cross-device pain pull support exists to close.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "theirs.md").write_text("# device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    # In scope so `lore sync`'s own commit step stages and commits it — the
    # local half of the divergence the test is proving gets integrated.
    (default / "task" / "ours.md").write_text("# device A\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pulled 1 commit(s) from origin." in r.stdout
    assert "Pushed to origin." in r.stdout
    assert (default / "theirs.md").exists()
    assert (default / "task" / "ours.md").exists()
    # Both devices' commits are on the remote — nothing left ahead or behind.
    assert _commit_count(Path(remote)) == _commit_count(default)


def test_sync_rebase_conflict_hands_off_to_the_resolver_and_fails_hard(tmp_path):
    """A conflict the resolver itself cannot settle (README.md has no sidecar
    counterpart, so it is not a record the field-wise merge can handle) must
    still abort the rebase and never strand a mid-rebase vault — but the
    remedy printed is no longer `lore resolve`, which is retired for this
    breaking change (see CHANGELOG.md): the vault is handed to the resolver,
    which fails for a policy-uncovered reason and reports `holding`."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    # Both sides edit the SAME already-tracked record file — an in-scope path,
    # so the LOCAL edit is one `lore sync`'s own commit step actually stages,
    # which is what makes this a genuine same-file conflict rather than an
    # uncommitted modification the rebase never sees.
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")

    (default / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1, "an unresolved conflict must surface in the exit code"
    assert "the resolver could not settle" in r.stderr
    assert "lore resolve" not in r.stderr, "the retired remedy is never printed"
    assert "git pull --rebase" not in r.stderr, "conflicts are settled through the CLI"

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "holding"
    assert entry["reason"] == "policy-failure"

    # The vault is NOT left mid-rebase: no rebase state dir, tree is clean, and
    # the local commit survives intact for the manual resolution.
    assert not (default / ".git" / "rebase-merge").exists()
    assert not (default / ".git" / "rebase-apply").exists()
    assert _git(default, "status", "--porcelain").stdout.strip() == ""
    assert (default / "task" / "README.md").read_text() == "edited on device A\n"


def test_sync_unreachable_remote_with_a_just_committed_change_holds(tmp_path):
    """Offline with a commit this run just made: the commit still lands and one
    fetch-failure notice fires, but the run exits NON-ZERO — the vault is
    holding unpublished work on this machine, which is the `holding` outcome,
    never `converged`. This supersedes the prior "offline is soft, exit stays
    0" contract for exactly this shape (see `_pull_and_push_one`'s docstring)."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    # Simulate going offline AFTER the upstream is established.
    _git(default, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    (default / "task" / "record.md").write_text("# a record\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode != 0, (
        f"an offline vault holding an unpublished commit must exit non-zero; "
        f"stderr={r.stderr!r}"
    )
    assert "fetch failed" in r.stderr
    assert _commit_count(default) == 2, "the commit must land before fetch is attempted"
    # One network probe already failed; the push is skipped, not double-reported.
    assert "push failed" not in r.stderr


def test_sync_pull_sets_up_a_no_upstream_branch_against_an_existing_remote_branch(tmp_path):
    """Both devices doing a 'first push' must converge, not reject forever.

    Device B wires origin with no upstream after device A has already pushed:
    a bare push is rejected (non-fast-forward) and ``-u`` never sets the
    upstream on a failed push, so without pulling against ``origin/<branch>``
    the vault would fail identically on every future sync.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    remote = _make_bare_remote(tmp_path / "remote.git")

    # Device A pushes first.
    device_a = _make_vault(tmp_path / "device-a", dirty=False)
    _wire_remote(device_a, remote)

    # Device B has its own independent history and no upstream — but no
    # overlapping paths, so the histories interleave without conflict.
    device_b = tmp_path / "v-default"
    device_b.mkdir()
    subprocess.run(["git", "init", str(device_b)], check=True, capture_output=True)
    for key, val in (("user.email", "b@e.st"), ("user.name", "DeviceB"), ("commit.gpgsign", "false")):
        _git(device_b, "config", key, val)
    branch_a = _git(device_a, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(device_b, "checkout", "-b", branch_a)
    (device_b / "b-record.md").write_text("# device B\n")
    _git(device_b, "add", "-A")
    _git(device_b, "commit", "-m", "device B init")
    _git(device_b, "remote", "add", "origin", str(remote))

    write_vault_config(config_home, [("default", "default", device_b)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pulled" in r.stdout
    assert "Pushed to origin." in r.stdout
    assert (device_b / "task" / "README.md").exists(), "device A's history must be integrated"
    assert _commit_count(Path(remote)) == _commit_count(device_b)


def test_sync_reindexes_after_a_pull_and_only_after_a_pull(tmp_path):
    """Pulled records must become searchable: the derived index is refreshed.

    The pulled fixture is a complete ``.md`` + ``.json`` record pair and the
    proof is a ``lore search`` HIT — a bare "Reindexed" line can be printed by a
    rebuild that indexed nothing (the indexer skips sidecar-less files), so the
    search result is the only assertion that actually covers the contract.
    """
    import json

    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    write_vault_config(config_home, [("default", "default", default)])

    # No pull happened — no reindex line.
    r0 = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r0.returncode == 0, r0.stderr
    assert "Reindexed" not in r0.stdout

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "decision").mkdir()
    (other / "decision" / "from-b.md").write_text("# Chose the quokka renderer\n")
    # The full sidecar shape `lore record create` writes: the index schema
    # requires the dates NOT NULL, and rebuild silently skips a violating record.
    (other / "decision" / "from-b.json").write_text(
        json.dumps(
            {
                "title": "Chose the quokka renderer",
                "status": "active",
                "created-at": "2026-07-29",
                "updated-at": "2026-07-29",
            }
        )
    )
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B decision")
    _git(other, "push", "origin")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pulled 1 commit(s) from origin." in r.stdout
    assert "Reindexed" in r.stdout, "a pull that changed files must refresh the index"

    # The pulled record is now visible to search on THIS device.
    s = run_cli(["search", "quokka"], config_home=config_home, state_dir=state_dir)
    assert s.returncode == 0, s.stderr
    assert "from-b" in s.stdout, (
        f"the pulled record must be searchable; stdout={s.stdout!r}"
    )


def test_pull_only_covers_every_vault_without_committing_any(tmp_path):
    """``--pull-only`` keeps sync's whole-install coverage and drops its write half."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    for name, vault in vaults.items():
        assert _commit_count(vault) == 1, f"{name} must not have been committed"
        assert _git(vault, "status", "--porcelain").stdout.strip(), (
            f"{name}'s uncommitted changes must be left exactly where they were"
        )


def test_pull_only_vault_filter_narrows_to_one(tmp_path):
    """The filter means the same thing under ``--pull-only`` as under a full sync."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)
    remote = _make_bare_remote(tmp_path / "remote.git")
    target = vaults["trailhead"]
    _git(target, "add", "-A")
    _git(target, "commit", "-m", "local")
    _wire_remote(target, remote)
    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "theirs.md").write_text("# device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    r = run_cli(
        ["sync", "--pull-only", "--vault", "trailhead"],
        config_home=config_home, state_dir=state_dir,
    )
    assert r.returncode == 0, r.stderr
    assert (target / "theirs.md").exists(), "the named vault must have been pulled"
    for name in ("default", "home-manager"):
        assert _commit_count(vaults[name]) == 1, f"{name} should have been left alone"


def test_sync_conflicting_vault_does_not_strand_the_others(tmp_path):
    """Per-vault isolation holds for the new failure mode: one conflicted vault
    exits the run 1, but the vaults after it still commit, pull, and push."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    # Vault 1 (ordered FIRST) will hit a rebase conflict.
    conflicted = _make_vault(tmp_path / "v-conflicted", dirty=False)
    remote_a = _make_bare_remote(tmp_path / "remote-a.git")
    _wire_remote(conflicted, remote_a)
    other_a = _clone_as_second_device(remote_a, tmp_path / "device-b-a")
    (other_a / "task" / "README.md").write_text("edited on device B\n")
    _git(other_a, "add", "-A")
    _git(other_a, "commit", "-m", "device B edit")
    _git(other_a, "push", "origin")
    (conflicted / "task" / "README.md").write_text("edited on device A\n")

    # Vault 2 is healthy and behind the remote.
    healthy = _make_vault(tmp_path / "v-healthy", dirty=True)
    remote_b = _make_bare_remote(tmp_path / "remote-b.git")
    _wire_remote(healthy, remote_b)
    other_b = _clone_as_second_device(remote_b, tmp_path / "device-b-b")
    (other_b / "elsewhere.md").write_text("# from device B\n")
    _git(other_b, "add", "-A")
    _git(other_b, "commit", "-m", "device B record")
    _git(other_b, "push", "origin")

    write_vault_config(
        config_home,
        [("default", "default", conflicted), ("healthy", "product", healthy)],
    )
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1, "the conflicted vault must surface in the exit code"
    assert "default" in r.stderr

    # The healthy vault still did its full commit → pull → push cycle.
    assert (healthy / "elsewhere.md").exists(), "the healthy vault must still pull"
    assert _git(healthy, "status", "--porcelain").stdout.strip() == ""
    assert _commit_count(Path(remote_b)) == _commit_count(healthy), (
        "the healthy vault must still push"
    )


def test_sync_unborn_vault_adopts_the_remote_branch(tmp_path):
    """A fresh `git init` + `remote add` vault must pull, not silently no-op.

    With no commits there is nothing to rebase, so without explicit adoption the
    run would print "Nothing to commit — vault is clean." and exit 0 having
    synced nothing — on exactly the new-device shape cross-device sync is for.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    remote = _make_bare_remote(tmp_path / "remote.git")
    device_a = _make_vault(tmp_path / "device-a", dirty=False)
    _wire_remote(device_a, remote)
    branch = _git(device_a, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    fresh = tmp_path / "v-default"
    fresh.mkdir()
    subprocess.run(["git", "init", str(fresh)], check=True, capture_output=True)
    for key, val in (("user.email", "b@e.st"), ("user.name", "DeviceB"), ("commit.gpgsign", "false")):
        _git(fresh, "config", key, val)
    _git(fresh, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _git(fresh, "remote", "add", "origin", str(remote))

    write_vault_config(config_home, [("default", "default", fresh)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Pulled 1 commit(s) from origin." in r.stdout
    assert (fresh / "task" / "README.md").exists(), "the remote history must be adopted"

    # Converged: upstream is set, and a second sync is a quiet no-op.
    r2 = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r2.returncode == 0, r2.stderr
    assert "Pulled" not in r2.stdout


def test_sync_unborn_vault_with_no_matching_remote_branch_reports_it(tmp_path):
    """When the remote has no branch to adopt, say so — a silent 0 hides a dead vault."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    remote = _make_bare_remote(tmp_path / "remote.git")
    device_a = _make_vault(tmp_path / "device-a", dirty=False)
    _wire_remote(device_a, remote)

    fresh = tmp_path / "v-default"
    fresh.mkdir()
    subprocess.run(["git", "init", str(fresh)], check=True, capture_output=True)
    for key, val in (("user.email", "b@e.st"), ("user.name", "DeviceB"), ("commit.gpgsign", "false")):
        _git(fresh, "config", key, val)
    _git(fresh, "symbolic-ref", "HEAD", "refs/heads/some-other-branch")
    _git(fresh, "remote", "add", "origin", str(remote))

    write_vault_config(config_home, [("default", "default", fresh)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "no commits" in r.stderr
    assert "Pulled" not in r.stdout


# ── lore status: vault drift ───────────────────────────────────────────────


def test_status_flags_a_never_committed_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")
    virgin = _make_vault(tmp_path / "v-virgin", commit=False)
    write_vault_config(
        config_home, [("default", "default", default), ("trailhead", "product", virgin)]
    )

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "vault trailhead:" in r.stdout
    assert "never committed" in r.stdout
    # And the remedy names the specific vault, not a bare "run lore sync".
    assert "lore sync --vault trailhead" in r.stdout


def test_status_flags_uncommitted_and_remoteless_vaults(tmp_path):
    config_home, state_dir, _ = _three_vaults(tmp_path)

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "uncommitted change(s)" in r.stdout
    assert "no origin remote" in r.stdout
    for name in ("default", "trailhead", "home-manager"):
        assert f"vault {name}:" in r.stdout


def test_status_reports_a_fully_synced_vault_as_synced(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"))
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "vault default: synced" in r.stdout


def test_status_survives_a_malformed_config(tmp_path):
    """The ruleset section must still report; the vault section degrades to stderr."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text("{not json")

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "unreadable" in r.stderr.lower()


# ── lore flush --no-sync: names what its own commit does not cover ─────────
#
# Only the opted-out form names unsynced vaults; the default form SYNCS them
# (see the sync-tail section below), which is why these two drive `--no-sync`.


def test_flush_no_sync_names_vaults_still_holding_unsynced_work(tmp_path):
    """`lore flush --no-sync` commits the session record only — it must say what
    it left, per vault, since no tail is going to cover it."""
    config_home, state_dir, vaults = _three_vaults(tmp_path)

    r = run_cli(["flush", "--no-sync"], config_home=config_home, state_dir=state_dir)
    # No session exists here; the notice is about VAULT state, so it fires anyway —
    # a clean/absent session says nothing about whether the vaults are committed.
    assert "run `lore sync`" in r.stdout
    assert "trailhead:" in r.stdout
    for name, vault in vaults.items():
        assert _commit_count(vault) == 1, f"{name} must not have been committed"


def test_flush_no_sync_is_silent_when_nothing_is_sync_fixable(tmp_path):
    """A remote-less but fully committed vault must NOT trigger the flush notice.

    "No origin remote" is a legitimate deliberate configuration that `lore sync`
    cannot fix. Attaching "run `lore sync`" to it would fire on every flush forever
    with a no-op remedy — the cry-wolf failure this reporting exists to prevent.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    # Committed and clean, but no remote at all.
    remoteless = _make_vault(tmp_path / "v-remoteless", dirty=False)
    write_vault_config(config_home, [("default", "default", remoteless)])

    r = run_cli(["flush", "--no-sync"], config_home=config_home, state_dir=state_dir)
    assert "run `lore sync`" not in r.stdout, (
        f"flush must stay silent when sync cannot help; stdout={r.stdout!r}"
    )

    # ...but `lore status` still reports it standing, with the remedy that applies.
    s = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert "no origin remote" in s.stdout
    assert "add an origin remote" in s.stdout


def test_status_remedy_for_a_missing_vault_directory_is_not_lore_sync(tmp_path):
    """`lore sync` cannot create a vault, so it must not be the offered remedy."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    _wire_remote(default, _make_bare_remote(tmp_path / "remote.git"))
    write_vault_config(
        config_home,
        [("default", "default", default), ("ghost", "product", tmp_path / "nope")],
    )

    r = run_cli(["status"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    ghost_line = next(ln for ln in r.stdout.splitlines() if "vault ghost:" in ln)
    assert "directory does not exist" in ghost_line
    assert "lore sync" not in ghost_line, f"unactionable remedy offered: {ghost_line!r}"
    assert "config.json" in ghost_line


# ── lore flush: the sync tail ──────────────────────────────────────────────
#
# `lore flush` ends by running the full sync flow (commit → pull → push) over
# every WRITABLE vault, superseding — for that tail only — flush's own
# explicit-paths commit scope. `--no-sync` opts out and preserves the
# commit-the-session-paths-and-name-the-rest behavior exactly. Shared vaults
# stay outside the tail: the shared-vault write gate is what keeps untrusted
# multi-user content from actuating a commit + push under this operator's git
# identity, and a sync tail is a write.


def _mark_shared(config_home: Path, name: str) -> None:
    """Flip an already-written config entry to ``shared: true``."""
    import json

    path = config_home / "lore" / "config.json"
    cfg = json.loads(path.read_text())
    for entry in cfg["vaults"]:
        if entry["name"] == name:
            entry["shared"] = True
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def test_flush_sync_tail_commits_and_pushes_unrelated_dirty_files(tmp_path):
    """The tail is a full sync: dirty vault files the flush did not stage land too."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    (default / "task" / "stray.md").write_text("# not staged by the flush\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _git(default, "status", "--porcelain").stdout.strip() == "", (
        f"the sync tail must leave the vault committed; stdout={r.stdout!r}"
    )
    assert "stray.md" in _git(
        default, "show", "--name-only", "--pretty=format:", "HEAD"
    ).stdout
    assert _commit_count(Path(remote)) == _commit_count(default), (
        "the sync tail must push what it committed"
    )


def test_flush_no_sync_preserves_the_explicit_paths_behavior(tmp_path):
    """`--no-sync` opts out: nothing but the session record is committed, and the
    vaults still holding uncommitted work are NAMED with the `lore sync` remedy."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    (default / "task" / "stray.md").write_text("# not staged by the flush\n")
    write_vault_config(config_home, [("default", "default", default)])
    before = _commit_count(default)

    r = run_cli(["flush", "--no-sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _commit_count(default) == before, "--no-sync must not commit the vault"
    assert "stray.md" in _git(default, "status", "--porcelain").stdout
    assert "run `lore sync`" in r.stdout, (
        f"--no-sync must still name what it left behind; stdout={r.stdout!r}"
    )


def test_flush_sync_tail_conflict_exits_zero_and_names_lore_resolve(tmp_path):
    """A conflict in the tail never fails the flush — it reports the resolve remedy."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    # In scope so the sync tail's own commit step stages the local edit — the
    # genuine same-file conflict this test depends on.
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")

    (default / "task" / "README.md").write_text("edited on device A\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, (
        f"the flush itself succeeded — a tail conflict must not fail it; "
        f"stderr={r.stderr!r}"
    )
    assert "lore resolve v-default" in r.stderr, (
        f"the conflict must name its remedy; stderr={r.stderr!r}"
    )
    # The vault is left consistent: the abort is verified, not assumed.
    assert not (default / ".git" / "rebase-merge").exists()
    assert not (default / ".git" / "rebase-apply").exists()
    assert (default / "task" / "README.md").read_text() == "edited on device A\n"


def test_flush_sync_tail_offline_is_soft(tmp_path):
    """Offline: `lore flush`'s own exit code is always dropped for the sync tail
    (see `cmd_flush`'s docstring — "neither ending changes the exit code"), so
    it stays 0 and the work is committed locally either way. But the tail's
    per-vault `cmd_sync` call now reports `holding` for exactly this shape (an
    unpublished commit behind an unreachable remote — see
    `_pull_and_push_one`), so `_sync_tail_notice` now fires where it
    previously did not: the flush names the vault and a remedy, not just the
    raw "fetch failed" line."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    _git(default, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (default / "task" / "stray.md").write_text("# not staged by the flush\n")
    write_vault_config(config_home, [("default", "default", default)])
    before = _commit_count(default)

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, f"the flush's own exit code is never the tail's; stderr={r.stderr!r}"
    assert _commit_count(default) == before + 1, "the tail's commit must still land"
    assert "fetch failed" in r.stderr
    assert "did not complete" in r.stderr and "'default'" in r.stderr, (
        f"the tail must now name the held vault, since it no longer reports "
        f"success for a vault holding an unpublished commit; stderr={r.stderr!r}"
    )


def test_flush_sync_tail_never_touches_a_shared_vault(tmp_path):
    """The shared-vault write gate holds for the tail: untrusted content is not
    committed or pushed under this operator's identity."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    shared = _make_vault(tmp_path / "v-shared", dirty=False)
    (default / "task" / "stray.md").write_text("# mine\n")
    (shared / "planted.md").write_text("# someone else's\n")
    write_vault_config(
        config_home,
        [("default", "default", default), ("teamvault", "product", shared)],
    )
    _mark_shared(config_home, "teamvault")
    shared_before = _commit_count(shared)

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _git(default, "status", "--porcelain").stdout.strip() == "", (
        "the writable vault must still be synced"
    )
    assert _commit_count(shared) == shared_before, "the shared vault must not be committed"
    assert "planted.md" in _git(shared, "status", "--porcelain").stdout


def test_flush_default_names_unsynced_work_in_a_shared_vault(tmp_path):
    """The tail structurally never touches a `shared: true` vault — so a default
    flush must still say one is holding unsynced work, scoped to shared vaults
    only (the notice `--no-sync` prints over its own, unpartitioned, set)."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    shared = _make_vault(tmp_path / "v-shared", dirty=False)
    (shared / "planted.md").write_text("# someone else's\n")
    write_vault_config(
        config_home,
        [("default", "default", default), ("teamvault", "product", shared)],
    )
    _mark_shared(config_home, "teamvault")

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "teamvault" in r.stderr, (
        f"a default flush must name the shared vault holding unsynced work; "
        f"stderr={r.stderr!r}"
    )
    assert "run `lore sync`" in r.stderr


def _strand_mid_rebase(vault: Path, tmp_path: Path) -> Path:
    """Leave ``vault`` genuinely stopped mid-rebase on a README conflict."""
    remote = _make_bare_remote(tmp_path / f"{vault.name}-remote.git")
    _wire_remote(vault, remote)

    other = _clone_as_second_device(remote, tmp_path / f"{vault.name}-device-b")
    (other / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")

    (vault / "README.md").write_text("edited on device A\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device A edit")
    _git(vault, "fetch", "origin")
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    rc = _git(vault, "rebase", f"origin/{branch}")
    assert rc.returncode != 0, "the fixture must actually conflict"
    assert (vault / ".git" / "rebase-merge").exists(), "the vault must be stranded mid-rebase"
    return vault


# ── lore sync: pre-flight refusal of an already-stranded vault ─────────────


def _snapshot(vault: Path) -> tuple[str, str]:
    return (
        _git(vault, "status", "--porcelain").stdout,
        _git(vault, "rev-parse", "HEAD").stdout,
    )


def _touch_stale_lock(vault: Path) -> Path:
    """Leave a stale ``index.lock`` behind, the state a SIGKILLed git leaves."""
    git_path = _git(vault, "rev-parse", "--git-path", "index.lock").stdout.strip()
    lock = vault / git_path
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    return lock


def _strand_mid_merge(vault: Path, tmp_path: Path) -> Path:
    """Leave ``vault`` stopped on a genuine merge conflict, ``MERGE_HEAD`` intact."""
    remote = _make_bare_remote(tmp_path / f"{vault.name}-remote.git")
    _wire_remote(vault, remote)
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    _git(vault, "checkout", "-b", "feature")
    (vault / "README.md").write_text("feature edit\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "feature edit")

    _git(vault, "checkout", branch)
    (vault / "README.md").write_text("main edit\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "main edit")

    rc = _git(vault, "merge", "feature", "--no-edit")
    assert rc.returncode != 0, "the fixture must actually conflict"
    assert (vault / ".git" / "MERGE_HEAD").exists(), "the vault must be stranded mid-merge"
    return vault


def _detach_head(vault: Path) -> None:
    """Check out ``vault``'s current commit directly, detaching HEAD."""
    head = _git(vault, "rev-parse", "HEAD").stdout.strip()
    rc = _git(vault, "checkout", head)
    assert rc.returncode == 0, rc.stderr
    rc_sym = _git(vault, "symbolic-ref", "-q", "HEAD")
    assert rc_sym.returncode != 0, "the fixture must actually detach HEAD"


def test_sync_refuses_a_stale_index_lock(tmp_path):
    """A leftover ``index.lock`` (a SIGKILLed git's own mess) must be refused by
    name, not silently attempted and left half-broken."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    good = _make_vault(tmp_path / "v-good")
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(
        config_home,
        [("good", "default", good), ("stuck", "product", stuck)],
    )
    _touch_stale_lock(stuck)
    before = _snapshot(stuck)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "stuck" in r.stderr
    assert "stale" in r.stderr.lower() and "lock" in r.stderr.lower()

    after = _snapshot(stuck)
    assert after == before, "a refused vault must be byte-identical after"
    assert _commit_count(good) == 2, "an unrelated clean vault must still complete its loop"


def test_sync_refuses_a_mid_rebase_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    good = _make_vault(tmp_path / "v-good")
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(
        config_home,
        [("good", "default", good), ("stuck", "product", stuck)],
    )
    _strand_mid_rebase(stuck, tmp_path)
    before = _snapshot(stuck)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "stuck" in r.stderr
    assert "mid-rebase" in r.stderr

    after = _snapshot(stuck)
    assert after == before, "a refused vault must be byte-identical after"
    assert _commit_count(good) == 2, "an unrelated clean vault must still complete its loop"


def test_sync_refuses_a_mid_merge_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    good = _make_vault(tmp_path / "v-good")
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(
        config_home,
        [("good", "default", good), ("stuck", "product", stuck)],
    )
    _strand_mid_merge(stuck, tmp_path)
    before = _snapshot(stuck)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "stuck" in r.stderr
    assert "mid-merge" in r.stderr

    after = _snapshot(stuck)
    assert after == before, "a refused vault must be byte-identical after"
    assert _commit_count(good) == 2, "an unrelated clean vault must still complete its loop"


def test_sync_refuses_a_detached_head_vault(tmp_path):
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    good = _make_vault(tmp_path / "v-good")
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(
        config_home,
        [("good", "default", good), ("stuck", "product", stuck)],
    )
    _detach_head(stuck)
    before = _snapshot(stuck)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1
    assert "stuck" in r.stderr
    assert "detached" in r.stderr.lower()

    after = _snapshot(stuck)
    assert after == before, "a refused vault must be byte-identical after"
    assert _commit_count(good) == 2, "an unrelated clean vault must still complete its loop"


def test_sync_refusal_message_names_the_found_condition(tmp_path):
    """The message must vary with the condition actually found — a mid-merge
    vault and a detached-head vault must produce DIFFERENT messages — and must
    never leak raw git or remote output (a URL, `fatal:`, `CONFLICT`, ...).

    The two vaults are deliberately named ``alpha``/``bravo`` rather than
    ``merging``/``detached`` — naming a vault after the condition it is
    stranded in would let the vault's own output label satisfy a substring
    check for free, without the message itself ever varying.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    alpha = _make_vault(tmp_path / "v-alpha", dirty=False)  # stranded mid-merge
    bravo = _make_vault(tmp_path / "v-bravo", dirty=False)  # stranded detached
    write_vault_config(
        config_home,
        [("alpha", "default", alpha), ("bravo", "product", bravo)],
    )
    remote_url = str((tmp_path / "v-alpha-remote.git").resolve())
    _strand_mid_merge(alpha, tmp_path)
    _detach_head(bravo)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1

    alpha_line = next(line for line in r.stderr.splitlines() if "alpha" in line)
    bravo_line = next(line for line in r.stderr.splitlines() if "bravo" in line)
    assert alpha_line != bravo_line, (
        "the message must name the found condition, not a fixed string"
    )
    assert "mid-merge" in alpha_line and "mid-merge" not in bravo_line
    assert "detached" in bravo_line.lower() and "detached" not in alpha_line.lower()

    for line in (alpha_line, bravo_line):
        assert remote_url not in line, "no remote output may appear in the message"
        for git_word in ("fatal:", "CONFLICT", "Auto-merging", "Automatic merge"):
            assert git_word not in line, f"no raw git output ({git_word!r}) may appear"


def test_flush_sync_tail_skips_a_mid_resolution_vault_without_aborting_it(tmp_path):
    """A vault mid-resolution must be skipped, not synced — syncing it would abort
    the very rebase `lore resolve` is in the middle of settling, throwing away
    in-progress judgment work. The remedy is still named, and the vault holding
    the flushed session is unaffected."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(
        config_home,
        [("default", "default", default), ("stuck", "product", stuck)],
    )
    _strand_mid_rebase(stuck, tmp_path)
    stuck_commits_before = _commit_count(stuck)

    r = run_cli(["flush"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert _git(default, "status", "--porcelain").stdout.strip() == "", (
        "the unaffected vault must still be synced"
    )
    # No sync attempt of any kind must have touched the stuck vault — not even
    # a "successful" commit of the still-conflicted tree, which `cmd_sync`
    # would otherwise make since it never checks resolution state itself.
    assert _commit_count(stuck) == stuck_commits_before, (
        "a mid-resolution vault must not be synced at all"
    )
    # The rebase must survive exactly as the fixture left it — `lore sync` would
    # have aborted it.
    assert (stuck / ".git" / "rebase-merge").exists(), (
        "syncing a mid-resolution vault must not abort its rebase"
    )
    conflicted = stuck.joinpath("README.md").read_text()
    assert "<<<<<<<" in conflicted and "edited on device A" in conflicted, (
        "the conflict markers `lore resolve` is working through must survive"
    )
    assert "stuck" in r.stderr and "mid-resolution" in r.stderr
    # The remedy names the vault's DIRECTORY, per `resolve_state.resolve_remedy`.
    assert "lore resolve v-stuck" in r.stderr


# ── lore sync: TRACKED content is always in scope, regardless of allow-list ─
#
# The commit-scope allow-list (record kinds, `sites/`, root `.gitignore`) was
# only ever meant to bound NEW, untracked content. A vault adopted via `lore
# vault add --path <existing repo>` can carry tracked content outside it (a
# root README, say), and a kind directory can be removed wholesale (its
# pathspec then absent from the allow-list's own existence filter) — both must
# still be committed, never silently read as "nothing to commit".


def test_sync_commits_a_tracked_root_file_outside_the_commit_scope_allowlist(tmp_path):
    """An adopted repo's tracked root README, outside every allow-listed kind
    directory / `sites/` / `.gitignore`: editing it must still be committed.
    Before the fix, the status probe was scoped to the same allow-list used for
    staging, so this edit was invisible and the vault read as clean forever."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / "README.md").write_text("adopted repo readme\n")
    _git(default, "add", "-A")
    _git(default, "commit", "-m", "adopt: pre-existing root README")
    (default / "README.md").write_text("adopted repo readme, edited\n")
    write_vault_config(config_home, [("default", "default", default)])
    before = _commit_count(default)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Nothing to commit" not in r.stdout, (
        f"a tracked root file outside the allow-list must not read as clean; "
        f"stdout={r.stdout!r}"
    )
    assert _commit_count(default) > before, "the README edit must be committed"
    name_status = _git(
        default, "show", "--name-status", "--pretty=format:", "HEAD"
    ).stdout.strip()
    assert name_status == "M\tREADME.md", name_status
    assert _git(default, "status", "--porcelain").stdout.strip() == "", (
        "the vault must end clean once the tracked edit is committed"
    )


def test_sync_commits_a_wholesale_removed_kind_directory_as_a_deletion(tmp_path):
    """Removing an ENTIRE kind directory (not one file inside it) makes the
    allow-list's own existence filter drop that kind's pathspec entirely — the
    directory no longer exists to name. The deletion is a change to already-
    tracked content, so it must still be staged and committed unscoped."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    write_vault_config(config_home, [("default", "default", default)])
    assert (default / "task").is_dir()

    shutil.rmtree(default / "task")

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    name_status = _git(
        default, "show", "--name-status", "--pretty=format:", "HEAD"
    ).stdout.strip()
    assert name_status == "D\ttask/README.md", name_status
    assert _git(default, "status", "--porcelain").stdout.strip() == "", (
        "the vault must end clean once the wholesale removal is committed"
    )


def test_sync_reports_untracked_content_outside_the_allowlist_in_a_notice(tmp_path):
    """Untracked content outside the allow-list (not `outpost/`, which has its
    own dedicated carve-out) is never staged — but it must be named in a
    notice, not silently swallowed, and it must not block the rest of the
    vault's in-scope content from committing."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=True)  # task/record.md: in scope
    (default / "junk.txt").write_text("scratch notes, never meant to be committed\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "junk.txt" in r.stderr, f"the stray file must be named in a notice; stderr={r.stderr!r}"
    assert "task/record.md" in _git(default, "ls-files").stdout.split(), (
        "in-scope content must still be committed alongside the notice"
    )
    assert "junk.txt" in _git(default, "status", "--porcelain").stdout, (
        "the stray file must remain uncommitted"
    )


# ── lore sync: commit scope is bounded to records and sites ────────────────
#
# `outpost/` is a vault's free-write daemon-config zone (like `sites/`), but
# unlike `sites/` it is an operator's local working set, never content to
# publish — so `lore sync`'s own commit step must never stage it, regardless
# of what a vault's `.gitignore` does or doesn't cover.


def test_sync_integrates_a_behind_vault_carrying_an_untracked_outpost_dir(tmp_path):
    """A vault one commit behind origin, carrying an untracked `outpost/`
    directory: it must still be read as clean and must still integrate. Before
    the fix, `_vault_is_dirty` counted `outpost/` as dirt, so ANY vault
    carrying it — every daemon-managed vault — never integrated, via
    `--pull-only` or the implicit pull that runs on every write."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "theirs.md").write_text("# device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    (default / "outpost").mkdir()
    (default / "outpost" / "config.json").write_text('{"tracked": false}\n')
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync", "--pull-only"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert (default / "theirs.md").exists(), (
        "a vault carrying an untracked outpost/ must still integrate a clean pull"
    )
    assert (default / "outpost" / "config.json").exists(), (
        "outpost/ itself must be left exactly where it was"
    )


def test_sync_outpost_edit_is_never_committed_and_stays_unstaged(tmp_path):
    """A vault with an uncommitted `outpost/` edit AND an uncommitted record
    edit: the record is committed; `outpost/` is left uncommitted and unstaged."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")  # dirty=True: task/record.md uncommitted
    (default / "outpost").mkdir()
    (default / "outpost" / "config.json").write_text('{"tracked": false}\n')
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    tracked = _git(default, "ls-files").stdout.split()
    assert "task/record.md" in tracked, "the record edit must still be committed"

    # `--untracked-files=all` so an untracked file inside an untracked
    # directory is reported by its own path, not collapsed to `outpost/`.
    status = _git(default, "status", "--porcelain", "--untracked-files=all").stdout
    outpost_line = next(line for line in status.splitlines() if "outpost/config.json" in line)
    assert outpost_line.startswith("??"), (
        f"outpost/ must be left uncommitted AND unstaged: {outpost_line!r}"
    )


def test_sync_outpost_only_change_commits_nothing_and_ends_clean(tmp_path):
    """A vault whose ONLY uncommitted change is under `outpost/`: nothing is
    committed, and the loop still completes and ends clean (exit 0), never
    tripping over an empty staged index."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / "outpost").mkdir()
    (default / "outpost" / "config.json").write_text('{"tracked": false}\n')
    write_vault_config(config_home, [("default", "default", default)])
    before = _commit_count(default)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Nothing to commit" in r.stdout
    assert _commit_count(default) == before, "an outpost-only change must not be committed"
    assert "config.json" in _git(
        default, "status", "--porcelain", "--untracked-files=all"
    ).stdout, (
        "the outpost/ file must remain exactly where it was"
    )


def test_sync_commits_a_root_gitignore_edit_and_ends_clean(tmp_path):
    """A vault with an uncommitted edit to its root `.gitignore` and no other
    change: the sync commits the edit and the vault ends clean. `.gitignore`
    is a scaffolded root file within the commit scope — deliberately, so a
    scaffold change still propagates to peers — unlike `outpost/`, which never
    is."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / ".gitignore").write_text("*.lock\n*.tmp\n")
    write_vault_config(config_home, [("default", "default", default)])
    before = _commit_count(default)

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    assert _commit_count(default) > before, "the .gitignore edit must be committed"
    name_status = _git(
        default, "show", "--name-status", "--pretty=format:", "HEAD"
    ).stdout.strip()
    assert name_status == "M\t.gitignore", name_status
    assert _git(default, "status", "--porcelain").stdout == "", (
        "the vault must end clean once its only change is committed"
    )


def test_sync_commits_a_root_gitignore_edit_but_leaves_outpost_uncommitted(tmp_path):
    """A vault with an uncommitted `.gitignore` edit AND an uncommitted
    `outpost/` edit: the `.gitignore` is committed, the `outpost/` edit is
    left uncommitted and unstaged. The input varied against the previous test
    is which root-adjacent content is in scope — `.gitignore` is, `outpost/`
    is not."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / ".gitignore").write_text("*.lock\n*.tmp\n")
    (default / "outpost").mkdir()
    (default / "outpost" / "config.json").write_text('{"tracked": false}\n')
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    name_status = _git(
        default, "show", "--name-status", "--pretty=format:", "HEAD"
    ).stdout.strip()
    assert name_status == "M\t.gitignore", (
        f"the .gitignore edit must be committed: {name_status!r}"
    )

    status = _git(default, "status", "--porcelain", "--untracked-files=all").stdout
    outpost_line = next(line for line in status.splitlines() if "outpost/config.json" in line)
    assert outpost_line.startswith("??"), (
        f"outpost/ must be left uncommitted AND unstaged: {outpost_line!r}"
    )


def test_sync_commits_a_deleted_record_file_as_a_deletion(tmp_path):
    """A deleted record file is committed as a deletion."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")  # dirty=True: uncommitted task/record.md
    write_vault_config(config_home, [("default", "default", default)])
    r0 = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r0.returncode == 0, r0.stderr
    assert "task/record.md" in _git(default, "ls-files").stdout.split()

    (default / "task" / "record.md").unlink()
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    assert "task/record.md" not in _git(default, "ls-files").stdout.split()
    name_status = _git(
        default, "show", "--name-status", "--pretty=format:", "HEAD"
    ).stdout.strip()
    assert name_status == "D\ttask/record.md", name_status


def test_sync_commits_an_untracked_record_file_as_an_addition(tmp_path):
    """An untracked record file is committed as an addition — the staging is
    not `--update`-shaped (a `git add -u` would silently skip a new file)."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / "task" / "brand-new.md").write_text("# never seen before\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "task/brand-new.md" in _git(default, "ls-files").stdout.split()


def test_sync_commits_an_untracked_sites_file(tmp_path):
    """An untracked file under `sites/` is committed — the free-write zone is
    content and must publish, unlike `outpost/`."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    (default / "sites" / "my-site").mkdir(parents=True)
    (default / "sites" / "my-site" / "index.html").write_text("<html></html>\n")
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "sites/my-site/index.html" in _git(default, "ls-files").stdout.split()


def test_sync_never_commits_the_lock_file_with_a_predating_gitignore(tmp_path):
    """`.lore.lock` is never committed, even in a vault whose `.gitignore`
    predates the scaffolded `*.lock` pattern — the existing belt-and-braces
    unstage must keep holding regardless of what the commit-scope pathspecs are."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = tmp_path / "v-default"
    default.mkdir()
    subprocess.run(["git", "init", str(default)], check=True, capture_output=True)
    for key, val in (("user.email", "t@e.st"), ("user.name", "Test"), ("commit.gpgsign", "false")):
        _git(default, "config", key, val)
    (default / "task").mkdir()
    (default / "task" / "README.md").write_text("vault\n")
    # Predates the scaffolded `*.lock` pattern entirely — no lock ignore at all.
    (default / ".gitignore").write_text("*.tmp\n")
    _git(default, "add", "-A")
    _git(default, "commit", "-m", "init")
    (default / "task" / "record.md").write_text("# a record\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    tracked = _git(default, "ls-files").stdout.split()
    assert "task/record.md" in tracked, "the real change must still be committed"
    assert ".lore.lock" not in tracked, (
        "the write-lock sidecar sync itself creates must never be committed, "
        "gitignored or not"
    )


def test_sync_commits_successfully_when_a_kind_directory_is_missing(tmp_path):
    """A vault missing at least one kind directory (the common case — no real
    vault carries all nine record kinds) still commits its records
    successfully, rather than fataling on an absent pathspec."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default")  # only `task/` exists on disk
    write_vault_config(config_home, [("default", "default", default)])

    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "task/record.md" in _git(default, "ls-files").stdout.split()


# ── lore sync: a contended vault lock ends the run immediately ────────────


def _two_vaults(tmp_path: Path):
    """Return ``(config_home, state_dir, {name: path})`` for a 2-vault install."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    vaults = {
        "held": _make_vault(tmp_path / "v-held"),
        "free": _make_vault(tmp_path / "v-free"),
    }
    write_vault_config(
        config_home,
        [("held", "default", vaults["held"]), ("free", "product", vaults["free"])],
    )
    return config_home, state_dir, vaults


class TestContendedVaultLock:
    def test_contended_sync_ends_immediately_names_the_vault_and_exits_zero(self, tmp_path):
        """With the lock held by a GENUINE second process, `lore sync --vault
        <name>` returns well under the holder's lifetime, names the vault and
        says a sync is already in progress, and exits 0 — the person at the
        terminal is not stranded behind the holder."""
        config_home, state_dir, vaults = _three_vaults_for_lock(tmp_path)
        held = vaults["trailhead"]

        holder = _spawn_holder(held, hold_for=5.0)
        try:
            t0 = time.monotonic()
            r = run_cli(
                ["sync", "--vault", "trailhead"],
                config_home=config_home, state_dir=state_dir,
            )
            elapsed = time.monotonic() - t0
        finally:
            holder.wait(timeout=15)

        assert r.returncode == 0, r.stderr
        assert elapsed < 2.0, (
            f"sync waited {elapsed:.3f}s — it must not wait out the 5s holder"
        )
        assert "trailhead" in r.stdout
        assert "in progress" in r.stdout.lower(), r.stdout

    def test_uncontended_sync_still_acquires_the_lock_and_commits(self, tmp_path):
        """Same entry point, same code path — the ONE input varied is whether
        another process holds the lock. Uncontended, it still does the full
        commit -> pull -> push work."""
        config_home, state_dir, vaults = _three_vaults_for_lock(tmp_path)
        vault = vaults["trailhead"]

        r = run_cli(
            ["sync", "--vault", "trailhead"],
            config_home=config_home, state_dir=state_dir,
        )
        assert r.returncode == 0, r.stderr
        assert _commit_count(vault) == 2, "the uncontended vault must still commit"
        assert _git(vault, "status", "--porcelain").stdout.strip() == ""

    def test_contended_run_leaves_the_vault_byte_identical(self, tmp_path):
        """No partial commit: contended, the vault's tree and history are
        untouched down to the byte."""
        config_home, state_dir, vaults = _three_vaults_for_lock(tmp_path)
        held = vaults["trailhead"]
        record_path = held / "task" / "record.md"
        before_bytes = record_path.read_bytes()
        before_head = _git(held, "rev-parse", "HEAD").stdout.strip()
        before_status = _git(held, "status", "--porcelain").stdout

        holder = _spawn_holder(held, hold_for=3.0)
        try:
            r = run_cli(
                ["sync", "--vault", "trailhead"],
                config_home=config_home, state_dir=state_dir,
            )
        finally:
            holder.wait(timeout=15)
        (held / "_held").unlink(missing_ok=True)  # the holder's own marker file

        assert r.returncode == 0, r.stderr
        assert record_path.read_bytes() == before_bytes
        assert _git(held, "rev-parse", "HEAD").stdout.strip() == before_head
        assert _git(held, "status", "--porcelain").stdout == before_status

    def test_multi_vault_run_reports_two_different_answers(self, tmp_path):
        """One vault's lock is held, the other's is free — ONE run, and each
        vault gets its own answer: the free vault completes its whole loop,
        the held one reports in-progress. Neither strands the other."""
        config_home, state_dir, vaults = _two_vaults(tmp_path)
        held, free = vaults["held"], vaults["free"]

        holder = _spawn_holder(held, hold_for=5.0)
        try:
            r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
        finally:
            holder.wait(timeout=15)

        assert r.returncode == 0, r.stderr
        assert _commit_count(held) == 1, "the held vault must not have committed"
        assert _commit_count(free) == 2, "the free vault must have completed its sync"
        assert _git(free, "status", "--porcelain").stdout.strip() == ""
        assert "in progress" in r.stdout.lower()
        held_lines = [ln for ln in r.stdout.splitlines() if "held:" in ln]
        assert held_lines and "in progress" in held_lines[0].lower()


def _three_vaults_for_lock(tmp_path: Path):
    """Reuse `_three_vaults` — the extra vaults just prove the held one does
    not strand the other configured vaults either."""
    return _three_vaults(tmp_path)


# ── lore sync: bounded, configurable publish retry against a moved history ──
#
# `_push_one` re-fetches and replays onto a moved history before giving up, up
# to a configured maximum. The discriminator that decides whether to retry is
# a CONJUNCTION — fetch succeeded AND the remote-tracking ref advanced past
# its pre-fetch value — never "fetch succeeded" alone, which would misclassify
# a hook/protected-branch rejection as a moved history and burn the whole
# retry budget against a condition no retry can ever clear. See
# `resolve_publish_retry_max` and `_push_one`'s docstring for the full
# discriminator contract.


def _make_pushed_vault(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A vault with one commit, wired to and pushed to a fresh bare remote."""
    vault = _make_vault(tmp_path / f"v-{name}", dirty=False)
    remote = _make_bare_remote(tmp_path / f"{name}-remote.git")
    _wire_remote(vault, remote)
    return vault, remote


def _quiet_emitters():
    """Collect every `say`/`say_err` line, for asserting neither leaks git/remote text."""
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)

    def say_err(text: str) -> None:
        lines.append(text)

    return say, say_err, lines


def _make_moving_forge(tmp_path: Path, name: str, vault: Path, remote: Path) -> None:
    """Make ``remote`` "move on every attempt" against ``vault``'s own pushes.

    A server-side ``pre-receive`` hook CANNOT do this: git refuses a direct
    ``update-ref`` from inside one — "ref updates forbidden inside quarantine
    environment" — precisely to stop a hook from moving a ref out from under
    the push it is currently vetting. (Confirmed empirically while building
    this fixture: a first attempt used exactly that, and it silently never
    advanced the ref at all — the exact "rebase fixture that stops testing
    anything" failure mode.) The only real way to advance history from a hook
    is a CLIENT-side ``pre-push`` hook on ``vault`` itself, driving an
    ordinary competing push from a separate attacker clone immediately before
    every one of the vault's own push attempts.
    """
    attacker = tmp_path / f"{name}-attacker"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(attacker)], check=True, capture_output=True
    )
    for key, val in (
        ("user.email", "attacker@e.st"), ("user.name", "Attacker"), ("commit.gpgsign", "false")
    ):
        _git(attacker, "config", key, val)

    # The vault's real branch is whatever `init.defaultBranch` produced when it
    # was created -- never assume "main": a machine/CI image with no override
    # (e.g. GitHub-hosted `ubuntu-latest`) falls back to a compiled-in default,
    # and a hardcoded `refs/heads/main` here would push to a ref the vault's
    # own branch never shares, so the "attack" never collides with the real
    # push at all.
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    hook = vault / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        f"cd {shlex.quote(str(attacker))} || exit 0\n"
        "echo \"advance-$$-$(date +%s%N)\" >> attack.txt\n"
        "git add -A >/dev/null 2>&1\n"
        "git commit -q -m advance >/dev/null 2>&1\n"
        f"git push -q origin HEAD:refs/heads/{shlex.quote(branch)} >/dev/null 2>&1\n"
        "exit 0\n"
    )
    hook.chmod(0o755)


def _write_plain_rejecting_hook(remote: Path) -> None:
    """A pre-receive hook that rejects every push WITHOUT moving history — the
    protected-branch / permission-refusal stand-in the discriminator must tell
    apart from a moved history."""
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)


def test_push_retry_moved_once_reintegrates_and_pushes_with_one_attempt(tmp_path):
    """A push rejected once by a history that moved: re-integrate, re-push,
    attempts used is 1, and the local commit lands on the forge."""
    vault, remote = _make_pushed_vault(tmp_path, "a")

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "from_b.md").write_text("device b\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device b")
    assert _git(other, "push", "origin").returncode == 0

    (vault / "task" / "from_a.md").write_text("device a\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device a")

    say, say_err, lines = _quiet_emitters()
    rc, ending, attempts_used = sync_mod._push_one(
        vault, say, say_err, committed=True, max_attempts=3
    )

    assert rc == 0, lines
    assert ending == sync_mod.PUBLISH_OK
    assert attempts_used == 1
    assert _commit_count(remote) == _commit_count(vault)
    # The commit really landed on the forge, not just locally.
    r = subprocess.run(
        ["git", "-C", str(remote), "log", "--format=%s"], capture_output=True, text=True
    )
    assert "device a" in r.stdout


def test_push_retry_exhausts_after_the_configured_maximum(tmp_path):
    """A forge that moves on EVERY attempt stops after exactly the configured
    maximum, ending retries-exhausted — not a generic failure. Run at two
    different maxima: the attempt count follows the configuration."""
    for max_attempts in (2, 5):
        vault, remote = _make_pushed_vault(tmp_path, f"loop-{max_attempts}")
        _make_moving_forge(tmp_path, f"loop-{max_attempts}", vault, remote)

        (vault / "task" / "local.md").write_text("local change\n")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-m", "local change")

        say, say_err, lines = _quiet_emitters()
        rc, ending, attempts_used = sync_mod._push_one(
            vault, say, say_err, committed=True, max_attempts=max_attempts
        )

        assert rc == 1, lines
        assert ending == sync_mod.PUBLISH_RETRIES_EXHAUSTED
        assert attempts_used == max_attempts, (
            f"expected {max_attempts} attempts consumed, got {attempts_used}"
        )
        assert _git(vault, "status", "--porcelain").stdout.strip() == ""


def test_push_retry_default_max_applies_when_config_names_none(tmp_path, monkeypatch):
    """The default applies with no override; an env var and a config key both
    override it, in precedence order — the input varied is where the maximum
    comes from."""
    config_home = tmp_path / "config"
    config_home.mkdir(parents=True)
    lore_dir = config_home / "lore"
    lore_dir.mkdir(parents=True)
    (lore_dir / "config.json").write_text(
        '{"vaults": [{"name": "default", "scope": "default"}]}'
    )
    env = {"XDG_CONFIG_HOME": str(config_home)}

    # No override anywhere: the module default.
    assert sync_mod.resolve_publish_retry_max(env=env) == sync_mod.DEFAULT_PUBLISH_RETRY_MAX

    # config.json names one: that value wins over the default.
    (lore_dir / "config.json").write_text(
        '{"vaults": [{"name": "default", "scope": "default"}], "publish_retry_max": 9}'
    )
    assert sync_mod.resolve_publish_retry_max(env=env) == 9

    # The env var wins over config.json.
    env_with_var = dict(env, LORE_PUBLISH_RETRY_MAX="4")
    assert sync_mod.resolve_publish_retry_max(env=env_with_var) == 4


def test_push_retry_unreachable_forge_consumes_no_attempts_and_stays_soft(tmp_path):
    """An unreachable forge is not a rejection: no attempts consumed, no
    retries-exhausted — the existing soft, converge-next-sweep path."""
    vault, remote = _make_pushed_vault(tmp_path, "gone")
    _git(vault, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))

    (vault / "task" / "local.md").write_text("local change\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "local change")

    say, say_err, lines = _quiet_emitters()
    rc, ending, attempts_used = sync_mod._push_one(
        vault, say, say_err, committed=True, max_attempts=3
    )

    assert rc == 0, lines
    assert ending == sync_mod.PUBLISH_OK
    assert attempts_used == 0
    assert ending != sync_mod.PUBLISH_RETRIES_EXHAUSTED


def test_push_retry_hook_rejection_ends_holding_consumes_zero_and_stays_clean(tmp_path):
    """A hook/protected-branch rejection is not a moved history: it ends
    `holding`, consumes zero attempts, never retries, and leaves the vault
    clean (the local commit stays, nothing is torn down). It exits non-zero,
    matching the genuine-replay-conflict `holding` path — a person must act
    either way (see the exit-code rule in the module's `_push_one` docstring)."""
    vault, remote = _make_pushed_vault(tmp_path, "hook")
    _write_plain_rejecting_hook(remote)

    (vault / "task" / "local.md").write_text("local change\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "local change")
    before_head = _git(vault, "rev-parse", "HEAD").stdout.strip()

    say, say_err, lines = _quiet_emitters()
    rc, ending, attempts_used = sync_mod._push_one(
        vault, say, say_err, committed=True, max_attempts=3
    )

    assert rc == 1, lines
    assert ending == sync_mod.PUBLISH_HOLDING
    assert attempts_used == 0
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""
    assert _git(vault, "rev-parse", "HEAD").stdout.strip() == before_head


def _assert_no_git_or_remote_leak(lines: list[str], path_name: str) -> None:
    """No stderr from git or the remote reaches ``lines`` — asserted for ONE
    ending path at a time, so a leak on one path's output can never be masked
    by another path's differently-shaped output (see
    ``test_push_retry_never_leaks_git_or_remote_stderr``)."""
    combined = "\n".join(lines)
    assert "fatal:" not in combined, f"{path_name}: fatal: leaked; combined={combined!r}"
    assert "hint:" not in combined, f"{path_name}: hint: leaked; combined={combined!r}"
    assert "rejected" not in combined.lower(), (
        f"{path_name}: git's own rejection text must never reach operator-facing "
        f"output; combined={combined!r}"
    )


def test_push_retry_never_leaks_git_or_remote_stderr(tmp_path):
    """No stderr from git or the remote reaches any message — across the
    moved-history, exhausted, unreachable, and hook-rejection paths, each
    asserted SEPARATELY so a leak on one ending can never be masked by
    another ending's differently-shaped output. lore's stderr embeds the
    remote URL verbatim, a credential in a team-synced vault."""
    # Moved-once path.
    vault, remote = _make_pushed_vault(tmp_path, "leak-a")
    other = _clone_as_second_device(remote, tmp_path / "leak-device-b")
    (other / "from_b.md").write_text("b\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device b")
    _git(other, "push", "origin")
    (vault / "task" / "from_a.md").write_text("a\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device a")
    say, say_err, lines = _quiet_emitters()
    sync_mod._push_one(vault, say, say_err, committed=True, max_attempts=3)
    _assert_no_git_or_remote_leak(lines, "moved-once")

    # Exhausted path.
    vault2, remote2 = _make_pushed_vault(tmp_path, "leak-loop")
    _make_moving_forge(tmp_path, "leak-loop", vault2, remote2)
    (vault2 / "task" / "local.md").write_text("local\n")
    _git(vault2, "add", "-A")
    _git(vault2, "commit", "-m", "local")
    say2, say_err2, lines2 = _quiet_emitters()
    sync_mod._push_one(vault2, say2, say_err2, committed=True, max_attempts=2)
    _assert_no_git_or_remote_leak(lines2, "exhausted")

    # Unreachable path.
    vault3, _ = _make_pushed_vault(tmp_path, "leak-gone")
    bad_remote_path = str(tmp_path / "does-not-exist.git")
    _git(vault3, "remote", "set-url", "origin", bad_remote_path)
    (vault3 / "task" / "local.md").write_text("local\n")
    _git(vault3, "add", "-A")
    _git(vault3, "commit", "-m", "local")
    say3, say_err3, lines3 = _quiet_emitters()
    sync_mod._push_one(vault3, say3, say_err3, committed=True, max_attempts=3)
    _assert_no_git_or_remote_leak(lines3, "unreachable")
    combined3 = "\n".join(lines3)
    assert bad_remote_path not in combined3, (
        f"unreachable: remote path leaked; combined={combined3!r}"
    )

    # Hook-rejection path.
    vault4, remote4 = _make_pushed_vault(tmp_path, "leak-hook")
    _write_plain_rejecting_hook(remote4)
    (vault4 / "task" / "local.md").write_text("local\n")
    _git(vault4, "add", "-A")
    _git(vault4, "commit", "-m", "local")
    say4, say_err4, lines4 = _quiet_emitters()
    sync_mod._push_one(vault4, say4, say_err4, committed=True, max_attempts=3)
    _assert_no_git_or_remote_leak(lines4, "hook-rejection")


def test_push_retry_leaves_the_vault_clean_in_every_ending(tmp_path):
    """Every ending — success-after-retry, exhaustion, unreachable, holding —
    leaves the vault's working tree clean (no partial rebase, no leftover
    conflict markers)."""
    # Success-after-retry.
    vault_a, remote_a = _make_pushed_vault(tmp_path, "clean-a")
    other = _clone_as_second_device(remote_a, tmp_path / "clean-device-b")
    (other / "from_b.md").write_text("b\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device b")
    _git(other, "push", "origin")
    (vault_a / "task" / "from_a.md").write_text("a\n")
    _git(vault_a, "add", "-A")
    _git(vault_a, "commit", "-m", "device a")
    say, say_err, _ = _quiet_emitters()
    sync_mod._push_one(vault_a, say, say_err, committed=True, max_attempts=3)
    assert _git(vault_a, "status", "--porcelain").stdout.strip() == ""
    assert not (vault_a / ".git" / "rebase-merge").exists()
    assert not (vault_a / ".git" / "rebase-apply").exists()

    # Exhaustion.
    vault_b, remote_b = _make_pushed_vault(tmp_path, "clean-loop")
    _make_moving_forge(tmp_path, "clean-loop", vault_b, remote_b)
    (vault_b / "task" / "local.md").write_text("local\n")
    _git(vault_b, "add", "-A")
    _git(vault_b, "commit", "-m", "local")
    say2, say_err2, _ = _quiet_emitters()
    rc, ending, _attempts = sync_mod._push_one(
        vault_b, say2, say_err2, committed=True, max_attempts=2
    )
    assert ending == sync_mod.PUBLISH_RETRIES_EXHAUSTED
    assert _git(vault_b, "status", "--porcelain").stdout.strip() == ""
    assert not (vault_b / ".git" / "rebase-merge").exists()
    assert not (vault_b / ".git" / "rebase-apply").exists()


def _make_conflicting_forge(tmp_path: Path, name: str, vault: Path, remote: Path) -> None:
    """Like :func:`_make_moving_forge`, but the attacker's edit conflicts with
    the SAME line of the SAME file the vault's own local commit touches — a
    genuine rebase conflict during the retry's replay, distinct from a moved
    but content-compatible history."""
    attacker = tmp_path / f"{name}-attacker"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(attacker)], check=True, capture_output=True
    )
    for key, val in (
        ("user.email", "attacker@e.st"), ("user.name", "Attacker"), ("commit.gpgsign", "false")
    ):
        _git(attacker, "config", key, val)

    # See `_make_moving_forge`'s comment: never assume "main".
    branch = _git(vault, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    hook = vault / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        f"cd {shlex.quote(str(attacker))} || exit 0\n"
        "echo attacker-line > task/README.md\n"
        "git add -A >/dev/null 2>&1\n"
        "git commit -q -m attacker-edit >/dev/null 2>&1\n"
        f"git push -q origin HEAD:refs/heads/{shlex.quote(branch)} >/dev/null 2>&1\n"
        "exit 0\n"
    )
    hook.chmod(0o755)


def test_push_retry_replay_conflict_hands_off_to_the_resolver(tmp_path):
    """When the replay itself cannot be integrated cleanly (a genuine content
    conflict, not just a moved-but-compatible history), the rebase is aborted
    and the conflict is handed to the resolver instead of just reported. The
    fixture's conflict (README.md, which has no sidecar counterpart) is one
    the resolver cannot settle either, so this pins the specific ending, exit
    code, and operator-facing message for `_push_one`'s
    `if rc_rebase != 0:` branch (`cli/sync.py`) reaching
    `_hand_off_to_resolver`'s own failure path — not just the vault's on-disk
    state, which a wrong or missing message could satisfy identically."""
    vault, remote = _make_pushed_vault(tmp_path, "conflict")
    _make_conflicting_forge(tmp_path, "conflict", vault, remote)

    (vault / "task" / "README.md").write_text("vault-line\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "vault edit")
    before_head = _git(vault, "rev-parse", "HEAD").stdout.strip()

    say, say_err, lines = _quiet_emitters()
    rc, ending, attempts = sync_mod._push_one(
        vault, say, say_err, committed=True, max_attempts=3,
        name="conflict", shared=False,
    )

    assert rc == 1, lines
    assert ending == sync_mod.PUBLISH_HOLDING, lines
    assert attempts == 1, lines
    assert any(
        "the resolver could not settle this conflict" in ln for ln in lines
    ), f"the hand-off's own failure message must reach the operator; lines={lines!r}"
    assert not (vault / ".git" / "rebase-merge").exists()
    assert not (vault / ".git" / "rebase-apply").exists()
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""
    assert _git(vault, "rev-parse", "HEAD").stdout.strip() == before_head


def _write_record(vault: Path, record_id: str, *, status: str) -> None:
    """Write a minimal valid task record directly — the shape
    `lore record create` writes (see `test_sync_reindexes_after_a_pull...`
    above): a record kind directory, a body, and a sidecar with the fields
    the index schema requires NOT NULL."""
    kind, _name = record_id.split("/", 1)
    (vault / kind).mkdir(parents=True, exist_ok=True)
    (vault / f"{record_id}.md").write_text("body text\n")
    (vault / f"{record_id}.json").write_text(
        json.dumps({
            "title": "A Task", "status": status,
            "created-at": "2026-07-29", "updated-at": "2026-07-29",
        })
    )


def test_push_retry_replay_conflict_on_a_judgment_field_is_awaiting_person_like_the_pull(
    tmp_path,
):
    """The vary-the-input pair that proves BOTH replay sites hand off, not
    just whichever one a fixture happens to hit first: a genuine both-sides
    field move (`status`), reached through `_push_one`'s moved-history replay
    instead of `_pull_one`'s rebase, reports the SAME `awaiting-person`
    outcome the pull site reports for the identical shape of conflict
    (`test_sync_reports_awaiting_person_for_a_both_sides_judgment_conflict`,
    `test_resolve_core.py`)."""
    vault, remote = _make_pushed_vault(tmp_path, "judgment")
    record_id = "task/a-task"
    _write_record(vault, record_id, status="open")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "seed record")
    _git(vault, "push", "origin")

    other = _clone_as_second_device(remote, tmp_path / "judgment-device-b")
    _write_record(other, record_id, status="done")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B moves status")
    _git(other, "push", "origin")

    _write_record(vault, record_id, status="ready")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "device A moves status")
    before_head = _git(vault, "rev-parse", "HEAD").stdout.strip()

    say, say_err, lines = _quiet_emitters()
    rc, ending, _attempts = sync_mod._push_one(
        vault, say, say_err, committed=True, max_attempts=3,
        name="judgment", shared=False,
    )

    assert rc == 1, lines
    assert ending == sync_mod.SYNC_AWAITING_PERSON, lines
    assert not (vault / ".git" / "rebase-merge").exists()
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""
    assert _git(vault, "rev-parse", "HEAD").stdout.strip() == before_head, (
        "the local commit the resolver held is still present at its pre-hold sha"
    )


def test_finish_push_replay_conflict_does_not_recurse_into_the_resolver(tmp_path, monkeypatch):
    """`resolve.py`'s `_finish` is ALREADY the tail of a resolution (a
    person's `lore resolve`, or the sweep's own `resolve_for_sweep`). A
    replay conflict on ITS OWN push (`_push_one` called with
    `hand_off=False`) must report the pre-task `holding` ending directly and
    must NEVER re-enter `resolve_for_sweep` — recursing there is exactly the
    regression the drift gate caught in the prior commit (name/shared were
    never forwarded from `resolve.py:931`, so the unconditional hand-off
    recursed with `name=""`, `shared=False`)."""
    calls = []

    def _counting_resolve_for_sweep(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("resolve_for_sweep must never be called from _finish's own push")

    monkeypatch.setattr(resolve_mod, "resolve_for_sweep", _counting_resolve_for_sweep)

    vault, remote = _make_pushed_vault(tmp_path, "finish-conflict")
    _make_conflicting_forge(tmp_path, "finish-conflict", vault, remote)
    (vault / "task" / "README.md").write_text("vault-line\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "vault edit")

    say, say_err, lines = _quiet_emitters()
    rc = resolve_mod._finish(
        vault, "finish-conflict", say, say_err, shared=False, include_shared=False
    )

    assert calls == [], f"resolve_for_sweep must not be re-entered; calls={calls!r}"
    assert rc == 1, lines
    assert any(
        "replaying onto the moved history failed" in ln for ln in lines
    ), f"the pre-task message must still reach the operator; lines={lines!r}"
    assert not (vault / ".git" / "rebase-merge").exists()
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""


def test_finish_push_replay_conflict_leaves_no_held_or_failed_marker(tmp_path):
    """The held marker and the failed-vault marker are mutually exclusive by
    design — a vault is either waiting on a person's judgment or failed for a
    policy reason, never both. `_finish`'s own push conflict (never handed to
    the resolver — see the sibling test above) must leave NEITHER marker,
    not stack a held marker (from an inner recursive resolve) on top of a
    failed marker (from the outer call misclassifying the inner's non-zero
    exit)."""
    vault, remote = _make_pushed_vault(tmp_path, "finish-marker")
    _make_conflicting_forge(tmp_path, "finish-marker", vault, remote)
    (vault / "task" / "README.md").write_text("vault-line\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "vault edit")

    say, say_err, _lines = _quiet_emitters()
    resolve_mod._finish(
        vault, "finish-marker", say, say_err, shared=False, include_shared=False
    )

    assert resolve_state_mod.read_held_marker(vault) is None, "no held marker"
    assert resolve_state_mod.read_failed_marker(vault) is None, "no failed marker either"


def _make_single_shot_race_hook(vault: Path, other: Path) -> None:
    """A client-side pre-push hook that, on its FIRST invocation only, pushes
    ``other``'s already-committed record to origin — simulating a second
    device winning the race between THIS vault's pull (which found nothing to
    integrate) and its own push attempt. The hook disables itself after firing
    once, via a marker file, so the retried push (after the publish replay)
    is not rejected again — this reproduces exactly one moved-history publish
    retry, not `_make_moving_forge`'s unbounded loop."""
    marker = vault / ".git" / "raced-once"
    hook = vault / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        f"MARKER={shlex.quote(str(marker))}\n"
        'if [ -e "$MARKER" ]; then exit 0; fi\n'
        'touch "$MARKER"\n'
        f"cd {shlex.quote(str(other))} || exit 0\n"
        "git push -q origin HEAD >/dev/null 2>&1\n"
        "exit 0\n"
    )
    hook.chmod(0o755)


def test_sync_reindexes_records_integrated_by_the_publish_replay(tmp_path):
    """A vault whose push loses a race — another device publishes between this
    vault's pull (which found nothing behind) and its own push attempt — is
    rejected, refetches, and REPLAYS the winner's commit before retrying the
    push. That replayed record must be reindexed exactly like an ordinary
    pull: the module's whole reindex rule exists so a record landed on disk
    this run is never invisible to `lore search`, and a record that arrived
    via the publish retry's replay is landed on disk exactly the same way a
    pull lands one.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=True)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "decision").mkdir()
    (other / "decision" / "from-b.md").write_text("# Chose the quokka renderer\n")
    (other / "decision" / "from-b.json").write_text(
        json.dumps(
            {
                "title": "Chose the quokka renderer",
                "status": "active",
                "created-at": "2026-07-29",
                "updated-at": "2026-07-29",
            }
        )
    )
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B decision")
    # Deliberately NOT pushed yet — the race hook pushes it at the moment
    # `default` attempts its own push, i.e. strictly after `default`'s pull
    # already ran and found nothing behind.

    _make_single_shot_race_hook(default, other)

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr
    assert "Reindexed" in r.stdout, (
        f"a record integrated by the publish replay must trigger a reindex; stdout={r.stdout!r}"
    )

    s = run_cli(["search", "quokka"], config_home=config_home, state_dir=state_dir)
    assert s.returncode == 0, s.stderr
    assert "from-b" in s.stdout, (
        f"the replayed record must be searchable; stdout={s.stdout!r}"
    )


# ── lore sync --json: a determinate per-vault outcome ──────────────────────
#
# `lore sync --json` reports a closed six-literal outcome per vault:
# in-progress, converged, published, holding, refused, retries-exhausted.
# Every test below runs the CLI end to end against a real fixture — never a
# unit call — because the outcome is derived from git state `cmd_sync`
# observes across its whole loop, not from any one function's return value.


def _extract_json_report(stdout: str) -> dict:
    """Pull the trailing JSON document out of ``--json``'s stdout.

    Prose stays the default output and is never suppressed (the JSON is an
    ADDITION, not a replacement — see `cmd_sync`'s docstring) so the report is
    the last thing printed. `json.dumps(doc, indent=2)` always opens with a
    line that is exactly ``{`` — no prose line in this module ever is — which
    anchors the split reliably without guessing at brace-matching.
    """
    lines = stdout.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln == "{")
    return json.loads("\n".join(lines[start:]))


def _vault_outcome(doc: dict, name: str) -> dict:
    matches = [v for v in doc["vaults"] if v["vault"] == name]
    assert len(matches) == 1, f"expected exactly one entry for {name!r}, got {matches}"
    return matches[0]


def _assert_no_mid_rebase(vault: Path) -> None:
    assert not (vault / ".git" / "rebase-merge").exists()
    assert not (vault / ".git" / "rebase-apply").exists()
    assert _git(vault, "status", "--porcelain").stdout.strip() == ""


def test_json_outcome_converged_behind_only(tmp_path):
    """A vault strictly behind, nothing local: converged, at the published
    history, never mid-rebase."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "theirs.md").write_text("# device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "converged"
    assert "condition" not in entry
    assert _git(default, "rev-parse", "HEAD").stdout.strip() == \
        _git(remote, "rev-parse", "HEAD").stdout.strip()
    _assert_no_mid_rebase(default)


def test_json_outcome_published_ahead_only(tmp_path):
    """A clean vault with only local, uncommitted work: publishes, and the
    forge ends up carrying the commit."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=True)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    (default / "task" / "record.md").write_text("# a record\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "published"
    assert "condition" not in entry
    assert _git(default, "rev-parse", "HEAD").stdout.strip() == \
        _git(remote, "rev-parse", "HEAD").stdout.strip()
    _assert_no_mid_rebase(default)


def test_json_outcome_published_diverged_settleable(tmp_path):
    """Local dirt plus a compatible remote commit: replays and publishes."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "theirs.md").write_text("# device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B record")
    _git(other, "push", "origin")

    (default / "task" / "ours.md").write_text("# device A\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "published"
    assert (default / "theirs.md").exists()
    assert (default / "task" / "ours.md").exists()
    assert _git(default, "rev-parse", "HEAD").stdout.strip() == \
        _git(remote, "rev-parse", "HEAD").stdout.strip()
    _assert_no_mid_rebase(default)


def test_json_outcome_holding_diverged_unsettleable(tmp_path):
    """A genuine both-sides content conflict: holding, vault clean and
    diverged, the local commit kept — never mid-rebase."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)

    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")

    (default / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "holding"
    assert "condition" not in entry
    # Diverged: the local commit landed and is NOT what the remote carries.
    assert _git(default, "rev-parse", "HEAD").stdout.strip() != \
        _git(remote, "rev-parse", "HEAD").stdout.strip()
    assert (default / "task" / "README.md").read_text() == "edited on device A\n"
    _assert_no_mid_rebase(default)


def test_json_outcome_holding_offline_with_a_just_committed_change(tmp_path):
    """Offline, with a commit landed THIS RUN and never published: `holding`,
    never `converged` — the design doc's "host hoarding work" case, which the
    full loop must never report as nothing-left-to-do."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    _git(default, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (default / "task" / "record.md").write_text("# a record\n")

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode != 0

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "holding"
    assert "condition" not in entry
    assert _git(default, "log", "--oneline").stdout.strip().splitlines().__len__() == 2, (
        "the commit must land even though it could not be published"
    )


def test_json_outcome_converged_offline_with_nothing_to_publish(tmp_path):
    """Offline, but with NOTHING committed this run and nothing already
    unpushed: `converged` still holds — the exception in
    `test_json_outcome_holding_offline_with_a_just_committed_change` is for
    unpublished work specifically, not for offline in general."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    _git(default, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "converged"


def test_json_pull_only_offline_gets_no_outcome_entry(tmp_path):
    """`--pull-only` never publishes, so an offline fetch there can never be
    hoarding anything — the loop could not determine an outcome at all, so it
    must claim none: no entry in the report, and this vault's own exit stays
    0."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    _git(default, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--pull-only", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    doc = _extract_json_report(r.stdout)
    matches = [v for v in doc["vaults"] if v["vault"] == "default"]
    assert matches == [], f"an offline pull-only vault must get no outcome entry: {matches}"


def test_json_lock_acquisition_failure_gets_no_outcome_entry(tmp_path):
    """A vault whose write-lock acquisition itself fails (here: something other
    than a plain file occupies the lock path, e.g. a read-only vault root
    would raise the same `OSError`) never reaches a determinate outcome this
    run — it must get no entry, exactly like a missing vault or a git error
    while staging, even though the schema string previously named only those
    two cases plus one more."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=True)
    # Occupy the lock path with a directory: `open(lock_path, "a")` raises
    # `IsADirectoryError`, an `OSError`, exactly the failure class a read-only
    # vault root would also raise on lock-file creation.
    (default / ".lore.lock").mkdir()

    write_vault_config(config_home, [("default", "default", default)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)

    assert r.returncode == 1
    assert "failed to acquire vault lock" in r.stderr
    doc = _extract_json_report(r.stdout)
    matches = [v for v in doc["vaults"] if v["vault"] == "default"]
    assert matches == [], f"a vault whose lock acquisition failed must get no entry: {matches}"


def test_json_target_selection_failure_prints_no_document_at_all(tmp_path):
    """An unknown `--vault` name fails BEFORE any vault is even selected — the
    run never reaches the point of building a document, so `--json` prints
    none at all, not an empty one."""
    config_home, state_dir, _vaults = _three_vaults(tmp_path)

    r = run_cli(
        ["sync", "--vault", "nope", "--json"], config_home=config_home, state_dir=state_dir
    )

    assert r.returncode == 1
    assert "unknown vault" in r.stderr.lower()
    assert "{" not in r.stdout, f"no document should be printed at all; stdout={r.stdout!r}"


def test_json_outcome_holding_hook_rejection_matches_replay_conflict_holding(tmp_path):
    """The two `holding` producers — a hook rejection that never moved the
    published history, and a genuine replay conflict — must be
    indistinguishable in both the emitted document and the run's exit code.
    They once differed: the hook-rejection path exited 0 while the
    replay-conflict path exited 1, so `holding` silently split into two
    different runtime behaviours behind one outcome literal."""
    # -- Producer 1: hook rejection, no history movement --------------------
    config_home_hook = tmp_path / "config-hook"
    state_dir_hook = tmp_path / "state-hook"
    state_dir_hook.mkdir(parents=True)
    hook_vault = _make_vault(tmp_path / "v-hook", dirty=False)
    hook_remote = _make_bare_remote(tmp_path / "hook-remote.git")
    _wire_remote(hook_vault, hook_remote)
    _write_plain_rejecting_hook(hook_remote)
    (hook_vault / "task" / "local.md").write_text("local change\n")

    write_vault_config(config_home_hook, [("default", "default", hook_vault)])
    r_hook = run_cli(
        ["sync", "--json"], config_home=config_home_hook, state_dir=state_dir_hook
    )
    doc_hook = _extract_json_report(r_hook.stdout)
    entry_hook = _vault_outcome(doc_hook, "default")

    # -- Producer 2: genuine replay conflict ---------------------------------
    config_home_conflict = tmp_path / "config-conflict"
    state_dir_conflict = tmp_path / "state-conflict"
    state_dir_conflict.mkdir(parents=True)
    conflict_vault = _make_vault(tmp_path / "v-conflict", dirty=False)
    conflict_remote = _make_bare_remote(tmp_path / "conflict-remote.git")
    _wire_remote(conflict_vault, conflict_remote)

    other = _clone_as_second_device(conflict_remote, tmp_path / "device-conflict-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")
    (conflict_vault / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home_conflict, [("default", "default", conflict_vault)])
    r_conflict = run_cli(
        ["sync", "--json"], config_home=config_home_conflict, state_dir=state_dir_conflict
    )
    doc_conflict = _extract_json_report(r_conflict.stdout)
    entry_conflict = _vault_outcome(doc_conflict, "default")

    # -- Both producers: same outcome, same document shape, same exit code --
    assert r_hook.returncode == 1, r_hook.stderr
    assert r_conflict.returncode == 1, r_conflict.stderr
    assert entry_hook["outcome"] == "holding"
    assert entry_conflict["outcome"] == "holding"
    assert set(entry_hook.keys()) == set(entry_conflict.keys())
    assert "condition" not in entry_hook
    assert "condition" not in entry_conflict


def test_json_outcome_refused_stranded_vault(tmp_path):
    """A vault already mid-rebase: refused, with its condition named, and
    left byte-identical."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    stuck = _make_vault(tmp_path / "v-stuck", dirty=False)
    write_vault_config(config_home, [("stuck", "default", stuck)])
    _strand_mid_rebase(stuck, tmp_path)
    before = _snapshot(stuck)

    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "stuck")
    assert entry["outcome"] == "refused"
    assert entry["condition"] == "mid-rebase"
    assert _snapshot(stuck) == before, "a refused vault must be byte-identical after"
    # Still genuinely mid-rebase — refusal never touches it, but it must
    # never be reported as something else either.
    assert (stuck / ".git" / "rebase-merge").exists()


def test_json_outcome_retries_exhausted_moving_forge(tmp_path):
    """A forge that keeps moving on every attempt: retries-exhausted, and the
    vault ends clean (never mid-rebase) with its commit intact locally."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    _make_moving_forge(tmp_path, "exhaust", default, remote)

    (default / "task" / "local.md").write_text("local change\n")

    write_vault_config(config_home, [("default", "default", default)])
    env = {"LORE_PUBLISH_RETRY_MAX": "2"}
    r = subprocess.run(
        [sys.executable, str(CLI_PATH), "sync", "--json"],
        capture_output=True, text=True,
        env={
            **os.environ, **env,
            "XDG_CONFIG_HOME": str(config_home), "XDG_STATE_HOME": str(state_dir),
            "HOME": str(state_dir / "home"), "LORE_EMAIL": "tester@example.com",
        },
    )
    assert r.returncode == 1

    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "default")
    assert entry["outcome"] == "retries-exhausted"
    assert "condition" not in entry
    _assert_no_mid_rebase(default)
    # DEFAULT_SYNC_MSG is no longer the literal subject of a message-less sync
    # — task/automatic-commits-name-the-host-and-what-they-published replaced
    # it with a generated message naming the host and what was staged.
    assert socket.gethostname() in _git(default, "log", "-1", "--format=%s").stdout.strip()


def test_json_outcome_in_progress_contended_vault(tmp_path):
    """A vault whose write lock is held by a genuine second process: reported
    in-progress, exits zero, and is left completely untouched."""
    config_home, state_dir, vaults = _three_vaults_for_lock(tmp_path)
    held = vaults["trailhead"]
    record_path = held / "task" / "record.md"
    before_bytes = record_path.read_bytes()

    holder = _spawn_holder(held, hold_for=5.0)
    try:
        r = run_cli(
            ["sync", "--vault", "trailhead", "--json"],
            config_home=config_home, state_dir=state_dir,
        )
    finally:
        holder.wait(timeout=15)
    (held / "_held").unlink(missing_ok=True)

    assert r.returncode == 0, r.stderr
    doc = _extract_json_report(r.stdout)
    entry = _vault_outcome(doc, "trailhead")
    assert entry["outcome"] == "in-progress"
    assert "condition" not in entry
    assert record_path.read_bytes() == before_bytes
    assert not (held / ".git" / "rebase-merge").exists()
    assert not (held / ".git" / "rebase-apply").exists()


def test_json_multi_vault_run_reports_three_different_outcomes(tmp_path):
    """One run, three vaults, three DIFFERENT outcomes in one document — the
    varied input is the vault set; a single exit code could not carry this."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    # Vault A: behind-only -> converged.
    a = _make_vault(tmp_path / "v-a", dirty=False)
    remote_a = _make_bare_remote(tmp_path / "a-remote.git")
    _wire_remote(a, remote_a)
    device_b_a = _clone_as_second_device(remote_a, tmp_path / "a-device-b")
    (device_b_a / "theirs.md").write_text("# device B\n")
    _git(device_b_a, "add", "-A")
    _git(device_b_a, "commit", "-m", "device B record")
    _git(device_b_a, "push", "origin")

    # Vault B: ahead-only -> published.
    b = _make_vault(tmp_path / "v-b", dirty=True)
    remote_b = _make_bare_remote(tmp_path / "b-remote.git")
    _wire_remote(b, remote_b)

    # Vault C: already mid-rebase -> refused.
    c = _make_vault(tmp_path / "v-c", dirty=False)
    _strand_mid_rebase(c, tmp_path)

    write_vault_config(
        config_home,
        [("a", "default", a), ("b", "product", b), ("c", "repo", c)],
    )
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1  # vault c's refusal is a hard failure this run

    doc = _extract_json_report(r.stdout)
    outcomes = {v["vault"]: v["outcome"] for v in doc["vaults"]}
    assert outcomes == {"a": "converged", "b": "published", "c": "refused"}
    assert len({outcomes["a"], outcomes["b"], outcomes["c"]}) == 3


def test_json_one_vault_awaiting_person_others_still_reach_determinate_outcomes(tmp_path):
    """AC8 through the new path: a host with several vaults, one of them
    held on a genuine judgment conflict, still lets every OTHER vault reach
    its own determinate outcome in the same run — three different outcomes,
    one of them the new `awaiting-person`."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    # Vault A: a both-sides judgment conflict -> awaiting-person.
    a = _make_vault(tmp_path / "v-a", dirty=False)
    remote_a = _make_bare_remote(tmp_path / "a-remote.git")
    _wire_remote(a, remote_a)
    record_id = "task/a-task"
    _write_record(a, record_id, status="open")
    _git(a, "add", "-A")
    _git(a, "commit", "-m", "seed record")
    _git(a, "push", "origin")
    device_b_a = _clone_as_second_device(remote_a, tmp_path / "a-device-b")
    _write_record(device_b_a, record_id, status="done")
    _git(device_b_a, "add", "-A")
    _git(device_b_a, "commit", "-m", "device B moves status")
    _git(device_b_a, "push", "origin")
    _write_record(a, record_id, status="ready")

    # Vault B: behind-only -> converged.
    b = _make_vault(tmp_path / "v-b", dirty=False)
    remote_b = _make_bare_remote(tmp_path / "b-remote.git")
    _wire_remote(b, remote_b)
    device_b_b = _clone_as_second_device(remote_b, tmp_path / "b-device-b")
    (device_b_b / "theirs.md").write_text("# device B\n")
    _git(device_b_b, "add", "-A")
    _git(device_b_b, "commit", "-m", "device B record")
    _git(device_b_b, "push", "origin")

    # Vault C: ahead-only -> published.
    c = _make_vault(tmp_path / "v-c", dirty=True)
    remote_c = _make_bare_remote(tmp_path / "c-remote.git")
    _wire_remote(c, remote_c)

    write_vault_config(
        config_home,
        [("a", "default", a), ("b", "product", b), ("c", "repo", c)],
    )
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1  # vault a's held ending is a hard failure this run

    doc = _extract_json_report(r.stdout)
    outcomes = {v["vault"]: v["outcome"] for v in doc["vaults"]}
    assert outcomes == {"a": "awaiting-person", "b": "converged", "c": "published"}

    assert _git(b, "status", "--porcelain").stdout.strip() == ""
    assert _git(b, "rev-parse", "HEAD").stdout.strip() == \
        _git(remote_b, "rev-parse", "HEAD").stdout.strip()
    assert _git(c, "rev-parse", "HEAD").stdout.strip() == \
        _git(remote_c, "rev-parse", "HEAD").stdout.strip()
    assert _git(a, "status", "--porcelain").stdout.strip() == "", "a's held ending is clean"
    assert not (a / ".git" / "rebase-merge").exists()


def test_json_holding_reason_distinguishes_policy_failure_from_remote_rejection(tmp_path):
    """The two `holding` producers a person cannot tell apart from the row's
    text alone must carry different `reason` tags in the stored document: a
    resolver failure (`policy-failure`) is cleared by fixing this host's
    data; a forge rejection (`remote-rejection`) is cleared by fixing the
    forge's policy or the credential pushing to it."""
    # -- Producer 1: resolver policy-failure (README.md has no sidecar) -----
    config_home_policy = tmp_path / "config-policy"
    state_dir_policy = tmp_path / "state-policy"
    state_dir_policy.mkdir(parents=True)
    policy_vault = _make_vault(tmp_path / "v-policy", dirty=False)
    policy_remote = _make_bare_remote(tmp_path / "policy-remote.git")
    _wire_remote(policy_vault, policy_remote)
    other = _clone_as_second_device(policy_remote, tmp_path / "policy-device-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")
    (policy_vault / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home_policy, [("default", "default", policy_vault)])
    r_policy = run_cli(
        ["sync", "--json"], config_home=config_home_policy, state_dir=state_dir_policy
    )
    doc_policy = _extract_json_report(r_policy.stdout)
    entry_policy = _vault_outcome(doc_policy, "default")

    # -- Producer 2: forge rejection, no history movement --------------------
    config_home_hook = tmp_path / "config-hook"
    state_dir_hook = tmp_path / "state-hook"
    state_dir_hook.mkdir(parents=True)
    hook_vault = _make_vault(tmp_path / "v-hook", dirty=False)
    hook_remote = _make_bare_remote(tmp_path / "hook-remote.git")
    _wire_remote(hook_vault, hook_remote)
    _write_plain_rejecting_hook(hook_remote)
    (hook_vault / "task" / "local.md").write_text("local change\n")

    write_vault_config(config_home_hook, [("default", "default", hook_vault)])
    r_hook = run_cli(
        ["sync", "--json"], config_home=config_home_hook, state_dir=state_dir_hook
    )
    doc_hook = _extract_json_report(r_hook.stdout)
    entry_hook = _vault_outcome(doc_hook, "default")

    assert entry_policy["outcome"] == "holding"
    assert entry_hook["outcome"] == "holding"
    assert entry_policy["reason"] == "policy-failure"
    assert entry_hook["reason"] == "remote-rejection"
    assert entry_policy["reason"] != entry_hook["reason"], (
        "the row's text may stay unified; the stored fact must not be"
    )


def test_the_failure_diagnostic_lands_in_a_named_durable_location(tmp_path):
    """The design doc's "goes to the terminal and the host's log" promise,
    made checkable: the failure's own detail is NOT only in this one run's
    stderr — it is readable back from a named, durable location on disk
    (`resolve_state`'s failed-vault marker) after the process that printed it
    has already exited."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    vault = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(vault, remote)
    other = _clone_as_second_device(remote, tmp_path / "device-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")
    (vault / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home, [("default", "default", vault)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1, r.stderr

    os.environ["XDG_STATE_HOME"] = str(state_dir)
    marker = resolve_state_mod.read_failed_marker(vault)
    assert marker is not None, "the failure's own marker must exist on disk"
    assert marker["reason"] == "policy-failure"
    assert marker["detail"], "a human reading this later needs to know WHY, not just that"
    assert marker["vault"] == "v-default"


def test_json_unresolved_vault_does_not_strand_the_others(tmp_path):
    """After a run where one vault could not be resolved (a genuine
    conflict), every OTHER vault is clean and at the published history, and
    the unresolved vault has still been fetched — its behind-count is
    current, not stale."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    good = _make_vault(tmp_path / "v-good", dirty=False)
    good_remote = _make_bare_remote(tmp_path / "good-remote.git")
    _wire_remote(good, good_remote)
    good_device_b = _clone_as_second_device(good_remote, tmp_path / "good-device-b")
    (good_device_b / "theirs.md").write_text("# device B\n")
    _git(good_device_b, "add", "-A")
    _git(good_device_b, "commit", "-m", "device B record")
    _git(good_device_b, "push", "origin")

    conflicted = _make_vault(tmp_path / "v-conflicted", dirty=False)
    conflicted_remote = _make_bare_remote(tmp_path / "conflicted-remote.git")
    _wire_remote(conflicted, conflicted_remote)
    other = _clone_as_second_device(conflicted_remote, tmp_path / "conflicted-device-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")
    # Two more commits land on origin AFTER the conflicting one, so a vault
    # whose ref database went stale would report a behind-count below 3.
    (other / "task" / "extra1.md").write_text("extra 1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "extra 1")
    (other / "task" / "extra2.md").write_text("extra 2\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "extra 2")
    _git(other, "push", "origin")
    (conflicted / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(
        config_home, [("good", "default", good), ("conflicted", "product", conflicted)],
    )
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1

    doc = _extract_json_report(r.stdout)
    good_entry = _vault_outcome(doc, "good")
    conflicted_entry = _vault_outcome(doc, "conflicted")
    assert good_entry["outcome"] == "converged"
    assert conflicted_entry["outcome"] == "holding"

    assert _git(good, "status", "--porcelain").stdout.strip() == ""
    assert _git(good, "rev-parse", "HEAD").stdout.strip() == \
        _git(good_remote, "rev-parse", "HEAD").stdout.strip()
    _assert_no_mid_rebase(good)
    _assert_no_mid_rebase(conflicted)

    # The behind-count is current: `git rev-list --count HEAD..origin/<branch>`
    # sees all 3 of device B's commits. The fetch runs before the rebase
    # attempt and `git rebase --abort` leaves remote-tracking refs alone, so a
    # vault parked in `holding` still reports an accurate distance from the
    # published history.
    branch = _git(conflicted, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    behind = _git(conflicted, "rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip()
    assert behind == "3", f"expected the ref database to reflect all 3 remote commits, got {behind}"


def test_render_sync_json_rejects_an_outcome_outside_the_closed_vocabulary():
    """`SYNC_OUTCOMES` is a closed set the schema string promises callers can
    rely on; nothing enforced that promise in code before this fix. A caller
    bug that slips an outcome outside the set must fail loudly here, not
    silently ship a value `--json` consumers were told could never appear."""
    import pytest

    with pytest.raises(ValueError):
        sync_mod.render_sync_json([("default", "not-a-real-outcome", None)])

    # A member of the real vocabulary still renders normally — the input that
    # varies the check's answer.
    doc = sync_mod.render_sync_json([("default", "converged", None)])
    assert doc["vaults"] == [{"vault": "default", "outcome": "converged"}]

    # `awaiting-person` is the newest member of the same closed set, added
    # in both places (`SYNC_OUTCOMES` and the schema string) — this is the
    # guard that keeps them from drifting.
    doc2 = sync_mod.render_sync_json([("default", "awaiting-person", None)])
    assert doc2["vaults"] == [{"vault": "default", "outcome": "awaiting-person"}]
    assert "awaiting-person" in sync_mod._SYNC_REPORT_SCHEMA


def test_render_sync_json_rejects_a_reason_outside_the_closed_vocabulary():
    """`SYNC_FAILURE_REASONS` is a closed set too, gated the same way as
    `SYNC_OUTCOMES` — a reason is meaningful only alongside `holding`, and
    only as one of the two literals a caller was told to expect."""
    import pytest

    with pytest.raises(ValueError):
        sync_mod.render_sync_json([("default", "holding", None, "not-a-real-reason")])

    doc = sync_mod.render_sync_json([("default", "holding", None, "policy-failure")])
    assert doc["vaults"] == [{"vault": "default", "outcome": "holding", "reason": "policy-failure"}]


def test_json_report_parses_and_prose_is_unchanged_without_json(tmp_path):
    """The document parses as JSON and carries the schema string; prose
    output is byte-identical whether or not --json is given."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    default = _make_vault(tmp_path / "v-default", dirty=True)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(default, remote)
    write_vault_config(config_home, [("default", "default", default)])

    r_plain = run_cli(["sync"], config_home=config_home, state_dir=state_dir)
    assert r_plain.returncode == 0, r_plain.stderr

    default2 = _make_vault(tmp_path / "v-default2", dirty=True)
    remote2 = _make_bare_remote(tmp_path / "remote2.git")
    _wire_remote(default2, remote2)
    config_home2 = tmp_path / "config2"
    write_vault_config(config_home2, [("default", "default", default2)])
    r_json = subprocess.run(
        [sys.executable, str(CLI_PATH), "sync", "--json"],
        capture_output=True, text=True,
        env={
            **os.environ,
            "XDG_CONFIG_HOME": str(config_home2), "XDG_STATE_HOME": str(state_dir),
            "HOME": str(state_dir / "home"), "LORE_EMAIL": "tester@example.com",
        },
    )
    assert r_json.returncode == 0, r_json.stderr

    doc = _extract_json_report(r_json.stdout)
    assert doc["schema"] == sync_mod._SYNC_REPORT_SCHEMA
    assert doc["vaults"] == [{"vault": "default", "outcome": "published"}]

    # The prose lines that precede the JSON block, in order, must equal the
    # plain run's full stdout — `--json` adds the document, it changes nothing
    # about what was already printed.
    json_start = r_json.stdout.splitlines().index("{")
    prose_lines = r_json.stdout.splitlines()[:json_start]
    assert "\n".join(prose_lines).strip() == r_plain.stdout.strip()


def test_json_single_vault_host_runs_the_whole_loop(tmp_path):
    """A single-vault host with no peers runs the whole loop, conflict
    handling included — the loop does not require a multi-vault config to
    behave correctly."""
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    solo = _make_vault(tmp_path / "v-solo", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(solo, remote)

    other = _clone_as_second_device(remote, tmp_path / "solo-device-b")
    (other / "task" / "README.md").write_text("edited on device B\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "device B edit")
    _git(other, "push", "origin")
    (solo / "task" / "README.md").write_text("edited on device A\n")

    write_vault_config(config_home, [("solo", "default", solo)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)
    assert r.returncode == 1

    doc = _extract_json_report(r.stdout)
    assert len(doc["vaults"]) == 1
    entry = doc["vaults"][0]
    assert entry["vault"] == "solo"
    assert entry["outcome"] == "holding"
    _assert_no_mid_rebase(solo)


def test_json_terminal_invocation_matches_a_direct_call(tmp_path):
    """Running the loop from a terminal (subprocess, argv, `--json`) produces
    the SAME end vault state as driving `cmd_sync` directly via a bare
    `SimpleNamespace` — the non-CLI shape a future sweep uses, mirroring how
    `flush`'s own tail already drives it. One behavioural test, not a claim."""
    from types import SimpleNamespace

    def _fixture(root: Path):
        vault = _make_vault(root / "v", dirty=False)
        remote = _make_bare_remote(root / "remote.git")
        _wire_remote(vault, remote)
        (vault / "task" / "record.md").write_text("# a record\n")
        return vault, remote

    terminal_vault, terminal_remote = _fixture(tmp_path / "terminal")
    direct_vault, direct_remote = _fixture(tmp_path / "direct")

    terminal_config = tmp_path / "terminal-config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    write_vault_config(terminal_config, [("v", "default", terminal_vault)])
    r = run_cli(["sync", "--json"], config_home=terminal_config, state_dir=state_dir)
    assert r.returncode == 0, r.stderr

    direct_config = tmp_path / "direct-config"
    write_vault_config(direct_config, [("v", "default", direct_vault)])
    old_env = dict(os.environ)
    os.environ["XDG_CONFIG_HOME"] = str(direct_config)
    os.environ["XDG_STATE_HOME"] = str(state_dir)
    os.environ["HOME"] = str(state_dir / "home")
    os.environ["LORE_EMAIL"] = "tester@example.com"
    try:
        rc = sync_mod.cmd_sync(
            SimpleNamespace(vault=None, message=None, pull_only=False)
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    assert rc == 0
    assert _git(terminal_vault, "rev-parse", "HEAD").stdout.strip() == \
        _git(terminal_remote, "rev-parse", "HEAD").stdout.strip()
    assert _git(direct_vault, "rev-parse", "HEAD").stdout.strip() == \
        _git(direct_remote, "rev-parse", "HEAD").stdout.strip()
    assert _git(terminal_vault, "status", "--porcelain").stdout.strip() == ""
    assert _git(direct_vault, "status", "--porcelain").stdout.strip() == ""
    assert _commit_count(terminal_vault) == _commit_count(direct_vault)


def test_a_converged_run_clears_the_failure_marker_it_finds(tmp_path):
    """A failure that is over must stop being reported as current.

    The marker is the durable half of a failure report — written so a person
    or a coordinator can recover what happened after the run that printed it
    exited. It is cleared on a successful push, and on nothing else: a vault
    that reaches its converged ending with nothing left to publish left the
    marker sitting there indefinitely. The holding endings that write no
    marker of their own (an offline hold, a replay conflict inside a finish
    tail) read back whatever is on disk, so a months-old reason could be
    attached verbatim to a later hold that has nothing to do with it.
    """
    config_home = tmp_path / "config"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    vault = _make_vault(tmp_path / "v-default", dirty=False)
    remote = _make_bare_remote(tmp_path / "remote.git")
    _wire_remote(vault, remote)
    _git(vault, "push", "-u", "origin", "HEAD")

    os.environ["XDG_STATE_HOME"] = str(state_dir)
    resolve_state_mod.mark_failed(
        vault, reason="remote-rejection", detail="a forge rejection from an earlier run"
    )
    assert resolve_state_mod.read_failed_marker(vault) is not None

    write_vault_config(config_home, [("default", "default", vault)])
    r = run_cli(["sync", "--json"], config_home=config_home, state_dir=state_dir)

    assert r.returncode == 0, r.stderr
    doc = _extract_json_report(r.stdout)
    assert doc["vaults"][0]["outcome"] == "converged"
    os.environ["XDG_STATE_HOME"] = str(state_dir)
    assert resolve_state_mod.read_failed_marker(vault) is None, (
        "the vault converged, so the failure it once had is over"
    )
