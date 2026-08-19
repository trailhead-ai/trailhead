"""Lineage membership — the board's whole filter, derived from sidecars alone.

Membership is a read-time derivation with nothing stored, so these tests feed
the derivation synthetic walks and read back the lineages. Three properties
carry weight beyond the plain rules:

  1. **Own-vault confinement.** A ``related: adr=`` edge resolves only against
     its own vault's adrs. A merged lookup would let one vault's record decide
     another vault's membership, so the same adr name in two vaults must
     anchor two lineages that never see each other.
  2. **Terminal records leave a trace, not a row.** A finished member is
     omitted from the lineage but survives in its count, and a dropped one
     leaves neither — so "3 of 5 done" and "3 of 4 done, 1 abandoned" stay
     distinguishable.
  3. **An unresolvable edge is visible, never silently dropped.** It becomes
     its own flagged lineage carrying the raw value that failed to resolve.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lore.pipeline import derive
from lore.pipeline.walk import VaultWalk

DERIVE_MODULE = (
    Path(__file__).parent.parent / "plugins" / "lore" / "lore" / "pipeline" / "derive.py"
)


def _walk(name: str, records: dict, *, shared: bool = False) -> VaultWalk:
    """A walked vault carrying *records*, with no read failures."""
    return VaultWalk(name, f"/vaults/{name}", shared, None, records, ())


def _adr(status: str = "active", **extra) -> dict:
    return {"kind": "adr", "title": "An ADR", "status": status, **extra}


def _spec(status: str = "ready", *, adrs=None, **extra) -> dict:
    sidecar = {"kind": "spec", "title": "A spec", "status": status, **extra}
    if adrs is not None:
        sidecar["related"] = {"adr": list(adrs)}
    return sidecar


def _task(status: str = "open", *, route=None, **extra) -> dict:
    sidecar = {"kind": "task", "title": "A task", "status": status, **extra}
    if route is not None:
        sidecar["labels"] = {"route": route}
    return sidecar


def _ids(lineages) -> list[str]:
    return [lineage.id for lineage in lineages]


def _derive(walks) -> list:
    """The lineages one derivation yields, dropping its warnings."""
    return list(derive.derive_board(walks).lineages)


class TestAdrAnchoredLineages:
    def test_an_active_adr_with_a_non_terminal_member_renders_rooted_at_the_adr(self):
        lineages = _derive(
            [_walk("local", {"adr/board": _adr(), "spec/one": _spec(adrs=["board"])})]
        )

        assert len(lineages) == 1
        assert lineages[0].root.record_id == "adr/board"
        assert [m.record_id for m in lineages[0].members] == ["spec/one"]

    def test_an_active_adr_with_no_non_terminal_members_does_not_render(self):
        lineages = _derive([_walk("local", {"adr/board": _adr()})])

        assert lineages == []

    def test_a_draft_adr_alone_sustains_its_own_lineage(self):
        """A draft root is itself the in-flight work — it needs gauntleting —
        so it renders before any spec has been derived from it."""
        lineages = _derive(
            [_walk("local", {"adr/fresh": _adr("draft")})]
        )

        assert _ids(lineages) == ["local:adr/fresh"]
        assert lineages[0].members == ()

    def test_an_active_adr_whose_only_members_are_terminal_does_not_render(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {"adr/board": _adr(), "spec/done": _spec("complete", adrs=["board"])},
                )
            ]
        )

        assert lineages == []


class TestTerminalMemberAccounting:
    def _lineage(self, member_statuses):
        records = {"adr/board": _adr(), "spec/live": _spec("planned", adrs=["board"])}
        for index, status in enumerate(member_statuses):
            records[f"spec/m{index}"] = _spec(status, adrs=["board"])
        lineages = _derive([_walk("local", records)])
        assert len(lineages) == 1
        return lineages[0]

    def test_complete_and_superseded_members_are_omitted_but_counted(self):
        lineage = self._lineage(["complete", "superseded"])

        assert [m.record_id for m in lineage.members] == ["spec/live"]
        assert lineage.completed_count == 2

    def test_a_dropped_member_is_omitted_and_left_out_of_the_count(self):
        """Dropped is abandoned, not finished: counting it would inflate the
        progress the count exists to show."""
        lineage = self._lineage(["dropped"])

        assert [m.record_id for m in lineage.members] == ["spec/live"]
        assert lineage.completed_count == 0


class TestOrphanedSeeds:
    def test_a_dropped_root_renders_while_a_non_terminal_spec_still_points_at_it(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/gone": _adr("dropped"),
                        "spec/seed": _spec("draft", adrs=["gone"]),
                    },
                )
            ]
        )

        assert _ids(lineages) == ["local:adr/gone"]
        assert lineages[0].members[0].flags == ("orphaned-seed",)

    def test_a_superseded_root_orphans_its_seeds_the_same_way(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/old": _adr("superseded"),
                        "spec/seed": _spec("ready", adrs=["old"]),
                    },
                )
            ]
        )

        assert lineages[0].members[0].flags == ("orphaned-seed",)

    def test_a_dropped_root_with_no_non_terminal_seeds_is_absent_entirely(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/gone": _adr("dropped"),
                        "spec/done": _spec("complete", adrs=["gone"]),
                    },
                )
            ]
        )

        assert lineages == []

    def test_a_live_root_leaves_its_members_unflagged(self):
        lineages = _derive(
            [_walk("local", {"adr/board": _adr(), "spec/one": _spec(adrs=["board"])})]
        )

        assert lineages[0].members[0].flags == ()


class TestEdgeNormalization:
    def test_a_bare_stem_and_a_prefixed_value_land_in_the_same_lineage(self):
        """Edge values are written as bare stems by convention only — a
        prefixed value stores cleanly and would otherwise read as dangling."""
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/board": _adr(),
                        "spec/bare": _spec(adrs=["board"]),
                        "spec/prefixed": _spec(adrs=["adr/board"]),
                    },
                )
            ]
        )

        assert _ids(lineages) == ["local:adr/board"]
        assert [m.record_id for m in lineages[0].members] == ["spec/bare", "spec/prefixed"]


    def test_two_edges_naming_one_target_route_that_record_once(self):
        """``foo`` and ``adr/foo`` are one edge after normalization — listing
        the spec twice would also double the lineage's completed count."""
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/board": _adr(),
                        "spec/live": _spec(adrs=["board", "adr/board"]),
                        "spec/done": _spec("complete", adrs=["board", "adr/board"]),
                    },
                )
            ]
        )

        assert [m.record_id for m in lineages[0].members] == ["spec/live"]
        assert lineages[0].completed_count == 1


