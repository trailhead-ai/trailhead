"""Canonical sidecar serialization + ``lore vault reformat``.

The ADR (``adr/record-storage-text-is-truth-the-index-is-derived``) calls sidecar
formatting load-bearing for git: pretty-printed, keys sorted, trailing newline.
These tests pin that byte shape AND the property it exists for — two edits to
disjoint fields merge cleanly with git's own 3-way merge — plus the volatile
``updated-at``/``updated-by`` pair being serialized last, so provenance churn
never lands in the diff context of a semantic key.

``lore vault reformat`` is the one-time migration verb: it rewrites every
existing sidecar in every configured vault into canonical form, under each
vault's ``vault_write_lock`` (it is a tree mutation like any other), and touches
the index not at all.

Convention (Axiom 6): CLI-level assertions run the real CLI as a subprocess with
``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` fenced into ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from conftest import load_script, make_vault, run_cli

REPO_ROOT = Path(__file__).parent.parent
SESSION_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


def _sidecar_mod():
    return load_script("lore.record.sidecar")


def _full_sidecar(**over):
    sidecar = {
        "title": "My Spec",
        "kind": "spec",
        "status": "draft",
        "keywords": ["zeta", "alpha"],
        "labels": {"worktree": "s5", "claude-code/model": "opus"},
        "created-at": "2026-01-01T00:00:00Z",
        "created-by": "tester@example.com",
        "updated-at": "2026-01-02T00:00:00Z",
        "updated-by": "tester@example.com",
    }
    sidecar.update(over)
    return sidecar


# ---------------------------------------------------------------------------
# 1 — the canonical byte shape
# ---------------------------------------------------------------------------


def test_canonical_form_is_pretty_sorted_and_newline_terminated():
    """Pretty-printed, semantic keys sorted, exactly one trailing newline."""
    sidecar = _sidecar_mod()
    text = sidecar.dumps(_full_sidecar())

    assert text.endswith("\n") and not text.endswith("\n\n")
    lines = text.split("\n")
    assert lines[0] == "{"
    # One key per line — pretty-printed, not compact.
    assert len(lines) > 5

    top_keys = [
        json.loads(line.strip().split(":", 1)[0].strip().rstrip(","))
        for line in lines[1:-2]
        if line.startswith('  "')
    ]
    semantic = [k for k in top_keys if k not in ("updated-at", "updated-by")]
    assert semantic == sorted(semantic)


def test_volatile_provenance_keys_serialize_after_every_semantic_key():
    """``updated-at``/``updated-by`` are last, in that order."""
    sidecar = _sidecar_mod()
    text = sidecar.dumps(_full_sidecar())
    top_keys = [
        json.loads(line.strip().split(":", 1)[0].strip().rstrip(","))
        for line in text.split("\n")
        if line.startswith('  "')
    ]
    assert top_keys[-2:] == ["updated-at", "updated-by"]


def test_canonical_form_round_trips_equal():
    """Parsing the canonical text back yields the input dict."""
    sidecar = _sidecar_mod()
    original = _full_sidecar()
    assert json.loads(sidecar.dumps(original)) == original


def test_nested_maps_are_sorted_but_lists_keep_their_order():
    """Nested maps sort (stable bytes); list values are semantic and never reordered."""
    sidecar = _sidecar_mod()
    text = sidecar.dumps(_full_sidecar())
    label_keys = [
        m.group(1)
        for m in (re.match(r'^    "([^"]+)": ', line) for line in text.split("\n"))
        if m
    ]
    assert label_keys == sorted(label_keys)
    assert json.loads(text)["keywords"] == ["zeta", "alpha"]


def test_serializer_is_idempotent():
    sidecar = _sidecar_mod()
    once = sidecar.dumps(_full_sidecar())
    assert sidecar.dumps(json.loads(once)) == once


# ---------------------------------------------------------------------------
# 2 — the property the format exists for: git-mergeable sidecars
# ---------------------------------------------------------------------------


def _merge_file(tmp_path, base_text, local_text, remote_text):
    """3-way merge via git's own ``merge-file``; returns (returncode, merged)."""
    base = tmp_path / "base.json"
    local = tmp_path / "local.json"
    remote = tmp_path / "remote.json"
    base.write_text(base_text)
    local.write_text(local_text)
    remote.write_text(remote_text)
    r = subprocess.run(
        ["git", "merge-file", "-p", str(local), str(base), str(remote)],
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout


def test_disjoint_field_edits_merge_cleanly(tmp_path):
    """Two edits touching different fields merge with no conflict."""
    sidecar = _sidecar_mod()
    base = _full_sidecar()
    local = _full_sidecar(status="ready")
    remote = _full_sidecar(keywords=["zeta", "alpha", "gamma"])

    rc, merged = _merge_file(
        tmp_path,
        sidecar.dumps(base),
        sidecar.dumps(local),
        sidecar.dumps(remote),
    )
    assert rc == 0, f"disjoint sidecar edits conflicted:\n{merged}"
    parsed = json.loads(merged)
    assert parsed["status"] == "ready"
    assert parsed["keywords"] == ["zeta", "alpha", "gamma"]


def test_adjacent_key_edits_still_conflict(tmp_path):
    """Documented limit: git merges LINES. Two keys that serialize as neighboring
    lines leave no context between them, so edits to both still conflict — which
    is why field-wise resolution exists at all."""
    sidecar = _sidecar_mod()
    rc, _ = _merge_file(
        tmp_path,
        sidecar.dumps(_full_sidecar()),
        sidecar.dumps(_full_sidecar(status="ready")),
        sidecar.dumps(_full_sidecar(title="Renamed Spec")),
    )
    assert rc != 0


def test_compact_form_conflicts_on_the_same_disjoint_edits(tmp_path):
    """Control: the compact one-line form makes the same edits a whole-file conflict."""
    def compact(d):
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    rc, _ = _merge_file(
        tmp_path,
        compact(_full_sidecar()),
        compact(_full_sidecar(status="ready")),
        compact(_full_sidecar(title="Renamed Spec")),
    )
    assert rc != 0


def test_updated_at_only_change_touches_no_semantic_key_line(tmp_path):
    """A provenance-only bump diffs against the volatile tail alone."""
    sidecar = _sidecar_mod()
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(sidecar.dumps(_full_sidecar()))
    new.write_text(sidecar.dumps(_full_sidecar(**{"updated-at": "2026-02-02T00:00:00Z"})))
    r = subprocess.run(
        ["git", "diff", "--no-index", "-U0", str(old), str(new)],
        capture_output=True,
        text=True,
    )
    changed = [
        line for line in r.stdout.split("\n")
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    ]
    assert changed, "expected a diff"
    for line in changed:
        assert "updated-at" in line, f"provenance bump touched a semantic line: {line!r}"


# ---------------------------------------------------------------------------
# 3 — every write site emits the canonical form
# ---------------------------------------------------------------------------


def test_record_create_writes_canonical_sidecar(tmp_path):
    sidecar_mod = _sidecar_mod()
    vault, state = make_vault(tmp_path)
    r = run_cli(
        ["record", "create", "--kind", "spec", "--title", "Canon Check"],
        vault=vault, state_dir=state, stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr
    path = vault / "spec" / "canon-check.json"
    raw = path.read_text()
    assert raw == sidecar_mod.dumps(json.loads(raw))


def test_session_candidate_writes_canonical_sidecar(tmp_path):
    sidecar_mod = _sidecar_mod()
    vault, state = make_vault(tmp_path)
    r = run_cli(
        ["session", "candidate", "--session-id", SESSION_ID,
         "--kind", "decision", "--phase", "Build"],
        vault=vault, state_dir=state, stdin_text="a candidate\n",
    )
    assert r.returncode == 0, r.stderr
    raw = (vault / "session" / f"{SESSION_ID}.json").read_text()
    assert raw == sidecar_mod.dumps(json.loads(raw))


# ---------------------------------------------------------------------------
# 4 — lore vault reformat
# ---------------------------------------------------------------------------


def _compact_all(vault: Path) -> None:
    """Rewrite every sidecar in *vault* into the pre-ADR compact form."""
    for path in vault.glob("*/*.json"):
        parsed = json.loads(path.read_text())
        path.write_text(json.dumps(parsed, sort_keys=True, separators=(",", ":")))


def _index_rows(state: Path):
    conn = sqlite3.connect(str(state / "lore" / "index.sqlite"))
    try:
        return conn.execute("SELECT * FROM records ORDER BY id").fetchall()
    finally:
        conn.close()


def _seeded_vault(tmp_path):
    vault, state = make_vault(tmp_path)
    for title in ("Alpha Rec", "Beta Rec"):
        r = run_cli(
            ["record", "create", "--kind", "spec", "--title", title],
            vault=vault, state_dir=state, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
    return vault, state


def test_reformat_rewrites_compact_sidecars_to_canonical(tmp_path):
    sidecar_mod = _sidecar_mod()
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)

    r = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    for path in vault.glob("*/*.json"):
        raw = path.read_text()
        assert raw == sidecar_mod.dumps(json.loads(raw)), path


def test_reformat_preserves_sidecar_content(tmp_path):
    vault, state = _seeded_vault(tmp_path)
    before = {p.name: json.loads(p.read_text()) for p in vault.glob("*/*.json")}
    _compact_all(vault)

    assert run_cli(["vault", "reformat"], vault=vault, state_dir=state).returncode == 0
    after = {p.name: json.loads(p.read_text()) for p in vault.glob("*/*.json")}
    assert after == before


def test_reformat_is_idempotent_on_canonical_sidecars(tmp_path):
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)
    assert run_cli(["vault", "reformat"], vault=vault, state_dir=state).returncode == 0

    stamps = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in vault.glob("*/*.json")}
    time.sleep(0.01)
    r = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    for path, (raw, mtime) in stamps.items():
        assert path.read_bytes() == raw
        assert path.stat().st_mtime_ns == mtime, f"{path} was rewritten needlessly"


