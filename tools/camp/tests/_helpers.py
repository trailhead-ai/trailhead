"""Shared helpers for camp's test suite.

Helpers here are the ones several test modules need identically. A helper that
only one module needs stays in that module; a helper whose callers genuinely
disagree about its behaviour stays split rather than growing a flag per caller.
"""
from __future__ import annotations

import atexit
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "camp"

#: The `camp` entry script, for a caller that wants a real subprocess.
CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

#: Absolute paths git records inside `.git` when a repo is built, keyed by the
#: file that holds them. Copying a repo to a new location leaves these pointing
#: at the original, so `_clone_template` rewrites exactly these two and nothing
#: else — every other path git stores is relative to the repo and survives a
#: move untouched. Rewriting by allowlist rather than scanning `.git` wholesale
#: keeps the substitution away from the object store and the index, where a
#: blind byte replacement would corrupt content it cannot parse.
_PATH_BEARING_GIT_FILES = ("config", "FETCH_HEAD")

#: One built-from-scratch repo per `origin` variant, reused for the life of the
#: process. Building a repo costs seven git subprocesses — an `init`, two
#: `config`s, an `add`, a `commit` that fsyncs, and for `origin` a `remote add`
#: and a `fetch` — which the ~100 call sites here otherwise pay per test. A
#: session-scoped template turns that into one directory copy plus a two-file
#: rewrite. Under xdist each worker is its own process and so builds its own
#: templates: a handful of builds total, rather than one per test.
_TEMPLATES: dict[bool, Path] = {}


def _template(origin: bool) -> Path:
    """Return the cached pristine repo for *origin*, building it on first use."""
    cached = _TEMPLATES.get(origin)
    if cached is not None:
        return cached

    root = Path(tempfile.mkdtemp(prefix="camp-tests-git-template-"))
    atexit.register(shutil.rmtree, root, True)
    repo = root / "repo"
    _build_git_repo(repo, origin=origin)
    _TEMPLATES[origin] = repo
    return repo


def _build_git_repo(path: Path, *, origin: bool) -> None:
    """Create the repo `init_git_repo` promises, the long way, via git itself."""
    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True)

    git("init", "-b", "main", str(path))
    git("-C", str(path), "config", "user.email", "test@test.com")
    git("-C", str(path), "config", "user.name", "Test")
    (path / "README.md").write_text("# test\n")
    git("-C", str(path), "add", "README.md")
    git("-C", str(path), "commit", "-m", "init", "--no-gpg-sign")

    if origin:
        git("-C", str(path), "remote", "add", "origin", str(path))
        git("-C", str(path), "fetch", "origin", "--quiet")


def _clone_template(template: Path, path: Path) -> None:
    """Copy *template* to *path*, repointing the paths git recorded inside it.

    Git's own background maintenance can transiently create and remove lock
    files (e.g. `.git/objects/maintenance.lock`) inside the shared template
    while a copy is in flight, which races `shutil.copytree` off a listing
    that already had the file. Lock files are ephemeral git-internal state
    that a fresh copy has no use for regardless, so they are excluded from
    the copy outright rather than raced.
    """
    shutil.copytree(
        template,
        path,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.lock"),
        # Named explicitly, rather than left to copytree's own default, so a
        # test can monkeypatch `shutil.copy2` and observe it: a default bound
        # at `shutil`'s own definition time is immune to that patch.
        copy_function=shutil.copy2,
    )

    for name in _PATH_BEARING_GIT_FILES:
        recorded = path / ".git" / name
        if not recorded.exists():
            continue
        recorded.write_text(
            recorded.read_text().replace(str(template), str(path))
        )


def init_git_repo(path: Path, *, origin: bool = False) -> None:
    """Create a git repo at *path* with one commit on `main`.

    Commits are made with `--no-gpg-sign` and a fixed identity so the suite does
    not depend on the developer's git configuration.

    With *origin* true the repo additionally gains an `origin` remote pointing at
    itself, fetched so remote-tracking refs exist. Callers that resolve a base
    like `origin/main`, or that exercise fetch, need it. Callers that do not
    leave it off so the repo stays the minimum their test needs — not because a
    remote would break them; it does not.

    The repo is copied from a per-process template rather than built call by
    call. What lands at *path* is a real, independent repository — its own
    object store, its own history to commit onto — and `test_helpers_git_repo`
    holds that equivalence.
    """
    _clone_template(_template(origin), path)


def camp_state_env(tmp_path: Path) -> dict[str, str]:
    """Return an env override pointing `CAMP_STATE_DIR` inside *tmp_path*.

    The directory is created, so a caller can write to it without a further
    mkdir. Callers needing more of the environment merge this into their own
    mapping rather than this helper growing their cases.
    """
    state_root = tmp_path / "camp-state"
    state_root.mkdir(parents=True, exist_ok=True)
    return {"CAMP_STATE_DIR": str(state_root)}