class TestUnresolvedRoots:
    def test_an_edge_matching_no_adr_becomes_a_flagged_singleton(self):
        lineages = _derive(
            [_walk("local", {"spec/lost": _spec(adrs=["nowhere"])})]
        )

        assert _ids(lineages) == ["local:spec/lost"]
        assert lineages[0].root.record_id == "spec/lost"
        assert lineages[0].root.flags == ("unresolved-root",)
        assert lineages[0].members == ()

    def test_the_raw_edge_value_rides_along_on_the_singleton(self):
        """The value that failed to resolve is the whole diagnostic, so it
        survives the derivation verbatim rather than being normalized away."""
        lineages = _derive(
            [_walk("local", {"spec/lost": _spec(adrs=["adr/nowhere"])})]
        )

        assert lineages[0].root.sidecar["related"]["adr"] == ["adr/nowhere"]

    def test_a_terminal_spec_with_a_dangling_edge_is_not_board_membership(self):
        """A finished spec is not in-flight work whichever way its edge points,
        exactly as a finished spec on a resolving edge is omitted."""
        lineages = _derive(
            [_walk("local", {"spec/done": _spec("complete", adrs=["nowhere"])})]
        )

        assert lineages == []


class TestOwnVaultConfinement:
    def test_an_edge_never_reaches_into_another_vault(self):
        lineages = _derive(
            [
                _walk("a", {"adr/board": _adr()}),
                _walk("b", {"spec/stray": _spec(adrs=["board"])}),
            ]
        )

        assert _ids(lineages) == ["b:spec/stray"]
        assert lineages[0].root.flags == ("unresolved-root",)

    def test_same_named_adrs_in_two_vaults_anchor_two_distinct_lineages(self):
        lineages = _derive(
            [
                _walk(
                    "a",
                    {"adr/board": _adr(), "spec/one": _spec(adrs=["board"], **{"updated-at": "2026-08-02"})},
                ),
                _walk(
                    "b",
                    {"adr/board": _adr(), "spec/two": _spec(adrs=["board"], **{"updated-at": "2026-08-01"})},
                ),
            ]
        )

        assert _ids(lineages) == ["a:adr/board", "b:adr/board"]
        assert [m.record_id for lineage in lineages for m in lineage.members] == [
            "spec/one",
            "spec/two",
        ]

    def test_every_lineage_id_carries_its_vault_qualifier(self):
        lineages = _derive(
            [
                _walk(
                    "solo",
                    {
                        "adr/board": _adr("draft"),
                        "spec/lost": _spec(adrs=["nowhere"]),
                        "task/idea": _task(route="brainstorm"),
                    },
                )
            ]
        )

        assert all(lineage.id.startswith("solo:") for lineage in lineages)
        assert sorted(_ids(lineages)) == [
            "solo:adr/board",
            "solo:spec/lost",
            "solo:task/idea",
        ]


