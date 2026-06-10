"""U-1 Assumption Probe — lore layered vaults, Trailhead Step 4.

Scout-gate probe for U-1: "confirm-token handshake across the agent→CLI
subprocess boundary." This probe settles all four questions the amended U-1
(binding amendments A-1 and A-2) requires before Slice 2 can build.

Findings (VALIDATED):
  1. Process model: main() is single-process; no write subcommand exists or is
     planned; write_to_shared is an internal function, NOT a CLI verb. An
     in-memory token suffices for the happy path IF TTY-detection holds.
  2. A-1: sys.stdin.isatty() is False when stdin is piped — TTY-detection
     DOES refuse the agent self-approve vector (subprocess.run input="y\n").
     The mint step is never reached on the non-TTY path (no token on disk).
  3. A-2: the candidate filesystem token semantics are all proven:
     single-use (replay fails), TTL ≤ 5min (backdated mtime → refused),
     (note-hash, target-layer) bound, atomic consume (unlink before write),
     mode 0700 dir / 0600 file.
  4. Theater verdict: TTY-detection (A-1) is the load-bearing primary check
     under the same-uid threat model. The filesystem token is defense-in-depth.
     A same-uid agent CAN read a 0600 file — but on the piped-stdin path,
     no token is ever written (mint fires only after TTY-confirmed yes),
     so there is nothing to read or replay.

CLEAN-UP NOTE: this file is an ephemeral scout-gate probe. The implementer
should delete tests/test_u1_promote_token_probe.py once Slice 2 is built and
its test contracts supersede these probes (tests/test_shared_write_gate.py).
Exact file to remove: tests/test_u1_promote_token_probe.py (the entire file).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Minimal candidate token implementation (the model being proven, not the real
# implementation — Slice 2 will place this in scripts/promote.py or layers.py)
# ---------------------------------------------------------------------------

TOKEN_TTL_SECONDS = 300  # 5 minutes — the A-2 mandatory bound


def _note_hash(note_path: Path) -> str:
    return hashlib.sha256(str(note_path.resolve()).encode()).hexdigest()[:16]


def mint_token(
    token_dir: Path,
    note_path: Path,
    target_layer_name: str,
    *,
    tty_confirmed: bool,
) -> str:
    """Mint a single-use confirm token.

    A-1 / A-2: the token is ONLY minted after TTY-confirmed yes.
    If tty_confirmed is False (agent piped stdin), refuses without touching disk.
    """
    if not tty_confirmed:
        raise RuntimeError(
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
    token_path_str: str,
    note_path: Path,
    target_layer_name: str,
) -> None:
    """Verify and atomically consume a token.

    Ordering: verify → unlink → (caller performs write).
    A crash after unlink / before write: token gone, note absent, user retries.
    """
    p = Path(token_path_str)

    if not p.exists():
        raise RuntimeError(
            "lore: shared write refused — token not found or already used.\n"
            "  run: lore promote <path> --to <layer>"
        )

    payload = json.loads(p.read_text())

    # TTL check
    age = time.time() - payload["minted_at"]
    if age > TOKEN_TTL_SECONDS:
        p.unlink(missing_ok=True)
        raise RuntimeError(
            f"lore: shared write refused — token expired ({age:.0f}s > {TOKEN_TTL_SECONDS}s).\n"
            "  run: lore promote <path> --to <layer>"
        )

    # Binding: note hash
    expected_hash = _note_hash(note_path)
    if payload["note_hash"] != expected_hash:
        raise RuntimeError(
            "lore: shared write refused — token is bound to a different note.\n"
            "  run: lore promote <path> --to <layer>"
        )

    # Binding: target layer
    if payload["target_layer"] != target_layer_name:
        raise RuntimeError(
            f"lore: shared write refused — token is bound to layer "
            f"{payload['target_layer']!r}, not {target_layer_name!r}.\n"
            "  run: lore promote <path> --to <layer>"
        )

    # Atomic single-use consume: unlink BEFORE the caller writes
    p.unlink()


# ---------------------------------------------------------------------------
# 1. Process model
# ---------------------------------------------------------------------------


class TestProcessModel:
    """Confirm there is no CLI verb that reaches a shared write separately."""

    def test_no_promote_subcommand_exists_yet(self) -> None:
        """cli/lore has no cmd_promote or 'promote' subcommand — the gate doesn't exist yet."""
        cli_lore = (
            Path(__file__).parent.parent
            / "plugins" / "lore" / "cli" / "lore"
        )
        text = cli_lore.read_text()
        # Slice 2 will add cmd_promote; until then it must not exist
        assert "def cmd_promote" not in text, (
            "cmd_promote already exists — probe is stale; "
            "remove this file and rely on test_shared_write_gate.py"
        )

    def test_no_write_to_shared_subcommand_planned(self) -> None:
        """write_to_shared is NOT a CLI subcommand — only an internal function."""
        cli_lore = (
            Path(__file__).parent.parent
            / "plugins" / "lore" / "cli" / "lore"
        )
        text = cli_lore.read_text()
        # If write_to_shared were a CLI verb, it would appear in add_parser() calls
        assert "write-to-shared" not in text and "write_to_shared_subcommand" not in text, (
            "a write-to-shared CLI subcommand exists — the process model finding is wrong; "
            "U-1 needs to be re-probed"
        )

    def test_main_is_single_dispatch(self) -> None:
        """main() is a thin single-process dispatch: parse_args → func(args)."""
        cli_lore = (
            Path(__file__).parent.parent
            / "plugins" / "lore" / "cli" / "lore"
        )
        text = cli_lore.read_text()
        assert "def main(" in text
        # main() must not fork a child process for the write path
        # (presence of subprocess.run is fine for Obsidian open etc;
        # the point is main() itself is a single invocation)
        # Confirm the single-dispatch pattern
        assert "args.func(args)" in text, "main() must call args.func(args)"


