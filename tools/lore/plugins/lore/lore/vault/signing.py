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
from pathlib import Path

#: Filename holding the signing configuration inside :func:`signing_dir`.
CONFIG_FILENAME = "signing.json"

#: Filename of the allowed-signers file `gpg.ssh.allowedSignersFile` reads,
#: written beside :data:`CONFIG_FILENAME` inside :func:`signing_dir`.
ALLOWED_SIGNERS_FILENAME = "allowed_signers"

#: The JSON field in :data:`CONFIG_FILENAME` naming the private key's
#: absolute path.
KEY_PATH_FIELD = "key-path"


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