class TestMultipleEdges:
    def test_a_spec_with_two_resolving_edges_is_emitted_in_both_lineages(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/one": _adr(),
                        "adr/two": _adr(),
                        "spec/both": _spec(adrs=["one", "two"]),
                    },
                )
            ]
        )

        assert sorted(_ids(lineages)) == ["local:adr/one", "local:adr/two"]
        assert all(
            [m.record_id for m in lineage.members] == ["spec/both"]
            for lineage in lineages
        )

    def test_one_resolving_and_one_dangling_edge_does_both(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {"adr/one": _adr(), "spec/half": _spec(adrs=["one", "nowhere"])},
                )
            ]
        )

        by_id = {lineage.id: lineage for lineage in lineages}
        assert sorted(by_id) == ["local:adr/one", "local:spec/half"]
        assert [m.record_id for m in by_id["local:adr/one"].members] == ["spec/half"]
        assert by_id["local:spec/half"].root.flags == ("unresolved-root",)


class TestRoutedTasks:
    def test_an_open_brainstorm_routed_task_joins_as_a_flagged_singleton(self):
        lineages = _derive(
            [_walk("local", {"task/idea": _task(route="brainstorm")})]
        )

        assert _ids(lineages) == ["local:task/idea"]
        assert lineages[0].root.flags == ("routed-task",)
        assert lineages[0].members == ()
        assert lineages[0].completed_count == 0

    def test_a_routed_task_past_open_is_excluded(self):
        """The route label says where the work belongs; the status says whether
        it is still waiting there."""
        records = {
            f"task/t{status}": _task(status, route="brainstorm")
            for status in ("ready", "in-progress", "blocked", "done", "dropped")
        }

        assert _derive([_walk("local", records)]) == []

    def test_an_open_task_routed_elsewhere_or_unrouted_is_excluded(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "task/plan": _task(route="plan"),
                        "task/bare": _task(),
                        "task/empty": _task(**{"labels": {}}),
                    },
                )
            ]
        )

        assert lineages == []


