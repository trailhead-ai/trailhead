"""Sidecar walk behind ``lore pipeline`` — collection, degradation, graph shape.

The walk is the only vault I/O on this surface. These tests lock the three
properties everything downstream rests on:

  1. It reads ``<vault>/<kind>/*.json`` sidecars and NOTHING else — a record's
     ``.md`` body is never opened, so each record is one atomically-written
     snapshot and a vault git-sync pull cannot hand back a half-record.
  2. Every read failure degrades to a named warning or a per-vault error
     marker; nothing raises out of the walk, and one bad file never costs the
     surrounding vault its remaining records.
  3. Each vault's records come back as its own ``{"kind/name": sidecar}``
     mapping — the shape ``record.graph.evaluate_dependencies`` consumes
     directly, per vault, never merged across vaults.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import load_script


def _walk_mod():
    return load_script("lore.pipeline.walk")


def _write_sidecar(vault: Path, kind: str, name: str, sidecar: dict) -> Path:
    kind_dir = vault / kind
    kind_dir.mkdir(parents=True, exist_ok=True)
    path = kind_dir / f"{name}.json"
    path.write_text(json.dumps({"kind": kind, **sidecar}), encoding="utf-8")
    (kind_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return path


class TestCollection:
    def test_walks_adr_spec_and_task_sidecars_keyed_by_qualified_id(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "adr", "root", {"title": "Root", "status": "active"})
        _write_sidecar(vault, "spec", "derived", {"title": "Derived", "status": "draft"})
        _write_sidecar(vault, "task", "chore", {"title": "Chore", "status": "open"})

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert result.error is None
        assert set(result.records) == {"adr/root", "spec/derived", "task/chore"}
        assert result.records["adr/root"]["title"] == "Root"
        assert result.warnings == ()

    def test_kinds_outside_the_pipeline_set_are_not_walked(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "kept", {"title": "Kept"})
        _write_sidecar(vault, "lesson", "ignored", {"title": "Ignored"})

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert set(result.records) == {"spec/kept"}

    def test_sidecar_value_is_the_parsed_json_verbatim_with_no_injected_keys(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "verbatim", {"title": "T", "status": "draft"})

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert result.records["spec/verbatim"] == {
            "kind": "spec",
            "title": "T",
            "status": "draft",
        }

    def test_shared_flag_is_carried_through_from_the_caller(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "s", {"title": "T"})

        assert _walk_mod().walk_vault("v", str(vault), shared=True).shared is True
        assert _walk_mod().walk_vault("v", str(vault), shared=False).shared is False


class TestBodiesAreNeverOpened:
    def test_records_walk_with_every_body_deleted(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "a", {"title": "A"})
        _write_sidecar(vault, "adr", "b", {"title": "B"})
        for body in vault.rglob("*.md"):
            body.unlink()

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert set(result.records) == {"spec/a", "adr/b"}
        assert result.warnings == ()
        assert result.error is None

    def test_no_markdown_path_is_ever_read(self, tmp_path, monkeypatch):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "a", {"title": "A"})
        opened: list[str] = []
        real_read_text = Path.read_text

        def spying_read_text(self, *args, **kwargs):
            opened.append(str(self))
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", spying_read_text)
        _walk_mod().walk_vault("v", str(vault), shared=False)

        assert opened == [str(vault / "spec" / "a.json")]


class TestPerFileDegradation:
    def test_invalid_json_becomes_a_warning_and_the_vault_still_renders(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "good", {"title": "Good"})
        (vault / "spec" / "broken.json").write_text("{not json", encoding="utf-8")

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert result.error is None
        assert set(result.records) == {"spec/good"}
        assert [w.file for w in result.warnings] == ["spec/broken.json"]
        assert "invalid JSON" in result.warnings[0].message

    def test_non_object_json_becomes_a_warning(self, tmp_path):
        vault = tmp_path / "v"
        (vault / "spec").mkdir(parents=True)
        (vault / "spec" / "listy.json").write_text("[1, 2]", encoding="utf-8")

        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert result.records == {}
        assert [w.file for w in result.warnings] == ["spec/listy.json"]
        assert "not a JSON object" in result.warnings[0].message

    def test_sidecar_deleted_between_listing_and_open_degrades_to_a_warning(
        self, tmp_path, monkeypatch
    ):
        """The torn read a vault git-sync pull produces: the directory listing
        names a file that is gone by the time it is opened. The vault's walk
        continues; the loss is reported, never raised."""
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "aaa", {"title": "First"})
        _write_sidecar(vault, "spec", "torn", {"title": "Torn"})
        _write_sidecar(vault, "spec", "zzz", {"title": "Last"})

        real_read_text = Path.read_text

        def tearing_read_text(self, *args, **kwargs):
            if self.name == "torn.json":
                self.unlink()
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", tearing_read_text)
        result = _walk_mod().walk_vault("v", str(vault), shared=False)

        assert result.error is None
        assert set(result.records) == {"spec/aaa", "spec/zzz"}
        assert [w.file for w in result.warnings] == ["spec/torn.json"]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
    def test_unreadable_kind_directory_is_a_warning_not_a_vault_error(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "kept", {"title": "Kept"})
        locked = vault / "task"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            result = _walk_mod().walk_vault("v", str(vault), shared=False)
        finally:
            locked.chmod(stat.S_IRWXU)

        assert result.error is None
        assert set(result.records) == {"spec/kept"}
        assert [w.file for w in result.warnings] == ["task/"]


class TestVaultLevelDegradation:
    def test_missing_vault_directory_yields_an_error_marker(self, tmp_path):
        result = _walk_mod().walk_vault("gone", str(tmp_path / "nope"), shared=False)

        assert result.error is not None
        assert result.records == {}
        assert result.warnings == ()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
    def test_unreadable_vault_directory_yields_an_error_marker(self, tmp_path):
        vault = tmp_path / "v"
        _write_sidecar(vault, "spec", "a", {"title": "A"})
        vault.chmod(0o000)
        try:
            result = _walk_mod().walk_vault("v", str(vault), shared=False)
        finally:
            vault.chmod(stat.S_IRWXU)

        assert result.error is not None
        assert result.records == {}

    def test_vault_with_no_records_is_consulted_cleanly(self, tmp_path):
        vault = tmp_path / "empty"
        vault.mkdir()

        result = _walk_mod().walk_vault("empty", str(vault), shared=False)

        assert result.error is None
        assert result.records == {}
        assert result.warnings == ()


class TestWalkVaults:
    def test_each_vault_keeps_its_own_record_mapping(self, tmp_path):
        one = tmp_path / "one"
        two = tmp_path / "two"
        _write_sidecar(one, "adr", "same", {"title": "One's ADR"})
        _write_sidecar(two, "adr", "same", {"title": "Two's ADR"})

        walks = _walk_mod().walk_vaults(
            [("one", one), ("two", two)], shared_names={"two"}
        )

        assert [w.name for w in walks] == ["one", "two"]
        assert walks[0].records["adr/same"]["title"] == "One's ADR"
        assert walks[1].records["adr/same"]["title"] == "Two's ADR"
        assert walks[0].shared is False
        assert walks[1].shared is True

    def test_a_broken_vault_does_not_stop_the_others(self, tmp_path):
        good = tmp_path / "good"
        _write_sidecar(good, "spec", "s", {"title": "S"})

        walks = _walk_mod().walk_vaults(
            [("missing", tmp_path / "nope"), ("good", good)], shared_names=set()
        )

        assert walks[0].error is not None
        assert walks[1].error is None
        assert set(walks[1].records) == {"spec/s"}


class TestEvaluatorGraphShape:
    """The walk's per-vault mapping is the evaluator's ``design_graph`` as-is."""

    def test_walk_records_feed_evaluate_dependencies_with_no_adapter(self, tmp_path):
        graph_mod = load_script("lore.record.graph")
        vault = tmp_path / "v"
        _write_sidecar(vault, "adr", "anchor", {"title": "Anchor", "status": "active"})
        _write_sidecar(vault, "spec", "shaped", {"title": "Shaped", "status": "draft"})

        records = _walk_mod().walk_vault("v", str(vault), shared=False).records
        statuses = graph_mod.evaluate_dependencies(
            records, ["adr/anchor", "spec/shaped@planned", "spec/absent"]
        )

        assert [s.met for s in statuses] == [True, False, False]
        assert [s.reason_code for s in statuses] == [None, "short-of-stage", "missing"]

    def test_a_second_vaults_records_cannot_satisfy_the_first_vaults_dependency(
        self, tmp_path
    ):
        """Confinement is the caller's: evaluating one vault's graph must not
        see another vault's same-named record."""
        graph_mod = load_script("lore.record.graph")
        personal = tmp_path / "personal"
        other = tmp_path / "other"
        _write_sidecar(personal, "spec", "gate", {"title": "Gate", "status": "draft"})
        _write_sidecar(other, "spec", "gate", {"title": "Gate", "status": "complete"})

        walks = _walk_mod().walk_vaults(
            [("personal", personal), ("other", other)], shared_names={"other"}
        )
        personal_only = graph_mod.evaluate_dependencies(
            walks[0].records, ["spec/gate@complete"]
        )

        assert personal_only[0].met is False
        assert personal_only[0].reason_code == "short-of-stage"
