"""Host readiness items — the one list ``lore status`` renders both ways.

``build_readiness_items`` returns a list of :class:`ReadinessItem`; ``lore
status`` prints each item's human line and ``lore status --json`` prints
:func:`render_json` of the same list. An item's JSON form carries an ``id``, a
``state`` (``ok`` | ``missing`` | ``could-not-check``), a plain-language
``summary`` and, where useful, a ``detail`` — never a fix command, which
belongs to whoever consumes the JSON.

Item ids: ``signing``, ``vault:<name>`` for each configured vault,
``forge:<name>`` for each vault that has a remote, and ``author``.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    DRIFT_MISSING,
    DRIFT_NOT_GIT,
    DRIFT_NO_REMOTE,
    DRIFT_RESOLVING,
    DRIFT_SYNC_FIXABLE,
    _resolve_all_vaults,
    _vault_drift,
)

SCHEMA = 1

OK = "ok"
MISSING = "missing"
COULD_NOT_CHECK = "could-not-check"

#: A vault directory that is absent, not a git repository, or has no remote is
#: not a usable copy; every other drift finding is about sync state.
_VAULT_MISSING_CODES = frozenset({DRIFT_MISSING, DRIFT_NOT_GIT, DRIFT_NO_REMOTE})

PROBE_TIMEOUT = 15.0
TOTAL_CAP = 20.0


@dataclass
class ReadinessItem:
    id: str
    state: str
    summary: str
    detail: str | None = None
    #: The plain ``lore status`` rendering of this item; ``None`` prints nothing.
    line: str | None = field(default=None, repr=False)
    line_to_stderr: bool = field(default=False, repr=False)

    def to_json(self) -> dict:
        data = {"id": self.id, "state": self.state, "summary": self.summary}
        if self.detail is not None:
            data["detail"] = self.detail
        return data


def render_json(items: list) -> str:
    return json.dumps({"schema": SCHEMA, "items": [i.to_json() for i in items]}, indent=2)


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------


def build_signing_item(env: dict | None = None) -> ReadinessItem:
    """The same checks ``lore signing status`` runs, as one item."""
    from ..vault import signing as signing_mod

    if signing_mod.load_key_path(env) is None and not signing_mod.git_requires_signing():
        message = (
            "not configured — vault commits follow your git "
            "settings, which do not require signing"
        )
        return ReadinessItem(
            "signing", OK,
            "vault commits follow your git settings, which do not require signing",
            line=f"lore: signing: {message}",
        )
    try:
        ok, message = signing_mod.describe_status(env)
    except (OSError, subprocess.SubprocessError) as exc:
        return ReadinessItem(
            "signing", COULD_NOT_CHECK, "signing state could not be checked",
            line=f"lore: signing: error — {exc}", line_to_stderr=True,
        )
    if ok:
        return ReadinessItem("signing", OK, message, line=f"lore: signing: {message}")
    return ReadinessItem(
        "signing", MISSING, message.split(" — ", 1)[0], line=f"lore: signing: {message}",
    )


# ---------------------------------------------------------------------------
# vaults
# ---------------------------------------------------------------------------


def _drift_remedy(name: str, codes: set) -> str:
    """Return the remedy line for a vault's drift ``codes``.

    Keyed on the stable ``DRIFT_*`` tokens, never on the human phrasing, so
    rewording a finding cannot silently mis-route its remedy.

    ``DRIFT_RESOLVING`` outranks everything: while a rebase is stopped mid-flight
    no other remedy is even safe to attempt, and ``lore sync`` would abort the
    resolution rather than finish it.

    Ordered by what actually unblocks the operator: if ANY finding is one
    ``lore sync`` resolves, that is the remedy even when a standing condition
    (no remote) sits beside it — committing the records is the step that reduces
    the exposure. Only when nothing is sync-fixable does the standing condition
    become the ask, and a remedy is never offered that would simply fail.
    """
    if DRIFT_RESOLVING in codes:
        return f"run `lore resolve {name}`"
    if codes & DRIFT_SYNC_FIXABLE:
        return f"run `lore sync --vault {name}`"
    if DRIFT_MISSING in codes:
        return "create the directory, or correct its path in config.json"
    if DRIFT_NOT_GIT in codes:
        return "run `git init` in the vault directory"
    if DRIFT_NO_REMOTE in codes:
        return "add an origin remote"
    return "inspect the vault"


@dataclass
class _Vault:
    name: str
    path: Path
    findings: list

    @property
    def codes(self) -> set:
        return {code for code, _ in self.findings}

    @property
    def has_remote(self) -> bool:
        return not (self.codes & _VAULT_MISSING_CODES)


def collect_vaults() -> "tuple[list[_Vault], str | None]":
    vaults, error = _resolve_all_vaults()
    if error is not None:
        return [], error
    return [_Vault(name, path, _vault_drift(Path(path))) for name, path in vaults], None


def _vault_config_item(error: str) -> ReadinessItem:
    return ReadinessItem(
        "vault:default", COULD_NOT_CHECK, "vault config is unreadable", detail=error,
        line=f"lore: vault config unreadable — {error}", line_to_stderr=True,
    )


def build_vault_items(vaults: "list[_Vault]", error: "str | None") -> list:
    """One ``vault:<name>`` item per vault, or the one config-unreadable item."""
    if error is not None:
        return [_vault_config_item(error)]
    return [_vault_item(v) for v in vaults]


def _vault_item(vault: _Vault) -> ReadinessItem:
    if not vault.findings:
        return ReadinessItem(
            f"vault:{vault.name}", OK,
            f"vault {vault.name} is a git repository with a remote and is synced",
            line=f"lore: vault {vault.name}: synced",
        )
    descriptions = "; ".join(desc for _, desc in vault.findings)
    line = f"lore: vault {vault.name}: {descriptions} — {_drift_remedy(vault.name, vault.codes)}"
    if vault.codes & _VAULT_MISSING_CODES:
        return ReadinessItem(f"vault:{vault.name}", MISSING, f"vault {vault.name}: {descriptions}", line=line)
    return ReadinessItem(
        f"vault:{vault.name}", OK,
        f"vault {vault.name} is a git repository with a remote",
        detail=descriptions, line=line,
    )


# ---------------------------------------------------------------------------
# forge probe
# ---------------------------------------------------------------------------

_HARDENING = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GCM_INTERACTIVE": "never",
    "GIT_ALLOW_PROTOCOL": "https:ssh:git",
}

_SSH_OPTIONS = "-o BatchMode=yes -o ConnectTimeout=5"

_HOST_KEY = ("host key verification failed", "remote host identification has changed")
_REFUSED = (
    "permission denied (publickey",
    "terminal prompts disabled",
    "authentication failed",
    "returned error: 401",
    "returned error: 403",
)
_REPOSITORY_NOT_FOUND = re.compile(r"repository (not found|'[^']*' not found)")

_RETRY = "the remote could not be checked — retry when online"

_FORGE_SUMMARY = {
    OK: "the remote is reachable with the credential in this shell",
    MISSING: "the remote refused this shell's {credential}, or the repository is not visible to it",
    COULD_NOT_CHECK: _RETRY,
    "host-key": "the remote's host key is not trusted by this host — connect once interactively to trust it",
}


def _run_probe(argv: list, env: dict, timeout: float) -> "tuple[int, str]":
    """Run *argv* in its own session; kill the whole process group on timeout.

    Nothing git-side bounds an https connect or TLS-handshake stall, so the
    subprocess timeout is the only bound there.
    """
    proc = subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return proc.returncode, stderr


def _classify(rc: int, stderr: str) -> str:
    """Map a probe's exit and stderr to a forge outcome; stderr is not kept."""
    if rc in (0, 2):
        return OK
    if rc != 128:
        return COULD_NOT_CHECK
    text = stderr.lower()
    if any(token in text for token in _HOST_KEY):
        return "host-key"
    if any(token in text for token in _REFUSED) or _REPOSITORY_NOT_FOUND.search(text):
        return MISSING
    return COULD_NOT_CHECK


