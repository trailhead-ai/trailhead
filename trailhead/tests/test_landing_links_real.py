"""Gate: every claim in landing_claims.toml resolves to a real on-disk anchor.

TDD contract (Slice 1 — forward check only; Slice 2 adds the inverse anti-rot check):

1. Schema-pin (B-3): landing_claims.toml parses via tomllib into [[claim]] entries with
   four required fields: kind / tool / ref / source.
2. Closed kind set (S-5): gate rejects an unknown kind with a named assertion; never
   silently skips.
3. Prose-assertion kinds (positioning / allowlisted-example) are explicitly skipped
   by the resolver — tested contract of the U-1 boundary.
4. Forward check: for every [[claim]], resolve ref against its oracle:
   - capability / skill / agent  → build_real_anchor_set()
   - command                     → _KNOWN_COMMANDS
   - preset                      → preset names from trailhead.presets
   - doc-link                    → (REPO_ROOT / ref).exists()
   Fails closed with a named assertion on mismatch.
5. build_real_anchor_set() enumerates from sorted glob of tools/*/capabilities.toml (R-1),
   asserts m.validate is True for each (R-2), includes empty caps (don't filter), is
   deterministic (R-5).
6. Hermeticity: only in-repo manifests + tmp_path synthetics; no network / ~/.claude/ / vault.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.presets import _STATIC_PRESETS

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLAIMS_FILE = _REPO_ROOT / "trailhead" / "landing_claims.toml"

# CLI subcommands — hardcoded closed set (B-1).
# add new subcommands here
_KNOWN_COMMANDS: frozenset[str] = frozenset({"install", "update", "doctor", "config"})

# Valid kind values — closed set (S-5).
_RESOLVABLE_KINDS = frozenset({"command", "preset", "skill", "agent", "capability", "doc-link"})
# Prose-assertion kinds: explicitly skipped by the resolver (U-1 / S-5 named exception list).
_PROSE_KINDS = frozenset({"positioning", "allowlisted-example"})
_ALL_VALID_KINDS = _RESOLVABLE_KINDS | _PROSE_KINDS


# ---------------------------------------------------------------------------
# Real anchor set builder (R-1, R-2, R-5)
# ---------------------------------------------------------------------------


def build_real_anchor_set(root: Path = _REPO_ROOT) -> dict[str, dict[str, set[str]]]:
    """Enumerate {tool: {capabilities: set, skills: set, agents: set}} from manifests.

    Discovers manifests from sorted(root.glob("tools/*/capabilities.toml")) (R-1).
    Asserts m.validate is True for each manifest before trusting its anchors (R-2).
    Includes capabilities with empty skills/agents — do not filter (R-5, deterministic).

    `root` defaults to the repo root; tests inject a tmp_path tools tree to exercise
    the R-2 guard against the REAL code path (not a hand-copied assertion).
    """
    anchors: dict[str, dict[str, set[str]]] = {}
    for path in sorted(root.glob("tools/*/capabilities.toml")):
        m = load_manifest(path)
        assert m.validate is True, (
            f"manifest {path} has validate=false; gate requires validate=true — "
            "the gate's oracle contract depends on load_manifest having validated all paths"
        )
        tool_anchors: dict[str, set[str]] = {
            "capabilities": set(m.capabilities.keys()),
            "skills": set(),
            "agents": set(),
        }
        for cap in m.capabilities.values():
            tool_anchors["skills"].update(cap["skills"])
            tool_anchors["agents"].update(cap["agents"])
        anchors[m.tool_name] = tool_anchors
    return anchors


def _preset_names() -> frozenset[str]:
    """Return the set of valid preset names.

    Enumerated from the same source of truth the installer uses
    (presets._STATIC_PRESETS + the runtime-computed "full"), so it can't drift
    as presets are added (mirrors R-1's discovery philosophy; M-1).
    """
    return frozenset(set(_STATIC_PRESETS.keys()) | {"full"})


# ---------------------------------------------------------------------------
# Forward checker
# ---------------------------------------------------------------------------


def check_claim(claim: dict, anchor_set: dict[str, dict[str, set[str]]]) -> None:
    """Resolve one claim entry against its oracle; raise AssertionError on failure.

    Explicitly skips prose-assertion kinds (positioning / allowlisted-example).
    Rejects unknown kinds with a named failure (S-5).

    Note: `source` is forward-looking metadata (which README makes the claim — used by
    Slice 2's inverse check and in error messages); it is NOT a resolution input, so a
    wrong `source` is not caught here (M-2).
    """
    kind = claim["kind"]
    ref = claim["ref"]
    tool = claim.get("tool", "")
    source = claim.get("source", "")

    if kind in _PROSE_KINDS:
        return  # explicitly skipped — prose honesty is the Slice-3/4 review's job

    if kind not in _RESOLVABLE_KINDS:
        raise AssertionError(
            f"landing_claims.toml has unknown kind={kind!r} for ref={ref!r} "
            f"(source={source!r}); valid kinds are: {sorted(_ALL_VALID_KINDS)}"
        )

    if kind == "command":
        assert ref in _KNOWN_COMMANDS, (
            f"landing_claims.toml claims command {ref!r}; "
            f"not found in _KNOWN_COMMANDS {sorted(_KNOWN_COMMANDS)}"
        )

    elif kind == "preset":
        presets = _preset_names()
        assert ref in presets, (
            f"landing_claims.toml claims preset {ref!r}; "
            f"not found in preset names {sorted(presets)}"
        )

    elif kind in ("capability", "skill", "agent"):
        tool_anchors = anchor_set.get(tool)
        assert tool_anchors is not None, (
            f"landing_claims.toml claims {kind} {ref!r} for tool={tool!r}; "
            f"tool not found in anchor set (known tools: {sorted(anchor_set)})"
        )
        plural = kind + "s" if kind != "capability" else "capabilities"
        ref_set = tool_anchors[plural]
        assert ref in ref_set, (
            f"landing_claims.toml claims {kind} {ref!r} for tool={tool!r}; "
            f"not found in {plural} anchor set"
        )

    elif kind == "doc-link":
        resolved = (_REPO_ROOT / ref).resolve()
        assert resolved.exists(), (
            f"landing_claims.toml claims doc-link {ref!r}; "
            f"path does not exist on disk: {resolved}"
        )


# ---------------------------------------------------------------------------
# B-3: Schema-pin tests (written FIRST — these must fail RED before the
#      landing_claims.toml file exists)
# ---------------------------------------------------------------------------


class TestClaimsManifestSchema:
    """B-3: pin the [[claim]] TOML schema before building the resolver."""

    def test_claims_file_exists(self):
        """landing_claims.toml must exist at trailhead/landing_claims.toml."""
        assert _CLAIMS_FILE.exists(), (
            f"trailhead/landing_claims.toml not found at {_CLAIMS_FILE}; "
            "create it with at least one [[claim]] entry"
        )

    def test_claims_file_parses_as_toml(self):
        """landing_claims.toml must be valid TOML."""
        with open(_CLAIMS_FILE, "rb") as f:
            data = tomllib.load(f)
        assert "claim" in data, "landing_claims.toml must have a [[claim]] array"

    def test_claims_entries_have_required_fields(self):
        """Every [[claim]] entry must have: kind, tool, ref, source."""
        with open(_CLAIMS_FILE, "rb") as f:
            data = tomllib.load(f)
        claims = data["claim"]
        assert isinstance(claims, list), "claim must be an array-of-tables"
        assert len(claims) > 0, "landing_claims.toml must have at least one [[claim]]"
        for i, claim in enumerate(claims):
            for field in ("kind", "tool", "ref", "source"):
                assert field in claim, (
                    f"[[claim]][{i}] is missing required field {field!r}: {claim}"
                )

    def test_claims_entries_have_valid_kind(self):
        """Every [[claim]] entry must have a kind in the closed valid set."""
        with open(_CLAIMS_FILE, "rb") as f:
            data = tomllib.load(f)
        for claim in data["claim"]:
            assert claim["kind"] in _ALL_VALID_KINDS, (
                f"[[claim]] has unknown kind={claim['kind']!r}; "
                f"valid kinds: {sorted(_ALL_VALID_KINDS)}"
            )

    def test_unknown_kind_synthetic_raises_named_assertion(self):
        """check_claim must reject an unknown kind with a named AssertionError (S-5)."""
        bad_claim = {"kind": "skil", "tool": "lore", "ref": "skills/area", "source": "lore"}
        with pytest.raises(AssertionError, match="unknown kind"):
            check_claim(bad_claim, {})

    def test_positioning_kind_is_skipped_not_rejected(self):
        """positioning kind is explicitly skipped — not an error, not resolved."""
        claim = {"kind": "positioning", "tool": "lore", "ref": "some prose", "source": "root"}
        # Must not raise
        check_claim(claim, {})

    def test_allowlisted_example_kind_is_skipped_not_rejected(self):
        """allowlisted-example kind is explicitly skipped — not an error, not resolved."""
        claim = {
            "kind": "allowlisted-example",
            "tool": "trailhead",
            "ref": "trailhead frobnicate",
            "source": "root",
        }
        # Must not raise
        check_claim(claim, {})


# ---------------------------------------------------------------------------
# Forward check: positive cases (one real anchor per resolvable kind)
# ---------------------------------------------------------------------------


class TestForwardCheckPositive:
    """A claim naming a REAL anchor must pass for each resolvable kind."""

    def test_real_command_claim_passes(self):
        """A claim for a known command resolves without error."""
        claim = {"kind": "command", "tool": "trailhead", "ref": "install", "source": "root"}
        check_claim(claim, {})

    def test_real_preset_claim_passes(self):
        """A claim for a known preset resolves without error."""
        claim = {"kind": "preset", "tool": "trailhead", "ref": "minimal", "source": "root"}
        check_claim(claim, {})

    def test_real_capability_claim_passes(self):
        """A claim for a known capability resolves without error."""
        anchor_set = build_real_anchor_set()
        claim = {"kind": "capability", "tool": "lore", "ref": "recall", "source": "lore"}
        check_claim(claim, anchor_set)

    def test_real_skill_claim_passes(self):
        """A claim for a known skill resolves without error."""
        anchor_set = build_real_anchor_set()
        # lore recall has skills/tend
        claim = {"kind": "skill", "tool": "lore", "ref": "skills/tend", "source": "lore"}
        check_claim(claim, anchor_set)

    def test_real_agent_claim_passes(self):
        """A claim for a known agent resolves without error."""
        anchor_set = build_real_anchor_set()
        claim = {
            "kind": "agent",
            "tool": "forge",
            "ref": "agents/artist.md",
            "source": "forge",
        }
        check_claim(claim, anchor_set)

    def test_real_doc_link_claim_passes(self):
        """A claim for an existing doc-link resolves without error."""
        claim = {
            "kind": "doc-link",
            "tool": "trailhead",
            "ref": "LICENSE",
            "source": "root",
        }
        check_claim(claim, {})


# ---------------------------------------------------------------------------
# Forward check: negative cases (one per resolvable kind)
# ---------------------------------------------------------------------------


class TestForwardCheckNegative:
    """A claim naming a NON-EXISTENT anchor must fail with a named assertion."""

    def test_nonexistent_command_fails_with_named_assertion(self):
        claim = {
            "kind": "command",
            "tool": "trailhead",
            "ref": "frobnicate",
            "source": "root",
        }
        with pytest.raises(AssertionError, match="frobnicate"):
            check_claim(claim, {})

    def test_nonexistent_preset_fails_with_named_assertion(self):
        claim = {
            "kind": "preset",
            "tool": "trailhead",
            "ref": "everything",
            "source": "root",
        }
        with pytest.raises(AssertionError, match="everything"):
            check_claim(claim, {})

    def test_nonexistent_capability_fails_with_named_assertion(self):
        anchor_set = build_real_anchor_set()
        claim = {
            "kind": "capability",
            "tool": "lore",
            "ref": "ghost-capability",
            "source": "lore",
        }
        with pytest.raises(AssertionError, match="ghost-capability"):
            check_claim(claim, anchor_set)

    def test_nonexistent_skill_fails_with_named_assertion(self):
        anchor_set = build_real_anchor_set()
        claim = {
            "kind": "skill",
            "tool": "lore",
            "ref": "skills/ghost",
            "source": "lore",
        }
        with pytest.raises(AssertionError, match="skills/ghost"):
            check_claim(claim, anchor_set)

    def test_nonexistent_agent_fails_with_named_assertion(self):
        anchor_set = build_real_anchor_set()
        claim = {
            "kind": "agent",
            "tool": "lore",
            "ref": "agents/ghost.md",
            "source": "lore",
        }
        with pytest.raises(AssertionError, match="agents/ghost.md"):
            check_claim(claim, anchor_set)

    def test_nonexistent_doc_link_fails_with_named_assertion(self):
        claim = {
            "kind": "doc-link",
            "tool": "trailhead",
            "ref": "./docs/missing.md",
            "source": "root",
        }
        with pytest.raises(AssertionError, match="missing.md"):
            check_claim(claim, {})


# ---------------------------------------------------------------------------
# build_real_anchor_set() oracle tests (R-1, R-2, R-5)
# ---------------------------------------------------------------------------


class TestBuildRealAnchorSet:
    """build_real_anchor_set() must enumerate a non-empty, stable, known-correct set."""

    def test_returns_non_empty_dict(self):
        anchors = build_real_anchor_set()
        assert len(anchors) > 0, "build_real_anchor_set() returned an empty dict"

    def test_known_tools_present(self):
        anchors = build_real_anchor_set()
        for tool in ("lore", "forge", "camp"):
            assert tool in anchors, f"tool {tool!r} missing from anchor set"

    def test_lore_recall_capability_present(self):
        anchors = build_real_anchor_set()
        assert "recall" in anchors["lore"]["capabilities"]

    def test_lore_loremaster_agent_present(self):
        anchors = build_real_anchor_set()
        assert "agents/loremaster.md" in anchors["lore"]["agents"]

    def test_forge_execute_capability_present(self):
        anchors = build_real_anchor_set()
        assert "execute" in anchors["forge"]["capabilities"]

    def test_forge_scout_agent_present(self):
        anchors = build_real_anchor_set()
        assert "agents/scout.md" in anchors["forge"]["agents"]

    def test_forge_artist_agent_present(self):
        """forge design has agents/artist.md — verify it's in the anchor set."""
        anchors = build_real_anchor_set()
        assert "agents/artist.md" in anchors["forge"]["agents"]

    def test_camp_dev_env_capability_present_and_not_filtered(self):
        """camp dev-env has empty skills/agents — must be present, not filtered (R-1)."""
        anchors = build_real_anchor_set()
        assert "dev-env" in anchors["camp"]["capabilities"]
        # empty skills/agents for camp is valid; sets just happen to be empty
        assert isinstance(anchors["camp"]["skills"], set)
        assert isinstance(anchors["camp"]["agents"], set)

    def test_lore_shared_vaults_capability_present_and_not_filtered(self):
        """lore shared-vaults has empty skills/agents — must be present, not filtered."""
        anchors = build_real_anchor_set()
        assert "shared-vaults" in anchors["lore"]["capabilities"]

    def test_result_is_stable_across_calls(self):
        """Repeated calls return the same set (deterministic — R-5)."""
        a = build_real_anchor_set()
        b = build_real_anchor_set()
        assert a == b

    def test_build_real_anchor_set_raises_on_validate_false_manifest(self, tmp_path):
        """build_real_anchor_set() itself must raise on a validate=false manifest (R-2).

        I-1 fix: drive the REAL function (via an injected tmp_path tools tree) instead of
        re-typing the guard inline — so deleting/weakening the line-62 assertion fails this
        test. A validate=false tool could reference missing paths that load_manifest never
        validated; the gate must refuse to trust it.
        """
        manifest_path = tmp_path / "tools" / "testtool" / "capabilities.toml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            "[tool]\n"
            'name = "testtool"\n'
            "validate = false\n"
            "\n"
            "[capabilities.dev-env]\n"
            'description = "provision/teardown dev-env instances"\n'
            "skills = []\n"
            "agents = []\n"
        )
        with pytest.raises(AssertionError, match="validate=false"):
            build_real_anchor_set(root=tmp_path)

    def test_build_real_anchor_set_accepts_injected_root(self, tmp_path):
        """A valid validate=true manifest under an injected root is enumerated (positive twin).

        Empty skill/agent lists keep validate=true honest — there are no paths to resolve.
        """
        manifest_path = tmp_path / "tools" / "testtool" / "capabilities.toml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            "[tool]\n"
            'name = "testtool"\n'
            "validate = true\n"
            "\n"
            "[capabilities.solo]\n"
            'description = "a lone capability"\n'
            "skills = []\n"
            "agents = []\n"
        )
        anchors = build_real_anchor_set(root=tmp_path)
        assert anchors["testtool"]["capabilities"] == {"solo"}


# ---------------------------------------------------------------------------
# Full forward check over the real landing_claims.toml
# ---------------------------------------------------------------------------


class TestForwardCheckOverRealClaims:
    """Run the full forward check over every [[claim]] in landing_claims.toml."""

    def test_all_claims_resolve(self):
        """Every [[claim]] in landing_claims.toml must resolve against its oracle."""
        assert _CLAIMS_FILE.exists(), (
            f"landing_claims.toml not found at {_CLAIMS_FILE}"
        )
        with open(_CLAIMS_FILE, "rb") as f:
            data = tomllib.load(f)
        claims = data.get("claim", [])
        assert len(claims) > 0, "landing_claims.toml has no [[claim]] entries"

        anchor_set = build_real_anchor_set()
        failures: list[str] = []
        for claim in claims:
            try:
                check_claim(claim, anchor_set)
            except AssertionError as e:
                failures.append(str(e))

        assert not failures, (
            f"{len(failures)} claim(s) in landing_claims.toml failed to resolve:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