class CampResult:
    """The subset of ``CompletedProcess`` the camp fixtures read."""

    def __init__(self, args, returncode, stdout, stderr):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_camp(argv, *, env):
    """Run a camp command in this process; returns a CompletedProcess-alike.

    For a fixture that wants a command's *effect* — a group on disk, a
    workspace brought up — rather than the fact that a separate process
    produced it. `test_helpers_camp_runner` holds the equivalence that makes
    the substitution sound: the same argv down either route writes the same
    thing and reports the same outcome.

    A test whose subject *is* the separate process — an exit status a shell
    sees, an inherited descriptor, a command that must survive its parent —
    spawns :data:`CLI_CAMP` itself instead.

    `env` replaces the environment wholesale for the duration of the call, the
    way a subprocess's would, so a variable the caller left out is absent
    rather than inherited from the test process. `sys.argv` is staged the same
    way, because camp's entry point reads it directly. An escaping exception
    is reported as the interpreter would report it at top level — traceback on
    stderr, exit code 1 — so a command that refuses by raising still looks to
    the caller like the subprocess failure it stands in for.
    """
    from camp.cli import dispatch

    out, err = io.StringIO(), io.StringIO()
    saved_env = dict(os.environ)
    saved_argv = list(sys.argv)
    try:
        os.environ.clear()
        os.environ.update(env)
        sys.argv = ["camp", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                dispatch.main()
                returncode = 0
            except SystemExit as exc:
                returncode = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            except BaseException:  # noqa: BLE001 - mirrors the interpreter's top level
                traceback.print_exc(file=err)
                returncode = 1
    finally:
        sys.argv = saved_argv
        os.environ.clear()
        os.environ.update(saved_env)
    return CampResult(list(argv), returncode, out.getvalue(), err.getvalue())


def call_and_exit_code(fn, *args, **kwargs) -> int:
    """Call *fn* and normalize its outcome to an exit code.

    camp's CLI handlers exit via ``sys.exit`` on a refusal but return
    normally (no exception at all) on success — this lets a caller compare
    "exit status" uniformly across both instead of special-casing which path
    a command took.
    """
    try:
        fn(*args, **kwargs)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def write_valid_window_record(ws_dir: Path) -> None:
    """Write a well-formed windows.json with one entry into *ws_dir*."""
    from camp.group.window_record import (
        WindowEntry,
        window_record_path_for,
        write_window_record,
    )

    write_window_record(
        window_record_path_for(ws_dir),
        [WindowEntry(window_id="@1", name="main", cwd=".", conversation_id="conv-abc")],
    )


def _valid_window_record_bytes(ws_dir: Path) -> bytes:
    from camp.group.window_record import window_record_path_for

    write_valid_window_record(ws_dir)
    path = window_record_path_for(ws_dir)
    data = path.read_bytes()
    path.unlink()
    return data


def write_corrupt_window_record(ws_dir: Path) -> None:
    """Write a windows.json at *ws_dir* that is present but not parseable JSON."""
    from camp.group.window_record import window_record_path_for

    path = window_record_path_for(ws_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json at all")


def write_truncated_window_record(ws_dir: Path) -> None:
    """Write a windows.json at *ws_dir* that is a byte-prefix of a valid one.

    A truncated write (process killed mid-``write``) must read as unreadable,
    never as an accidentally-valid empty record — so this is a distinct
    fixture from :func:`write_corrupt_window_record`'s arbitrary garbage.
    """
    from camp.group.window_record import window_record_path_for

    full = _valid_window_record_bytes(ws_dir)
    path = window_record_path_for(ws_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(full[: len(full) // 2])


#: The record conditions a workspace-reading verb must answer identically for.
#: ``missing`` is the baseline every other state is compared against.
WINDOW_RECORD_STATES = ("missing", "valid", "corrupt", "truncated")


def seed_window_record(ws_dir: Path, state: str) -> None:
    """Put *ws_dir*'s windows.json into *state*.

    ``missing`` writes nothing — the caller's fresh workspace already has no
    record. Any other name is a typo in the caller's state list and raises
    rather than silently seeding nothing, which would turn a state the test
    believes it covered into a second copy of the baseline.
    """
    if state == "missing":
        return
    if state == "valid":
        write_valid_window_record(ws_dir)
    elif state == "corrupt":
        write_corrupt_window_record(ws_dir)
    elif state == "truncated":
        write_truncated_window_record(ws_dir)
    else:
        raise ValueError(f"unknown window-record state {state!r}")


def assert_identical_across_record_states(
    results: dict[str, tuple[int, str, str]],
    *,
    verb: str,
    compare_stderr: bool = False,
) -> None:
    """Assert every entry of *results* matches the ``missing`` baseline.

    *results* maps a :data:`WINDOW_RECORD_STATES` name to the
    ``(exit_code, stdout, stderr)`` that running *verb* produced against a
    workspace in that state. The property is that a workspace command which
    does not read the window record cannot be made to answer differently by
    one — and in particular never answers with a traceback.

    *compare_stderr* additionally pins stderr byte-equality. It is off by
    default because several of these verbs legitimately emit path- or
    environment-dependent stderr that is normalized per caller; a caller that
    CAN compare stderr must pass ``True`` rather than rely on the default,
    since silently dropping that comparison is how this check gets weaker
    without anyone noticing.
    """
    baseline_code, baseline_out, baseline_err = results["missing"]
    assert baseline_code == 0, f"baseline (no record) unexpectedly failed: {baseline_err}"
    for state, (code, out, err) in results.items():
        if state == "missing":
            continue
        assert code == baseline_code, (
            f"camp {verb} exit status differs for a {state} window record: "
            f"{code} != {baseline_code} (stderr: {err!r})"
        )
        assert out == baseline_out, (
            f"camp {verb} stdout differs for a {state} window record: "
            f"{out!r} != {baseline_out!r}"
        )
        if compare_stderr:
            assert err == baseline_err, (
                f"camp {verb} stderr differs for a {state} window record: "
                f"{err!r} != {baseline_err!r}"
            )
        assert "Traceback" not in err, (
            f"camp {verb} printed a raw traceback for a {state} window record: {err!r}"
        )
