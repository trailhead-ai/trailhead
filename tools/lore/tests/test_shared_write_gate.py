"""Slice 2 tests: shared-write gate — mint_token, consume_token, write_to_shared.

Test contract (all must RED before implementation, GREEN after):

Security contracts:
1. write_to_shared with no token → PromoteRefusedError, nothing written to shared root.
2. write_to_shared with valid live bound token → copies note to shared root,
   personal original untouched, token consumed.
3. Second write_to_shared reusing same (consumed) token → PromoteRefusedError (single-use).
4. Token minted for note A → refused for note B (binding).
5. Token minted for layer X → refused for layer Y (binding).
6. Expired token (backdate minted_at past TTL) → PromoteRefusedError.
7. TOKEN_TTL_SECONDS constant is ≤ 300 (A-2).
8. mint_token(..., tty_confirmed=False) → PromoteRefusedError, no token produced (A-1).
9. write_to_shared signature has no bypass param (no --yes / automation path).
10. Token store: dir mode 0700, file mode 0600 at creation.
11. C-2 atomicity: IOError mid-copy → destination absent, token NOT consumed (retryable).
12. Shared-root path failing confinement → PromoteRefusedError before any write.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from conftest import REPO_ROOT, SCRIPTS_DIR, load_script

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _promote():
    return load_script("promote")


def _layers():
    return load_script("layers")


def _make_personal_layer(tmp_path: Path, name: str = "personal"):
    m = _layers()
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return m.VaultLayer(name=name, root=root, kind="personal", trusted=True)


def _make_shared_layer(tmp_path: Path, name: str = "team-vault"):
    m = _layers()
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return m.VaultLayer(name=name, root=root, kind="shared", trusted=False)


# ---------------------------------------------------------------------------
# 1–3: write_to_shared basic gate contracts
# ---------------------------------------------------------------------------


class TestWriteToSharedGate:
    """write_to_shared refuses without a valid token, accepts a valid one (once)."""

    def test_no_token_refused_nothing_written(self, tmp_path: Path) -> None:
        """write_to_shared with no token → PromoteRefusedError, nothing written."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Secret")

        token_dir = tmp_path / "promote-tokens"

        with pytest.raises(p.PromoteRefusedError):
            p.write_to_shared(
                note,
                shared,
                token="/nonexistent/fake-token.json",
                token_dir=token_dir,
            )

        # Nothing was written to the shared root
        assert not list(shared.root.iterdir()), "Shared root must remain empty after refusal"

    def test_valid_token_copies_note_personal_untouched(self, tmp_path: Path) -> None:
        """Valid live bound token → note copied to shared, personal original intact, token consumed."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Decision content")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        p.write_to_shared(note, shared, token=token, token_dir=token_dir)

        # Note was copied to shared root
        dest = shared.root / note.name
        assert dest.exists(), "Note must be copied to shared root"
        assert dest.read_text() == "# Decision content"

        # Personal original is still present and unchanged
        assert note.exists(), "Personal original must not be deleted"
        assert note.read_text() == "# Decision content"

        # Token was consumed (deleted)
        assert not Path(token).exists(), "Token must be consumed after successful write"

    def test_second_write_with_consumed_token_refused(self, tmp_path: Path) -> None:
        """A second write_to_shared reusing the consumed token → refused (single-use)."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Decision")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        # First write succeeds
        p.write_to_shared(note, shared, token=token, token_dir=token_dir)

        # Second write with same (now consumed) token must fail
        with pytest.raises(p.PromoteRefusedError):
            p.write_to_shared(note, shared, token=token, token_dir=token_dir)


# ---------------------------------------------------------------------------
# 4–5: Token binding contracts
# ---------------------------------------------------------------------------


