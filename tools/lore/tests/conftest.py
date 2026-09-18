"""Shared pytest fixtures and import helpers for the lore test suite."""

import atexit
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"

# Makes the `lore` package (plugins/lore/lore/) importable by its dotted name
# — see load_script() — and makes the plugin-root-level `_bootstrap` module
# (the sole remaining bare-stem load target) importable too.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def _isolate_ambient_env(tmp_path, monkeypatch):
    """Pin HOME/XDG_STATE_HOME/XDG_CONFIG_HOME to tmp_path-scoped dirs.

    Autouse guard against any in-process call that reads these vars ambiently
    (the ``env=None`` default — e.g. ``lore.locking.session_write_lock``,
    ``lore.record.store.validate_and_write``) resolving the real
    ``~/.local/state/lore`` or ``~/.config/lore`` on a shell where they are
    unset. Subprocess-based tests are unaffected: ``run_cli`` (below) already
    overrides ``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` explicitly per call,
    which continues to take precedence over these ambient defaults.

    ``XDG_STATE_HOME`` is pinned to ``tmp_path / "state"``, matching the
    suite's existing convention (see ``make_vault`` below) — the same value
    tests that build their own state dir under ``tmp_path`` already use, so
    this fixture never creates the directories itself (state_dir/config_dir
    are documented as pure — "never creates anything on disk" — and several
    tests ``mkdir()`` these paths themselves without ``exist_ok``).
    """
    home = tmp_path / "home"
    state = tmp_path / "state"
    config = tmp_path / "config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))


def write_default_config(config_home: Path, vault_path: Path) -> None:
    """Seed config.json under config_home with a single default-scope vault.

    Writes ``config_home/lore/config.json`` (matching ``_resolve_config_path``'s
    ``XDG_CONFIG_HOME → <home>/lore/config.json`` derivation) so that the CLI
    subprocess resolves the active vault from config.  Idempotent: overwrites any
    existing config.json.

    Config is the only resolution path — ``LORE_VAULT`` is not injected by
    the harnesses, so this seeding is what points the CLI at the test vault.
    """
    write_vault_config(config_home, [("default", "default", vault_path)])


def write_vault_config(config_home: Path, vaults) -> None:
    """Seed config.json under config_home with an arbitrary set of vaults.

    ``vaults`` is an iterable of ``(name, scope, path)`` triples written in order.
    The multi-vault counterpart to :func:`write_default_config`, for tests that
    exercise whole-install behavior — a ``lore sync`` covering every vault, a
    ``lore status`` drift report — where a single-vault config cannot distinguish
    "covered every vault" from "covered the default one".

    The caller owns validity: ``load_config`` still requires exactly one
    ``default``-scope vault, so a test that wants a *rejected* config passes a set
    that deliberately violates that.
    """
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": name, "scope": scope, "path": str(path)}
                    for name, scope, path in vaults
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_cli(args, *, vault, state_dir, stdin_text=None, env_extra=None, cwd=None):
    """Run the lore CLI in this process; returns a CompletedProcess-alike.

    Shared harness for the record/session CLI tests — fences XDG_STATE_HOME +
    XDG_CONFIG_HOME (+ a stable LORE_EMAIL) so tests never touch the real vault,
    state, or config dir. ``env_extra`` overlays extra env vars.

    ``config.json`` pointing at ``vault`` so config-based resolution — the
    only resolution path, since ``LORE_VAULT`` is not injected — resolves to
    the test vault. Because ``record create``/``update``/``delete`` consult
    ``config_dir("lore")/config.json``, an inherited ambient config (e.g. on a CI
    runner) would otherwise reroute records away from the test vault.
    Callers that exercise layered vaults pass their own XDG_CONFIG_HOME via
    ``env_extra`` (applied last, so their config wins over the seeded default).

    ``cwd`` sets the working directory for the call. The group-default routing
    path resolves the active camp group from ``Path.cwd()``, so a test that
    exercises routing inside a bound member repo passes that repo as ``cwd``
    (paired with a ``LORE_GROUPS_DIR`` override via ``env_extra``).

    The call goes through ``lore.cli.dispatch.main`` in this interpreter rather
    than spawning one, which is what the returned object stands in for: the
    fields tests read — ``returncode``, ``stdout``, ``stderr`` — carry what the
    same argv would have produced out of a subprocess, including a traceback on
    stderr and a non-zero code when a command raises. What it does *not* cover
    is the ``cli/lore`` entry script itself, whose own ``sys.path`` bootstrap
    and exec are exercised as real processes by ``test_bin_wrapper``.

    A test whose subject is process-level behaviour — a real file descriptor, a
    signal, a blocking read — needs a real process and calls
    :func:`run_cli_subprocess` (or, for a silent stdin pipe,
    :func:`run_cli_with_silent_pipe`) instead.
    """
    full_env = _cli_env(vault, state_dir, env_extra)
    return _run_cli_in_process(args, full_env, stdin_text, cwd)


