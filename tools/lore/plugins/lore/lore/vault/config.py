"""Config model for lore layered vaults.

This module is the **single parse+validate boundary** for ``config.json`` —
the ``$XDG_CONFIG_HOME/lore/config.json`` file that declares all configured
vaults. It exposes a ``Vault`` NamedTuple, ``load_config`` for parse+validate,
the lightweight query helpers ``is_shared`` / ``is_configured_vault``, the
**config-based active-vault resolver** ``resolve_active_vault`` (returns the
``default``-scope vault path, or the floor ``state_dir("lore")/vaults/default``),
and the **config-mutation API:** ``add_vault_entry``, ``remove_vault_entry``,
``write_config_atomic``.

**Mutation API:**

All three helpers operate on the *raw parsed config dict* (the JSON object with
its ``"vaults"`` array), not on the validated ``list[Vault]``.  This matches the
on-disk JSON shape and is the ergonomic input for the CLI (load → mutate →
write).

- ``add_vault_entry(config, vault_entry)`` — append a dict to
  ``config["vaults"]``.
- ``remove_vault_entry(config, name)`` — drop the entry whose ``"name"`` matches
  after normalization; no-op if absent.
- ``write_config_atomic(path, config)`` — **``json.dump`` to a temp file in the
  same directory, ``json.load``-re-read to verify the file is valid + structurally
  sane, then ``os.replace`` over ``config.json``**.  A crash
  or malformed write never leaves ``config.json`` unparseable; the temp file is
  cleaned up on failure.

**Vault model invariants:**

- ``name`` is stored **normalized** (``/`` → ``_`` via
  :func:`normalize_vault_name`).  Raw names like ``trailhead-ai/trailhead``
  are converted at load time so callers never see a ``/`` in a vault name.
- ``scope`` ∈ ``{repo, product, suite, team, default}``.
- ``records`` is a (possibly empty) list of record kind strings; empty means
  *all kinds eligible*.
- ``shared`` is a bool (default ``False`` = own/trusted); the ``default``-scope
  vault is always ``shared=False`` and may not be declared ``shared: true``.
- ``path`` is the resolved ``Path`` for the vault's on-disk directory. A
  *relative* explicit ``path`` is confined via ``layers.assert_within_root``
  to the vaults root; an *absolute* explicit ``path`` is **honored as given**
  (the spec's "an explicit path is honored" rule) and may legitimately point
  outside the vaults tree — confinement is only enforced for the
  derived/relative case. (Destructive ops like ``vault delete
  --remove-from-disk`` re-confine at the call site; see ``cli/lore``.)

**Validation rules — all hard errors (raise :class:`VaultConfigError`):**

1. ``scope`` ∈ ``{repo, product, suite, team, default}``.
2. Exactly one ``default``-scope vault.
3. Names globally unique after normalization.
4. The ``default`` vault may NOT carry a ``records`` allowlist.
5. The ``default`` vault may NOT be ``shared: true``.
6. Every ``records`` kind ∈ ``record_model.KINDS``.
7. Normalized name validates via ``layers.validate_layer_name``
   (non-empty, no residual ``/``/``\\``/``..``/null).

**Path derivation:** default path is
``state_dir("lore")/vaults/<normalized-name>``; an explicit ``path`` entry
overrides. A *relative* explicit path is confined via
``layers.assert_within_root(candidate, vaults_root)`` (symlink-safe,
realpath-based); an *absolute* explicit path is honored as given. **Side
effect:** when an explicit path is present, validation ``mkdir``s the vaults
root so realpath-confinement can resolve through real directories — i.e.
:func:`validate_config` is not strictly filesystem-inert in that case.

**Important — apply ``validate_layer_name`` AFTER normalization only.**
``layers.validate_layer_name`` rejects ``/`` (``layers.py:78``), and a repo
name like ``trailhead-ai/trailhead`` contains ``/`` verbatim. Normalization
converts it to ``trailhead-ai_trailhead`` first; the validator then passes.

Pure stdlib: ``json``, ``os``, ``pathlib``.
"""
# NOTE: deliberately no ``from __future__ import annotations``. Under string
# annotations, the stdlib dataclass/NamedTuple machinery looks the defining
# module up in ``sys.modules`` to resolve field annotations — same caution as
# record_model.py.

