"""Read-only install-state and host-readiness report for `trailhead doctor`.

The report has two parts.  The first says what trailhead has installed,
discovered from on-disk state:

  - per harness: the registered marketplace + the installed tools (markers),
  - the CLI shim dir and whether each CLI-bearing tool (any tool whose manifest
    declares `cli_bin`) resolves on PATH,
  - a named `trailhead` field for the bare-name management CLI itself (not part
    of the manifest-derived CLI map): the `which("trailhead")` PATH resolution,
    plus — only for a `<repo>/bin/trailhead`-shaped hit — a checkout
    present/missing verdict. A null-resolved path is healthy in a
    function-based install (a subprocess can't see shell functions) and gets
    no verdict; a pip console-script hit gets checkout n/a, also no verdict.
  - the python3 version on PATH (informational).

The second is a host-readiness section: one line per local prerequisite of
INSTALL.md's host-preparation steps, in step order.  Each line reads `ok`,
`missing` or `could-not-check`; a `missing` line names a fix command and the
INSTALL.md step that supplies it, both taken from this module's own table.
Outpost's checkout and build and the supervisor entry are read from disk.
Lore's items come from `lore status --json` run as a subprocess under a
timeout; a lore that is absent, slow, failing or unreadable reads as a
`could-not-check` line naming lore, never as ok.  The section ends with
exactly one verdict line, `HOST READY` or `HOST NOT READY: <n> missing, <m>
could not be checked`; a prerequisite that could not be checked counts as not
ready.

The report is not a gate: ``exit_code`` is 0 whatever the verdict, unless the
report itself crashes.

Injectability: ``which_runner``, ``python_version_runner``, ``lore_runner`` and
``supervisor_dir`` are injectable so tests never shell out or read the host.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from trailhead import outpost_lifecycle, outpost_supervisor
from trailhead.capabilities import ConfineError, ManifestError, cli_bearing_manifests
from trailhead.harness import HarnessError, get_harness
from trailhead.pathint import resolve_shim_dir, trailhead_bin_executable
from trailhead.paths import PathResolutionError, config_dir, state_dir
from trailhead.provenance import read_stamp_with_reason
from trailhead.wire import default_manifest_paths


def _discover_cli_names(manifest_paths: dict[str, Path] | None = None) -> list[str]:
    """Every CLI-bearing tool name — discovered from each tool's manifest.

    Not gated by any install config flag: doctor reports on-disk/PATH reality
    regardless of what a config would install.

    Each tool's manifest is loaded independently, so a single broken manifest
    (malformed TOML, a confinement violation, a missing required field) only
    drops that one tool from the CLI list rather than crashing the report —
    doctor's contract is to always exit 0.
    """
    paths = manifest_paths if manifest_paths is not None else default_manifest_paths()
    names: list[str] = []
    for name, path in paths.items():
        try:
            bearing = cli_bearing_manifests({name: path})
        except (ManifestError, ConfineError):
            continue
        names.extend(bearing)
    return names


@dataclass
class DoctorResult:
    """Result of run_doctor()."""

    data: dict
    human_output: str
    exit_code: int


def _default_python_version_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _discover_harnesses(composed_base: Path, env: dict[str, str]) -> dict[str, dict]:
    """Report each composed harness tree's registration state via the harness seam.

    Registration state is read through the :class:`~trailhead.harness.base.Harness`
    (``is_registered`` / ``installed_tools`` / ``manifest_name``) rather than by
    re-deriving the harness's on-disk marker scheme here.  An unknown harness dir
    (no registered implementation) is still reported present, but its scheme can't
    be introspected through the seam, so its state reads as empty.

    ``env`` is passed to the seam so a harness that keeps its state per
    configuration reports the configuration this run resolves — Claude Code
    installs into one config dir at a time, and a report that read only the
    global composed tree would call a config dir with no plugin state fully
    installed.

    ``manifest_name`` returns ``None`` both when the manifest is absent and when
    it's present but unparseable; ``marketplace_malformed`` disambiguates the two
    (via :meth:`~trailhead.harness.base.Harness.manifest_exists`) so
    ``_build_human`` can render "present but corrupt" distinctly from "not there".
    """
    out: dict[str, dict] = {}
    if not composed_base.is_dir():
        return out
    for hdir in sorted(composed_base.iterdir()):
        if not hdir.is_dir():
            continue
        try:
            harness = get_harness(hdir.name)
        except HarnessError:
            out[hdir.name] = {"registered": False, "installed": [], "marketplace": None}
            continue
        marketplace = harness.manifest_name(hdir)
        out[hdir.name] = {
            "registered": harness.is_registered(hdir, env=env),
            "installed": harness.installed_tools(hdir, env=env),
            "marketplace": marketplace,
            "marketplace_malformed": marketplace is None and harness.manifest_exists(hdir),
        }
    return out


def _trailhead_field(resolved: Optional[str]) -> dict:
    """Build the `trailhead` report field from a bare `which("trailhead")` hit.

    Checkout derivation applies ONLY to a `<repo>/bin/trailhead`-shaped hit —
    the resolved file's parent named `bin`, with `<parent.parent>` containing
    `trailhead/__init__.py` — so a pip console-script install (which resolves
    outside that shape) reports checkout n/a rather than a false "missing"
    verdict. A null-resolved path (the common case for a healthy
    shellenv-function install, invisible to this subprocess) also gets no
    verdict; only the on-PATH, repo-shaped case is ever checked for
    executability, via pathint's shared ``trailhead_bin_executable`` — the same
    check shellenv uses to decide whether to emit the bare-name function, so
    the two can never disagree about whether a checkout is runnable.
    """
    if resolved is None:
        return {"path": None, "checkout": None, "checkout_present": None}

    path = Path(resolved)
    if path.name == "trailhead" and path.parent.name == "bin":
        repo = path.parent.parent
        if (repo / "trailhead" / "__init__.py").is_file():
            present = trailhead_bin_executable(repo)
            return {"path": resolved, "checkout": str(repo), "checkout_present": present}

    return {"path": resolved, "checkout": None, "checkout_present": None}


def _python_version(python_runner: Callable) -> str:
    try:
        result = python_runner(["python3", "--version"])
    except FileNotFoundError:
        return "not found on PATH"
    return (result.stdout or result.stderr or "").strip() or "unknown"


def run_doctor(
    *,
    as_json: bool = False,
    env: dict[str, str] | None = None,
    which_runner: Callable[[str], Optional[str]] | None = None,
    python_version_runner: Callable | None = None,
    manifest_paths: dict[str, Path] | None = None,
    supervisor_dir: Path | None = None,
    lore_runner: Callable | None = None,
    platform: str | None = None,
) -> DoctorResult:
    """Build a read-only report of what trailhead has installed and whether the
    host is ready. exit_code is 0."""
    _env = env if env is not None else dict(os.environ)
    _which = which_runner or shutil.which
    _pyrunner = python_version_runner or _default_python_version_runner

    composed_base = state_dir("trailhead", env=_env) / "composed"
    harnesses = _discover_harnesses(composed_base, _env)

    shim_dir = resolve_shim_dir(env=_env)
    clis = {name: _which(name) for name in _discover_cli_names(manifest_paths)}

    provenance, provenance_rejected_reason = read_stamp_with_reason(env=_env)

    data = {
        "harnesses": harnesses,
        "shim_dir": str(shim_dir),
        "shim_dir_present": shim_dir.exists(),
        "clis": clis,
        "trailhead": _trailhead_field(_which("trailhead")),
        "python3_version": _python_version(_pyrunner),
        "provenance": provenance,
        "provenance_rejected_reason": provenance_rejected_reason,
        "readiness": build_readiness(
            env=_env, supervisor_dir=supervisor_dir, lore_runner=lore_runner, platform=platform
        ),
    }

    return DoctorResult(data=data, human_output=_build_human(data), exit_code=0)


def _build_trailhead_human(field: dict) -> str:
    """Render the `trailhead:` human line: PATH resolution plus, when
    derivable, a checkout present/missing verdict. A python subprocess can't
    see shell functions, so a null path directs the user to check in a live
    shell rather than implying trailhead isn't installed; and because a
    pip-installed `trailhead` earlier on PATH can shadow (or be shadowed by)
    the shellenv function, that ordering caveat is always shown."""
    if field["path"] is None:
        return (
            "not on PATH (a shellenv function may still provide it, invisible "
            "to this subprocess — run `command -v trailhead` in a new shell to "
            "check; note a pip-installed trailhead earlier on PATH can shadow "
            "or be shadowed by that function)"
        )
    shadow_note = "note: a pip-installed trailhead earlier on PATH can shadow the shellenv function"
    if field["checkout"] is None:
        return f"{field['path']} ({shadow_note})"
    verdict = "present" if field["checkout_present"] else "missing"
    return f"{field['path']} (checkout {verdict}: {field['checkout']}; {shadow_note})"


def _build_human(data: dict) -> str:
    lines = ["trailhead doctor (read-only report):", ""]

    harnesses = data["harnesses"]
    if not harnesses:
        lines.append("  no harnesses installed")
    else:
        for hname, info in harnesses.items():
            lines.append(f"  {hname}:")
            reg = "registered" if info["registered"] else "not registered"
            if info.get("marketplace"):
                mkt = info["marketplace"]
            elif info.get("marketplace_malformed"):
                mkt = "(unreadable)"
            else:
                mkt = "(none)"
            lines.append(f"    marketplace: {mkt} ({reg})")
            installed = ", ".join(info["installed"]) or "(none)"
            lines.append(f"    installed: {installed}")
    lines.append("")

    lines.append("  CLIs on PATH:")
    for name, resolved in data["clis"].items():
        lines.append(f"    {name}: {resolved or 'not on PATH'}")
    lines.append(f"    trailhead: {_build_trailhead_human(data['trailhead'])}")
    lines.append(
        f"  shim dir: {data['shim_dir']} ({'present' if data['shim_dir_present'] else 'absent'})"
    )
    lines.append(f"  python3: {data['python3_version']}")
    lines.append("")
    lines.extend(_build_provenance_human(data["provenance"], data["provenance_rejected_reason"]))
    lines.append("")
    lines.extend(_build_readiness_human(data["readiness"]))

    return "\n".join(lines)


def _build_provenance_human(
    provenance: Optional[dict], rejected_reason: Optional[str] = None
) -> list[str]:
    """Render the `install provenance:` block: the stamped checkout + HEAD,
    and the outcome of the last update check, when present.

    A stamp that exists on disk but was REJECTED (confinement, a malformed
    `sha`, malformed JSON) is reported distinctly from a
    genuinely absent one — the fail-closed paths that reject a stamp are
    otherwise indistinguishable from "never installed"."""
    if provenance is None:
        if rejected_reason:
            return [f"  install provenance: rejected — {rejected_reason}"]
        return ["  install provenance: no install provenance recorded"]

    lines = [
        "  install provenance:",
        f"    checkout: {provenance['checkout']}",
        f"    wired at: {provenance['sha']} ({provenance['wired_at']})",
    ]
    last_check = provenance.get("last_check")
    if last_check is None:
        lines.append("    last update check: no update check has run yet")
    else:
        outcome = last_check["outcome"]
        checked_at = last_check.get("checked_at", "")
        reason = last_check.get("reason")
        detail = f" — {reason}" if reason else ""
        lines.append(f"    last update check: {outcome} ({checked_at}){detail}")

    return lines



# ---------------------------------------------------------------------------
# Host readiness
# ---------------------------------------------------------------------------

# INSTALL.md host-preparation step numbers.  Every fix below cites one of these.
STEP_FORGE = 2
STEP_OUTPOST = 3
STEP_VAULTS = 4
STEP_SIGNING = 5
STEP_AUTHOR = 6
STEP_SUPERVISOR = 7
STEP_DOCTOR = 8

LORE_STATUS_TIMEOUT = 30
LORE_STATUS_SCHEMA = 1

_OK = "ok"
_MISSING = "missing"
_COULD_NOT_CHECK = "could-not-check"
_STATES = (_OK, _MISSING, _COULD_NOT_CHECK)

_FORGE_OK_SUMMARY = "reachable from this shell (the background service's access is not checked)"

_OUTPOST_CHECKOUT = '"$HOME"/code/outpost'

# Every outpost fix is one line that sh, bash, zsh and fish read identically: a
# fixed `sh -c '<script>'` whose values arrive as positional arguments.  The
# script body holds no single quote and no backslash, so fish's escapes inside
# single quotes never apply to it.
_CLONE_UNLESS_PRESENT = '[ -d "$1" ] || gh repo clone trailhead-ai/outpost "$1"'


def _quote(value: str) -> str:
    """Quote *value* so sh and fish read the same word (fish escapes `\\` inside single quotes)."""
    return '"\\\\"'.join(shlex.quote(part) for part in value.split("\\"))


def _sh_fix(script: str, *args: str) -> str:
    """A fix that runs *script* under sh with *args* (already quoted) as $1, $2, ..."""
    return f"sh -c '{script}' sh {' '.join(args)}"


def _outpost_clone_fix(checkout: str) -> str:
    """Clone the checkout unless it is there.  ``checkout`` is already shell-safe."""
    return _sh_fix(_CLONE_UNLESS_PRESENT, checkout)


def _outpost_config_fix(config_path: Path) -> str:
    """Clone the default checkout unless it is there, then write the config unless one exists."""
    return _sh_fix(
        f'{{ {_CLONE_UNLESS_PRESENT}; }} && mkdir -p "$(dirname "$2")"'
        ' && { [ -e "$2" ] || printf "$3" "$1" > "$2"; }',
        _OUTPOST_CHECKOUT,
        _quote(str(config_path)),
        "'checkout = \"%s\"\\n'",
    )


def _outpost_checkout_key_fix(config_path: Path) -> str:
    """Put a ``checkout`` line at the top of a config that lacks one, keeping every other byte.

    The line goes first so it stays a root key however the file's tables are laid out.
    """
    return _sh_fix(
        f'{{ {_CLONE_UNLESS_PRESENT}; }} && line=$(printf "$3" "$1")'
        ' && { [ "$(head -n 1 "$2")" = "$line" ]'
        ' || { tmp=$(mktemp "$2.XXXXXX")'
        ' && { printf "$4" "$line"; cat "$2"; } > "$tmp"'
        ' && mv "$tmp" "$2"; }; }',
        _OUTPOST_CHECKOUT,
        _quote(str(config_path)),
        "'checkout = \"%s\"'",
        "'%s\\n'",
    )


def _outpost_build_fix(checkout: Path) -> str:
    return _sh_fix('cd "$1" && npm ci && npm run build', _quote(str(checkout)))


def _outpost_repair_fix(config_path: Path) -> str:
    return _sh_fix('${EDITOR:-vi} "$1"', _quote(str(config_path)))


# Fix entries keyed by an exact item id.  Outpost's fixes depend on the host's
# resolved paths, so `_outpost_item` builds them.
_EXACT_FIXES: dict[str, tuple[str, int]] = {
    "signing": ("lore signing enable", STEP_SIGNING),
    "author": ("lore vault config --makes-vault-content yes", STEP_AUTHOR),
    "supervisor": ("trailhead outpost enable", STEP_SUPERVISOR),
}
# A vault reported missing is already configured; plain `lore status` prints the
# per-vault remedy for whichever drift it has.
_PREFIX_FIXES: dict[str, tuple[str, int]] = {
    "vault:": ("lore status", STEP_VAULTS),
    "forge:": ("gh auth login && gh auth setup-git", STEP_FORGE),
}

# Where an item with no fix entry (or the lore call itself) sorts.
_LORE_CALL_STEP = STEP_FORGE
_UNKNOWN_STEP = STEP_DOCTOR


class LoreStatusError(Exception):
    """`lore status --json` could not be turned into a list of readiness items."""


def fix_for(item_id: str) -> tuple[str, int] | None:
    """The fix command and INSTALL.md step for an item id, or None when unknown."""
    if item_id in _EXACT_FIXES:
        return _EXACT_FIXES[item_id]
    for prefix, fix in _PREFIX_FIXES.items():
        if item_id.startswith(prefix):
            return fix
    return None


def _default_lore_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def parse_lore_status(text: str) -> list[dict]:
    """The items of a `lore status --json` report, or LoreStatusError.

    Anything that is not a schema-1 report whose every item has a string `id`
    and a known `state` is refused whole: a partly-understood report must not
    let an item read ok.
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise LoreStatusError("its output was not JSON")
    if not isinstance(payload, dict):
        raise LoreStatusError("its output was not a JSON object")
    if payload.get("schema") != LORE_STATUS_SCHEMA:
        raise LoreStatusError(
            f"it reported schema {payload.get('schema')!r}, which this trailhead does not "
            "understand — update trailhead"
        )
    items = payload.get("items")
    if not isinstance(items, list):
        raise LoreStatusError("its report had no item list")
    parsed = []
    for raw in items:
        if not isinstance(raw, dict):
            raise LoreStatusError("one of its items was not an object")
        item_id, state = raw.get("id"), raw.get("state")
        if not isinstance(item_id, str) or not item_id:
            raise LoreStatusError("one of its items had no id")
        if state not in _STATES:
            raise LoreStatusError(f"item {item_id} had an unrecognised state {state!r}")
        summary = raw.get("summary")
        parsed.append(
            {
                "id": item_id,
                "state": state,
                "summary": summary if isinstance(summary, str) else "",
            }
        )
    return parsed


