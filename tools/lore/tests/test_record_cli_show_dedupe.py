"""Tests for repeat-fetch dedupe on ``lore record show``.

Within one session a record's body does not change between the first fetch and
the second, and the first copy is already in the agent's context window. The
second copy buys nothing and is re-ingested on every subsequent turn, so
``record show`` records what it has already shown (in a machine-local per-session
shown-set under ``state_dir("lore")/shown``, outside the vault — ``record show``
is a read path and the session record is a syncing vault record) and answers a
repeat with a compact acknowledgement instead of the body.

The hazard that shapes the contract is compaction: "it is already in context"
stops being true the moment the session compacts, and an agent cannot tell it is
being starved. So dedupe never silently withholds — it names ``--full`` in its
own response, and it fails OPEN in every case where "already shown" cannot be
established about content the agent actually saw. Test contract:

  dedupe:
    - the first show in a session prints the full body unchanged.
    - a second show of the same id in the same session prints the compact form:
      the record id, its status, its ``updated-at``, and the literal ``--full``,
      with no body text — and materially smaller than the body.
  the escape:
    - ``--full`` returns the whole body with no dedupe applied.
  fail-open:
    - no session id in the environment → both shows print the full body.
    - a resolvable worktree name but no session id → both shows print the full
      body; the worktree fallback key is stable across DIFFERENT sessions in one
      worktree, so keying dedupe on it would suppress a first-ever show.
    - two different session ids → both shows print the full body.
    - a body that changed between the two shows → the second prints the full body.
  --json parity:
    - the deduped envelope keeps ``record_id``/``kind``/``name``/``sidecar`` and
      replaces ``body`` with ``null`` plus ``deduped: true`` and a ``hint``
      naming ``--full``, so a JSON consumer detects the compact form
      structurally rather than by parsing prose.
    - ``--json --full`` on a repeat emits the full envelope, not deduped.
  scope:
    - dedupe is applied in ``record show`` alone, never in the shared renderer,
      so two consecutive ``lore session show`` calls both print the full body.
  confinement:
    - a session key that would escape the shown-set root is rejected, not written.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run  # noqa: F401

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"

#: A multi-paragraph fixture body, so "the compact form is materially smaller"
#: is a real measurement rather than an artifact of a one-line record.
LONG_BODY = "\n\n".join(
    f"Paragraph {n}: " + ("the quick brown fox jumps over the lazy dog. " * 12)
    for n in range(1, 9)
) + "\n"

#: Env overlay that guarantees NO session id, whatever the developer's shell or
#: CI runner has exported. ``_session_id_from_args_or_env`` treats "" as unset.
NO_SESSION = {"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""}


def _session_env(session_id: str) -> dict:
    return {"CLAUDE_CODE_SESSION_ID": session_id, "CLAUDE_SESSION_ID": ""}


def _create_record(vault, state, *, kind="task", title="Dedupe Fixture",
                   body=LONG_BODY) -> str:
    r = _run(
        ["record", "create", "--kind", kind, "--title", title, "--keyword", "test"],
        vault=vault, state_dir=state, stdin_text=body, env_extra=NO_SESSION,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


def _show(rid, vault, state, *, extra=None, env_extra=None, cwd=None):
    r = _run(
        ["record", "show", rid, *(extra or [])],
        vault=vault, state_dir=state, env_extra=env_extra, cwd=cwd,
    )
    assert r.returncode == 0, f"show failed: {r.stderr}"
    return r


# ---------------------------------------------------------------------------
# dedupe: first show full, second show compact
# ---------------------------------------------------------------------------

def test_first_show_in_a_session_prints_the_full_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    r = _show(rid, vault, state, env_extra=_session_env(SESSION_A))
    assert "Paragraph 1:" in r.stdout
    assert "Paragraph 8:" in r.stdout


def test_first_show_is_byte_identical_to_the_undeduped_output(tmp_path):
    """The first show must not perturb the existing output by one byte."""
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    deduping = _show(rid, vault, state, env_extra=_session_env(SESSION_A))
    baseline = _show(rid, vault, state, extra=["--full"],
                     env_extra=_session_env(SESSION_B))
    assert deduping.stdout == baseline.stdout


def test_second_show_in_the_same_session_prints_the_compact_form(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    _show(rid, vault, state, env_extra=env)
    second = _show(rid, vault, state, env_extra=env)
    assert rid in second.stdout
    assert "open" in second.stdout, "the compact form states the record's status"
    assert "updated-at" in second.stdout
    assert "--full" in second.stdout
    assert "Paragraph 1:" not in second.stdout
    assert "Paragraph 8:" not in second.stdout


def test_compact_form_is_materially_smaller_than_the_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    first = _show(rid, vault, state, env_extra=env)
    second = _show(rid, vault, state, env_extra=env)
    assert len(second.stdout) < len(first.stdout) / 4


# ---------------------------------------------------------------------------
# the escape: --full
# ---------------------------------------------------------------------------

def test_full_flag_returns_the_body_after_a_first_show(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    _show(rid, vault, state, env_extra=env)
    r = _show(rid, vault, state, extra=["--full"], env_extra=env)
    assert "Paragraph 1:" in r.stdout
    assert "Paragraph 8:" in r.stdout


# ---------------------------------------------------------------------------
# fail-open
# ---------------------------------------------------------------------------

def test_no_session_id_both_shows_print_the_full_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    for _ in range(2):
        r = _show(rid, vault, state, env_extra=NO_SESSION)
        assert "Paragraph 8:" in r.stdout


def test_worktree_fallback_never_keys_dedupe(tmp_path):
    """A resolvable worktree name with no session id must not suppress a show."""
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    project = tmp_path / "worktrees" / "some-worktree"
    project.mkdir(parents=True, exist_ok=True)
    env = dict(NO_SESSION)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    for _ in range(2):
        r = _show(rid, vault, state, env_extra=env, cwd=project)
        assert "Paragraph 8:" in r.stdout


def test_different_session_ids_both_print_the_full_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    first = _show(rid, vault, state, env_extra=_session_env(SESSION_A))
    second = _show(rid, vault, state, env_extra=_session_env(SESSION_B))
    assert "Paragraph 8:" in first.stdout
    assert "Paragraph 8:" in second.stdout


def test_changed_body_defeats_dedupe(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    _show(rid, vault, state, env_extra=env)
    upd = _run(["record", "update", rid], vault=vault, state_dir=state,
               stdin_text="rewritten body: the marmot ate the parsnip\n",
               env_extra=env)
    assert upd.returncode == 0, f"update failed: {upd.stderr}"
    second = _show(rid, vault, state, env_extra=env)
    assert "the marmot ate the parsnip" in second.stdout
    assert "--full" not in second.stdout


# ---------------------------------------------------------------------------
# --json parity
# ---------------------------------------------------------------------------

def test_json_first_show_keeps_the_existing_envelope(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    r = _show(rid, vault, state, extra=["--json"], env_extra=_session_env(SESSION_A))
    payload = json.loads(r.stdout)
    assert payload["record_id"] == rid
    assert isinstance(payload["body"], str)
    assert "Paragraph 8:" in payload["body"]
    assert not payload.get("deduped")


def test_json_repeat_show_emits_the_deduped_envelope(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    first = json.loads(_show(rid, vault, state, extra=["--json"], env_extra=env).stdout)
    second = json.loads(_show(rid, vault, state, extra=["--json"], env_extra=env).stdout)
    for key in ("record_id", "kind", "name", "sidecar"):
        assert second[key] == first[key], f"{key} must survive dedupe unchanged"
    assert second["body"] is None
    assert second["deduped"] is True
    assert "--full" in second["hint"]


def test_json_full_on_a_repeat_is_not_deduped(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state)
    env = _session_env(SESSION_A)
    _show(rid, vault, state, extra=["--json"], env_extra=env)
    payload = json.loads(
        _show(rid, vault, state, extra=["--json", "--full"], env_extra=env).stdout
    )
    assert isinstance(payload["body"], str)
    assert "Paragraph 8:" in payload["body"]
    assert not payload.get("deduped")


# ---------------------------------------------------------------------------
# scope: session show is untouched
# ---------------------------------------------------------------------------

def test_session_show_is_unaffected_by_dedupe(tmp_path):
    vault, state = _make_vault(tmp_path)
    env = _session_env(SESSION_A)
    cap = _run(["session", "candidate", "--kind", "lesson", "--phase", "Build"],
               vault=vault, state_dir=state,
               stdin_text="a finding worth keeping\n", env_extra=env)
    assert cap.returncode == 0, f"candidate failed: {cap.stderr}"
    outputs = []
    for _ in range(2):
        r = _run(["session", "show"], vault=vault, state_dir=state, env_extra=env)
        assert r.returncode == 0, f"session show failed: {r.stderr}"
        outputs.append(r.stdout)
    assert "a finding worth keeping" in outputs[0]
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# confinement
# ---------------------------------------------------------------------------

def test_shown_path_confines_the_session_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    m = load_script("lore.cli.shown_state")
    layers = load_script("lore.vault.layers")
    benign = m.shown_path(SESSION_A)
    assert benign.parent == m.shown_state_root()
    for escape in ("../evil", "..", "a/b", "a\\b", "sub/../../evil", ""):
        with pytest.raises(layers.LayerConfinementError):
            m.shown_path(escape)
