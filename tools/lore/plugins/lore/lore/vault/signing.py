"""Host signing configuration — the file-backed key vault commits sign with.

A host that runs lore unattended (the sweep, an automatic publish, a session
flush with nobody watching) cannot answer a passphrase prompt. This module
reads a small, host-local configuration naming an unencrypted SSH private key
and turns it into the ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/
``GIT_CONFIG_VALUE_n`` environment overrides that make every git commit lore
makes or replays sign with that key instead — no agent, no tty, no passphrase.

**Where the configuration lives.** :func:`signing_dir` is
``state_dir("lore")/signing``, honoring ``XDG_STATE_HOME``/``HOME`` overrides
exactly like ``lore/cli/common.py``'s ``_resolve_lore_state_dir`` and
``lore/vault/config.py``'s ``_resolve_vaults_root`` — duplicated here rather
than imported, matching this codebase's existing per-module copy of that
resolver rather than reaching across the ``cli`` → ``vault`` import direction.
Inside that directory:

- :data:`CONFIG_FILENAME` (``signing.json``) — a JSON object with one field,
  :data:`KEY_PATH_FIELD` (``"key-path"``), naming the absolute path of the
  private key to sign with. The key may live anywhere on disk (lore's own
  signing directory or an operator-adopted path elsewhere); this module treats
  both identically and never copies it. The key must have no passphrase —
  that is what lets it sign with no agent and no tty.
- :data:`ALLOWED_SIGNERS_FILENAME` (``allowed_signers``) — the allowed-signers
  file `gpg.ssh.allowedSignersFile` points at, written beside the key
  configuration. Each line carries a `namespaces="git"` option, restricting
  the entry to the signature namespace git itself signs commits under, so it
  can never also validate a signature made for an unrelated purpose with the
  same key.

Enabling and inspecting this configuration (``lore signing enable``/
``status``) is a separate module's job; this one only reads what is there.

**The override contract.** :func:`apply_env_overrides` is the single function
every signing call site uses. It returns *base_env* unchanged — same content,
so a caller that always calls it sees no behavioral difference from not
calling it at all — whenever :func:`load_key_path` returns ``None``, the
returned path does not name an existing regular file, or that file's mode is
wider than ``0600``: a missing, absent, malformed, dangling, or over-wide
configuration means git runs exactly as it does with no configuration at all,
honoring the adopter's own signing setup — the same condition
``describe_status`` already refuses with a ``chmod`` remedy, so this hot path
must agree rather than sign with a key ``status`` calls broken. When a usable key is
present, it appends five entries (``gpg.format=ssh``, ``user.signingkey=<key
path>``, ``commit.gpgsign=true``, ``gpg.ssh.allowedSignersFile=<allowed-signers
path>``, ``gpg.ssh.program=ssh-keygen``) after whatever ``GIT_CONFIG_*``
entries *base_env* already carries, raising ``GIT_CONFIG_COUNT`` to cover
them. The fifth entry pins the SSH signing helper itself: without it, an
adopter's own ``gpg.ssh.program`` (a 1Password ``op-ssh-sign`` shim, for
instance) would still run and could fail or hijack the signature even though
``user.signingkey`` and the other three entries point at lore's own key. git
applies later entries last, so lore's signing keys win over the adopter's own
config (global or repo) without this module ever writing to a vault's git
config, while any operator-supplied override with an unrelated key stays in
effect. An inherited ``GIT_CONFIG_COUNT`` is not trusted as an index base —
lore's five entries are written starting at index 0, replacing whatever the
inherited count claimed to enumerate — when it does not parse as a
non-negative integer, or when it claims an entry (``GIT_CONFIG_KEY_<i>`` for
some ``i`` below the count) that is not actually present: git itself aborts
every config read with "fatal: unable to parse command-line config" against
such a gap, so trusting it would break every git call this module makes, not
just signing.

**A key must never live inside a vault.** ``lore sync``'s untracked-file
allowlist auto-stages new content under a record-kind directory or ``sites/``
inside any configured vault, so a key placed there would be committed and
pushed, leaking it. :func:`validate_adoptable_key` refuses to adopt such a
path (checked against the RESOLVED path, following symlinks), and
:func:`describe_status` reports an already-configured key sitting inside a
vault as failing, even though ``enable --key`` itself is the only path that
would normally reach that state and already refuses it — a hand-edited
configuration must be caught the same way.

**A limit this module cannot fix.** A parent process that already invoked
``git -c key=value`` encodes that override in ``GIT_CONFIG_PARAMETERS``, a
separate environment channel git consults ahead of the
``GIT_CONFIG_COUNT``/``KEY``/``VALUE`` triples this module writes. When the
parent's ``-c`` names the same git config key lore also overrides (rare, but
possible for a caller that already passes ``-c user.signingkey=...`` or
similar), the parent's value wins regardless of what this module appends —
there is no environment-only way to outrank ``GIT_CONFIG_PARAMETERS`` from
outside the process that set it.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path

#: Filename holding the signing configuration inside :func:`signing_dir`.
CONFIG_FILENAME = "signing.json"

#: Filename of the allowed-signers file `gpg.ssh.allowedSignersFile` reads,
#: written beside :data:`CONFIG_FILENAME` inside :func:`signing_dir`.
ALLOWED_SIGNERS_FILENAME = "allowed_signers"

#: The JSON field in :data:`CONFIG_FILENAME` naming the private key's
#: absolute path.
KEY_PATH_FIELD = "key-path"

#: Filename of the key ``enable`` generates when run with no ``--key``.
GENERATED_KEY_FILENAME = "id_ed25519"

#: Timeout for every ``ssh-keygen`` subprocess this module runs. These calls
#: never consult an agent or a tty (they operate on a file path with an
#: explicit, possibly-empty passphrase), so a hang here would mean a stuck
#: binary, not a stuck prompt — bounded the same as every other lore
#: subprocess call.
_SSH_KEYGEN_TIMEOUT = 15


def signing_dir(env: "dict | None" = None) -> Path:
    """Return ``state_dir("lore")/signing``, honoring XDG overrides.

    Mirrors ``lore.vault.config._resolve_vaults_root``: lazy-import
    ``_bootstrap`` + ``trailhead.paths``, catch ``(ImportError, SystemExit)``
    and fall back to the plain XDG default so this works in a vanilla
    checkout with no trailhead install.

    Args:
        env: Optional environment dict for test isolation (``HOME``,
             ``XDG_STATE_HOME``). When ``None``, ``os.environ`` is used.
    """
    try:
        import _bootstrap

        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths

        if env is not None:
            return _paths.state_dir("lore", env=env) / "signing"
        return _paths.state_dir("lore") / "signing"
    except (ImportError, SystemExit):
        base = env.get("XDG_STATE_HOME", "") if env else os.environ.get("XDG_STATE_HOME", "")
        if base and os.path.isabs(base):
            return Path(base) / "lore" / "signing"
        home = env.get("HOME", "") if env else ""
        if home:
            return Path(home) / ".local" / "state" / "lore" / "signing"
        return Path.home() / ".local" / "state" / "lore" / "signing"


def load_key_path(env: "dict | None" = None) -> "Path | None":
    """Return the configured signing key's path, or ``None``.

    ``None`` covers every way the configuration can fail to name a usable
    key: no :data:`CONFIG_FILENAME` present, unreadable, malformed JSON, a
    JSON value that is not an object, or a missing/non-string
    :data:`KEY_PATH_FIELD`. This function does **not** check that the named
    file exists on disk — :func:`apply_env_overrides` does that, because
    "the config names a key" and "the key is usable right now" are different
    facts a caller may want separately (``lore signing status`` reports both).
    """
    config_path = signing_dir(env) / CONFIG_FILENAME
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    key_path = data.get(KEY_PATH_FIELD)
    if not isinstance(key_path, str) or not key_path:
        return None
    return Path(key_path)


def _override_entries(env: "dict | None" = None) -> "list[tuple[str, str]] | None":
    """Return the four override entries, or ``None`` if no usable key exists.

    "Usable" requires mode no wider than ``0600`` in addition to existing as a
    regular file: a key widened to, say, ``0644`` is exactly the state
    ``describe_status`` already refuses with a ``chmod`` remedy, and this hot
    path must agree — applying the override anyway would sign with a key
    ``status`` calls broken, and silently succeeding would hide the drift
    from the operator who never sees a failure to investigate.
    """
    key_path = load_key_path(env)
    if key_path is None or not key_path.is_file():
        return None
    if stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        return None
    allowed_signers = signing_dir(env) / ALLOWED_SIGNERS_FILENAME
    return [
        ("gpg.format", "ssh"),
        ("user.signingkey", str(key_path)),
        ("commit.gpgsign", "true"),
        ("gpg.ssh.allowedSignersFile", str(allowed_signers)),
        ("gpg.ssh.program", "ssh-keygen"),
    ]


def _inherited_git_config_count(base_env: dict) -> int:
    """Return the trustworthy inherited ``GIT_CONFIG_COUNT``, or ``0``.

    ``0`` covers "absent" as well as "malformed": non-numeric, negative, or
    claiming an entry (some ``GIT_CONFIG_KEY_<i>`` or ``GIT_CONFIG_VALUE_<i>``
    for ``i`` below the count) that is not actually present in *base_env*.
    Both halves of the pair are checked — a count that claims a key with no
    matching value is exactly as fatal to git's own parser as a missing key,
    since it is the same "no entry at this index" gap either way. That last
    case is not a
    theoretical nicety — git itself refuses to read ANY config, for this call
    or any other, against a ``GIT_CONFIG_COUNT`` with a gap in its indices
    ("fatal: unable to parse command-line config"), so an ungapped count is
    required for this module's own git calls to work at all, not just for
    signing to apply cleanly. In the malformed case lore's own entries are
    written starting at index 0, which is what "replaced by lore's own
    entries alone" means: any ``GIT_CONFIG_KEY_0``/``VALUE_0`` the malformed
    count could not itself make trustworthy gets overwritten rather than
    read.
    """
    raw = base_env.get("GIT_CONFIG_COUNT", "")
    try:
        count = int(raw)
    except ValueError:
        return 0
    if count < 0:
        return 0
    for i in range(count):
        if f"GIT_CONFIG_KEY_{i}" not in base_env or f"GIT_CONFIG_VALUE_{i}" not in base_env:
            return 0
    return count


def apply_env_overrides(base_env: dict, *, env: "dict | None" = None) -> dict:
    """Return a copy of *base_env* with the host signing overrides appended.

    Returns a plain copy of *base_env* — same content, unmodified — when
    :func:`load_key_path` (resolved against *env*, the signing-config
    lookup environment — distinct from *base_env*, the git-call environment
    being built) names no usable key. See the module docstring for the
    appended-entries contract when a key is present.
    """
    entries = _override_entries(env)
    result = dict(base_env)
    if entries is None:
        return result
    start = _inherited_git_config_count(base_env)
    result["GIT_CONFIG_COUNT"] = str(start + len(entries))
    for offset, (key, value) in enumerate(entries):
        result[f"GIT_CONFIG_KEY_{start + offset}"] = key
        result[f"GIT_CONFIG_VALUE_{start + offset}"] = value
    return result


class SigningEnableError(Exception):
    """A refusal to enable signing with the requested key — nothing written.

    Every message names the reason and the remedy, so ``cli/signing.py`` can
    print it verbatim behind lore's ``lore: <message>`` error shape.
    """


class EnableResult:
    """The outcome of :func:`enable`, for the CLI layer to report."""

    def __init__(self, *, key_path: Path, pub_line: str, generated: bool) -> None:
        self.key_path = key_path
        self.pub_line = pub_line
        self.generated = generated


def _probe_key(path: Path) -> "tuple[str | None, bool]":
    """Return ``(public key line, needs_passphrase)`` for the key at *path*.

    Attempts to load *path* with an empty passphrase via ``ssh-keygen -y``.
    On success, the public key line (type + base64 + comment, exactly as
    ``ssh-keygen -y`` prints it) is returned and ``needs_passphrase`` is
    ``False``. On failure the public key line is ``None`` and
    ``needs_passphrase`` distinguishes "this key needs a passphrase we don't
    have" from "this is not a private key ssh-keygen can load at all" —
    ``ssh-keygen``'s own stderr says "incorrect passphrase" only for the
    former.

    Callers must have already confirmed *path* is a regular file at mode
    ``0600`` or tighter: a wider mode makes ``ssh-keygen`` refuse to even
    attempt the load, with a "bad permissions" message that would otherwise
    be misread as "not a private key".
    """
    result = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
        capture_output=True, text=True, timeout=_SSH_KEYGEN_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return result.stdout.strip(), False
    needs_passphrase = "incorrect passphrase" in result.stderr.lower()
    return None, needs_passphrase


def _fingerprint(path: Path) -> "str | None":
    result = subprocess.run(
        ["ssh-keygen", "-lf", str(path)],
        capture_output=True, text=True, timeout=_SSH_KEYGEN_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _configured_vault_roots(env: "dict | None" = None) -> "list[tuple[str, Path]]":
    """Return ``[(vault name, vault root), …]`` for every vault this host
    knows about — the vault-side equivalent of ``cli/common.py``'s
    ``_resolve_all_vaults``, duplicated here rather than imported for the
    same reason :func:`signing_dir` duplicates its own state-dir resolver:
    this module lives under ``vault/`` and must not import from ``cli/``
    (see the module docstring's note on the import direction).

    Used only to refuse or flag a signing key that sits inside a vault
    working tree: such a key is auto-staged by ``lore sync``'s untracked
    allowlist (a record-kind directory, or ``sites/``) and pushed to the
    vault's remote, leaking the private key.
    """
    from . import config as vault_config_mod

    config_path = vault_config_mod._resolve_config_path(env=env)
    floor = [("default", Path(vault_config_mod.resolve_active_vault(env=env)))]
    if not config_path.exists():
        return floor
    try:
        vaults = vault_config_mod.load_config(str(config_path), env=env)
    except (vault_config_mod.VaultConfigError, OSError, ValueError):
        return floor
    return [(v.name, Path(v.path)) for v in vaults]


def _vault_containing(path: Path, env: "dict | None" = None) -> "tuple[str, Path] | None":
    """Return ``(vault name, resolved vault root)`` when *path* — resolved,
    following symlinks — lies at or inside any configured vault's working
    tree; ``None`` otherwise.

    A segment-wise containment check (``Path.resolve()`` plus membership in
    ``.parents``), never a string-prefix comparison: a vault rooted at
    ``/data/vault`` must not treat ``/data/vault-other`` as contained within
    it, which a naive ``str.startswith`` would.
    """
    resolved = path.resolve()
    for name, root in _configured_vault_roots(env):
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return name, root_resolved
    return None


def _classify_key_path(path: Path) -> str:
    """Return ``"present"``, ``"missing"``, or ``"unreadable"`` for *path*.

    ``Path.is_file()`` returns ``False`` both when *path* does not exist and
    when a ``PermissionError`` prevents even stat'ing it (a behavior change
    in Python 3.14) — collapsing "there is no key here" into "there is a key
    here this process cannot see". Left uncorrected, an adopted key sitting
    in a directory this process cannot read would look identical to no key
    being configured at all, and a bare ``enable`` would silently fall back
    to generating or switching to a different key instead of refusing.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unreadable"
    return "present" if stat.S_ISREG(st.st_mode) else "missing"


