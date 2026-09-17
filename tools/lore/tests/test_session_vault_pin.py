"""Session records are writable ONLY into the default-scope vault.

A session record is the operator's own capture log. It rides one machine and one
identity, so it belongs in the personal ``default``-scope vault and nowhere else —
never in a product/team vault that syncs to a shared remote.

The rule is enforced at the two places a session record can be born:

  - **Routing** — ``resolve_vault`` elects ``default`` for ``kind == "session"``
    regardless of the scope flags supplied, and the ``record create --vault NAME``
    override (which bypasses routing entirely) refuses a non-default name.
  - **The write seam** — ``record_store.validate_and_write`` and the
    ``session_store`` capture primitives refuse a session write aimed anywhere but
    the default vault. This is the backstop that binds a producer written later by
    someone who never read the routing rule.

An install with **no** ``config.json`` has no configured vaults to route between —
the single vanilla vault IS the floor — so the seam guard has nothing to enforce
there and stays out of the way (Axiom 3: support vanilla usage).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script, write_vault_config

SID = "aaaaaaaa-1111-4111-8111-111111111111"
OTHER_SID = "bbbbbbbb-2222-4222-8222-222222222222"

# The ambient harness session id must not leak into a subprocess whose session key
# the test pins explicitly.
_NO_AMBIENT_SID = {"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""}


# ---------------------------------------------------------------------------
# A two-vault install: the shape the rule is about
# ---------------------------------------------------------------------------


class _Install:
    """A ``default``-scope vault plus a ``product``-scope ``trailhead`` vault."""

    def __init__(self, tmp_path: Path):
        self.config_home = tmp_path / "config"
        self.state = tmp_path / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.default_vault = tmp_path / "v-default"
        self.other = tmp_path / "v-trailhead"
        for v in (self.default_vault, self.other):
            v.mkdir(parents=True, exist_ok=True)
        write_vault_config(
            self.config_home,
            [
                ("default", "default", self.default_vault),
                ("trailhead", "product", self.other),
            ],
        )

    def run(self, args, *, stdin_text=None):
        env = {
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.config_home),
            "LORE_EMAIL": "tester@example.com",
            **_NO_AMBIENT_SID,
        }
        import os

        full_env = dict(os.environ)
        full_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            capture_output=True,
            text=True,
            env=full_env,
            input=stdin_text,
        )


def _session_exists(vault: Path, key: str) -> bool:
    return (vault / "session" / f"{key}.json").exists()


# ---------------------------------------------------------------------------
# Routing: which vault a session write is ALLOWED to elect
# ---------------------------------------------------------------------------


class TestSessionRoutingPinsToDefault:

    def test_record_create_session_refuses_a_named_non_default_vault(self, tmp_path):
        """``--vault`` bypasses routing, so it needs its own refusal.

        Varying input: the vault NAME. The same create is accepted for ``default``
        and refused for ``trailhead``.
        """
        inst = _Install(tmp_path)

        refused = inst.run(
            ["record", "create", "--kind", "session", "--title", SID,
             "--vault", "trailhead"],
        )
        assert refused.returncode != 0, refused.stdout
        assert not _session_exists(inst.other, SID), (
            "a refused session create must write nothing into the named vault"
        )

        allowed = inst.run(
            ["record", "create", "--kind", "session", "--title", OTHER_SID,
             "--vault", "default"],
        )
        assert allowed.returncode == 0, allowed.stderr
        assert _session_exists(inst.default_vault, OTHER_SID)

    def test_a_typed_scope_flag_routes_other_kinds_but_is_refused_on_a_session(
        self, tmp_path
    ):
        """Varying input: the KIND, with the scope flag held constant.

        ``--product trailhead`` still routes an ordinary record into the product
        vault. On a session it is refused rather than honored-then-overridden:
        routing it to ``default`` anyway would stamp a ``product`` sidecar field
        naming a vault the record does not live in.
        """
        inst = _Install(tmp_path)

        decision = inst.run(
            ["record", "create", "--kind", "decision", "--title", "some decision",
             "--product", "trailhead"],
        )
        assert decision.returncode == 0, decision.stderr
        assert list((inst.other / "decision").glob("*.json")), (
            "a non-session kind must still route to the product vault"
        )

        session = inst.run(
            ["record", "create", "--kind", "session", "--title", SID,
             "--product", "trailhead"],
        )
        assert session.returncode != 0
        assert not _session_exists(inst.other, SID)
        assert not _session_exists(inst.default_vault, SID), (
            "a refused create must not silently reroute to the default vault"
        )

    def test_routing_elects_the_default_vault_for_a_session_kind(self, tmp_path):
        """The resolver pin, exercised directly on the function that routes.

        Varying input: the KIND, with one supplied product scope held constant.
        That scope elects the product vault for a decision and the default vault
        for a session — which is what stops an inherited camp-group binding from
        carrying a session out of ``default`` without anyone typing a flag.
        """
        inst = _Install(tmp_path)
        vault_config = load_script("lore.vault.config")
        vault_resolve = load_script("lore.vault.resolve")
        vaults = vault_config.load_config(
            str(inst.config_home / "lore" / "config.json"),
            env={"XDG_STATE_HOME": str(inst.state)},
        )
        scopes = {"product": "trailhead"}

        assert vault_resolve.resolve_vault(scopes, "decision", vaults).name == "trailhead"
        assert vault_resolve.resolve_vault(scopes, "session", vaults).name == "default"


# ---------------------------------------------------------------------------
# The write seam: the backstop under every producer, present and future
# ---------------------------------------------------------------------------


def _config_env(inst: _Install) -> dict:
    return {
        "XDG_STATE_HOME": str(inst.state),
        "XDG_CONFIG_HOME": str(inst.config_home),
    }


def _open_index_factory(state: Path):
    index_store = load_script("lore.search.index")

    def _open():
        return index_store.open_index(env={"XDG_STATE_HOME": str(state)})

    return _open


class TestWriteSeamRefusesANonDefaultVault:

    def test_capture_candidate_refuses_a_non_default_vault_root(self, tmp_path):
        """Varying input: the vault root handed to the capture primitive."""
        inst = _Install(tmp_path)
        store = load_script("lore.session.store")
        vault_config = load_script("lore.vault.config")

        with pytest.raises(vault_config.SessionVaultError):
            store.capture_candidate(
                SID, "- candidate entry\n",
                vault_root=str(inst.other),
                committer="tom@example.com",
                open_index=_open_index_factory(inst.state),
                env=_config_env(inst),
            )
        assert not _session_exists(inst.other, SID), "nothing may be written"

        store.capture_candidate(
            SID, "- candidate entry\n",
            vault_root=str(inst.default_vault),
            committer="tom@example.com",
            open_index=_open_index_factory(inst.state),
            env=_config_env(inst),
        )
        assert _session_exists(inst.default_vault, SID)

    def test_validate_and_write_refuses_a_session_outside_the_default_vault(
        self, tmp_path, monkeypatch
    ):
        """The generic record write API is a session producer too.

        Varying input: the destination vault root, with the kind held at
        ``session``.
        """
        inst = _Install(tmp_path)
        monkeypatch.setenv("LORE_EMAIL", "tester@example.com")
        record_store = load_script("lore.record.store")
        vault_config = load_script("lore.vault.config")
        index_store = load_script("lore.search.index")
        conn = index_store.open_index(env={"XDG_STATE_HOME": str(inst.state)})

        sidecar = {
            "version": "v1",
            "kind": "session",
            "title": SID,
            "status": "dirty",
            "annotations": {},
        }
        try:
            refused = record_store.place_record(
                SID, "session", None, vault_root=str(inst.other)
            )
            with pytest.raises(vault_config.SessionVaultError):
                record_store.validate_and_write(
                    refused, dict(sidecar), f"# session: {SID}\n", conn,
                    env=_config_env(inst),
                )
            assert not _session_exists(inst.other, SID)

            allowed = record_store.place_record(
                SID, "session", None, vault_root=str(inst.default_vault)
            )
            record_store.validate_and_write(
                allowed, dict(sidecar), f"# session: {SID}\n", conn,
                env=_config_env(inst),
            )
            assert _session_exists(inst.default_vault, SID)
        finally:
            conn.close()

    def test_a_vanilla_install_without_config_is_not_fenced(self, tmp_path):
        """Varying input: whether a ``config.json`` exists at all.

        With no config there are no configured vaults to route between, so the
        single vanilla vault accepts its own session capture. This is the branch
        that keeps Axiom 3 usage working under the rule.
        """
        vault = tmp_path / "vanilla"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        store = load_script("lore.session.store")

        store.capture_candidate(
            SID, "- candidate entry\n",
            vault_root=str(vault),
            committer="tom@example.com",
            open_index=_open_index_factory(state),
            env={"XDG_STATE_HOME": str(state), "XDG_CONFIG_HOME": str(tmp_path / "noconfig")},
        )
        assert _session_exists(vault, SID)


# ---------------------------------------------------------------------------
# Legacy sessions already sitting in a non-default vault stay readable until the
# one-shot migration moves them. The pin is on WRITES, not on reads.
# ---------------------------------------------------------------------------


def _plant_legacy_session(vault: Path, key: str, *, status: str = "dirty") -> None:
    """Write a session record straight onto disk, as a pre-pin capture left it."""
    session_dir = vault / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "version": "v1",
        "kind": "session",
        "title": key,
        "status": status,
        "created-at": "2026-08-10T00:00:00Z",
        "created-by": "tom@example.com",
        "updated-at": "2026-08-10T00:00:00Z",
        "updated-by": "tom@example.com",
        "annotations": {},
    }
    (session_dir / f"{key}.json").write_text(json.dumps(sidecar, indent=2))
    (session_dir / f"{key}.md").write_text(
        f"# session: {key}\n- candidate 2026-08-10T00:00:00Z kind=spec phase=Plan\n  legacy\n"
    )


class TestLegacySessionsStayReadable:

    def test_session_show_still_resolves_a_planted_non_default_session(self, tmp_path):
        """Varying input: which vault holds the key.

        The 229 sessions already in product/team vaults must stay reachable until
        the migration relocates them — the pin governs new writes only.
        """
        inst = _Install(tmp_path)
        _plant_legacy_session(inst.other, SID)

        shown = inst.run(["session", "show", "--session-id", SID])
        assert shown.returncode == 0, shown.stderr
        assert SID in shown.stdout

        missing = inst.run(["session", "show", "--session-id", OTHER_SID])
        assert missing.returncode != 0
