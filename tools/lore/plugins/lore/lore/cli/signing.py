"""``lore signing`` — enable and check unattended vault-commit signing.

Thin argparse/print shell over ``vault/signing.py``'s ``enable`` and
``describe_status``, which hold the actual key generation, adoption
validation, and status-check logic. This module owns only the CLI surface:
argument parsing, the ``lore: <message>`` error shape, and exit codes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def cmd_signing(args) -> int:
    action = getattr(args, "signing_action", None)
    if action == "enable":
        return _cmd_signing_enable(args)
    if action == "status":
        return _cmd_signing_status(args)
    print(
        f"lore signing: unknown action {action!r}. "
        "Use 'lore signing enable' or 'lore signing status'.",
        file=sys.stderr,
    )
    return 1


def _cmd_signing_enable(args) -> int:
    from ..vault import signing as signing_mod

    key_arg = getattr(args, "key", None)

    try:
        result = signing_mod.enable(key=Path(key_arg) if key_arg else None)
    except signing_mod.SigningEnableError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("lore: ssh-keygen is not on PATH — install OpenSSH's client tools",
              file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(
            "lore: ssh-keygen timed out — check for a stuck ssh-keygen "
            "process and retry",
            file=sys.stderr,
        )
        return 1
    except subprocess.CalledProcessError as exc:
        # `_generate_key`'s `subprocess.run` passes `capture_output=True,
        # text=True`, so `exc.stderr` is always a `str` here, never `bytes`.
        stderr = (exc.stderr or "").strip()
        detail = f" — {stderr}" if stderr else ""
        print(f"lore: ssh-keygen failed{detail} — check its output and retry", file=sys.stderr)
        return 1

    verb = "Generated" if result.generated else "Configured"
    print(f"lore: {verb} signing key at {result.key_path}")
    print(result.pub_line)
    pub_path = Path(f"{result.key_path}.pub")
    if pub_path.is_file():
        print(
            "Register it with GitHub for a Verified badge (this command is not run):\n"
            f"  gh ssh-key add {pub_path} --type signing --title \"lore\""
        )
    else:
        print(
            "Register it with GitHub for a Verified badge: save the public "
            "key line above to a file, then run (this command is not run):\n"
            "  gh ssh-key add <path-to-saved-file> --type signing --title \"lore\""
        )
    return 0


def _cmd_signing_status(args) -> int:
    from ..vault import signing as signing_mod

    ok, message = signing_mod.describe_status()
    if ok:
        print(f"lore: {message}")
        return 0
    print(f"lore: {message}", file=sys.stderr)
    return 1


def add_signing_subparser(sub) -> None:
    """Register the ``signing`` command parser and its enable/status actions."""
    p_signing = sub.add_parser(
        "signing",
        help="Enable and check unattended vault-commit signing with a host key",
    )
    p_signing_sub = p_signing.add_subparsers(dest="signing_action", required=True)

    p_enable = p_signing_sub.add_parser(
        "enable",
        help="Generate or adopt the host's no-passphrase SSH signing key",
    )
    p_enable.add_argument(
        "--key", default=None, metavar="PATH",
        help="Adopt an existing no-passphrase SSH private key at PATH, in place",
    )
    p_enable.set_defaults(func=cmd_signing)

    p_status = p_signing_sub.add_parser(
        "status",
        help="Report whether this host signs vault commits unattended",
    )
    p_status.set_defaults(func=cmd_signing)