import json
import os
from pathlib import Path
from typing import NamedTuple

from . import layers
from ..record import model as record_model

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The closed set of valid scope values.
VALID_SCOPES: frozenset[str] = frozenset({"repo", "product", "suite", "team", "default"})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class VaultConfigError(Exception):
    """Parse or validation error in config.json.

    Each instance names the specific violation in its message so the CLI
    layer can re-emit it as a clean non-zero stderr line without
    needing to inspect the exception type further.
    """


# ---------------------------------------------------------------------------
# Vault model
# ---------------------------------------------------------------------------


class Vault(NamedTuple):
    """A single configured vault entry.

    Attributes:
        name:    Normalized vault name (``/`` replaced with ``_``); safe as
                 a single filesystem path segment.
        scope:   One of ``repo|product|suite|team|default``.
        path:    Resolved absolute ``Path`` to the vault directory.
        records: Allowed record kinds; empty list means *all kinds eligible*.
        shared:  ``True`` iff the vault is marked untrusted/shared; the
                 ``default`` vault is always ``False``.
    """

    name: str
    scope: str
    path: Path
    records: list
    shared: bool


# ---------------------------------------------------------------------------
# normalize_vault_name
# ---------------------------------------------------------------------------


def normalize_vault_name(name: str) -> str:
    """Return ``name`` with every ``/`` replaced by ``_`` (idempotent).

    A repo-scope vault like ``trailhead-ai/trailhead`` is stored and compared
    as ``trailhead-ai_trailhead`` so the name is always a safe single path
    segment. Applying normalization twice is a no-op (``/`` is gone after the
    first application, so subsequent calls are identity).

    Examples::

        >>> normalize_vault_name("trailhead-ai/trailhead")
        'trailhead-ai_trailhead'
        >>> normalize_vault_name("trailhead-ai_trailhead")
        'trailhead-ai_trailhead'
    """
    return name.replace("/", "_")


# ---------------------------------------------------------------------------
# is_shared / is_configured_vault
# ---------------------------------------------------------------------------


def is_shared(vault: Vault) -> bool:
    """Return the vault's explicit ``shared`` flag (default ``False``).

    Reads ``vault.shared`` directly — the attribute is set from the config
    entry's ``"shared"`` boolean (or defaulted to ``False`` when absent).
    The ``default``-scope vault is always ``False``; a ``shared: true`` vault
    is trusted external content from the index's perspective.
    """
    return vault.shared


def shared_flag(vault: Vault) -> int:
    """Return the index trust flag for ``vault`` — ``1`` shared, ``0`` own.

    The single source of the ``bool → 0/1`` mapping that the index's ``records.shared``
    column expects, so the write path (``record create``), the per-vault scan
    (``vault add``), and the full ``reindex`` cannot derive the trust flag
    differently. Untrusted (``shared: true``) content must fence identically
    however it reached the index — see [[vault_resolve]] / the index's fence.
    """
    return 1 if is_shared(vault) else 0


def is_configured_vault(name: str, vaults: list) -> bool:
    """Return ``True`` iff ``name`` (after normalization) is in ``vaults``.

    Normalizes ``name`` before comparing against every vault's already-
    normalized ``name`` field. Safe to call with either the raw
    ``org/repo`` form or the stored ``org_repo`` form.

    Args:
        name:   The vault name to look up (normalization applied here).
        vaults: The list of :class:`Vault` instances from :func:`load_config`.
    """
    normalized = normalize_vault_name(name)
    return any(v.name == normalized for v in vaults)


# ---------------------------------------------------------------------------
# Path resolver — mirrors index_store._resolve_index_path pattern
# ---------------------------------------------------------------------------