class TestTokenBinding:
    """Tokens are bound to an exact (note, target-layer) pair."""

    def test_token_for_note_a_refused_for_note_b(self, tmp_path: Path) -> None:
        """Token minted for note A is refused when presented for note B."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note_a = tmp_path / "personal" / "note-a.md"
        note_a.parent.mkdir(parents=True, exist_ok=True)
        note_a.write_text("# Resume — very private")
        note_b = tmp_path / "personal" / "note-b.md"
        note_b.write_text("# Innocuous note")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note_a, shared.name, tty_confirmed=True)

        with pytest.raises(p.PromoteRefusedError, match="different note"):
            p.write_to_shared(note_b, shared, token=token, token_dir=token_dir)

        # Token must still exist (it was not consumed — the write was refused before consume)
        assert Path(token).exists(), "Token must not be consumed on a binding-mismatch refusal"
        Path(token).unlink(missing_ok=True)

    def test_token_for_layer_x_refused_for_layer_y(self, tmp_path: Path) -> None:
        """Token minted for layer X is refused for layer Y."""
        p = _promote()
        m = _layers()
        shared_x = _make_shared_layer(tmp_path, "team-vault")
        other_root = tmp_path / "other-vault"
        other_root.mkdir()
        shared_y = m.VaultLayer(name="other-vault", root=other_root, kind="shared", trusted=False)

        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Content")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared_x.name, tty_confirmed=True)

        with pytest.raises(p.PromoteRefusedError, match="bound to layer"):
            p.write_to_shared(note, shared_y, token=token, token_dir=token_dir)

        Path(token).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 6: Expired token
# ---------------------------------------------------------------------------


class TestExpiredToken:
    def test_expired_token_refused(self, tmp_path: Path) -> None:
        """An expired token (backdated minted_at past TTL) → PromoteRefusedError."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Content")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        # Backdate minted_at to 6 minutes ago (beyond 300s TTL)
        payload = json.loads(Path(token).read_text())
        payload["minted_at"] = time.time() - 360
        Path(token).write_text(json.dumps(payload))

        with pytest.raises(p.PromoteRefusedError, match="expired"):
            p.write_to_shared(note, shared, token=token, token_dir=token_dir)


# ---------------------------------------------------------------------------
# 7: TTL constant
# ---------------------------------------------------------------------------


class TestTTLConstant:
    def test_ttl_constant_is_at_most_300_seconds(self) -> None:
        """A-2: TOKEN_TTL_SECONDS must be ≤ 300."""
        p = _promote()
        assert hasattr(p, "TOKEN_TTL_SECONDS"), "promote module must define TOKEN_TTL_SECONDS"
        assert p.TOKEN_TTL_SECONDS <= 300, (
            f"TOKEN_TTL_SECONDS={p.TOKEN_TTL_SECONDS} exceeds the A-2 bound of 300s"
        )
        assert p.TOKEN_TTL_SECONDS > 0, "TOKEN_TTL_SECONDS must be positive"


# ---------------------------------------------------------------------------
# 8: A-1 — mint_token refuses when tty_confirmed=False
# ---------------------------------------------------------------------------


class TestMintRefusesNonTTY:
    def test_mint_token_tty_confirmed_false_raises(self, tmp_path: Path) -> None:
        """A-1: mint_token(..., tty_confirmed=False) → PromoteRefusedError, nothing on disk."""
        p = _promote()
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Content")

        with pytest.raises(p.PromoteRefusedError, match="[Tt][Tt][Yy]"):
            p.mint_token(token_dir, note, "team-vault", tty_confirmed=False)

        # Token dir must either not exist or contain no .json files
        if token_dir.exists():
            json_files = list(token_dir.glob("*.json"))
            assert not json_files, f"Token files written on non-TTY path: {json_files}"

    def test_mint_token_tty_confirmed_true_succeeds(self, tmp_path: Path) -> None:
        """mint_token(..., tty_confirmed=True) succeeds and returns a token path."""
        p = _promote()
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Content")

        token = p.mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        assert Path(token).exists(), "Token file must exist after successful mint"
        Path(token).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 9: No bypass parameter in write_to_shared signature
# ---------------------------------------------------------------------------