class TestRecencyOrdering:
    def test_lineages_are_ordered_newest_first(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/old": _adr("draft", **{"updated-at": "2026-08-01T00:00:00Z"}),
                        "adr/new": _adr("draft", **{"updated-at": "2026-08-03T00:00:00Z"}),
                        "adr/mid": _adr("draft", **{"updated-at": "2026-08-02T00:00:00Z"}),
                    },
                )
            ]
        )

        assert _ids(lineages) == ["local:adr/new", "local:adr/mid", "local:adr/old"]

    def test_a_lineage_is_as_recent_as_its_newest_non_terminal_record(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/stale": _adr(**{"updated-at": "2026-01-01T00:00:00Z"}),
                        "spec/fresh": _spec(
                            adrs=["stale"], **{"updated-at": "2026-08-05T00:00:00Z"}
                        ),
                        "adr/recent": _adr("draft", **{"updated-at": "2026-06-01T00:00:00Z"}),
                    },
                )
            ]
        )

        assert _ids(lineages) == ["local:adr/stale", "local:adr/recent"]

    def test_a_record_with_no_timestamp_sorts_last_rather_than_raising(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "adr/undated": _adr("draft"),
                        "adr/dated": _adr("draft", **{"updated-at": "2026-08-01T00:00:00Z"}),
                    },
                )
            ]
        )

        assert _ids(lineages) == ["local:adr/dated", "local:adr/undated"]


class TestPriorityTiering:
    """Splitting the derived lineages into the priority and recency tiers."""

    def test_a_root_priority_label_places_the_lineage_in_the_priority_tier(self):
        priority, recency = derive.split_tiers(
            _derive(
                [_walk("local", {"adr/board": _adr("draft", labels={"priority": "1"})})]
            )
        )

        assert [l.id for l in priority] == ["local:adr/board"]
        assert recency == []

    def test_a_member_priority_label_does_not_lift_the_lineage_out_of_recency(self):
        priority, recency = derive.split_tiers(
            _derive(
                [
                    _walk(
                        "local",
                        {
                            "adr/board": _adr(),
                            "spec/one": _spec(adrs=["board"], labels={"priority": "1"}),
                        },
                    )
                ]
            )
        )

        assert priority == []
        assert [l.id for l in recency] == ["local:adr/board"]

    def test_integer_priorities_sort_ascending(self):
        priority, _ = derive.split_tiers(
            _derive(
                [
                    _walk(
                        "local",
                        {
                            "adr/two": _adr("draft", labels={"priority": "2"}),
                            "adr/one": _adr("draft", labels={"priority": "1"}),
                        },
                    )
                ]
            )
        )

        assert [l.id for l in priority] == ["local:adr/one", "local:adr/two"]

    def test_a_non_integer_priority_sorts_after_every_integer_and_keeps_its_raw_value(self):
        priority, _ = derive.split_tiers(
            _derive(
                [
                    _walk(
                        "local",
                        {
                            "adr/soon": _adr("draft", labels={"priority": "soon"}),
                            "adr/two": _adr("draft", labels={"priority": "2"}),
                        },
                    )
                ]
            )
        )

        assert [l.id for l in priority] == ["local:adr/two", "local:adr/soon"]
        assert priority[1].root.sidecar["labels"]["priority"] == "soon"

    def test_the_comparison_never_raises_across_mixed_int_and_string_priorities(self):
        records = {
            f"adr/r{index}": _adr("draft", labels={"priority": value})
            for index, value in enumerate(["3", "soon", "1", "later", "2"])
        }

        priority, _ = derive.split_tiers(_derive([_walk("local", records)]))

        assert len(priority) == 5

    def test_equal_integer_priorities_tie_break_by_lineage_recency_newest_first(self):
        priority, _ = derive.split_tiers(
            _derive(
                [
                    _walk(
                        "local",
                        {
                            "adr/older": _adr(
                                "draft",
                                labels={"priority": "1"},
                                **{"updated-at": "2026-08-01T00:00:00Z"},
                            ),
                            "adr/newer": _adr(
                                "draft",
                                labels={"priority": "1"},
                                **{"updated-at": "2026-08-02T00:00:00Z"},
                            ),
                        },
                    )
                ]
            )
        )

        assert [l.id for l in priority] == ["local:adr/newer", "local:adr/older"]

    def test_a_shared_root_priority_label_is_ignored_for_tiering(self):
        """A binding constraint on this board: a shared vault's labels never
        influence ordering, even though the label still projects verbatim."""
        priority, recency = derive.split_tiers(
            _derive(
                [
                    _walk(
                        "team",
                        {"adr/board": _adr("draft", labels={"priority": "1"})},
                        shared=True,
                    )
                ]
            )
        )

        assert priority == []
        assert [l.id for l in recency] == ["team:adr/board"]

    def test_a_singleton_with_a_priority_label_joins_the_priority_tier(self):
        lineages = _derive(
            [
                _walk(
                    "local",
                    {
                        "task/idea": {
                            "kind": "task",
                            "title": "An idea",
                            "status": "open",
                            "labels": {"route": "brainstorm", "priority": "1"},
                        }
                    },
                )
            ]
        )

        priority, _ = derive.split_tiers(lineages)

        assert [l.id for l in priority] == ["local:task/idea"]


