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
  configuration.

Enabling and inspecting this configuration (``lore signing enable``/
``status``) is a separate module's job; this one only reads what is there.

**The override contract.** :func:`apply_env_overrides` is the single function
every signing call site uses. It returns *base_env* unchanged — same content,
so a caller that always calls it sees no behavioral difference from not
calling it at all — whenever :func:`load_key_path` returns ``None`` or the
returned path does not name an existing file: a missing, absent, malformed, or
dangling configuration means git runs exactly as it does with no configuration
at all, honoring the adopter's own signing setup. When a usable key is
present, it appends four entries (``gpg.format=ssh``, ``user.signingkey=<key
path>``, ``commit.gpgsign=true``, ``gpg.ssh.allowedSignersFile=<allowed-signers
path>``) after whatever ``GIT_CONFIG_*`` entries *base_env* already carries,
raising ``GIT_CONFIG_COUNT`` to cover them. git applies later entries last, so
lore's signing keys win over the adopter's own config (global or repo) without
this module ever writing to a vault's git config, while any operator-supplied
override with an unrelated key stays in effect. An inherited
``GIT_CONFIG_COUNT`` that does not parse as a non-negative integer is not
trusted as an index base — lore's four entries are written starting at index
0, replacing whatever the malformed count claimed to enumerate.
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
    """Return the four override entries, or ``None`` if no usable key exists."""
    key_path = load_key_path(env)
    if key_path is None or not key_path.is_file():
        return None
    allowed_signers = signing_dir(env) / ALLOWED_SIGNERS_FILENAME
    return [
        ("gpg.format", "ssh"),
        ("user.signingkey", str(key_path)),
        ("commit.gpgsign", "true"),
        ("gpg.ssh.allowedSignersFile", str(allowed_signers)),
    ]


def _inherited_git_config_count(base_env: dict) -> int:
    """Return the trustworthy inherited ``GIT_CONFIG_COUNT``, or ``0``.

    ``0`` covers "absent" as well as "malformed" (non-numeric or negative) —
    in the malformed case lore's own entries are written starting at index 0,
    which is what "replaced by lore's own entries alone" means: any
    ``GIT_CONFIG_KEY_0``/``VALUE_0`` the malformed count could not itself
    make trustworthy gets overwritten rather than read.
    """
    raw = base_env.get("GIT_CONFIG_COUNT", "")
    try:
        count = int(raw)
    except ValueError:
        return 0
    return count if count >= 0 else 0


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
    )
    if result.returncode == 0:
        return result.stdout.strip(), False
    needs_passphrase = "incorrect passphrase" in result.stderr.lower()
    return None, needs_passphrase


def _fingerprint(path: Path) -> "str | None":
    result = subprocess.run(
        ["ssh-keygen", "-lf", str(path)],
        capture_output=True, text=True, timeout=_SSH_KEYGEN_TIMEOUT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _absolute_expanded(raw: "str | Path") -> Path:
    """Expand ``~`` and make *raw* absolute, without resolving symlinks.

    Deliberately not ``Path.resolve()``, which would also collapse symlinks
    the operator may have pointed at a key elsewhere.
    """
    expanded = os.path.expanduser(str(raw))
    return Path(os.path.abspath(expanded))


def validate_adoptable_key(raw: "str | Path") -> Path:
    """Return *raw*, expanded/absolute, after confirming it is adoptable.

    Raises :class:`SigningEnableError` — naming the reason and the remedy —
    for a path that is missing, not a regular file, readable by anyone but
    its owner, or a key ``ssh-keygen`` cannot load with an empty passphrase
    (including one that needs a real passphrase, which cannot sign with
    nobody present on any platform). Performs no writes; a caller building
    on this can validate before touching disk.
    """
    path = _absolute_expanded(raw)
    if not path.exists():
        raise SigningEnableError(
            f"{path} does not exist — point --key at an existing private key, "
            "or run `lore signing enable` with no argument to generate one"
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
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)


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
        directory / ALLOWED_SIGNERS_FILENAME, f"{_resolve_principal()} {pub_line}\n"
    )


def _generate_key(dest: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(dest), "-C", socket.gethostname()],
        check=True, capture_output=True, timeout=_SSH_KEYGEN_TIMEOUT,
    )
    os.chmod(dest, 0o600)


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
    substance: the fingerprint does not change) — a dangling configuration
    (the file was deleted or replaced with a passphrase-protected key) falls
    through to generating a fresh key at :data:`GENERATED_KEY_FILENAME`,
    since that is what a host with no configured key does on this path.
    """
    directory = signing_dir(env)

    if key is not None:
        resolved = validate_adoptable_key(key)
        pub_line, _ = _probe_key(resolved)
        _write_configuration(directory, resolved, pub_line)
        return EnableResult(key_path=resolved, pub_line=pub_line, generated=False)

    _ensure_signing_dir(directory)

    existing = load_key_path(env)
    if existing is not None and existing.is_file():
        pub_line, _ = _probe_key(existing)
        if pub_line is not None:
            _write_configuration(directory, existing, pub_line)
            return EnableResult(key_path=existing, pub_line=pub_line, generated=False)

    dest = directory / GENERATED_KEY_FILENAME
    _generate_key(dest)
    pub_line, _ = _probe_key(dest)
    _write_configuration(directory, dest, pub_line)
    return EnableResult(key_path=dest, pub_line=pub_line, generated=True)


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
    if not key_path.is_file():
        return False, (
            f"the configured key {key_path} is missing — run `lore signing enable`"
        )

    mode = stat.S_IMODE(key_path.stat().st_mode)
    if mode & 0o077:
        return False, (
            f"the configured key {key_path} is readable by others — run "
            f"`chmod 600 {key_path}`"
        )

    pub_line, _ = _probe_key(key_path)
    if pub_line is None:
        return False, (
            "the configured key needs a passphrase, which cannot sign with "
            "nobody present — run `lore signing enable`"
        )

    allowed_signers = signing_dir(env) / ALLOWED_SIGNERS_FILENAME
    if not allowed_signers.is_file():
        return False, "the allowed-signers file is missing — run `lore signing enable`"

    key_material = " ".join(pub_line.split(" ")[:2])
    if key_material not in allowed_signers.read_text(encoding="utf-8"):
        return False, (
            "the allowed-signers file does not match the configured key — "
            "run `lore signing enable`"
        )

    fingerprint = _fingerprint(key_path) or pub_line
    return True, f"this host signs unattended (key {fingerprint})"