def _forge_item(name: str, outcome: str, credential: str) -> ReadinessItem:
    state = COULD_NOT_CHECK if outcome == "host-key" else outcome
    summary = _FORGE_SUMMARY[outcome].format(credential=credential)
    return ReadinessItem(f"forge:{name}", state, f"vault {name}: {summary}")


def _git_output(path, *args: str, timeout: float = 5) -> str:
    """Stdout of ``git -C <path> <args>``, or ``""`` when git fails, is absent or
    outlasts *timeout*."""
    try:
        done = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _ssh_command(vault: "_Vault", base_env: dict, timeout: float = 5) -> str:
    """The ssh command git itself would use for *vault*: ``GIT_SSH_COMMAND``, then
    the vault's ``core.sshCommand``, then plain ``ssh``."""
    return (
        base_env.get("GIT_SSH_COMMAND")
        or _git_output(vault.path, "config", "--get", "core.sshCommand", timeout=timeout)
        or "ssh"
    )


def _credential_kind(vault: "_Vault", timeout: float = 5) -> str:
    url = _git_output(vault.path, "remote", "get-url", "origin", timeout=timeout)
    return "https credential" if url.startswith(("https://", "http://")) else "ssh key"


def _child_pids() -> set:
    """Pids of this process's direct children; empty when ``ps`` is unavailable."""
    try:
        done = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    me = os.getpid()
    pids = set()
    for line in done.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == str(me):
            pids.add(int(fields[0]))
    return pids