class TestHostileSidecarShapes:
    """A sidecar is whatever JSON object was on disk — a synced vault's entry
    never passed this CLI's validator, so every field read here may be the
    wrong type."""

    def test_a_non_map_related_field_contributes_no_edges(self):
        records = {
            "spec/a": {"kind": "spec", "status": "ready", "related": "adr=board"},
            "spec/b": {"kind": "spec", "status": "ready", "related": {"adr": "board"}},
            "spec/c": {"kind": "spec", "status": "ready", "related": {"adr": [None, 7]}},
        }

        assert _derive([_walk("local", records)]) == []

    def test_a_non_map_labels_field_never_routes_a_task(self):
        records = {"task/a": {"kind": "task", "status": "open", "labels": ["route"]}}

        assert _derive([_walk("local", records)]) == []

    def test_a_non_string_status_is_no_status_at_all(self):
        records = {"adr/odd": {"kind": "adr", "status": {"draft": True}}}

        assert _derive([_walk("local", records)]) == []


class TestDerivationIsPure:
    def test_the_module_reaches_no_filesystem_and_no_stream(self):
        """The derivation's contract is that it is a function of the walk's
        output alone. Nothing here may read a file, so a later rule cannot
        quietly start consulting one and make the board unreproducible."""
        tree = ast.parse(DERIVE_MODULE.read_text(encoding="utf-8"))
        forbidden = {"os", "pathlib", "sys", "json", "shutil", "subprocess", "io"}

        imported: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)

        assert not imported & forbidden, sorted(imported & forbidden)
        assert not called & {"open", "print", "input", "eval", "exec"}


def _deps(*entries) -> dict:
    """A sidecar fragment carrying *entries* as stored ``depends-on`` values."""
    return {"depends-on": list(entries)}


