"""Shared-write gate: session-scoped confirm token + write_to_shared.

This module is the single choke point every shared vault write passes through.
No shared write can occur without a valid, live, single-use, (note, layer)-bound
token minted by an interactive TTY-confirmed session.

Security design (Slice 2, binding amendments A-1, A-2, C-2, D-2):

  TTY-detection is the PRIMARY wall (A-1):
    mint_token() refuses if tty_confirmed=False. The caller derives this flag
    from sys.stdin.isatty() — never from a CLI argument. The agent cannot
    self-approve by piping "y\\n" because on the non-TTY path, mint_token raises
    and no token is ever written to disk.

  Filesystem token is defense-in-depth (A-2):
    The token is minted ONLY after TTY-confirmed yes, written to
    state_dir("lore")/promote-tokens/ (dir 0700 / file 0600), with a minted_at
    timestamp for TTL enforcement. Single-use: consumed atomically after copy.

  Atomic copy prevents partial files (C-2):
    write_to_shared writes to a .tmp sibling then os.replace into place.
    The ordering is: verify-token → atomic-copy → consume-token.
    A crash after copy / before consume leaves the note present and a stale
    token that simply expires — never a partial file, promote is retryable.

  No bypass parameter (D-2, A-1):
    write_to_shared has no --yes / auto / force parameter. The only path to
    a valid token is mint_token(..., tty_confirmed=True).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_TTL_SECONDS = 300  # A-2: 5 minutes maximum


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromoteRefusedError(Exception):
    """Raised when a shared write is refused.

    Carries a human-readable message suitable for printing to stderr.
    The message includes the exact command the human must run (D-2 Advocate).
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _note_hash(note_path: Path) -> str:
    """Return a stable short hash of the resolved note path."""
    return hashlib.sha256(str(note_path.resolve()).encode()).hexdigest()[:16]