def run_cli_subprocess(args, *, vault, state_dir, stdin_text=None, env_extra=None, cwd=None):
    """Run the lore CLI as a real subprocess; returns CompletedProcess.

    The escape hatch from :func:`run_cli` for a test whose subject is something
    only a separate process has — an inherited file descriptor, an exit status
    the shell sees, a stream that is not this interpreter's.
    """
    full_env = _cli_env(vault, state_dir, env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_text,
        cwd=str(cwd) if cwd is not None else None,
    )


def _cli_env(vault, state_dir, env_extra):
    """Build the fenced environment both CLI runners hand the command."""
    full_env = dict(os.environ)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    _xdg_config = Path(state_dir) / "_xdg_config"
    full_env["XDG_CONFIG_HOME"] = str(_xdg_config)
    full_env["LORE_EMAIL"] = "tester@example.com"
    write_default_config(_xdg_config, Path(vault))
    if env_extra:
        full_env.update(env_extra)
    return full_env


class _InProcessResult:
    """The subset of ``CompletedProcess`` the CLI tests read."""

    def __init__(self, args, returncode, stdout, stderr):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_cli_in_process(args, full_env, stdin_text, cwd):
    """Call ``dispatch.main`` under the environment a subprocess would have had.

    Everything a spawned interpreter would give the command for free has to be
    staged by hand here and undone afterwards, because this process outlives the
    call and the next test inherits whatever is left:

    - ``os.environ`` is swapped wholesale, not updated, so a variable the caller
      deliberately left out is absent rather than inherited from this process.
    - stdin is a real OS pipe, pre-filled and closed at the write end. The
      command ``select``s on stdin and asks it for a ``fileno``, which an
      in-memory buffer cannot answer; a pipe that is already at EOF gives the
      same reading as a subprocess whose input was closed.
    - an escaping exception is reported the way the interpreter reports one at
      top level — traceback on stderr, exit code 1 — so a command that refuses
      by raising still looks to the test like the failure a subprocess returned.
    """
    import contextlib
    import io
    import traceback

    from lore.cli import dispatch

    out, err = io.StringIO(), io.StringIO()
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_stdin = sys.stdin
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (stdin_text or "").encode())
    os.close(write_fd)
    try:
        os.environ.clear()
        os.environ.update(full_env)
        if cwd is not None:
            os.chdir(str(cwd))
        sys.stdin = os.fdopen(read_fd, "r")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                returncode = dispatch.main(list(args)) or 0
            except SystemExit as exc:
                returncode = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            except BaseException:  # noqa: BLE001 - mirrors the interpreter's top level
                traceback.print_exc(file=err)
                returncode = 1
    finally:
        sys.stdin.close()
        sys.stdin = saved_stdin
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
    return _InProcessResult(list(args), returncode, out.getvalue(), err.getvalue())


def run_cli_with_silent_pipe(args, *, vault, state_dir, timeout=5.0):
    """Run the CLI with stdin attached to a real, open, never-EOF'ing pipe.

    Regression harness for the "``lore record update`` blocks forever on a
    silent open stdin" class of bug: opens an actual OS pipe via ``os.pipe()``,
    hands the CLI subprocess the *read* end as its stdin, and — deliberately —
    never writes to or closes the *write* end while the subprocess runs. A
    buggy ``sys.stdin.read()`` blocks forever on this; the fix must return
    within ``timeout`` instead.

    Returns ``(CompletedProcess, elapsed_seconds)``. Guards itself with
    ``subprocess.run(..., timeout=timeout)`` so a regression fails this test
    with a clear ``TimeoutExpired`` rather than hanging the whole suite.
    """
    full_env = dict(os.environ)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    _xdg_config = Path(state_dir) / "_xdg_config"
    full_env["XDG_CONFIG_HOME"] = str(_xdg_config)
    full_env["LORE_EMAIL"] = "tester@example.com"
    write_default_config(_xdg_config, Path(vault))

    read_fd, write_fd = os.pipe()
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            capture_output=True,
            text=True,
            env=full_env,
            stdin=read_fd,
            timeout=timeout,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)  # never written to — this is the "silent, open" pipe
    elapsed = time.monotonic() - start
    return proc, elapsed


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(vault_dir, state_dir)`` under ``tmp_path``, creating both."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return vault, state