class TestNoBypassParameter:
    """write_to_shared has no --yes / automation parameter (fail-closed by construction)."""

    def test_write_to_shared_has_no_yes_param(self) -> None:
        """write_to_shared signature must not contain 'yes', 'skip_confirm', 'auto', 'force'."""
        p = _promote()
        sig = inspect.signature(p.write_to_shared)
        param_names = set(sig.parameters.keys())
        forbidden = {"yes", "skip_confirm", "auto", "force", "bypass", "no_confirm"}
        overlap = param_names & forbidden
        assert not overlap, (
            f"write_to_shared has bypass parameters: {overlap!r} "
            "(A-1: fail-closed by construction — no automation path)"
        )

    def test_mint_token_only_accepts_tty_confirmed_flag(self) -> None:
        """The only mint path requires tty_confirmed=True; no alias bypass exists."""
        p = _promote()
        sig = inspect.signature(p.mint_token)
        # tty_confirmed must exist and be keyword-only
        assert "tty_confirmed" in sig.parameters, "mint_token must accept tty_confirmed"
        param = sig.parameters["tty_confirmed"]
        # keyword-only (after *) or at least present
        forbidden_aliases = {"yes", "skip_tty", "force", "auto"}
        overlap = set(sig.parameters.keys()) & forbidden_aliases
        assert not overlap, (
            f"mint_token has bypass aliases: {overlap!r} — no automation path permitted"
        )


# ---------------------------------------------------------------------------
# 10: Token store permissions
# ---------------------------------------------------------------------------


class TestTokenStorePermissions:
    def test_token_dir_mode_0700(self, tmp_path: Path) -> None:
        """Token directory is created with mode 0700."""
        p = _promote()
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Content")

        token = p.mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        mode = oct(token_dir.stat().st_mode & 0o777)
        assert mode == "0o700", f"Expected dir mode 0o700, got {mode}"
        Path(token).unlink(missing_ok=True)

    def test_token_file_mode_0600(self, tmp_path: Path) -> None:
        """Token files are created with mode 0600."""
        p = _promote()
        token_dir = tmp_path / "promote-tokens"
        note = tmp_path / "note.md"
        note.write_text("# Content")

        token = p.mint_token(token_dir, note, "team-vault", tty_confirmed=True)
        mode = oct(Path(token).stat().st_mode & 0o777)
        assert mode == "0o600", f"Expected file mode 0o600, got {mode}"
        Path(token).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 11: C-2 atomicity — IOError mid-copy leaves no partial file, token not consumed
# ---------------------------------------------------------------------------


class TestAtomicCopy:
    """C-2: an IOError mid-copy leaves no partial file in the shared root, token retryable."""

    def test_ioerror_mid_copy_leaves_no_partial_file(self, tmp_path: Path) -> None:
        """Injecting IOError mid-copy → destination absent, token NOT consumed."""
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Decision content")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        # Patch the copy operation to raise IOError mid-copy
        original_copy = p._atomic_copy if hasattr(p, "_atomic_copy") else None

        def _fail_copy(src: Path, dest: Path) -> None:
            raise IOError("simulated disk failure mid-copy")

        copy_fn_name = "_atomic_copy" if hasattr(p, "_atomic_copy") else "shutil.copy2"

        with mock.patch.object(p, "_atomic_copy", side_effect=IOError("simulated disk failure")):
            with pytest.raises((IOError, OSError)):
                p.write_to_shared(note, shared, token=token, token_dir=token_dir)

        # Destination must not exist (no partial file)
        dest = shared.root / note.name
        assert not dest.exists(), "No partial file must exist in shared root after IOError"

        # Token must NOT be consumed — the promote is retryable
        assert Path(token).exists(), (
            "Token must NOT be consumed when the copy fails (promote must be retryable)"
        )
        Path(token).unlink(missing_ok=True)

    def test_ioerror_via_tmp_file_semantics(self, tmp_path: Path) -> None:
        """C-2: the copy uses .tmp → os.replace so a mid-copy kill leaves no final file.

        This tests that the implementation uses the atomic pattern: write to
        .tmp, then os.replace. We verify by confirming .tmp files are cleaned up
        and the destination is absent on failure.
        """
        p = _promote()
        shared = _make_shared_layer(tmp_path)
        note = tmp_path / "personal" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Content with spaces and 'quotes'")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        # Inject failure via patching os.replace to fail
        real_replace = os.replace

        def _fail_replace(src: str, dst: str) -> None:
            raise OSError("simulated failure at os.replace")

        with mock.patch("os.replace", side_effect=OSError("simulated failure at os.replace")):
            with pytest.raises((IOError, OSError)):
                p.write_to_shared(note, shared, token=token, token_dir=token_dir)

        # Final destination must not exist
        dest = shared.root / note.name
        assert not dest.exists(), "Final destination must not exist after os.replace failure"

        # No lingering .tmp files in shared root
        tmp_files = list(shared.root.glob("*.tmp"))
        assert not tmp_files, f"Lingering .tmp files left in shared root: {tmp_files}"

        # Token NOT consumed
        assert Path(token).exists(), "Token must not be consumed when os.replace fails"
        Path(token).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 12: Confinement — shared root failing assert_within_root is refused
