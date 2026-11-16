"""Repositories behind a vault, and the default-branch view of each, as plain data.

Two questions, no record text ever reaching git:

* :func:`vault_repositories` -- which repository roots belong to each vault, read
  from the camp groups whose ``[[lore_scopes]]`` name that vault (every member of
  such a group is one of the vault's repositories). A vault with none carries a
  reason: ``camp-absent`` (trailhead or camp cannot be imported),
  ``config-unreadable`` (a group TOML is malformed or unreadable) or ``unbound``
  (no group names it). It never raises for a missing or broken camp.
* :func:`read_repository` -- one repository's default branch as a
  :class:`Repository` (the ref read, whether it is remote-tracking or local, every
  file AND directory path on it, its top-level entries, its submodule gitlinks and
  when it was last fetched) or an :class:`Unreadable` carrying a reason:
  ``missing``, ``not-a-repository``, ``no-default-branch`` or ``git-failed``.

The ref is the first that exists of ``refs/remotes/origin/HEAD`` (followed),
``refs/remotes/origin/main``, ``refs/remotes/origin/master``, ``refs/heads/main``,
``refs/heads/master``. Only the ref's tree is read, never the working tree or the
index. Directories are paths too, and a submodule gitlink is recorded so a path
beneath it counts as present (:meth:`Repository.contains`).

The last fetch time is the newest modification time among the ``FETCH_HEAD`` files
of the common git dir and of every linked worktree (``<common>/worktrees/*``),
because ``FETCH_HEAD`` is per worktree and fetches run from worktrees.

Git hygiene: every call is list-form argv with ``-C <root>`` (never a shell), no
stdin, a timeout and an output cap, in an environment with every inherited
``GIT_*`` variable removed and ``GIT_OPTIONAL_LOCKS=0``, ``GIT_NO_LAZY_FETCH=1``
and ``GIT_TERMINAL_PROMPT=0`` set. Discovery cannot climb above the root
(``GIT_CEILING_DIRECTORIES`` is the root's parent), so a plain directory inside
another repository is not that repository. Nothing here fetches. The one ref name
git reports back (``origin/HEAD``'s target) must match
:data:`_ORIGIN_REF` with no ``..`` before it goes back to git, as a fully
qualified ref after ``--end-of-options``; otherwise the next fallback is tried.
Any other failure is the reason ``git-failed``, never an exception out of this
module.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..vault import layers
from ..vault.config import normalize_vault_name

CAMP_ABSENT = layers.CAMP_ABSENT
CONFIG_UNREADABLE = layers.CONFIG_UNREADABLE
UNBOUND = "unbound"

MISSING = "missing"
NOT_A_REPOSITORY = "not-a-repository"
NO_DEFAULT_BRANCH = "no-default-branch"
GIT_FAILED = "git-failed"

#: Seconds a single git call may run.
GIT_TIMEOUT_SECONDS = 30.0
#: Largest ``ls-tree`` output, in bytes, that is read.
MAX_TREE_BYTES = 64 * 1024 * 1024

_ORIGIN_REF = "refs/remotes/origin/[A-Za-z0-9._/-]+"
_ORIGIN_PREFIX = "refs/remotes/origin/"
_LOCAL_PREFIX = "refs/heads/"
_ORIGIN_HEAD = "refs/remotes/origin/HEAD"
_FALLBACKS = (
    "refs/remotes/origin/main",
    "refs/remotes/origin/master",
    "refs/heads/main",
    "refs/heads/master",
)


# ---------------------------------------------------------------------------
# Plain data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VaultRepositories:
    """The repository roots of one vault, or the reason there are none.

    ``roots`` are absolute, resolved, de-duplicated and in camp-group order;
    ``reason`` is ``None`` when ``roots`` is non-empty.
    """

    roots: tuple[Path, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class Repository:
    """A repository's default branch as read, without fetching.

    ``ref`` is the full ref read (``refs/remotes/origin/main``); ``branch`` its
    short name; ``local`` is true when no remote-tracking default existed and a
    local branch was read. ``paths`` holds every file and directory path on the
    ref (``tools``, ``tools/lore``, ``tools/lore/x.py``), ``top_level`` the
    entries with no ``/``, ``submodules`` the paths of submodule gitlinks.
    ``fetched_at`` is the newest ``FETCH_HEAD`` modification time as an aware UTC
    datetime, ``None`` when the repository has never been fetched.
    """

    root: Path
    ref: str
    local: bool
    paths: frozenset[str]
    top_level: frozenset[str]
    submodules: frozenset[str]
    fetched_at: datetime | None

    @property
    def branch(self) -> str:
        for prefix in (_ORIGIN_PREFIX, _LOCAL_PREFIX):
            if self.ref.startswith(prefix):
                return self.ref[len(prefix):]
        return self.ref

    def contains(self, path: str) -> bool:
        """True when *path* is on the ref, or lies beneath a submodule gitlink."""
        if path in self.paths:
            return True
        return any(path.startswith(sub + "/") for sub in self.submodules)


@dataclass(frozen=True)
class Unreadable:
    """A repository that could not be read, and why (``reason`` is one of the constants above)."""

    root: Path
    reason: str


# ---------------------------------------------------------------------------
# A vault's repositories
# ---------------------------------------------------------------------------


def _member_root(raw: str, toml_path: str) -> Path:
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute() and toml_path:
        path = Path(toml_path).parent / path
    return path.resolve()


def vault_repositories(
    vault_names: Iterable[str],
    *,
    groups_dir: Path | None = None,
    import_camp: Callable[[], tuple] | None = None,
) -> dict[str, VaultRepositories]:
    """Map each of *vault_names* to its repository roots, or to the reason it has none.

    A group binds a vault when one of its ``[[lore_scopes]]`` names it (names
    compared after ``/`` normalization, as vault election does); every member of
    the group is then a repository of that vault. A relative ``repo_root`` resolves
    against the directory of the group's TOML and ``~`` against ``HOME``, never
    against the cwd. ``groups_dir`` defaults to camp's; ``import_camp`` is the camp
    import seam of :func:`lore.vault.layers.load_camp_groups`.
    """
    names = list(vault_names)
    loaded = layers.load_camp_groups(groups_dir, import_camp=import_camp)
    if loaded.reason is not None:
        return {n: VaultRepositories((), loaded.reason) for n in names}

    roots: dict[str, list[Path]] = {n: [] for n in names}
    for cfg in loaded.configs:
        bound = {normalize_vault_name(ls["name"]) for ls in cfg.get("lore_scopes", [])}
        members = [
            _member_root(m["repo_root"], cfg.get("_toml_path", ""))
            for m in cfg.get("members", [])
        ]
        for name in names:
            if name in bound:
                for root in members:
                    if root not in roots[name]:
                        roots[name].append(root)
    return {
        n: VaultRepositories(tuple(r), None) if r else VaultRepositories((), UNBOUND)
        for n, r in roots.items()
    }


def read_repositories(
    roots: Iterable[Path],
    *,
    env: Mapping[str, str] | None = None,
    read: Callable[..., Repository | Unreadable] | None = None,
) -> dict[Path, Repository | Unreadable]:
    """Read each distinct root once, so a repository bound by several vaults costs one read."""
    reader = read or read_repository
    out: dict[Path, Repository | Unreadable] = {}
    for root in roots:
        if root not in out:
            out[root] = reader(root, env=env)
    return out


# ---------------------------------------------------------------------------
# One repository's default branch
# ---------------------------------------------------------------------------


class _GitFailed(Exception):
    """A git call failed; ``stderr`` is what it said (empty for a timeout or the cap)."""

    def __init__(self, stderr: str = ""):
        super().__init__(stderr)
        self.stderr = stderr


@dataclass(frozen=True)
class _Git:
    root: Path
    env: dict[str, str]
    timeout: float
    max_output_bytes: int

    def run(self, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, bytes]:
        """Run ``git -C <root> <args>``; return (exit code, stdout) for an exit in *ok*.

        Raises :class:`_GitFailed` for any other exit, a timeout, output over the
        cap, or git being unrunnable.
        """
        chunks: list[bytes] = []
        state = {"size": 0, "over": False}
        with tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(
                    ["git", "-C", str(self.root), *args],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=err,
                    env=self.env,
                )
            except OSError as exc:
                raise _GitFailed(str(exc)) from exc

            def drain() -> None:
                assert proc.stdout is not None
                while True:
                    block = proc.stdout.read(65536)
                    if not block:
                        return
                    state["size"] += len(block)
                    if state["size"] > self.max_output_bytes:
                        state["over"] = True
                        proc.kill()
                        return
                    chunks.append(block)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                reader.join()
                raise _GitFailed("timed out") from None
            reader.join()
            if proc.stdout is not None:
                proc.stdout.close()
            if state["over"]:
                raise _GitFailed("output over the cap")
            err.seek(0)
            stderr = err.read().decode("utf-8", "replace")
        if proc.returncode not in ok:
            raise _GitFailed(stderr)
        return proc.returncode, b"".join(chunks)


def _git_env(base: Mapping[str, str] | None, root: Path) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.startswith("GIT_")}
    env.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CEILING_DIRECTORIES=str(root.parent),
        LC_ALL="C",
    )
    return env


def _safe_origin_ref(ref: str) -> bool:
    return (
        re.fullmatch(_ORIGIN_REF, ref) is not None
        and ".." not in ref
        and ref != _ORIGIN_HEAD
    )


def _candidate_refs(git: _Git) -> Iterable[str]:
    # 128: git refuses a symbolic ref it cannot parse (a planted ``..``); that is no usable origin/HEAD.
    code, out = git.run("symbolic-ref", "--quiet", _ORIGIN_HEAD, ok=(0, 1, 128))
    if code == 0:
        target = out.decode("utf-8", "replace").strip()
        if _safe_origin_ref(target):
            yield target
    yield from _FALLBACKS


def _resolve_ref(git: _Git) -> str | None:
    for ref in _candidate_refs(git):
        code, _ = git.run(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}",
            ok=(0, 1),
        )
        if code == 0:
            return ref
    return None


def _fetched_at(common_dir: Path) -> datetime | None:
    candidates = [common_dir / "FETCH_HEAD"]
    try:
        candidates += sorted((common_dir / "worktrees").glob("*/FETCH_HEAD"))
    except OSError:
        pass
    newest = None
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return None if newest is None else datetime.fromtimestamp(newest, timezone.utc)


def _parse_tree(raw: bytes) -> tuple[frozenset[str], frozenset[str]]:
    paths: set[str] = set()
    gitlinks: set[str] = set()
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        meta, _, name = entry.partition(b"\t")
        path = name.decode("utf-8", "surrogateescape")
        paths.add(path)
        if meta.split(b" ")[1:2] == [b"commit"]:
            gitlinks.add(path)
    return frozenset(paths), frozenset(gitlinks)


def read_repository(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = GIT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_TREE_BYTES,
) -> Repository | Unreadable:
    """Read *root*'s default branch without fetching, or say why it cannot be read.

    *env* is the environment git inherits from (default ``os.environ``) minus every
    ``GIT_*`` variable; its ``PATH`` finds git. *timeout* and *max_output_bytes*
    bound each call and the tree listing.
    """
    root = Path(root)
    if not root.is_dir():
        return Unreadable(root, MISSING)
    git = _Git(root, _git_env(env, root), timeout, max_output_bytes)
    try:
        try:
            _, out = git.run("rev-parse", "--git-common-dir")
        except _GitFailed as exc:
            if "not a git repository" in exc.stderr:
                return Unreadable(root, NOT_A_REPOSITORY)
            raise
        common = Path(out.decode("utf-8", "replace").strip())
        common = common if common.is_absolute() else root / common
        ref = _resolve_ref(git)
        if ref is None:
            return Unreadable(root, NO_DEFAULT_BRANCH)
        _, tree = git.run("ls-tree", "-r", "-t", "-z", "--end-of-options", ref)
    except _GitFailed:
        return Unreadable(root, GIT_FAILED)
    paths, gitlinks = _parse_tree(tree)
    return Repository(
        root=root,
        ref=ref,
        local=ref.startswith(_LOCAL_PREFIX),
        paths=paths,
        top_level=frozenset(p for p in paths if "/" not in p),
        submodules=gitlinks,
        fetched_at=_fetched_at(common),
    )