def test_reformat_reindexes_nothing(tmp_path):
    """Content is unchanged, so every index row must be byte-identical after."""
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)
    before = _index_rows(state)

    assert run_cli(["vault", "reformat"], vault=vault, state_dir=state).returncode == 0
    assert _index_rows(state) == before


def test_reformat_reports_per_vault_counts(tmp_path):
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)

    r = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "default" in r.stdout
    assert "2 rewritten" in r.stdout

    again = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
    assert "0 rewritten" in again.stdout
    assert "2 already canonical" in again.stdout


def test_reformat_skips_a_malformed_sidecar_and_reports_it(tmp_path):
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)
    broken = vault / "spec" / "alpha-rec.json"
    broken.write_text("{ not json")

    r = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert broken.read_text() == "{ not json"
    assert "1 skipped" in r.stdout


def test_reformat_leaves_the_sites_tree_alone(tmp_path):
    """``sites/`` is the vault's static-site free-write zone, not a record tree."""
    vault, state = _seeded_vault(tmp_path)
    site = vault / "sites" / "demo"
    site.mkdir(parents=True)
    payload = '{"a":1,"b":2}'
    (site / "data.json").write_text(payload)

    assert run_cli(["vault", "reformat"], vault=vault, state_dir=state).returncode == 0
    assert (site / "data.json").read_text() == payload