def _absolute_expanded(raw: "str | Path") -> Path:
    """Expand ``~`` and make *raw* absolute, without resolving symlinks.

    Deliberately not ``Path.resolve()``, which would also collapse symlinks
    the operator may have pointed at a key elsewhere.
    """
    expanded = os.path.expanduser(str(raw))
    return Path(os.path.abspath(expanded))


def validate_adoptable_key(raw: "str | Path", env: "dict | None" = None) -> Path:
    """Return *raw*, expanded/absolute, after confirming it is adoptable.

    Raises :class:`SigningEnableError` — naming the reason and the remedy —
    for a path that is missing, lies inside a configured vault's working
    tree, is not a regular file, readable by anyone but its owner, or a key
    ``ssh-keygen`` cannot load with an empty passphrase (including one that
    needs a real passphrase, which cannot sign with nobody present on any
    platform). Performs no writes; a caller building on this can validate
    before touching disk.

    The vault-containment check runs before every other check but existence:
    it is checked against the RESOLVED path (following symlinks), separate
    from the returned, unresolved *path* — this function deliberately does
    not resolve symlinks in what it returns, so an operator pointing --key at
    a symlink elsewhere on disk keeps working, but a key reachable via a
    symlink placed inside a vault must not escape the refusal that way.
    """
    path = _absolute_expanded(raw)
    if not path.exists():
        raise SigningEnableError(
            f"{path} does not exist — point --key at an existing private key, "
            "or run `lore signing enable` with no argument to generate one"
        )
    contained = _vault_containing(path, env)
    if contained is not None:
        vault_name, vault_root = contained
        raise SigningEnableError(
            f"{path} is inside vault {vault_name!r} ({vault_root}) — a key "
            "there gets staged and pushed by `lore sync`; move it outside "
            "any configured vault, then run `lore signing enable --key "
            "<new path>`"
        )
    if not path.is_file():
        raise SigningEnableError(
            f"{path} is not a private key — point --key at a regular SSH "
            "private key file"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SigningEnableError(
            f"{path} is readable by others (mode {oct(mode)[-3:]}) — run "
            f"`chmod 600 {path}`"
        )
    pub_line, needs_passphrase = _probe_key(path)
    if pub_line is None:
        if needs_passphrase:
            raise SigningEnableError(
                f"{path} needs a passphrase, which cannot sign with nobody "
                "present — use a key without one, or run `lore signing "
                "enable` with no argument to generate one"
            )
        raise SigningEnableError(
            f"{path} is not a private key ssh-keygen can load — point --key "
            "at a regular SSH private key file"
        )
    return path


def _resolve_principal() -> str:
    """Return the allowed-signers principal: the resolved committer email,
    falling back to the hostname when no git identity resolves."""
    from . import vault as vault_mod

    email = vault_mod.resolve_committer_email()
    return email or socket.gethostname()


def _ensure_signing_dir(directory: Path) -> None:
    """Create *directory* at mode ``0700``, refusing a symlinked path.

    ``directory.is_symlink()`` uses ``lstat`` (never follows), so a symlink
    planted at the signing directory's own path is refused before this
    function ever creates or chmods anything through it — creating through a
    symlink would write and tighten permissions on whatever it points at
    instead of a directory lore actually owns.
    """
    if directory.is_symlink():
        raise SigningEnableError(
            f"{directory} is a symlink — remove it and let lore create a "
            "plain directory there"
        )
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise SigningEnableError(
            f"cannot set permissions on {directory}: {exc}"
        ) from exc


def _write_configuration(directory: Path, key_path: Path, pub_line: str) -> None:
    """Point :data:`CONFIG_FILENAME` and :data:`ALLOWED_SIGNERS_FILENAME` at
    *key_path*, creating *directory* at mode ``0700`` if needed."""
    from ..record.store import write_temp_then_rename

    _ensure_signing_dir(directory)
    write_temp_then_rename(
        directory / CONFIG_FILENAME,
        json.dumps({KEY_PATH_FIELD: str(key_path)}, indent=2) + "\n",
    )
    write_temp_then_rename(
        directory / ALLOWED_SIGNERS_FILENAME,
        f'{_resolve_principal()} namespaces="git" {pub_line}\n',
    )


def _generate_key(dest: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(dest), "-C", socket.gethostname()],
        check=True, capture_output=True, text=True, timeout=_SSH_KEYGEN_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    os.chmod(dest, 0o600)


def _usable_pub_line_or_refusal(path: Path) -> "tuple[str | None, str | None]":
    """Return ``(pub_line, refusal_message)`` for a key file that already
    exists on disk.

    ``pub_line`` is not ``None`` exactly when *path* loads with no
    passphrase. Otherwise ``refusal_message`` names the reason and the
    remedy for refusing to touch *path* — this function never generates over
    or beside an existing file, so a caller reaching a non-``None`` refusal
    must stop rather than fall through to :func:`_generate_key`.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return None, (
            f"{path} is readable by others (mode {oct(mode)[-3:]}) — run "
            f"`chmod 600 {path}`"
        )
    pub_line, needs_passphrase = _probe_key(path)
    if pub_line is not None:
        return pub_line, None
    if needs_passphrase:
        return None, (
            f"{path} needs a passphrase, which cannot sign with nobody "
            "present — replace it with a key that has none, or point --key "
            "at a different key"
        )
    return None, (
        f"{path} is not a private key ssh-keygen can load — replace it, or "
        "point --key at a different key"
    )


def enable(*, key: "str | Path | None" = None, env: "dict | None" = None) -> EnableResult:
    """Implement ``lore signing enable`` — adopt or generate the host key.

    With *key* given, validates it (:func:`validate_adoptable_key`) BEFORE
    any write — a refused adoption leaves :data:`CONFIG_FILENAME` and
    :data:`ALLOWED_SIGNERS_FILENAME` untouched, whether or not they already
    existed from a prior ``enable``. On success it rewrites both files to
    name the adopted key, leaving the key file itself byte-for-byte and
    mode unchanged, and leaving any previously generated key on disk.

    With no *key*, an already-configured, still-loadable key is left alone
    (re-writing the same config/allowed-signers content is a no-op in
    substance: the fingerprint does not change). When the configured key is
    missing or there is no configuration at all, and a key file already sits
    at :data:`GENERATED_KEY_FILENAME` (the default path a prior no-argument
    ``enable`` would have written), that file is reused if it still loads
    with no passphrase — the configuration is simply rewritten to name it
    again. A key file present at either location but unusable (needs a
    passphrase, wrong mode, or not a key ``ssh-keygen`` can load at all)
    raises :class:`SigningEnableError` rather than being silently generated
    over or beside: this function never runs ``ssh-keygen -f`` against a path
    it has not first confirmed is empty, since that call would otherwise
    either fail outright or hang on ssh-keygen's own hidden overwrite prompt.
    A key is generated fresh at :data:`GENERATED_KEY_FILENAME` only when
    nothing exists there yet.
    """
    directory = signing_dir(env)

    if key is not None:
        resolved = validate_adoptable_key(key, env=env)
        pub_line, _ = _probe_key(resolved)
        _write_configuration(directory, resolved, pub_line)
        return EnableResult(key_path=resolved, pub_line=pub_line, generated=False)

    _ensure_signing_dir(directory)

    existing = load_key_path(env)
    if existing is not None:
        classification = _classify_key_path(existing)
        if classification == "unreadable":
            raise SigningEnableError(
                f"cannot check {existing} — permission denied — fix its "
                "permissions or its directory's, or point --key at a "
                "different key"
            )
        if classification == "present":
            pub_line, refusal = _usable_pub_line_or_refusal(existing)
            if pub_line is not None:
                _write_configuration(directory, existing, pub_line)
                return EnableResult(key_path=existing, pub_line=pub_line, generated=False)
            raise SigningEnableError(refusal)

    dest = directory / GENERATED_KEY_FILENAME
    if dest.exists():
        if not dest.is_file():
            raise SigningEnableError(
                f"{dest} is not a private key — remove it, or point --key at "
                "a different key"
            )
        pub_line, refusal = _usable_pub_line_or_refusal(dest)
        if pub_line is not None:
            _write_configuration(directory, dest, pub_line)
            return EnableResult(key_path=dest, pub_line=pub_line, generated=False)
        raise SigningEnableError(refusal)

    _generate_key(dest)
    pub_line, _ = _probe_key(dest)
    _write_configuration(directory, dest, pub_line)
    return EnableResult(key_path=dest, pub_line=pub_line, generated=True)


def git_requires_signing() -> bool:
    """Whether this user's own git configuration signs every commit.

    Reads ``commit.gpgsign`` from the system and global scopes (run from
    ``/`` so no repository's local config answers). When the answer cannot
    be read, assume signing is required, so the caller keeps pointing at
    ``lore signing enable`` rather than reporting everything is fine.
    """
    try:
        proc = subprocess.run(
            ["git", "config", "--type=bool", "--get", "commit.gpgsign"],
            cwd="/", capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=_SSH_KEYGEN_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if proc.returncode == 1 and not proc.stdout.strip():
        return False
    if proc.returncode != 0:
        return True
    return proc.stdout.strip() == "true"


def describe_status(env: "dict | None" = None) -> "tuple[bool, str]":
    """Return ``(ok, message)`` describing whether this host signs unattended.

    *message* never carries the ``lore: `` prefix (both ``lore signing
    status`` and ``lore status``'s per-host line add their own); on failure
    it always ends with the command that fixes it, matching ``lore
    status``'s existing drift-remedy pattern.
    """
    if shutil.which("ssh-keygen") is None:
        return False, "ssh-keygen is not on PATH — install OpenSSH's client tools"

    key_path = load_key_path(env)
    if key_path is None:
        return False, "no signing key is configured — run `lore signing enable`"

    classification = _classify_key_path(key_path)
    if classification == "unreadable":
        return False, (
            f"the configured key {key_path} cannot be read (permission "
            "denied) — fix its permissions or its directory's, or run "
            "`lore signing enable --key <path>` naming a different key"
        )
    if classification == "missing":
        return False, (
            f"the configured key {key_path} is missing — run `lore signing enable`"
        )

    contained = _vault_containing(key_path, env)
    if contained is not None:
        vault_name, vault_root = contained
        return False, (
            f"the configured key {key_path} is inside vault {vault_name!r} "
            f"({vault_root}) — move it outside any configured vault, then "
            "run `lore signing enable --key <new path>`"
        )

    mode = stat.S_IMODE(key_path.stat().st_mode)
    if mode & 0o077:
        return False, (
            f"the configured key {key_path} is readable by others — run "
            f"`chmod 600 {key_path}`"
        )

    pub_line, needs_passphrase = _probe_key(key_path)
    if pub_line is None:
        if needs_passphrase:
            return False, (
                "the configured key needs a passphrase, which cannot sign "
                "with nobody present — replace it with a key that has none, "
                "or run `lore signing enable --key <path>` naming one"
            )
        return False, (
            "the configured key is not a private key ssh-keygen can load — "
            "replace it, or run `lore signing enable --key <path>` naming "
            "one that is"
        )

    allowed_signers = signing_dir(env) / ALLOWED_SIGNERS_FILENAME
    if not allowed_signers.is_file():
        return False, "the allowed-signers file is missing — run `lore signing enable`"

    try:
        allowed_signers_text = allowed_signers.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, (
            "the allowed-signers file does not match the configured key — "
            "run `lore signing enable`"
        )

    key_material = " ".join(pub_line.split(" ")[:2])
    if key_material not in allowed_signers_text:
        return False, (
            "the allowed-signers file does not match the configured key — "
            "run `lore signing enable`"
        )

    fingerprint = _fingerprint(key_path) or pub_line
    return True, f"this host signs unattended (key {fingerprint})"