def load_script(name: str):
    """Load a lore module freshly each call, isolating state across tests.

    ``name`` is either the bare stem ``"_bootstrap"`` (the one plugin-root-level
    module that deliberately sits outside the ``lore`` package — it bootstraps
    that package's own importability) or a dotted path into the ``lore``
    package (e.g. ``"lore.vault.vault"``) for a module that lives there.

    The bare stem loads via ``spec_from_file_location`` + ``exec_module``,
    bypassing the import system entirely — each call gets a brand-new module
    object, deliberately never registered in ``sys.modules``.

    Dotted paths load via ``importlib.import_module`` once, then
    ``importlib.reload`` per call: relative imports (``from . import x``)
    only resolve under the real import system, so the fresh-module-per-test
    isolation the bare-stem path gets from ``exec_module`` comes from
    ``reload`` instead.
    """
    if "." in name:
        mod = importlib.import_module(name)
        return importlib.reload(mod)
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Git-backed vault fixtures
#
# The sync, resolve, pull and lock suites need vaults that are real git
# repositories, and between them build several dozen per run. Built the
# obvious way that is six git subprocesses each — an init, three configs, an
# add, and a commit that fsyncs — so the builders below keep one pristine
# vault per shape and hand out copies.
#
# A shape is (identity, committed): the identity is baked into the commit
# object and the commit into the history, so neither can be applied to a copy
# after the fact. Everything else a caller varies — dirt in the worktree — is
# a file written after the copy lands. Each distinct shape is built once per
# process, and under xdist each worker is its own process.
# ---------------------------------------------------------------------------

_VAULT_TEMPLATES: dict[tuple[tuple[str, str], bool], Path] = {}
_BARE_REMOTE_TEMPLATE: list[Path] = []

DEFAULT_VAULT_IDENTITY = ("t@e.st", "Test")


def _run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True
    )


def _copy_git_tree(template: Path, path: Path) -> Path:
    """Copy *template* to *path*.

    A straight copy is enough here: neither shape records its own location
    anywhere inside it. Git stores paths relative to the repository, and these
    templates carry no remote — the one thing that would write an absolute URL
    into `.git/config`. Callers attach their own remotes afterwards, to
    wherever they actually want them.

    Git's own background maintenance can transiently create and remove lock
    files (e.g. `.git/objects/maintenance.lock`) inside the shared template
    while a copy is in flight, which raced `shutil.copytree` off a listing
    that already had the file. Lock files are ephemeral git-internal state
    that a fresh copy has no use for regardless, so they are excluded from
    the copy outright rather than raced.
    """
    import shutil

    shutil.copytree(
        template, path, symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock")
    )
    return path


def _build_vault_template(path: Path, identity, commit: bool) -> Path:
    """Create the vault `make_git_vault` promises, the long way, via git."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    email, name = identity
    for key, val in (("user.email", email), ("user.name", name), ("commit.gpgsign", "false")):
        _run_git(path, "config", key, val)
    (path / "README.md").write_text("vault\n")
    # Mirrors what `config.installer` scaffolds into every real vault. Lore's
    # write locks are `*.lock` sidecars living inside the vault, so a fixture
    # without this would test a vault shape no install ever has.
    (path / ".gitignore").write_text("*.lock\n")
    if commit:
        _run_git(path, "add", "-A")
        _run_git(path, "commit", "-m", "init")
    return path


def _vault_template(identity, commit: bool) -> Path:
    key = (tuple(identity), commit)
    cached = _VAULT_TEMPLATES.get(key)
    if cached is not None:
        return cached
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="lore-tests-vault-template-"))
    atexit.register(shutil.rmtree, root, True)
    template = _build_vault_template(root / "vault", identity, commit)
    _VAULT_TEMPLATES[key] = template
    return template


def make_git_vault(
    path: Path,
    *,
    identity=DEFAULT_VAULT_IDENTITY,
    commit: bool = True,
    dirty: bool = False,
) -> Path:
    """Return a git vault at *path*: README, `*.lock` ignore, fixed identity.

    ``commit=False`` reproduces the never-committed vault — the state a real
    vault was actually found in: git-init'd, zero commits, records untracked.
    ``dirty=True`` leaves an uncommitted file in the worktree.

    Copied from a per-shape template rather than built call by call. What lands
    is a real repository with its own object store and its own history to
    commit onto; `test_helpers_git_vault` holds that equivalence.
    """
    _copy_git_tree(_vault_template(identity, commit), path)
    if dirty:
        (path / "dirt.md").write_text("# uncommitted\n")
    return path


def make_bare_remote(path: Path) -> Path:
    """Return an empty bare repository at *path*, for use as an origin."""
    if not _BARE_REMOTE_TEMPLATE:
        import shutil
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="lore-tests-bare-template-"))
        atexit.register(shutil.rmtree, root, True)
        bare = root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        _BARE_REMOTE_TEMPLATE.append(bare)
    return _copy_git_tree(_BARE_REMOTE_TEMPLATE[0], path)