def _verify_token(token_path: str, note_path: Path, target_layer_name: str) -> None:
    """Verify a token is present, live, and bound — without consuming it.

    Raises PromoteRefusedError on any failure.
    """
    p = Path(token_path)

    if not p.exists():
        raise PromoteRefusedError(
            "lore: shared write refused — token not found or already used.\n"
            f"  run: lore promote {note_path} --to {target_layer_name}"
        )

    try:
        payload = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise PromoteRefusedError(
            f"lore: shared write refused — token unreadable: {exc}\n"
            f"  run: lore promote {note_path} --to {target_layer_name}"
        ) from exc

    # TTL check
    age = time.time() - payload.get("minted_at", 0)
    if age > TOKEN_TTL_SECONDS:
        p.unlink(missing_ok=True)
        raise PromoteRefusedError(
            f"lore: shared write refused — token expired "
            f"({age:.0f}s > {TOKEN_TTL_SECONDS}s TTL).\n"
            f"  run: lore promote {note_path} --to {target_layer_name}"
        )

    # Binding: note hash
    expected_hash = _note_hash(note_path)
    if payload.get("note_hash") != expected_hash:
        raise PromoteRefusedError(
            "lore: shared write refused — token is bound to a different note.\n"
            f"  run: lore promote {note_path} --to {target_layer_name}"
        )

    # Binding: target layer
    if payload.get("target_layer") != target_layer_name:
        raise PromoteRefusedError(
            f"lore: shared write refused — token is bound to layer "
            f"{payload.get('target_layer')!r}, not {target_layer_name!r}.\n"
            f"  run: lore promote {note_path} --to {target_layer_name}"
        )


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy src to dest atomically via a .tmp sibling then os.replace.

    If this raises, dest is absent (no partial file). The .tmp sibling is
    cleaned up on failure.
    """
    tmp = dest.parent / (dest.name + ".tmp")
    try:
        tmp.write_bytes(src.read_bytes())
        os.replace(str(tmp), str(dest))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mint_token(
    token_dir: Path,
    note_path: Path,
    target_layer_name: str,
    *,
    tty_confirmed: bool,
) -> str:
    """Mint a single-use confirm token.

    A-1: the token is ONLY minted after TTY-confirmed yes. If tty_confirmed is
    False (e.g. stdin is piped), raises PromoteRefusedError without touching disk.

    A-2: the token is written to token_dir (mode 0700) as a JSON file
    (mode 0600) containing the note hash, target layer, and minted_at timestamp.

    Args:
        token_dir:         Directory to store token files.
        note_path:         The note to be promoted (path is hashed for binding).
        target_layer_name: Name of the target shared layer (for binding).
        tty_confirmed:     Must be True (derived from sys.stdin.isatty() by caller).

    Returns:
        Absolute string path to the minted token file.

    Raises:
        PromoteRefusedError: if tty_confirmed is False.
    """
    if not tty_confirmed:
        raise PromoteRefusedError(
            "lore: promote refused — stdin is not a TTY. "
            "A human at an interactive terminal must confirm; "
            "the agent cannot self-approve this step.\n"
            "  run: lore promote <path> --to <layer>"
        )

    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    binding = f"{_note_hash(note_path)}:{target_layer_name}"
    token_id = hashlib.sha256(
        (binding + str(os.urandom(16))).encode()
    ).hexdigest()[:32]

    token_path = token_dir / f"{token_id}.json"
    payload = {
        "note_hash": _note_hash(note_path),
        "target_layer": target_layer_name,
        "minted_at": time.time(),
    }
    token_path.write_text(json.dumps(payload))
    token_path.chmod(0o600)
    return str(token_path)


def consume_token(
    token_path: str,
    note_path: Path,
    target_layer_name: str,
) -> None:
    """Verify and atomically consume a token (verify → unlink).

    For standalone use when the write and the token-verify happen in the same
    atomic step. For write_to_shared, verification is split: verify first,
    copy, then consume — so that a failed copy does not burn the token.

    Raises PromoteRefusedError on any failure.
    """
    _verify_token(token_path, note_path, target_layer_name)
    Path(token_path).unlink()


def write_to_shared(
    note: Path,
    target_layer,  # VaultLayer — avoid importing layers to keep this module standalone
    *,
    token: str,
    token_dir: Path | None = None,
) -> Path:
    """Copy a personal note into a shared layer, gated by a valid confirm token.

    This is the ONLY entry point for shared vault writes. It:
      1. Verifies the token (present, live, bound) — without consuming it.
      2. Copies the note atomically (.tmp → os.replace) into the shared root.
      3. Consumes the token only after a successful copy.

    A kill between step 2 and 3 leaves the note present and a stale token
    that simply expires (TTL). A failure in step 2 leaves no partial file
    and the token is not consumed — the promote is retryable.

    The personal original is never touched (D-5: copy, never move).
    No --yes / automation parameter exists on this function (A-1, D-2).

    Args:
        note:         Path to the personal note to promote.
        target_layer: VaultLayer for the shared destination.
        token:        String path to the minted confirm token.
        token_dir:    Token directory (used for stale-token reaping; optional).

    Returns:
        The destination Path where the note was copied.

    Raises:
        PromoteRefusedError: if the token is absent / expired / mis-bound.
        LayerConfinementError: if the destination escapes the shared root.
        IOError / OSError: if the copy fails (token is not consumed; retryable).
    """
    # Step 1: Verify token (without consuming)
    _verify_token(token, note, target_layer.name)

    # Confinement: destination must be inside the shared root
    dest = target_layer.root / note.name
    try:
        from layers import assert_within_root
        assert_within_root(dest, target_layer.root)
    except ImportError:
        # layers not importable — perform manual check
        resolved_dest = dest.resolve() if dest.exists() else (target_layer.root / note.name)
        resolved_root = target_layer.root.resolve()
        # Use str prefix check as fallback
        if not str(resolved_dest).startswith(str(resolved_root)):
            from layers import LayerConfinementError
            raise LayerConfinementError(
                f"lore: destination {dest!r} escapes shared root {target_layer.root!r}"
            )

    # Step 2: Atomic copy (.tmp → os.replace); may raise — token NOT consumed
    _atomic_copy(note, dest)

    # Step 3: Consume token (single-use; only reached on successful copy)
    Path(token).unlink(missing_ok=True)

    return dest