class TestDependencyProjection:
    """A ``depends-on`` entry is evaluated, projected, and — when unmet — shown.

    Gating is additive to membership, never subtractive: a gated record keeps
    its place in its lineage and gains a flag plus a reason, because a
    dependency the operator cannot see is one they cannot act on.
    """

    def test_the_projected_shape_tracks_the_evaluator_it_is_built_from(self):
        """A field added to the evaluator's verdict must be projected, not
        silently dropped, so the two shapes are pinned to each other."""
        from lore.record import graph as graph_mod

        assert derive.Dependency._fields == graph_mod.DependencyStatus._fields

    def test_a_target_short_of_the_required_stage_gates_the_record(self):
        records = {
            "adr/board": _adr("draft"),
            "spec/foo": _spec("ready", adrs=["board"]),
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo@planned")),
        }

        lineages = _derive([_walk("local", records)])
        bar = {m.record_id: m for m in lineages[0].members}["spec/bar"]

        assert [d.met for d in bar.dependencies] == [False]
        assert bar.dependencies[0].kind == "spec"
        assert bar.dependencies[0].name == "foo"
        assert bar.dependencies[0].stage == "planned"
        assert bar.dependencies[0].reason_code == "short-of-stage"
        assert bar.dependencies[0].reason
        assert derive.GATED in bar.flags

    def test_a_target_at_or_past_the_required_stage_leaves_the_record_ungated(self):
        for status in ("planned", "complete"):
            records = {
                "adr/board": _adr("draft"),
                "spec/foo": _spec(status, adrs=["board"]),
                "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo@planned")),
            }

            lineages = _derive([_walk("local", records)])
            bar = {m.record_id: m for m in lineages[0].members}["spec/bar"]

            assert [d.met for d in bar.dependencies] == [True], status
            assert bar.dependencies[0].reason_code is None, status
            assert derive.GATED not in bar.flags, status

    def test_a_dangling_target_reads_missing_and_gates(self):
        records = {
            "adr/board": _adr("draft"),
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/nowhere")),
        }

        lineages = _derive([_walk("local", records)])
        bar = lineages[0].members[0]

        assert bar.dependencies[0].reason_code == "missing"
        assert bar.dependencies[0].met is False
        assert derive.GATED in bar.flags

    def test_a_failed_target_reads_target_failed_and_gates(self):
        for status in ("superseded", "dropped"):
            records = {
                "adr/board": _adr("draft"),
                "spec/foo": _spec(status, adrs=["board"]),
                "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo")),
            }

            lineages = _derive([_walk("local", records)])
            bar = {m.record_id: m for m in lineages[0].members}["spec/bar"]

            assert bar.dependencies[0].reason_code == "target-failed", status
            assert derive.GATED in bar.flags, status

    def test_entries_project_in_stored_order_with_duplicates_kept(self):
        """One verdict per entry, never a map keyed by target: a consumer
        reading the Nth verdict is reading the Nth stored entry."""
        records = {
            "adr/board": _adr("draft"),
            "spec/foo": _spec("planned", adrs=["board"]),
            "spec/bar": _spec(
                "draft", adrs=["board"],
                **_deps("spec/nowhere", "spec/foo@planned", "spec/nowhere"),
            ),
        }

        lineages = _derive([_walk("local", records)])
        bar = {m.record_id: m for m in lineages[0].members}["spec/bar"]

        assert [d.name for d in bar.dependencies] == ["nowhere", "foo", "nowhere"]
        assert [d.met for d in bar.dependencies] == [False, True, False]

    def test_an_adr_root_is_gated_by_its_own_unmet_dependency(self):
        records = {"adr/board": _adr("draft", **_deps("spec/nowhere"))}

        lineages = _derive([_walk("local", records)])

        assert derive.GATED in lineages[0].root.flags

    def test_a_gated_orphaned_seed_keeps_both_flags(self):
        records = {
            "adr/board": _adr("dropped"),
            "spec/seed": _spec("draft", adrs=["board"], **_deps("spec/nowhere")),
        }

        lineages = _derive([_walk("local", records)])

        assert lineages[0].members[0].flags == (derive.ORPHANED_SEED, derive.GATED)

    def test_a_record_with_no_depends_on_projects_no_dependencies(self):
        records = {"adr/board": _adr("draft")}

        lineages = _derive([_walk("local", records)])

        assert lineages[0].root.dependencies == ()
        assert derive.GATED not in lineages[0].root.flags

    def test_a_wrong_typed_depends_on_field_reads_as_absent(self):
        records = {"adr/board": _adr("draft", **{"depends-on": "spec/foo"})}

        lineages = _derive([_walk("local", records)])

        assert lineages[0].root.dependencies == ()