def _resolve_vaults_root(env: dict | None = None) -> Path:
    """Return ``state_dir("lore")/vaults`` honoring XDG overrides.

    Mirrors the ``_resolve_index_path`` pattern in ``index_store.py``:
    lazy-import ``_bootstrap`` + ``trailhead.paths``, catch
    ``(ImportError, SystemExit)`` and fall back to the XDG default.

    Args:
        env: Optional environment dict for test isolation (``XDG_STATE_HOME``).
             When ``None``, ``os.environ`` is used.
    """
    try:
        import _bootstrap

        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths

        if env is not None:
            return _paths.state_dir("lore", env=env) / "vaults"
        return _paths.state_dir("lore") / "vaults"
    except (ImportError, SystemExit):
        base = env.get("XDG_STATE_HOME", "") if env else ""
        if base and os.path.isabs(base):
            return Path(base) / "lore" / "vaults"
        return Path.home() / ".local" / "state" / "lore" / "vaults"


def _resolve_config_path(env: dict | None = None) -> Path:
    """Return ``config_dir("lore")/config.json`` honoring XDG overrides.

    Mirrors ``_resolve_vaults_root``: lazy-import ``_bootstrap`` +
    ``trailhead.paths``, catch ``(ImportError, SystemExit)`` and fall back
    to the XDG default.

    Args:
        env: Optional environment dict for test isolation
             (``XDG_CONFIG_HOME``).  When ``None``, ``os.environ`` is used.
    """
    try:
        import _bootstrap

        _bootstrap.ensure_trailhead_importable()
        import trailhead.paths as _paths

        if env is not None:
            return _paths.config_dir("lore", env=env) / "config.json"
        return _paths.config_dir("lore") / "config.json"
    except (ImportError, SystemExit):
        base = env.get("XDG_CONFIG_HOME", "") if env else ""
        if base and os.path.isabs(base):
            return Path(base) / "lore" / "config.json"
        return Path.home() / ".config" / "lore" / "config.json"


def resolve_active_vault(env: dict | None = None) -> Path:
    """Return the active vault's ``Path`` from config, or the floor.

    Resolves ``config_dir("lore")/config.json`` → :func:`load_config` →
    returns the ``scope=="default"`` vault's ``path``.  Falls back silently
    to ``state_dir("lore")/vaults/default`` when:

    - ``config.json`` is absent (no ``lore init`` yet),
    - the file contains malformed JSON,
    - the config fails validation (e.g. two ``default``-scope vaults), or
    - the config contains no ``default``-scope vault.

    The fallback is **silent** — no stderr diagnostic.  This is the intended
    behavior under the no-backwards-compat axiom (Council Critical #4,
    accepted-as-risk).  A corrupt ``config.json`` is surfaced only by
    ``lore vault ls``, not by an inline notice.

    Args:
        env: Optional ``{str: str}`` environment override for XDG path
             resolution and test isolation.  Mirrors the ``_resolve_vaults_root``
             injectable pattern — pass
             ``{"XDG_CONFIG_HOME": ..., "XDG_STATE_HOME": ..., "HOME": ...}``
             in tests so the real config/state dirs are never touched (Axiom 6).

    Returns:
        Absolute ``Path`` to the active vault directory.
    """
    floor = _resolve_vaults_root(env=env) / "default"
    try:
        config_path = _resolve_config_path(env=env)
        vaults = load_config(str(config_path), env=env)
        # load_config guarantees exactly one default-scope vault exists
        return next(v.path for v in vaults if v.scope == "default")
    except Exception:
        return floor


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def load_config(config_path: str, env: dict | None = None) -> list:
    """Parse and validate ``config.json`` at *config_path*; return a ``Vault`` list.

    Reads ``config_path`` (a ``"vaults"`` JSON array), normalizes every name,
    derives or resolves each vault's path, and validates the full set against
    the invariants listed in this module's docstring. Any violation raises
    :class:`VaultConfigError` naming the specific problem.

    Args:
        config_path: Absolute or relative path to ``config.json``.
        env:         Optional ``{str: str}`` override for the XDG environment
                     (forwarded to :func:`_resolve_vaults_root`). Pass the
                     test's ``monkeypatch.setenv`` dict or build one explicitly
                     when calling from tests that need isolation.

    Returns:
        A list of :class:`Vault` instances in config order, names normalized.

    Raises:
        VaultConfigError: On any validation failure (see module docstring).
        OSError / json.JSONDecodeError: On unreadable or malformed JSON.

    Invariants:
    - Exactly one vault with ``scope == "default"`` exists.
    - All vault names are unique after normalization.
    - The ``default`` vault has an empty ``records`` list.
    - The ``default`` vault has ``shared == False``.
    - Every ``records`` kind is a member of ``record_model.KINDS``.
    - Every normalized name passes ``layers.validate_layer_name``.
    - Every resolved path is within the vaults root (``assert_within_root``).
    """
    # Read and parse the JSON config, then validate the parsed dict.
    raw_text = Path(config_path).read_text(encoding="utf-8")
    data = json.loads(raw_text)
    return validate_config(data, env=env)


