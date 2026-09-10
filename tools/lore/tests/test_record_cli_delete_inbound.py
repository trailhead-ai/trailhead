"""Tests for ``lore record delete``'s inbound-reference guard.

Deleting a record that other records still point at leaves those references
dangling, and nothing in the vault repairs them afterwards. The ordering that
prevents it — repoint first, delete second — was enforced only by an agent
reading prose correctly; this guard moves it into the tool. Test contract:

  refusal, by reference shape:
    - a body ``[[<kind>/<stem>]]`` wikilink in another record blocks the delete,
      and removing that wikilink lets the same delete through.
    - a sidecar ``related.<kind>`` edge naming the record blocks it likewise.
    - the refusal names the referrer and leaves all three artifacts in place.

  the boundaries of "reference":
    - bare prose naming the record does not block — that is exactly the shape a
      consolidation annotation leaves behind, on purpose.
    - the record's own wikilink to itself does not block its own delete.
    - a referrer whose body cannot be read blocks it: an unreadable body is not
      a cleared one, and reporting it as clear is the failure the guard exists
      to prevent.
    - a referrer in a ``shared: true`` vault is reported, not obeyed — untrusted
      content does not get a veto over the operator's own delete.

  override:
    - ``--force`` deletes anyway and names what it left dangling.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create(vault, state, *, kind, title, body, extra=()):
    r = _run(
        ["record", "create", "--kind", kind, "--title", title, *extra],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


def _target_and_referrer(tmp_path, *, referrer_body, referrer_extra=()):
    """``lesson/target`` plus one other record whose reference shape is the knob.

    Returns ``(vault, state, target_id, referrer_id)``. Every refusal test
    varies only how the referrer names the target, so the guard's definition of
    a reference is what the assertions are actually about.
    """
    vault, state = _make_vault(tmp_path)
    target = _create(vault, state, kind="lesson", title="Target", body="target body\n")
    referrer = _create(
        vault, state, kind="lesson", title="Referrer",
        body=referrer_body, extra=referrer_extra,
    )
    return vault, state, target, referrer


def _artifacts(vault, record_id):
    kind, _, name = record_id.partition("/")
    return Path(vault) / kind / f"{name}.md", Path(vault) / kind / f"{name}.json"


def _write_config(config_home: Path, vaults: list) -> Path:
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_cfg(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args, vault=vault, state_dir=state, stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


# ---------------------------------------------------------------------------
# refusal, by reference shape
# ---------------------------------------------------------------------------


def test_delete_refuses_while_a_body_wikilink_still_points_at_the_record(tmp_path):
    """The refusal names the referrer, and clearing the wikilink lifts it."""
    vault, state, target, referrer = _target_and_referrer(
        tmp_path, referrer_body="see [[lesson/target]] for context\n"
    )
    body_path, sidecar_path = _artifacts(vault, target)

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert referrer in r.stderr
    assert body_path.exists() and sidecar_path.exists()

    # Repoint the referrer, and the very same delete goes through.
    ref_body, _ = _artifacts(vault, referrer)
    ref_body.write_text("see nothing for context\n", encoding="utf-8")

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists() and not sidecar_path.exists()


def test_delete_refuses_while_a_sidecar_related_edge_still_names_the_record(tmp_path):
    """A ``related`` edge is a reference as surely as a wikilink is."""
    vault, state, target, referrer = _target_and_referrer(
        tmp_path, referrer_body="no wikilink here\n",
        referrer_extra=("--related", "lesson=target"),
    )
    body_path, _ = _artifacts(vault, target)

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode != 0
    assert referrer in r.stderr
    assert body_path.exists()

    r = _run(
        ["record", "update", referrer, "--unset-related", "lesson=target"],
        vault=vault, state_dir=state, stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists()


# ---------------------------------------------------------------------------
# the boundaries of "reference"
# ---------------------------------------------------------------------------


def test_delete_is_not_blocked_by_bare_prose_naming_the_record(tmp_path):
    """A consolidation annotation names the folded record in prose, deliberately
    not as a link. Prose is not navigation, so it must not block the delete."""
    vault, state, target, _ = _target_and_referrer(
        tmp_path,
        referrer_body="lesson/target (consolidated into [[lesson/survivor]])\n",
    )
    body_path, _ = _artifacts(vault, target)

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists()


def test_delete_is_not_blocked_by_the_records_own_self_reference(tmp_path):
    """A record linking to itself is not a reason it cannot be deleted."""
    vault, state = _make_vault(tmp_path)
    target = _create(
        vault, state, kind="lesson", title="Target",
        body="I am [[lesson/target]]\n",
    )
    body_path, _ = _artifacts(vault, target)

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists()


def test_delete_refuses_when_a_records_body_could_not_be_read(tmp_path):
    """An unreadable body was not checked, so it cannot be reported clear. The
    refusal says so in its own words rather than counting it as a reference."""
    vault, state, target, referrer = _target_and_referrer(
        tmp_path, referrer_body="clean body\n"
    )
    ref_body, _ = _artifacts(vault, referrer)
    ref_body.write_bytes(b"\xff\xfe not utf-8 at all\n")
    body_path, _ = _artifacts(vault, target)

    r = _run(["record", "delete", target], vault=vault, state_dir=state)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert referrer in r.stderr
    assert body_path.exists()


def test_delete_is_not_vetoed_by_a_referrer_in_a_shared_vault(tmp_path):
    """Content the operator does not control cannot block their delete. It is
    reported — the reference really will dangle — and the delete proceeds."""
    default_vault, state = _make_vault(tmp_path)
    shared_vault = tmp_path / "vault_shared"
    shared_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {
                "name": "team-notes", "scope": "team", "records": ["decision"],
                "path": str(shared_vault), "shared": True,
            },
        ],
    )
    r = _run_cfg(
        ["record", "create", "--kind", "lesson", "--title", "Target"],
        vault=default_vault, state=state, config_home=config_home,
        stdin_text="target body\n",
    )
    assert r.returncode == 0, r.stderr
    target = r.stdout.strip()
    r = _run_cfg(
        ["record", "create", "--kind", "decision", "--title", "Outside",
         "--team", "team-notes"],
        vault=default_vault, state=state, config_home=config_home,
        stdin_text="see [[lesson/target]]\n",
    )
    assert r.returncode == 0, r.stderr
    outsider = r.stdout.strip()
    body_path, _ = _artifacts(default_vault, target)

    r = _run_cfg(["record", "delete", target],
                 vault=default_vault, state=state, config_home=config_home)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists()
    assert outsider in (r.stderr + r.stdout)


# ---------------------------------------------------------------------------
# override
# ---------------------------------------------------------------------------


def test_delete_force_deletes_anyway_and_names_what_it_left_dangling(tmp_path):
    """The override is not silence: it says which references it just broke."""
    vault, state, target, referrer = _target_and_referrer(
        tmp_path, referrer_body="see [[lesson/target]] for context\n"
    )
    body_path, sidecar_path = _artifacts(vault, target)

    r = _run(["record", "delete", target, "--force"], vault=vault, state_dir=state)

    assert r.returncode == 0, r.stderr
    assert not body_path.exists() and not sidecar_path.exists()
    assert referrer in (r.stderr + r.stdout)