# ---------------------------------------------------------------------------
# 2. A-1: TTY detection blocks piped-stdin self-approve
# ---------------------------------------------------------------------------


class TestTTYDetection:
    """A-1: sys.stdin.isatty() is False with piped stdin — blocks agent self-approve."""

    def test_isatty_false_with_piped_stdin(self, tmp_path: Path) -> None:
        """sys.stdin.isatty() returns False when subprocess receives piped input."""
        probe = tmp_path / "isatty_probe.py"
        probe.write_text(textwrap.dedent("""\
            import sys
            print(f"isatty={sys.stdin.isatty()}")
        """))
        result = subprocess.run(
            [sys.executable, str(probe)],
            input="y\n",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "isatty=False" in result.stdout, (
            f"Expected isatty=False with piped stdin; got: {result.stdout!r}"
        )

    def test_promote_refuses_piped_stdin(self, tmp_path: Path) -> None:
        """A lore-promote stand-in refuses with exit 1 when stdin is piped."""
        probe = tmp_path / "promote_probe.py"
        probe.write_text(textwrap.dedent("""\
            import sys
            if not sys.stdin.isatty():
                print("REFUSED: stdin is not a TTY", file=sys.stderr)
                sys.exit(1)
            answer = input("Confirm? [y/N] ")
            print("APPROVED" if answer.strip().lower() == "y" else "CANCELLED")
            sys.exit(0)
        """))
        result = subprocess.run(
            [sys.executable, str(probe)],
            input="y\n",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"Piped-stdin promote must exit 1; got rc={result.returncode}"
        )
        assert "REFUSED" in result.stderr, (
            f"Expected REFUSED in stderr; got: {result.stderr!r}"
        )

    def test_no_token_minted_on_piped_path(self, tmp_path: Path) -> None:
        """A-2: no token file is written to disk on the non-TTY (piped) path."""
        token_dir = tmp_path / "promote-tokens"
        # tty_confirmed=False must refuse without touching disk
        with pytest.raises(RuntimeError, match="not a TTY"):
            mint_token(token_dir, tmp_path / "note.md", "team-vault", tty_confirmed=False)
        # Token dir should either not exist or have no .json files
        if token_dir.exists():
            json_files = list(token_dir.glob("*.json"))
            assert not json_files, (
                f"Token files written on non-TTY path: {json_files}"
            )


# ---------------------------------------------------------------------------
# 3. A-2: filesystem token semantics
# ---------------------------------------------------------------------------


class TestTokenSemantics:
    """A-2: storage, TTL, binding, single-use — all proven."""

    def test_no_token_refused(self, tmp_path: Path) -> None:
        """write_to_shared with no token → RuntimeError, nothing written."""
        with pytest.raises(RuntimeError, match="not found or already used"):
            consume_token("/nonexistent/fake.json", tmp_path / "note.md", "team-vault")

    def test_valid_token_consumed_once(self, tmp_path: Path) -> None:
        """A valid token can be consumed exactly once."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        # First consume: success
        consume_token(token, note, "team-vault")
        # Token file must be gone
        assert not Path(token).exists(), "Token file must be deleted after single use"

    def test_replay_refused(self, tmp_path: Path) -> None:
        """A second consume of the same (now-consumed) token is refused."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        consume_token(token, note, "team-vault")
        with pytest.raises(RuntimeError, match="not found or already used"):
            consume_token(token, note, "team-vault")

    def test_binding_wrong_note_refused(self, tmp_path: Path) -> None:
        """Token minted for note A is refused when presented for note B."""
        token_dir = tmp_path / "promote-tokens"
        note_a = tmp_path / "note-a.md"
        note_a.write_text("# Resume — very private")
        note_b = tmp_path / "note-b.md"
        note_b.write_text("# Innocuous note")
        token = mint_token(token_dir, note_a, "team-vault", tty_confirmed=True)
        with pytest.raises(RuntimeError, match="different note"):
            consume_token(token, note_b, "team-vault")
        Path(token).unlink(missing_ok=True)

    def test_binding_wrong_layer_refused(self, tmp_path: Path) -> None:
        """Token minted for layer 'team-vault' is refused for 'other-vault'."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        with pytest.raises(RuntimeError, match="bound to layer"):
            consume_token(token, note, "other-vault")
        Path(token).unlink(missing_ok=True)

    def test_expired_token_refused(self, tmp_path: Path) -> None:
        """A token with minted_at > 5min ago is refused (TTL check)."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        # Backdate minted_at to 6 minutes ago
        payload = json.loads(Path(token).read_text())
        payload["minted_at"] = time.time() - 360
        Path(token).write_text(json.dumps(payload))
        with pytest.raises(RuntimeError, match="expired"):
            consume_token(token, note, "team-vault")

    def test_token_dir_mode_0700(self, tmp_path: Path) -> None:
        """Token directory is created with mode 0700."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        mode = oct(token_dir.stat().st_mode & 0o777)
        assert mode == "0o700", f"Expected 0o700, got {mode}"
        Path(token).unlink(missing_ok=True)

    def test_token_file_mode_0600(self, tmp_path: Path) -> None:
        """Token files are created with mode 0600."""
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        token = mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        mode = oct(Path(token).stat().st_mode & 0o777)
        assert mode == "0o600", f"Expected 0o600, got {mode}"
        Path(token).unlink(missing_ok=True)

    def test_ttl_constant_is_five_minutes_or_less(self) -> None:
        """A-2: the TTL constant must be <= 300 seconds (5 minutes)."""
        assert TOKEN_TTL_SECONDS <= 300, (
            f"TOKEN_TTL_SECONDS={TOKEN_TTL_SECONDS} exceeds the A-2 bound of 300s"
        )
        assert TOKEN_TTL_SECONDS > 0, "TOKEN_TTL_SECONDS must be positive"


# ---------------------------------------------------------------------------
# 4. Theater verdict — TTY-detection is the primary wall
# ---------------------------------------------------------------------------


class TestTheaterVerdict:
    """Confirm that same-uid CAN read a 0600 file (token is not the primary wall)."""

    def test_same_uid_can_read_0600_token(self, tmp_path: Path) -> None:
        """0600 files are readable by the owning uid — the agent could read a live token."""
        secret = tmp_path / "token.json"
        secret.write_text('{"note_hash": "abc", "minted_at": 9999999999}')
        secret.chmod(0o600)
        # The owning process (same uid) can read it
        content = secret.read_text()
        assert "note_hash" in content, (
            "Same-uid cannot read own 0600 file — unexpected; filesystem model differs"
        )

    def test_tty_detection_is_primary_wall_piped_never_reaches_mint(
        self, tmp_path: Path
    ) -> None:
        """Primary wall proof: piped-stdin path never reaches mint_token.

        If TTY-detection fires first, the token is never written — so a same-uid
        agent has nothing to read from the token store, regardless of 0600 perms.
        This is why TTY-detection is the real boundary, not filesystem permissions.
        """
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Secret")
        # Simulate the agent path: tty_confirmed=False
        with pytest.raises(RuntimeError, match="not a TTY"):
            mint_token(token_dir, note, "team-vault", tty_confirmed=False)
        # Nothing on disk → nothing for agent to read or replay
        if token_dir.exists():
            assert not list(token_dir.glob("*.json")), (
                "Token files on disk after non-TTY refuse — TTY gate is leaking"
            )