def validate_config(data: dict, env: dict | None = None) -> list:
    """Validate an already-parsed config dict; return a ``Vault`` list.

    This is the in-memory validation entry point: it performs the same
    invariant checks :func:`load_config` performs, but against a parsed dict
    rather than a file on disk. Callers that build a candidate config in
    memory (e.g. ``lore vault add`` assembling ``existing + new entry``) can
    validate it BEFORE persisting, so a semantically-invalid-but-well-formed
    entry is never written to ``config.json`` and then rejected.

    Args:
        data: The raw parsed config dict (a ``{"vaults": [...]}`` object).
        env:  Optional XDG environment override (see :func:`load_config`).

    Returns:
        A list of :class:`Vault` instances in config order, names normalized.

    Raises:
        VaultConfigError: On any validation failure (see module docstring).
    """
    raw_entries = data.get("vaults", [])

    # Derive the vaults root for path confinement. We pass the current
    # process env through when no override is provided so XDG_STATE_HOME
    # set via monkeypatch.setenv is honored.
    if env is None:
        env = dict(os.environ)
    vaults_root = _resolve_vaults_root(env=env)

    vaults: list[Vault] = []
    seen_names: set[str] = set()
    default_count = 0

    for entry in raw_entries:
        raw_name = entry.get("name", "")
        scope = entry.get("scope", "")
        records = entry.get("records", [])
        shared = bool(entry.get("shared", False))
        explicit_path = entry.get("path")

        # --- 1. Validate scope ---
        if scope not in VALID_SCOPES:
            raise VaultConfigError(
                f"lore: vault {raw_name!r} has invalid scope {scope!r}; "
                f"must be one of {sorted(VALID_SCOPES)}"
            )

        # --- Normalize the name (/ → _) ---
        name = normalize_vault_name(raw_name)

        # --- 7. Validate the normalized name via layers ---
        try:
            layers.validate_layer_name(name)
        except layers.LayerConfinementError as exc:
            raise VaultConfigError(
                f"lore: vault name {raw_name!r} (normalized: {name!r}) is invalid: {exc}"
            ) from exc

        # --- 3. Globally unique names after normalization ---
        if name in seen_names:
            raise VaultConfigError(
                f"lore: duplicate vault name {name!r} (after normalization); "
                "vault names must be globally unique"
            )
        seen_names.add(name)

        # --- 6. Every records kind ∈ record_model.KINDS ---
        for kind in records:
            if kind not in record_model.KINDS:
                raise VaultConfigError(
                    f"lore: vault {name!r} records entry {kind!r} is not a "
                    f"valid record kind; valid kinds: {sorted(record_model.KINDS)}"
                )

        # --- default-scope-specific rules ---
        if scope == "default":
            default_count += 1
            # --- 4. default vault may NOT have a records allowlist ---
            if records:
                raise VaultConfigError(
                    f"lore: the default-scope vault {name!r} may not carry a "
                    "records allowlist; it is the "
                    "resolution floor and must accept every kind"
                )
            # --- 5. default vault may NOT be shared: true ---
            if shared:
                raise VaultConfigError(
                    f"lore: the default-scope vault {name!r} may not be "
                    "shared: true — it is the user's own resolution floor"
                )

        # --- Resolve path ---
        if explicit_path is not None:
            candidate = Path(explicit_path).resolve()
            # Confine the explicit path to the vaults root only when the
            # root already has a real parent on disk; during tests the root
            # may not exist yet so we create it for confinement to work.
            vaults_root.mkdir(parents=True, exist_ok=True)
            try:
                layers.assert_within_root(Path(explicit_path), vaults_root)
                resolved_path = candidate
            except layers.LayerConfinementError:
                # Explicit paths outside the default vaults root are allowed
                # when they are given as absolute and exist (the spec says
                # "an explicit path is honored"). We only confine to the
                # vaults root for relative paths.
                if os.path.isabs(explicit_path):
                    resolved_path = candidate
                else:
                    raise VaultConfigError(
                        f"lore: vault {name!r} explicit relative path "
                        f"{explicit_path!r} resolves outside the vaults root"
                    )
        else:
            resolved_path = vaults_root / name

        vaults.append(
            Vault(
                name=name,
                scope=scope,
                path=resolved_path,
                records=list(records),
                shared=shared,
            )
        )

    # --- 2. Exactly one default-scope vault ---
    if default_count == 0:
        raise VaultConfigError(
            "lore: config.json must contain exactly one vault with scope 'default'; found zero"
        )
    if default_count > 1:
        raise VaultConfigError(
            f"lore: config.json must contain exactly one vault with "
            f"scope 'default'; found {default_count}"
        )

    return vaults