def _lore_items(runner: Callable) -> list[dict]:
    argv = ["lore", "status", "--json"]
    try:
        proc = runner(argv, LORE_STATUS_TIMEOUT)
    except FileNotFoundError:
        return [_lore_failure("lore is not on PATH, so its items could not be checked")]
    except subprocess.TimeoutExpired:
        return [
            _lore_failure(
                f"`lore status --json` did not finish within {LORE_STATUS_TIMEOUT}s"
            )
        ]
    except OSError as exc:
        return [_lore_failure(f"lore could not be run: {exc}")]
    if proc.returncode == 2:
        return [
            _lore_failure(
                "this lore does not accept `lore status --json` — update lore "
                "(run `trailhead update`)"
            )
        ]
    if proc.returncode != 0:
        return [_lore_failure(f"`lore status --json` exited {proc.returncode}")]
    try:
        return parse_lore_status(proc.stdout or "")
    except LoreStatusError as exc:
        return [_lore_failure(f"lore's report could not be used: {exc}")]


def _lore_failure(summary: str) -> dict:
    return {"id": "lore", "state": _COULD_NOT_CHECK, "summary": summary}


def _outpost_item(env: dict[str, str]) -> dict:
    def item(state: str, summary: str, fix: str | None = None) -> dict:
        out = {"id": "outpost", "state": state, "summary": summary}
        if fix is not None:
            out["fix"], out["step"] = fix, STEP_OUTPOST
        return out

    try:
        config_path = config_dir("outpost", env=env) / "config.toml"
    except PathResolutionError as exc:
        return item(_COULD_NOT_CHECK, f"Outpost's config directory could not be resolved: {exc}")
    if not config_path.is_file():
        if os.path.lexists(config_path):
            return item(
                _COULD_NOT_CHECK,
                f"Outpost's config path {config_path} exists but is not a regular file",
            )
        return item(
            _MISSING,
            "Outpost has no config.toml naming its checkout",
            _outpost_config_fix(config_path),
        )
    repair = _outpost_repair_fix(config_path)
    try:
        with config_path.open("rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return item(_MISSING, f"Outpost's config at {config_path} is not valid TOML: {exc}", repair)
    except OSError as exc:
        return item(_COULD_NOT_CHECK, f"Outpost's config at {config_path} could not be read: {exc}")
    if "checkout" not in config:
        return item(
            _MISSING,
            f"Outpost's config at {config_path} has no checkout key",
            _outpost_checkout_key_fix(config_path),
        )
    value = config["checkout"]
    if not isinstance(value, str) or not Path(value).is_absolute():
        return item(
            _MISSING,
            f"Outpost's config checkout must be an absolute path, got {value!r}",
            repair,
        )
    if not os.path.lexists(value):
        return item(
            _MISSING,
            f"Outpost's configured checkout {value} does not exist",
            _outpost_clone_fix(_quote(value)),
        )
    if not Path(value).is_dir():
        return item(_MISSING, f"Outpost's configured checkout {value} is not a directory", repair)
    checkout = Path(value).resolve()
    try:
        outpost_lifecycle._resolve_entrypoint(env)
    except outpost_lifecycle.OutpostLifecycleError as exc:
        return item(_MISSING, f"Outpost is not built: {exc}", _outpost_build_fix(checkout))
    return item(_OK, "Outpost is configured and built")


def _supervisor_item(env: dict[str, str], supervisor_dir: Path | None, platform: str | None) -> dict:
    try:
        enabled = outpost_supervisor.is_enabled(env, platform=platform, supervisor_dir=supervisor_dir)
    except Exception as exc:
        return {
            "id": "supervisor",
            "state": _COULD_NOT_CHECK,
            "summary": f"the supervisor entry could not be checked: {exc}",
        }
    if enabled:
        return {"id": "supervisor", "state": _OK, "summary": (
            "the Outpost supervisor entry file exists "
            "(whether the service manager runs it is not checked)"
        )}
    return {
        "id": "supervisor",
        "state": _MISSING,
        "summary": "no supervisor entry keeps Outpost running",
    }


def build_readiness(
    *,
    env: dict[str, str],
    supervisor_dir: Path | None = None,
    lore_runner: Callable | None = None,
    platform: str | None = None,
) -> dict:
    """The host-readiness items in INSTALL.md step order, plus one verdict line.

    Each item is ``{id, state, summary}``; a missing item also carries ``fix``
    and ``step`` from this module's table.  Lore's own text is used only as the
    summary, never as a fix.
    """
    raw = [_outpost_item(env), _supervisor_item(env, supervisor_dir, platform)]
    raw.extend(_lore_items(lore_runner or _default_lore_runner))

    items = []
    for item in raw:
        fix = (item["fix"], item["step"]) if "fix" in item else fix_for(item["id"])
        if item["id"].startswith("forge:") and item["state"] == _OK:
            item["summary"] = _FORGE_OK_SUMMARY
        if item["state"] == _MISSING and fix is not None:
            item["fix"], item["step"] = fix
        items.append((_step_of(item["id"], fix), item))
    items.sort(key=lambda pair: pair[0])
    ordered = [entry for _, entry in items]

    missing = sum(1 for i in ordered if i["state"] == _MISSING)
    unchecked = sum(1 for i in ordered if i["state"] == _COULD_NOT_CHECK)
    if missing == 0 and unchecked == 0:
        verdict = "HOST READY"
    else:
        verdict = f"HOST NOT READY: {missing} missing, {unchecked} could not be checked"
    return {"verdict": verdict, "items": ordered}


def _step_of(item_id: str, fix: tuple[str, int] | None) -> int:
    if fix is not None:
        return fix[1]
    if item_id == "lore":
        return _LORE_CALL_STEP
    if item_id == "outpost":
        return STEP_OUTPOST
    return _UNKNOWN_STEP


def _build_readiness_human(readiness: dict) -> list[str]:
    lines = ["  host readiness:"]
    for item in readiness["items"]:
        lines.append(f"    [{item['state']}] {item['id']}: {item['summary']}")
        if "fix" in item:
            lines.append(f"      fix: {item['fix']}")
            lines.append(f"      (INSTALL.md step {item['step']})")
    lines.append(f"  {readiness['verdict']}")
    return lines