def _reap_new_children(before: set) -> None:
    """Kill and reap every child spawned since *before* that is still around."""
    for pid in _child_pids() - before:
        for kill in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                kill()
                break
            except OSError:
                continue
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass


def _probe_forges(vaults, env, runner, timeout, total_cap) -> list:
    base_env = os.environ if env is None else env
    results: dict = {}
    credentials: dict = {}
    deadline = time.monotonic() + total_cap
    children_before = _child_pids()

    def remaining() -> float:
        return deadline - time.monotonic()

    def probe(vault):
        argv = ["git", "-C", str(vault.path), "ls-remote", "--exit-code", "--", "origin", "HEAD"]
        try:
            credentials[vault.name] = _credential_kind(vault, min(5, max(0.0, remaining())))
            ssh = _ssh_command(vault, base_env, min(5, max(0.0, remaining())))
            probe_env = {**base_env, **_HARDENING, "GIT_SSH_COMMAND": f"{ssh} {_SSH_OPTIONS}"}
            budget = min(timeout, remaining())
            if budget <= 0:
                return
            results[vault.name] = _classify(*runner(argv, probe_env, budget))
        except Exception:
            results[vault.name] = COULD_NOT_CHECK

    threads = [threading.Thread(target=probe, args=(v,), daemon=True) for v in vaults]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, remaining()))
    if any(t.is_alive() for t in threads):
        _reap_new_children(children_before)
    return [
        _forge_item(v.name, results.get(v.name, COULD_NOT_CHECK), credentials.get(v.name, "credential"))
        for v in vaults
    ]


# ---------------------------------------------------------------------------
# author
# ---------------------------------------------------------------------------


def _config_unreadable(vault_config_mod, env: dict | None) -> bool:
    """A config file that exists but cannot be read as a JSON object."""
    try:
        path = vault_config_mod._resolve_config_path(env=env)
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict)


def build_author_item(env: dict | None = None) -> ReadinessItem:
    """Whether this host has declared if it makes vault content."""
    from ..vault import config as vault_config_mod

    if _config_unreadable(vault_config_mod, env):
        return ReadinessItem(
            "author", COULD_NOT_CHECK,
            "this host's makes_vault_content declaration could not be read",
        )
    try:
        declared = vault_config_mod.read_makes_vault_content(env=env)
    except vault_config_mod.VaultConfigError as exc:
        return ReadinessItem(
            "author", MISSING, "this host's makes_vault_content declaration is malformed",
            detail="malformed",
            line=(
                f"lore: author: malformed — {exc} — run `lore vault config "
                "--makes-vault-content yes` (or `no`)"
            ),
        )
    if declared is None:
        return ReadinessItem(
            "author", MISSING, "this host has not declared whether it makes vault content",
            detail="undeclared",
            line=(
                "lore: author: undeclared — run `lore vault config "
                "--makes-vault-content yes` (or `no`)"
            ),
        )
    return ReadinessItem(
        "author", OK,
        "this host makes vault content" if declared else "this host does not make vault content",
        detail="yes" if declared else "no",
    )


# ---------------------------------------------------------------------------
# the list
# ---------------------------------------------------------------------------


def build_readiness_items(
    env: dict | None = None,
    *,
    probe_runner=None,
    probe_timeout: float = PROBE_TIMEOUT,
    total_cap: float = TOTAL_CAP,
) -> list:
    """Build every readiness item for this host, in report order.

    Order: ``signing``, each ``vault:<name>``, each ``forge:<name>``, ``author``.

    Args:
        env: Environment mapping for the signing and author reads and as the
             base of the probe environment; ``None`` reads ``os.environ``.
        probe_runner: ``(argv, env, timeout) -> (returncode, stderr)`` that may
             raise ``subprocess.TimeoutExpired``; defaults to a real subprocess.
        probe_timeout: Per-probe timeout, capped at *total_cap*.
        total_cap: Seconds after which unfinished probes read ``could-not-check``.
    """
    items = [build_signing_item(env)]
    vaults, error = collect_vaults()
    items.extend(build_vault_items(vaults, error))
    if error is None:
        with_remote = [v for v in vaults if v.has_remote]
        items.extend(_probe_forges(
            with_remote, env, probe_runner or _run_probe, probe_timeout, total_cap))
    items.append(build_author_item(env))
    return items