# ---------------------------------------------------------------------------
# 5 — reformat is a tree mutation: it runs under the vault write lock
# ---------------------------------------------------------------------------


_HOLDER = r"""
import pathlib, sys, time
sys.path.insert(0, sys.argv[4])
from lore import locking

vault, hold_for, new_sidecar = sys.argv[1], float(sys.argv[2]), sys.argv[3]
with locking.vault_write_lock(vault):
    pathlib.Path(vault, "_held").write_text("1")
    # A write that lands *while reformat waits* — it must survive and be picked
    # up, never clobbered by a read-then-rewrite that ignored the lock.
    pathlib.Path(new_sidecar).write_text(sys.argv[5])
    time.sleep(hold_for)
"""


def test_reformat_waits_for_a_concurrent_writer_and_never_loses_its_write(tmp_path):
    """A write landing while the lock is held is serialized, never lost."""
    sidecar_mod = _sidecar_mod()
    vault, state = _seeded_vault(tmp_path)
    _compact_all(vault)

    landed = vault / "spec" / "landed-mid-reformat.json"
    (vault / "spec" / "landed-mid-reformat.md").write_text("body\n")

    payload = json.dumps(
        {
            "title": "Landed Mid Reformat", "kind": "spec", "status": "draft",
            "created-at": "2026-01-01T00:00:00Z", "created-by": "t@example.com",
            "updated-at": "2026-01-01T00:00:00Z", "updated-by": "t@example.com",
        },
        sort_keys=True, separators=(",", ":"),
    )
    holder = subprocess.Popen(
        [
            sys.executable, "-c", _HOLDER, str(vault), "1.0", str(landed),
            str(REPO_ROOT / "plugins" / "lore"), payload,
        ],
        stderr=subprocess.PIPE, text=True,
        env={**os.environ, "XDG_STATE_HOME": str(state)},
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (vault / "_held").exists():
        time.sleep(0.005)
    assert (vault / "_held").exists(), "holder never acquired the vault lock"

    try:
        t0 = time.monotonic()
        r = run_cli(["vault", "reformat"], vault=vault, state_dir=state)
        waited = time.monotonic() - t0
    finally:
        holder.wait(timeout=15)

    assert r.returncode == 0, r.stderr
    assert waited >= 0.4, f"reformat did not block on the vault lock ({waited:.3f}s)"
    raw = landed.read_text()
    assert json.loads(raw)["title"] == "Landed Mid Reformat"
    assert raw == sidecar_mod.dumps(json.loads(raw))