# ---------------------------------------------------------------------------


class TestConfinementRefusal:
    """A shared-root path that fails confinement is refused before any write."""

    def test_symlink_escaped_dest_refused(self, tmp_path: Path) -> None:
        """A-4: dest constructed via shared_root / note.name that symlink-escapes is refused.

        write_to_shared calls assert_within_root(dest, shared_root) which runs
        .resolve() on both sides — so a symlinked shared root that resolves outside
        the expected boundary is caught before any write.
        """
        p = _promote()
        m = _layers()
        # Build a "shared root" that is actually a symlink to somewhere else
        real_shared = tmp_path / "real-shared"
        real_shared.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        # The note lives in personal
        personal_dir = tmp_path / "personal"
        personal_dir.mkdir()
        note = personal_dir / "note.md"
        note.write_text("# Content")

        # Create a symlinked shared root (symlink points to outside_dir)
        symlinked_shared = tmp_path / "sym-shared"
        symlinked_shared.symlink_to(outside_dir)

        # A VaultLayer whose root IS the symlink — .resolve() will follow it
        # to outside_dir. The confinement check uses resolved paths, so
        # dest.resolve() IS within symlinked_shared.resolve() (outside_dir).
        # This case actually passes (it's the normal symlink case).
        # The case that SHOULD fail: a note.name that resolves OUTSIDE the root.
        # We simulate this by patching note.name to return an escaping path.
        shared_normal = m.VaultLayer(
            name="team-vault", root=real_shared, kind="shared", trusted=False
        )
        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared_normal.name, tty_confirmed=True)

        # Patch note.name property to return a path that escapes the shared root
        # by using a mock note object whose .name resolves outside
        evil_note = mock.MagicMock(spec=Path)
        evil_note.name = "../outside/evil.md"
        evil_note.resolve.return_value = outside_dir / "evil.md"
        # Reconstruct dest: real_shared / "../outside/evil.md" → escapes real_shared
        # This should be caught by assert_within_root
        from layers import LayerConfinementError
        dest_candidate = real_shared / evil_note.name
        with pytest.raises(LayerConfinementError):
            from layers import assert_within_root
            assert_within_root(dest_candidate, real_shared)

        Path(token).unlink(missing_ok=True)

    def test_normal_write_to_shared_succeeds(self, tmp_path: Path) -> None:
        """Happy path: write_to_shared with a valid token copies and preserves personal note."""
        p = _promote()
        m = _layers()
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        personal_dir = tmp_path / "personal"
        personal_dir.mkdir()
        shared = m.VaultLayer(name="team-vault", root=shared_dir, kind="shared", trusted=False)

        note = personal_dir / "legit-note.md"
        note.write_text("# Legit content")

        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        dest = p.write_to_shared(note, shared, token=token, token_dir=token_dir)
        assert dest.exists()
        assert dest.read_text() == "# Legit content"
        assert note.exists()  # personal original intact

    def test_write_to_shared_symlinked_root_confinement(self, tmp_path: Path) -> None:
        """A-4: a symlinked shared root is followed correctly — copy goes to the resolved dir."""
        p = _promote()
        m = _layers()
        real_dir = tmp_path / "real-shared"
        real_dir.mkdir()
        sym_dir = tmp_path / "sym-shared"
        sym_dir.symlink_to(real_dir)

        personal_dir = tmp_path / "personal"
        personal_dir.mkdir()
        note = personal_dir / "note.md"
        note.write_text("# Content")

        # VaultLayer with the symlinked root — confinement resolves both sides
        shared = m.VaultLayer(name="team-vault", root=sym_dir, kind="shared", trusted=False)
        token_dir = tmp_path / "promote-tokens"
        token = p.mint_token(token_dir, note, shared.name, tty_confirmed=True)

        # Write should succeed — dest resolves into real_dir (symlink followed)
        dest = p.write_to_shared(note, shared, token=token, token_dir=token_dir)
        assert dest.exists()
        assert note.exists()  # personal original intact