class TestDependencyConfinement:
    """The evaluator is a pure function over whatever graph it is handed and
    offers no confinement of its own — so a merged graph would let one vault's
    record satisfy another vault's dependency. Every evaluation here runs
    against exactly the vault the depending record came from."""

    def test_a_target_satisfied_only_in_another_vault_stays_unmet(self):
        own = _walk("local", {
            "adr/board": _adr("draft"),
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo@planned")),
        })
        other = _walk("team", {"spec/foo": _spec("planned")}, shared=True)

        lineages = _derive([own, other])
        by_id = {lineage.id: lineage for lineage in lineages}
        bar = by_id["local:adr/board"].members[0]

        assert bar.dependencies[0].met is False
        assert bar.dependencies[0].reason_code == "missing"
        assert derive.GATED in bar.flags

    def test_the_same_dependency_resolves_independently_in_each_vault(self):
        satisfied = _walk("local", {
            "adr/board": _adr("draft"),
            "spec/foo": _spec("planned", adrs=["board"]),
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo@planned")),
        })
        starved = _walk("team", {
            "adr/board": _adr("draft"),
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/foo@planned")),
        })

        by_id = {lineage.id: lineage for lineage in _derive([satisfied, starved])}
        local_bar = {m.record_id: m for m in by_id["local:adr/board"].members}["spec/bar"]
        team_bar = by_id["team:adr/board"].members[0]

        assert local_bar.dependencies[0].met is True
        assert team_bar.dependencies[0].met is False


class TestRoutedTaskDependencies:
    """A task's ``depends-on`` is a different grammar — bare task names — and
    this surface does not evaluate it. Projecting the stored entries
    unevaluated keeps them visible without inventing a gating verdict the
    design-kind evaluator would have got wrong."""

    def test_a_routed_task_projects_its_entries_unevaluated_and_ungated(self):
        records = {
            "task/idea": _task("open", route="brainstorm", **_deps("other-task")),
        }

        lineages = _derive([_walk("local", records)])
        root = lineages[0].root

        assert [d.met for d in root.dependencies] == [None]
        assert root.dependencies[0].kind is None
        assert root.dependencies[0].name == "other-task"
        assert root.dependencies[0].stage is None
        assert root.dependencies[0].reason_code is None
        assert "task" in root.dependencies[0].reason
        assert root.flags == (derive.ROUTED_TASK,)

    def test_a_non_string_entry_projects_without_a_name(self):
        records = {
            "task/idea": _task("open", route="brainstorm", **_deps({"nested": 1})),
        }

        lineages = _derive([_walk("local", records)])

        assert lineages[0].root.dependencies[0].name is None


class TestEvaluationFailureDegrades:
    """A sidecar is whatever JSON was on disk, so a target's ``status`` may be
    a type the evaluator cannot compare. That costs the depending record its
    place on the board and nothing more — the vault still renders."""

    def test_a_record_whose_evaluation_raises_is_dropped_and_reported(self):
        records = {
            "adr/board": _adr("draft"),
            "spec/target": {"kind": "spec", "title": "T", "status": []},
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/target")),
        }

        derivation = derive.derive_board([_walk("local", records)])

        assert [lineage.id for lineage in derivation.lineages] == ["local:adr/board"]
        assert derivation.lineages[0].members == ()
        assert [w.file for w in derivation.warnings] == ["spec/bar.json"]
        assert derivation.warnings[0].vault == "local"
        assert derivation.warnings[0].shared is False
        assert derivation.warnings[0].message

    def test_the_rest_of_the_vault_still_derives(self):
        records = {
            "adr/board": _adr("draft"),
            "spec/target": {"kind": "spec", "title": "T", "status": []},
            "spec/bar": _spec("draft", adrs=["board"], **_deps("spec/target")),
            "spec/ok": _spec("draft", adrs=["board"]),
        }

        derivation = derive.derive_board([_walk("local", records)])

        assert [m.record_id for m in derivation.lineages[0].members] == ["spec/ok"]

    def test_a_clean_derivation_reports_no_warnings(self):
        records = {"adr/board": _adr("draft")}

        assert derive.derive_board([_walk("local", records)]).warnings == ()
