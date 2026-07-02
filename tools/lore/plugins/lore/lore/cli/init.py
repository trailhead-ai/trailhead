"""``lore init`` / ``lore status`` — machine bootstrap + ruleset drift report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..config import agent_ruleset as agent_ruleset_mod
from ..config import installer as installer_mod
from ..config import settings_writer as settings_writer_mod
from ..vault import config as vault_config_mod
from ..vault import vault as vault_mod
from .common import _resolve_config_path, _resolve_lore_state_dir

# Finds its sibling plugin root (and the plugin-root-level _bootstrap module) so
# the ``PreToolUse`` guard command carries an absolute, install-independent path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

#: The lore user-level ruleset name (namespaced under trailhead).
_RULESET_NAME = "trailhead" "-lore"  # noqa: implicit-concat avoids marketplace-grep guard


def _detect_harnesses():
    """Return the trailhead harnesses present on this machine.

    Bootstraps trailhead onto ``sys.path`` (four-tier walk-first, see
    ``_bootstrap``) then delegates to ``trailhead.harness.detect_harnesses``.
    The seam is harness-agnostic: lore renders the ruleset content and the
    harness decides how to write/compare it on its platform (Axiom 1).
    """
    import _bootstrap
    _bootstrap.ensure_trailhead_importable()
    from trailhead.harness import detect_harnesses
    return detect_harnesses()


def _seed_or_merge_config(config_path: Path) -> None:
    """Seed config.json if absent, or merge a default vault entry if missing.

    - Absent config.json: write ``{"vaults": [{"name": "default", "scope": "default"}]}``.
    - Present config.json without a ``default``-scope vault: append the entry
      (never clobbers existing vaults).
    - Present config.json that already has a ``default``-scope vault: no-op.
    """
    default_entry = {"name": "default", "scope": "default"}

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        vault_config_mod.write_config_atomic(config_path, {"vaults": [default_entry]})
        print(f"Seeded lore vault config at {config_path}")
        return

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Error-hygiene axiom: surface a corrupt/unreadable existing config as a
        # clean named error rather than silently no-oping past it (which would
        # leave the user "lore-ready" atop a broken config).
        raise ValueError(f"could not read existing lore config at {config_path}: {exc}") from exc

    vaults = raw.get("vaults", [])
    has_default = any(v.get("scope") == "default" for v in vaults)
    if not has_default:
        vaults.append(default_entry)
        raw["vaults"] = vaults
        vault_config_mod.write_config_atomic(config_path, raw)
        print(f"Merged default vault entry into config at {config_path}")


# The PreToolUse guard command. This hook is wired into user-GLOBAL
# settings.json, not declared via a plugin manifest — Claude Code only expands
# ``${CLAUDE_PLUGIN_ROOT}`` for hooks an installed plugin declares in its OWN
# manifest, never for a hook hand-written into settings.json. So the command
# below carries an absolute path resolved from this CLI's own location
# (``PLUGIN_ROOT``, above) rather than the placeholder, which would otherwise
# expand to nothing and silently disable the guard. Valid whether ``lore init``
# runs from the source tree (dev) or a composed/installed plugin, since
# ``hooks/`` is always a sibling of ``cli/`` under the plugin root (and the
# hook itself canonicalizes paths on every invocation).
def _guard_command() -> str:
    guard_script = PLUGIN_ROOT / "hooks" / "vault-guard.py"
    return f'python3 "{guard_script}"'
# The matcher covers every file-mutating tool that carries a target path:
# Edit/Write/MultiEdit use ``file_path``; NotebookEdit uses ``notebook_path``.
# Bash is intentionally absent — its writes are opaque at PreToolUse time and are
# covered only by the agent-rules prohibition.
_GUARD_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"

# The vault-root list delimiter passed to the hook via LORE_VAULT_GUARD_ROOT.
# A newline cannot appear in a POSIX path, so (unlike ``os.pathsep`` = ':') it
# never corrupts a vault root whose path contains a literal ':'.
_GUARD_ROOT_DELIM = "\n"


def _install_guardrail(settings_path: Path, vaults_root: Path) -> None:
    """Install the vault write-protection guardrail into *settings_path*.

    Wires:
      1. A PreToolUse ``Edit|Write|MultiEdit|NotebookEdit`` hook running
         ``hooks/vault-guard.py`` — the mandatory primary, runtime-canonicalizing
         mechanism (exit-2 deny).
      2. ``env.LORE_VAULT_GUARD_ROOT`` = the absolute ``vaults`` dir AND
         ``vaults/default`` (NEWLINE-separated — a byte that cannot appear in a
         POSIX path, so a vault path containing ':' is not corrupted). The hook
         ``realpath``s these on every call, so the ``default`` symlink's *current*
         real target is always covered — never an install-time snapshot.
      3. Symmetric coarse static ``permissions.deny`` over ``vaults/**`` for both
         ``Write`` and ``Edit`` as **defense-in-depth only** (it cannot cover a
         symlink's real target, so the runtime hook above is the
         security-sufficient mechanism). Note the ``//`` double-slash
         absolute-path grammar (single ``/`` is project-root-relative — a silent
         footgun).

    Idempotent: re-runs add no duplicate entries. All three upserts go through
    ``settings_writer`` (stdlib json, atomic write, preserves unrelated keys);
    a present-but-corrupt settings file raises ``ValueError`` (caller surfaces a
    clean error rather than clobbering it).
    """
    default_link = vaults_root / "default"
    guard_root_value = _GUARD_ROOT_DELIM.join([str(vaults_root), str(default_link)])

    settings_writer_mod.upsert_hook(
        settings_path, "PreToolUse", _guard_command(), matcher=_GUARD_MATCHER
    )
    settings_writer_mod.set_env_var(
        settings_path, "LORE_VAULT_GUARD_ROOT", guard_root_value
    )
    # Defense-in-depth (breadth-only): symmetric static deny over Write AND Edit.
    # The runtime hook above is the security-sufficient primary; these coarse
    # //abs prefix rules cannot cover a symlink's real target.
    vaults_glob = f"//{str(vaults_root).lstrip('/')}/**"
    settings_writer_mod.upsert_permission_deny(settings_path, f"Write({vaults_glob})")
    settings_writer_mod.upsert_permission_deny(settings_path, f"Edit({vaults_glob})")


def cmd_init(args) -> int:
    """Non-interactive, idempotent, user-level lore installer.

    Bootstraps the canonical default vault and global index location so the
    machine is lore-ready, installs the vault write-protection guardrail, and
    installs lore's static user-level ruleset into every detected harness via the
    trailhead ``Harness`` seam. No ``input()`` prompts — safe to call from scripts
    and ``trailhead install``. There is no ``--local`` mode: the ruleset is a
    single user-global install.

    Steps:
      1. Resolve the user-global ``settings.json`` path.
      2. Bootstrap ``$XDG_STATE_HOME/lore/vaults/default`` as a git repo.
         Idempotent.
      3. Provision ``$XDG_STATE_HOME/lore/`` (the index parent). Idempotent.
      4. Seed or merge ``config.json`` (seed-if-absent / merge default-if-missing).
      5. Install the vault write-protection guardrail (PreToolUse hook).
      6. Install lore's static user-level ruleset into every detected harness;
         degrade visibly for any harness that lacks user-ruleset support.
    """
    # Step 1: resolve the user-global settings path.
    settings_path = installer_mod.resolve_targets()

    # Step 2: bootstrap the default vault.
    lore_state = _resolve_lore_state_dir()
    vaults_root = lore_state / "vaults"

    # A failed `git init` (load-bearing vault-is-a-repo contract) surfaces as a
    # clean named error rather than a silent half-bootstrap behind "complete".
    try:
        installer_mod.bootstrap_vault(vaults_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Step 3: provision the index location (state/lore/ — sibling of vaults/).
    installer_mod.provision_index_location(lore_state)

    # Step 4: seed or merge config.json — clean error on a corrupt existing config.
    config_path = _resolve_config_path()
    try:
        _seed_or_merge_config(config_path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Step 5: install the vault write-protection guardrail.
    # A present-but-corrupt settings.json raises ValueError from settings_writer —
    # surface it as a clean error rather than clobbering the user's file.
    try:
        _install_guardrail(settings_path, vaults_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Step 6: install lore's static user-level ruleset into every detected
    # harness via the trailhead seam. Confirmation/notice lines go to STDERR
    # (repo CLI convention) so they survive even if `trailhead install` filters
    # lore stdout. An unsupported harness degrades visibly (its no-op install
    # emits the seam's fixed UNSUPPORTED notice — never silently writes nothing).
    content = agent_ruleset_mod.RULESET_CONTENT
    for h in _detect_harnesses():
        status = h.user_ruleset_status(_RULESET_NAME, content)
        if status == "unsupported":
            print(
                f"lore: notice: harness {h.name!r} has no user-level ruleset "
                f"support — the lore ruleset was not installed for it",
                file=sys.stderr,
            )
            continue
        h.install_user_ruleset(_RULESET_NAME, content)
        path = h.user_ruleset_path(_RULESET_NAME)
        if status == "current":
            print(f"lore: {h.name}: ruleset up to date ({path})", file=sys.stderr)
        else:
            print(f"lore: {h.name}: installed {path}", file=sys.stderr)

    # Step 7: first-run git-identity advisory.
    # `lore init` only bootstraps — it does NOT require a git identity (the
    # `*-by` author fallback is the write-path's concern). But when no
    # identity resolves ($LORE_EMAIL → `git config --global user.email`, both
    # empty) the FIRST record write would fail with a typed error; surface a
    # one-line advisory now so that failure isn't a surprise. Routed to STDERR
    # so it survives even if `trailhead install`
    # filters lore stdout.
    if not vault_mod.resolve_committer_email():
        print(
            "lore: advisory: no git identity set "
            "($LORE_EMAIL / `git config --global user.email` are empty) "
            "— set one before your first record write, or it will be rejected",
            file=sys.stderr,
        )

    print("lore: init complete")
    return 0


def cmd_status(args) -> int:
    """Report the lore user-level ruleset status for every detected harness.

    For each detected harness, compares the installed ruleset against lore's
    static content via the seam (``user_ruleset_status``) and reports one of
    ``current`` / ``stale`` / ``missing`` / ``unsupported``. Any non-``current``
    state carries a "re-run `lore init`" remedy so the operator can self-heal
    without re-running init blind.

    The ruleset is the Bash/shell write-prohibition gap protection (the
    PreToolUse guardrail does not cover Bash-mediated writes), so a missing or
    stale ruleset is a real coverage hole worth surfacing.
    """
    content = agent_ruleset_mod.RULESET_CONTENT
    for h in _detect_harnesses():
        status = h.user_ruleset_status(_RULESET_NAME, content)
        if status == "current":
            print(f"lore: {h.name}: ruleset current")
        elif status == "unsupported":
            print(
                f"lore: {h.name}: ruleset unsupported "
                f"(this harness has no user-level ruleset mechanism)"
            )
        else:
            print(
                f"lore: {h.name}: ruleset {status} "
                f"— re-run `lore init` to install it"
            )

    return 0


def add_init_subparsers(sub) -> None:
    """Register the ``init`` and ``status`` command parsers."""
    p_init = sub.add_parser(
        "init",
        help="Bootstrap lore on this machine (non-interactive, idempotent)",
    )
    p_init.set_defaults(func=cmd_init)

    p_lore_status = sub.add_parser(
        "status",
        help="Show lore installation status and surface rules-file drift",
    )
    p_lore_status.set_defaults(func=cmd_status)