# ---------------------------------------------------------------------------
# Config mutation API
# ---------------------------------------------------------------------------


def add_vault_entry(config: dict, vault_entry: dict) -> None:
    """Append *vault_entry* to the ``"vaults"`` array in *config*.

    Operates on the raw parsed config dict (the JSON object shape); does not
    validate the entry.  Validation is the caller's responsibility (the
    CLI validates before calling this).

    Args:
        config:      The raw config dict (e.g. from ``json.loads`` or
                     ``_read_config``).  Modified in place.
        vault_entry: A dict representing one vault entry (at minimum
                     ``{"name": ..., "scope": ...}``).
    """
    config.setdefault("vaults", []).append(vault_entry)


def remove_vault_entry(config: dict, name: str) -> None:
    """Remove the entry named *name* from ``config["vaults"]`` (in place).

    Compares *name* after normalization (``/`` → ``_``) against each entry's
    ``"name"`` field (also normalized for comparison), consistent with the
    module-wide normalization convention.  If no entry matches, this is a
    no-op (does not raise).

    Args:
        config: The raw config dict.  Modified in place.
        name:   The vault name to remove.  Accepts both the raw
                (``org/repo``) and normalized (``org_repo``) form.
    """
    normalized_target = normalize_vault_name(name)
    config["vaults"] = [
        v
        for v in config.get("vaults", [])
        if normalize_vault_name(v.get("name", "")) != normalized_target
    ]


def write_config_atomic(path, config: dict) -> None:
    """Write *config* to *path* atomically with post-write verification.

    **Protocol:**

    1. ``json.dump`` the config to a temp file in the same directory as
       *path* (same-directory temp ensures ``os.replace`` is atomic — same
       filesystem, no cross-device rename).
    2. ``json.load``-re-read the temp file to verify the written bytes are
       valid JSON and contain the expected ``"vaults"`` list.
    3. ``os.replace`` the temp file over *path*.

    If step 2 raises (malformed JSON or missing ``"vaults"`` key), the temp
    file is removed and *path* is left byte-for-byte unchanged.  A crash
    between steps 1 and 3 leaves only the temp file; *path* is intact.

    Args:
        path:   Path-like pointing to ``config.json``.  Must be writable;
                parent directory must exist.
        config: The raw config dict to serialize.

    Raises:
        Any exception from ``json.load`` re-verification (e.g.
        ``json.JSONDecodeError``, ``ValueError``) propagates to the caller
        after the temp file is cleaned up.
        ``OSError`` from the dump or replace propagates as-is.
    """
    path = Path(path)
    # Pid-scoped temp name (same directory → same filesystem, atomic replace)
    # so two concurrent writers can't clobber each other's in-flight temp file,
    # matching record_store.write_temp_then_rename's convention.
    tmp_path = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
        # Verify: re-read and confirm the "vaults" key is present
        with tmp_path.open("r", encoding="utf-8") as fh:
            verified = json.load(fh)
        if "vaults" not in verified:
            raise ValueError("write_config_atomic: re-read verified file missing 'vaults' key")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
