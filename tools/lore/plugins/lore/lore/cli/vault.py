"""``lore vault`` — add / delete / ls / config for the layered vault registry."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .common import _resolve_config_path, _resolve_groups_dir, _resolve_vaults_root


def cmd_vault(args) -> int:
    """Dispatch ``lore vault <action>`` — add / delete / ls / config.

    Thin shell over ``vault_config`` (the parse/validate/mutate boundary) and
    ``index_store`` (the per-vault scan/remove seam). Routes by ``vault_action``.
    """
    action = getattr(args, "vault_action", None)
    if action == "add":
        return _cmd_vault_add(args)
    if action == "delete":
        return _cmd_vault_delete(args)
    if action == "ls":
        return _cmd_vault_ls(args)
    if action == "config":
        return _cmd_vault_config(args)
    if action == "resolve":
        return _cmd_vault_resolve(args)
    print(
        f"lore vault: unknown action {action!r}. "
        f"Use 'lore vault add', 'lore vault delete', 'lore vault ls', "
        f"'lore vault config', or 'lore vault resolve'.",
        file=sys.stderr,
    )
    return 1


def _read_raw_config(config_path: Path) -> dict:
    """Return the parsed raw config dict, or a fresh empty shape if absent."""
    if not config_path.exists():
        return {"vaults": []}
    return json.loads(config_path.read_text(encoding="utf-8"))


def _cmd_vault_add(args) -> int:
    """``lore vault add NAME --scope SCOPE [--path PATH] [--record KIND ...]``.

    Validates the new entry, writes it to ``config.json`` (atomic), then scans the
    resolved vault's on-disk records into the global index — so registering an
    already-populated directory makes its records immediately searchable. Re-adding
    a name whose directory still exists reattaches + re-scans (no clobber).
    """
    from ..config import installer as installer_mod
    from ..record import model as record_model_mod
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod
    from ..vault import config as vault_config_mod

    config_path = _resolve_config_path()
    name = args.name
    normalized = vault_config_mod.normalize_vault_name(name)
    scope = args.scope
    records = list(args.record or [])

    # Validate scope value early (argparse already constrains it, but be explicit).
    if scope not in vault_config_mod.VALID_SCOPES:
        print(
            f"lore: invalid scope {scope!r}; must be one of "
            f"{sorted(vault_config_mod.VALID_SCOPES)}",
            file=sys.stderr,
        )
        return 1

    # The default vault may not carry a records allowlist.
    if scope == "default" and records:
        print(
            "lore: the default-scope vault may not carry a --record allowlist; "
            "it is the resolution floor and accepts every kind",
            file=sys.stderr,
        )
        return 1

    # Every --record kind must be a known record kind (fail early).
    for kind in records:
        if kind not in record_model_mod.KINDS:
            print(
                f"lore: --record {kind!r} is not a valid record kind; "
                f"valid kinds: {sorted(record_model_mod.KINDS)}",
                file=sys.stderr,
            )
            return 1

    config = _read_raw_config(config_path)

    # Duplicate-name guard (compared after normalization).
    existing = {
        vault_config_mod.normalize_vault_name(v.get("name", ""))
        for v in config.get("vaults", [])
    }
    if normalized in existing:
        print(
            f"lore: vault name {normalized!r} is already configured; "
            "vault names must be globally unique",
            file=sys.stderr,
        )
        return 1

    entry: dict = {"name": normalized, "scope": scope}
    if args.path:
        entry["path"] = str(Path(args.path).expanduser())
    if records:
        entry["records"] = records
    if args.shared:
        entry["shared"] = True

    vault_config_mod.add_vault_entry(config, entry)

    # Validate the full candidate config IN MEMORY before persisting, so a
    # well-formed-but-semantically-invalid entry (one that passes the inline
    # guards above but fails load_config's deeper checks — e.g. a name that
    # validate_layer_name rejects after normalization, or an explicit --path
    # that fails confinement) is never written to config.json and then
    # rejected. config.json stays byte-for-byte unchanged on a rejected add.
    try:
        vaults = vault_config_mod.validate_config(config)
    except vault_config_mod.VaultConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Resolve the vault now (before writing config) so we can scaffold its
    # directory.  Failing here before the config write means a failed scaffold
    # never leaves a dangling config entry pointing at a non-existent path.
    vault = next((v for v in vaults if v.name == normalized), None)
    if vault is None:
        print(f"lore: vault {normalized!r} not found after validation", file=sys.stderr)
        return 1

    # Create the vault directory when it does not yet exist.  Existing
    # directories (already-populated repos registered via --path, or a prior
    # partial add) are left untouched.
    if not vault.path.exists():
        try:
            vault.path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"lore: failed to create vault directory {vault.path}: {exc}", file=sys.stderr)
            return 1
        # Reuse the shared scaffolding helper so the vault-is-a-git-repo contract
        # has one implementation (also used by `lore init`'s bootstrap_vault).
        try:
            installer_mod.git_init(vault.path)
        except ValueError as exc:
            print(f"lore: {exc}", file=sys.stderr)
            return 1

    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        vault_config_mod.write_config_atomic(config_path, config)
    except Exception as exc:
        print(f"lore: failed to write config: {exc}", file=sys.stderr)
        return 1

    shared_flag = vault_config_mod.shared_flag(vault)
    try:
        with record_store_mod.index_transaction() as conn:
            count = index_store_mod.scan_vault(
                str(vault.path), conn, shared=shared_flag
            )
            conn.commit()
    except Exception as exc:
        print(f"lore: vault add indexed config but failed to scan: {exc}", file=sys.stderr)
        return 1

    print(f"Added vault {normalized!r} ({scope}); indexed {count} record(s).")
    return 0


def _cmd_vault_delete(args) -> int:
    """``lore vault delete NAME [--remove-from-disk [--yes]]``.

    Removes the config entry and the vault's index rows (on-disk dir kept by
    default). ``--remove-from-disk`` is the only destructive op: it prints a
    preview (resolved dir + record count) and requires ``--yes``; it rmtree's ONLY
    the exact resolved configured path after ``assert_within_root`` confines its
    realpath to ``state_dir("lore")/vaults`` — refusing any path that resolves
    outside that root or reaches it via a symlink.
    """
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import layers as layers_mod

    config_path = _resolve_config_path()
    name = args.name
    normalized = vault_config_mod.normalize_vault_name(name)

    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lore: cannot read config: {exc}", file=sys.stderr)
        return 1
    except vault_config_mod.VaultConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    vault = next((v for v in vaults if v.name == normalized), None)
    if vault is None:
        print(f"lore: vault {normalized!r} is not configured", file=sys.stderr)
        return 1

    # --remove-from-disk gating happens BEFORE any config/index mutation so a
    # refused destructive request leaves everything intact.
    if args.remove_from_disk:
        vaults_root = _resolve_vaults_root()
        # The LITERAL configured path (pre-resolution) is what we guard: a symlink
        # at the configured path must be refused even when its target resolves
        # within the vaults root. ``load_config`` resolves the path, so we read the
        # raw entry to recover the literal configured value.
        raw = _read_raw_config(config_path)
        literal = next(
            (e.get("path") for e in raw.get("vaults", [])
             if vault_config_mod.normalize_vault_name(e.get("name", "")) == normalized),
            None,
        )
        literal_path = Path(literal) if literal else Path(vault.path)
        realpath = Path(vault.path).resolve()
        try:
            layers_mod.assert_within_root(realpath, vaults_root)
        except layers_mod.LayerConfinementError as exc:
            print(
                f"lore: refusing to remove {vault.path} from disk — it resolves "
                f"outside the vaults root {vaults_root} (confinement): {exc}",
                file=sys.stderr,
            )
            return 1
        # Symlink guard: refuse only when the vault dir's OWN leaf is a symlink,
        # so its target is not followed by rmtree. A symlinked ANCESTOR (macOS
        # /var → /private/var, a symlinked $HOME or NFS mount) must NOT trip this
        # — comparing whole-path realpath equality would false-positive on those
        # and break --remove-from-disk on common setups. We compare the leaf's
        # realpath against its resolved-parent + leaf name: they differ only when
        # the leaf itself is a link.
        leaf_is_symlink = (
            literal_path.is_symlink()
            or Path(literal_path).resolve()
            != Path(literal_path).parent.resolve() / literal_path.name
        )
        if leaf_is_symlink:
            print(
                f"lore: refusing to remove {literal_path} from disk — its directory "
                "is a symlink",
                file=sys.stderr,
            )
            return 1

        # Preview + require --yes (no bare-flag destruction).
        record_count = 0
        if realpath.is_dir():
            with record_store_mod.index_transaction() as conn:
                record_count = conn.execute(
                    "SELECT COUNT(*) FROM records WHERE vault=?", (str(vault.path),)
                ).fetchone()[0]
        print(
            f"--remove-from-disk would permanently delete the directory:\n"
            f"  {realpath}\n"
            f"  ({record_count} indexed record(s))"
        )
        if not args.yes:
            print(
                "lore: aborted — re-run with --yes to confirm permanent deletion",
                file=sys.stderr,
            )
            return 1

    # Remove config entry + index rows (the always-non-destructive part).
    config = _read_raw_config(config_path)
    vault_config_mod.remove_vault_entry(config, normalized)
    try:
        vault_config_mod.write_config_atomic(config_path, config)
    except Exception as exc:
        print(f"lore: failed to write config: {exc}", file=sys.stderr)
        return 1

    try:
        with record_store_mod.index_transaction() as conn:
            removed = index_store_mod.remove_vault(str(vault.path), conn)
            conn.commit()
    except Exception as exc:
        print(f"lore: removed config entry but failed to clear index rows: {exc}",
              file=sys.stderr)
        return 1

    if args.remove_from_disk:
        shutil.rmtree(Path(vault.path).resolve())
        print(f"Deleted vault {normalized!r} (config + {removed} index row(s) + on-disk dir).")
    else:
        print(f"Deleted vault {normalized!r} (config + {removed} index row(s); dir kept).")
    return 0


def _cmd_vault_ls(args) -> int:
    """``lore vault ls`` — list name/scope/path/records for each configured vault.

    Tolerates a not-yet-created config (pre-``init``): prints a hint and exits 0,
    never a traceback.
    """
    from ..vault import config as vault_config_mod

    config_path = _resolve_config_path()
    if not config_path.exists():
        print("No vault config found; run 'lore init' to seed one.")
        return 0

    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lore: cannot read config: {exc}", file=sys.stderr)
        return 1
    except vault_config_mod.VaultConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for v in vaults:
        allowlist = ", ".join(v.records) if v.records else "(all kinds)"
        shared = " shared" if v.shared else ""
        print(f"{v.name}\t{v.scope}{shared}\t{v.path}\t[{allowlist}]")
    return 0


def _cmd_vault_config(args) -> int:
    """``lore vault config`` — open ``config.json`` in ``$EDITOR``.

    Launches the editor via ``subprocess`` LIST form (never ``shell=True``) so a
    crafted ``$EDITOR`` cannot inject a shell command. On editor exit, prints a
    reminder that ``lore reindex`` is needed for any scope/``shared`` change to
    take effect.
    """
    config_path = _resolve_config_path()
    if not config_path.exists():
        print("No vault config found; run 'lore init' to seed one.", file=sys.stderr)
        return 1

    editor = os.environ.get("EDITOR", "").strip() or "vi"
    try:
        subprocess.run([editor, str(config_path)])
    except OSError as exc:
        print(f"lore: failed to launch editor {editor!r}: {exc}", file=sys.stderr)
        return 1
    print(
        "Note: run 'lore reindex' for any scope or shared change to take effect "
        "(the index is derived from config)."
    )
    return 0


def _print_vault_resolution(result: dict, *, as_json: bool) -> None:
    """Print *result* as JSON, or as the one-line human rendering."""
    if as_json:
        print(json.dumps(result))
        return
    source = ",".join(f"{s}:{n}" for s, n in result["source"].items()) or "none"
    unmatched = ",".join(result["unmatched_scopes"]) or "none"
    print(
        f"kind={result['kind']} vault={result['vault'] or 'none'} "
        f"path={result['path']} scope={result['scope']} source={source} "
        f"skipped={result['skipped'] or 'none'} "
        f"skipped_reason={result['skipped_reason'] or 'none'} "
        f"unmatched_scopes={unmatched}"
    )


def _cmd_vault_resolve(args) -> int:
    """``lore vault resolve --kind KIND [--json]`` — group-aware vault-resolution query.

    Reports where a record of KIND would land right now from the CURRENT
    working directory's camp-group binding — the same
    ``_resolve_group_scopes`` (``cli/record.py``) + ``explain_resolution``
    (``vault/resolve.py``) path ``record create`` uses for its own routing —
    with no ``--repo``/``--team``/etc. flags of its own. This is a
    deliberately minimal surface: it exists for the ranger sweep to shell out
    to, not as a general routing-preview tool.

    Always emits the fixed eight keys ``kind``/``vault``/``path``/``scope``/
    ``source``/``skipped``/``skipped_reason``/``unmatched_scopes`` — as JSON
    with ``--json``, or as one human-readable line otherwise. A resolution
    that lands on the unconditional default floor — no camp-group binding, a
    binding whose ``records`` allowlist excludes KIND, or a binding naming a
    vault absent from ``config.json`` (today silently skipped by
    ``explain_resolution``) — always reports ``scope: "default"`` and
    ``vault: null`` as the single unbound signal, regardless of which of
    those three reasons produced it; ``source`` (the scope bindings that fed
    resolution), ``skipped``/``skipped_reason``, and ``unmatched_scopes``
    carry the reason.

    Exit 0 for any resolvable query — resolution is total (never raises for
    "no match"), matching vanilla usage (Axiom 3) when no ``config.json``
    exists at all. Only a bad ``--kind`` or an unreadable ``config.json`` is
    an error: ``lore: <msg>`` on stderr, nonzero exit.
    """
    from ..record import model as record_model_mod
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod
    from .record import _resolve_group_scopes

    kind = args.kind
    if kind not in record_model_mod.KINDS:
        print(
            f"lore: --kind {kind!r} is not a valid record kind; "
            f"valid kinds: {sorted(record_model_mod.KINDS)}",
            file=sys.stderr,
        )
        return 1

    config_path = _resolve_config_path()
    if not config_path.exists():
        # Vanilla usage (Axiom 3): no config.json means no routing is even
        # attempted (matching record create's degrade-to-vanilla path) — the
        # only vault is the implicit default floor.
        result = {
            "kind": kind,
            "vault": None,
            "path": str(vault_config_mod.resolve_active_vault()),
            "scope": "default",
            "source": {},
            "skipped": None,
            "skipped_reason": None,
            "unmatched_scopes": [],
        }
        _print_vault_resolution(result, as_json=args.json)
        return 0

    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"lore: cannot read config: {exc}", file=sys.stderr)
        return 1
    except vault_config_mod.VaultConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None
    participating_scopes: dict = {}
    if cwd is not None:
        participating_scopes = _resolve_group_scopes(cwd=cwd, groups_dir=_resolve_groups_dir())

    resolution = vault_resolve_mod.explain_resolution(participating_scopes, kind, vaults)
    chosen = resolution.chosen
    is_default = chosen.scope == "default"
    result = {
        "kind": kind,
        "vault": None if is_default else chosen.name,
        "path": str(chosen.path),
        "scope": chosen.scope,
        "source": dict(participating_scopes),
        "skipped": resolution.skipped.name if resolution.skipped is not None else None,
        "skipped_reason": resolution.skipped_reason,
        "unmatched_scopes": [f"{s}:{n}" for s, n in resolution.unmatched],
    }
    _print_vault_resolution(result, as_json=args.json)
    return 0


def add_vault_subparser(sub) -> None:
    """Register the ``vault`` command parser and its add/delete/ls/config actions."""
    p_vault = sub.add_parser(
        "vault",
        help="Manage configured layered vaults (add/delete/ls/config)",
    )
    p_vault_sub = p_vault.add_subparsers(dest="vault_action", required=True)

    p_vault_add = p_vault_sub.add_parser("add", help="Register a configured vault")
    p_vault_add.add_argument("name", help="Vault name (a repo name may contain '/')")
    p_vault_add.add_argument(
        "--scope", required=True,
        choices=["repo", "product", "suite", "team", "default"],
        help="Vault scope (precedence repo > product > suite > team > default)",
    )
    p_vault_add.add_argument("--path", default=None, help="Override the on-disk path")
    p_vault_add.add_argument(
        "--record", action="append", default=[], metavar="KIND",
        help="Restrict the vault to this record kind (repeatable; not on default)",
    )
    p_vault_add.add_argument(
        "--shared", action="store_true",
        help="Mark the vault as untrusted/shared (its hits are fenced by search)",
    )
    p_vault_add.set_defaults(func=cmd_vault)

    p_vault_delete = p_vault_sub.add_parser("delete", help="Remove a configured vault")
    p_vault_delete.add_argument("name", help="Vault name to remove")
    p_vault_delete.add_argument(
        "--remove-from-disk", dest="remove_from_disk", action="store_true",
        help="Also delete the on-disk directory (requires --yes; confined)",
    )
    p_vault_delete.add_argument(
        "--yes", action="store_true",
        help="Confirm the destructive --remove-from-disk operation",
    )
    p_vault_delete.set_defaults(func=cmd_vault)

    p_vault_ls = p_vault_sub.add_parser("ls", help="List configured vaults")
    p_vault_ls.set_defaults(func=cmd_vault)

    p_vault_config = p_vault_sub.add_parser("config", help="Edit config.json in $EDITOR")
    p_vault_config.set_defaults(func=cmd_vault)

    p_vault_resolve = p_vault_sub.add_parser(
        "resolve",
        help="Report where a record of --kind would resolve to right now (group-aware)",
    )
    p_vault_resolve.add_argument(
        "--kind", required=True, metavar="KIND",
        help="Record kind to resolve routing for",
    )
    p_vault_resolve.add_argument(
        "--json", action="store_true",
        help="Emit the fixed-key resolution object as JSON",
    )
    p_vault_resolve.set_defaults(func=cmd_vault)
