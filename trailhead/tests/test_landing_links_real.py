"""Gate: every claim in landing_claims.toml resolves to a real on-disk anchor.

TDD contract (Slice 1 — forward check; Slice 2 — inverse anti-rot check):

Slice 1 — forward check:
1. Schema-pin (B-3): landing_claims.toml parses via tomllib into [[claim]] entries with
   four required fields: kind / tool / ref / source.
2. Closed kind set (S-5): gate rejects an unknown kind with a named assertion; never
   silently skips.
3. Prose-assertion kinds (positioning / allowlisted-example) are explicitly skipped
   by the resolver — tested contract of the U-1 boundary.
4. Forward check: for every [[claim]], resolve ref against its oracle:
   - skill / agent  → build_real_anchor_set() (per-tool selectable inventory)
   - command        → _KNOWN_COMMANDS ({install, uninstall, doctor})
   - doc-link       → (REPO_ROOT / ref).exists()
   Fails closed with a named assertion on mismatch.
5. build_real_anchor_set() enumerates from sorted glob of tools/*/capabilities.toml (R-1),
   asserts m.validate is True for each (R-2), reads the convention-discovered subagents/
   skills inventory off each Manifest, is deterministic (R-5).
6. Hermeticity: only in-repo manifests + tmp_path synthetics; no network / ~/.claude/ / vault.

Slice 2 — inverse anti-rot check (D-5, U-3, S-2, R-4, R-5):
7. README_INDEX names the four READMEs the gate scans.
8. extract_fenced_commands(readme_text) → set[tuple[str,str]] extracts (tool, subcommand)
   pairs from fenced ```sh / ```bash blocks ONLY (U-3 grammar boundedness).
9. extract_relative_links(readme_text) → set[str] extracts markdown relative links
   (leading ./ or ../) but NOT absolute http(s):// or anchor-only #frag links.
10. check_inverse(readme_path, claims, repo_root) asserts:
    - every extracted (tool, sub) is registered in claims or has kind=allowlisted-example (R-4)
    - every extracted relative link is confined to the repo root (S-2) and exists on disk
      OR is registered as a doc-link claim
    - R-5: all comparisons on sorted sets; READMEs scanned in sorted order
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLAIMS_FILE = _REPO_ROOT / "trailhead" / "landing_claims.toml"

# CLI subcommands — hardcoded closed set (B-1).
# The config-driven CLI has exactly three commands: install / uninstall / doctor.
# (The old preset/capability model and the update/config commands were removed.)
# add new subcommands here
_KNOWN_COMMANDS: frozenset[str] = frozenset({"install", "uninstall", "doctor"})

# Valid kind values — closed set (S-5).
# The capability-GROUP and preset concepts were removed; install selects subagents
# and skills by name, so only command / skill / agent / doc-link resolve.
_RESOLVABLE_KINDS = frozenset({"command", "skill", "agent", "doc-link"})
# Prose-assertion kinds: explicitly skipped by the resolver (U-1 / S-5 named exception list).
_PROSE_KINDS = frozenset({"positioning", "allowlisted-example"})
_ALL_VALID_KINDS = _RESOLVABLE_KINDS | _PROSE_KINDS


# ---------------------------------------------------------------------------
# Real anchor set builder (R-1, R-2, R-5)
# ---------------------------------------------------------------------------


def build_real_anchor_set(root: Path = _REPO_ROOT) -> dict[str, dict[str, set[str]]]:
    """Enumerate {tool: {skills: set, agents: set}} from manifests.

    Discovers manifests from sorted(root.glob("tools/*/capabilities.toml")) (R-1).
    Asserts m.validate is True for each manifest before trusting its anchors (R-2).

    The selectable inventory is read off the Manifest's convention-discovered
    subagents/skills (the capability-GROUP concept was removed). Anchors are stored
    in the claim-ref vocabulary so a claim's `ref` compares directly:
      * skills → "skills/<name>" for every name in m.skills
      * agents → "agents/<name>.md" for every name in m.subagents

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
        anchors[m.tool_name] = {
            "skills": {f"skills/{name}" for name in m.skills},
            "agents": {f"agents/{name}.md" for name in m.subagents},
        }
    return anchors


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

    elif kind in ("skill", "agent"):
        tool_anchors = anchor_set.get(tool)
        assert tool_anchors is not None, (
            f"landing_claims.toml claims {kind} {ref!r} for tool={tool!r}; "
            f"tool not found in anchor set (known tools: {sorted(anchor_set)})"
        )
        plural = kind + "s"
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

    def test_real_skill_claim_passes(self):
        """A claim for a known skill resolves without error."""
        anchor_set = build_real_anchor_set()
        # lore has skills/check-in (skills/tend was deleted in Slice 7)
        claim = {"kind": "skill", "tool": "lore", "ref": "skills/check-in", "source": "lore"}
        check_claim(claim, anchor_set)

    def test_real_agent_claim_passes(self):
        """A claim for a known agent resolves without error."""
        anchor_set = build_real_anchor_set()
        claim = {
            "kind": "agent",
            "tool": "craft",
            "ref": "agents/artist.md",
            "source": "craft",
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
        for tool in ("lore", "craft", "camp"):
            assert tool in anchors, f"tool {tool!r} missing from anchor set"

    def test_lore_finish_skill_present(self):
        anchors = build_real_anchor_set()
        assert "skills/finish" in anchors["lore"]["skills"]

    def test_lore_librarian_agent_present(self):
        anchors = build_real_anchor_set()
        assert "agents/librarian.md" in anchors["lore"]["agents"]

    def test_craft_execute_skill_present(self):
        anchors = build_real_anchor_set()
        assert "skills/execute" in anchors["craft"]["skills"]

    def test_craft_assumption_prover_agent_present(self):
        anchors = build_real_anchor_set()
        assert "agents/assumption-prover.md" in anchors["craft"]["agents"]

    def test_craft_artist_agent_present(self):
        """craft has agents/artist.md — verify it's in the anchor set."""
        anchors = build_real_anchor_set()
        assert "agents/artist.md" in anchors["craft"]["agents"]

    def test_camp_has_no_skills_or_agents(self):
        """camp ships only a CLI + hooks — its anchor set has no skills and no agents (R-1).

        The worktree SKILL was removed: the workspace exists before the harness
        opens, so worktree orchestration is operator-facing (README), not a skill.
        """
        anchors = build_real_anchor_set()
        assert anchors["camp"]["skills"] == set()
        assert anchors["camp"]["agents"] == set()
        assert isinstance(anchors["camp"]["skills"], set)
        assert isinstance(anchors["camp"]["agents"], set)

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
            'base = ["skills/_shared"]\n'  # missing dir — only an unvalidated manifest tolerates it
        )
        with pytest.raises(AssertionError, match="validate=false"):
            build_real_anchor_set(root=tmp_path)

    def test_build_real_anchor_set_accepts_injected_root(self, tmp_path):
        """A valid validate=true manifest under an injected root is enumerated (positive twin).

        The selectable inventory is discovered on disk: an agents/<name>.md file under the
        plugin root becomes an "agents/<name>.md" anchor.
        """
        plugin_root = tmp_path / "tools" / "testtool" / "plugins" / "testtool"
        (plugin_root / "agents").mkdir(parents=True)
        (plugin_root / "agents" / "solo.md").write_text("# solo agent\n")
        manifest_path = tmp_path / "tools" / "testtool" / "capabilities.toml"
        manifest_path.write_text(
            "[tool]\n"
            'name = "testtool"\n'
            "validate = true\n"
        )
        anchors = build_real_anchor_set(root=tmp_path)
        assert anchors["testtool"]["agents"] == {"agents/solo.md"}


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


# ---------------------------------------------------------------------------
# Slice 2 — inverse anti-rot check (D-5, U-3, S-2, R-4, R-5)
# ---------------------------------------------------------------------------

# The READMEs the gate scans (sorted — R-5 determinism).
# Slice 3 added tools/portage/README.md; Slice 4 added tools/landing/README.md.
README_INDEX: list[Path] = sorted(
    [
        _REPO_ROOT / "README.md",
        _REPO_ROOT / "tools" / "lore" / "README.md",
        _REPO_ROOT / "tools" / "craft" / "README.md",
        _REPO_ROOT / "tools" / "camp" / "README.md",
        _REPO_ROOT / "tools" / "portage" / "README.md",
        _REPO_ROOT / "tools" / "landing" / "README.md",
    ]
)

# Tools whose fenced-block commands the gate tracks.
_TRACKED_TOOLS = frozenset({"trailhead", "lore", "craft", "camp", "portage", "landing"})

# Fenced sh/bash block pattern (U-3: bounded to fenced blocks only).
_FENCED_BLOCK_RE = re.compile(r"```(?:sh|bash)\n(.*?)```", re.DOTALL)
# Command line pattern within a fenced block.
_CMD_LINE_RE = re.compile(
    r"^\s*(" + "|".join(sorted(_TRACKED_TOOLS)) + r")\s+(\S+)", re.MULTILINE
)
# Markdown relative link pattern: [label](./path) or [label](../path).
# Excludes absolute http(s):// and anchor-only #frag links. The path capture stops
# at whitespace so a markdown title attribute — [label](./p "title") — does not leak
# into the path (M-2).
_REL_LINK_RE = re.compile(r"\[([^\]]*)\]\((\.\.?/[^)\s]+)")


def extract_fenced_commands(readme_text: str) -> set[tuple[str, str]]:
    """Extract (tool, subcommand) pairs from fenced ```sh / ```bash blocks (U-3).

    Only fenced blocks with language tag 'sh' or 'bash' are scanned —
    bare inline prose mentions ("you can run install") are NOT extracted.
    Returns a set of (tool, subcommand) tuples; sorted() for determinism (R-5).
    """
    found: set[tuple[str, str]] = set()
    for block_match in _FENCED_BLOCK_RE.finditer(readme_text):
        block_text = block_match.group(1)
        for cmd_match in _CMD_LINE_RE.finditer(block_text):
            found.add((cmd_match.group(1), cmd_match.group(2)))
    return found


def extract_relative_links(readme_text: str) -> set[str]:
    """Extract markdown relative links (leading ./ or ../) from readme_text.

    Excludes absolute http(s):// URLs and anchor-only #frag links.
    Returns a set of path strings; sorted() for determinism (R-5).
    """
    return {m.group(2) for m in _REL_LINK_RE.finditer(readme_text)}


def _is_command_registered(
    tool: str, subcommand: str, claims: list[dict]
) -> bool:
    """Return True if (tool, subcommand) is registered in the claims manifest.

    Matching rule (Slice 2 contract):
    - kind="allowlisted-example" with ref="<tool> <subcommand>" — escape hatch (R-4)
    - kind="command"  with tool=<tool> and ref=<subcommand>
    - kind="skill"    with tool=<tool> and ref starts with <subcommand>
                      (ref == subcommand, OR ref.startswith(subcommand + "/"))
    - kind="agent"    same prefix rule as skill
    The prefix rule lets a fenced `lore finish` match kind=skill ref="skills/finish"
    only when the subcommand matches; a bare subcommand also matches ref==subcommand.
    """
    allowlist_key = f"{tool} {subcommand}"
    for claim in claims:
        if claim.get("tool") != tool:
            continue
        kind = claim.get("kind", "")
        ref = claim.get("ref", "")
        if kind == "allowlisted-example" and ref == allowlist_key:
            return True
        if kind == "command" and ref == subcommand:
            return True
        if kind in ("skill", "agent"):
            if ref == subcommand or ref.startswith(subcommand + "/"):
                return True
    return False


def check_inverse(
    readme_path: Path,
    claims: list[dict],
    repo_root: Path,
) -> None:
    """Check a single README for commands/links that are unregistered in claims.

    For each extracted (tool, subcommand) from fenced sh/bash blocks:
      - must be registered or allowlisted (R-4); else raises AssertionError with
        "README <path> shows command `<tool> <sub>` not registered in landing_claims.toml"

    For each extracted relative link:
      - must be confined to repo_root (S-2); else raises with "relative link <x> escapes repo root"
      - must either exist on disk or be registered as a doc-link claim in claims. The link is
        normalized to a repo-root-relative path before the registration compare, since doc-link
        claim refs are repo-root-relative (e.g. "LICENSE") while extracted links are
        readme-dir-relative with a ./ or ../ prefix (I-1).

    Sets are sorted before assertions (R-5).
    """
    readme_text = readme_path.read_text(encoding="utf-8")
    repo_root_resolved = repo_root.resolve()

    # --- commands ---
    commands = extract_fenced_commands(readme_text)
    unregistered_cmds = sorted(
        f"{tool} {sub}"
        for tool, sub in commands
        if not _is_command_registered(tool, sub, claims)
    )
    assert not unregistered_cmds, (
        f"README {readme_path} shows command(s) not registered in landing_claims.toml:\n"
        + "\n".join(f"  `{c}`" for c in unregistered_cmds)
    )

    # --- relative links ---
    links = extract_relative_links(readme_text)
    readme_dir = readme_path.parent
    doc_link_refs = {c["ref"] for c in claims if c.get("kind") == "doc-link"}
    for link in sorted(links):
        resolved = (readme_dir / link).resolve()
        # S-2: confinement check — must not escape repo root
        try:
            rel_to_root = resolved.relative_to(repo_root_resolved)
        except ValueError:
            raise AssertionError(
                f"README {readme_path}: relative link {link!r} escapes repo root "
                f"({repo_root_resolved})"
            )
        # Must exist on disk OR be registered as a doc-link claim. doc-link refs are
        # repo-root-relative, so compare against the normalized rel_to_root, not the raw
        # readme-dir-relative link (I-1 — fixes the dead-branch namespace mismatch).
        if not resolved.exists() and str(rel_to_root) not in doc_link_refs:
            raise AssertionError(
                f"README {readme_path}: relative link {link!r} does not resolve to "
                f"an existing path and is not registered as a doc-link claim"
            )


# ---------------------------------------------------------------------------
# Slice 2 tests (write FIRST — must fail RED before helpers are implemented)
# ---------------------------------------------------------------------------


class TestExtractFencedCommands:
    """extract_fenced_commands() grammar-boundedness tests (U-3)."""

    def test_extracts_command_from_sh_block(self):
        """A trailhead command in a ```sh block is extracted."""
        text = "```sh\ntrailhead doctor\n```"
        result = extract_fenced_commands(text)
        assert ("trailhead", "doctor") in result

    def test_extracts_command_from_bash_block(self):
        """A trailhead command in a ```bash block is extracted."""
        text = "```bash\ntrailhead install\n```"
        result = extract_fenced_commands(text)
        assert ("trailhead", "install") in result

    def test_bare_prose_mention_not_extracted(self):
        """A bare inline mention 'you can run install' is NOT extracted (U-3)."""
        text = "you can run trailhead install to get started\n"
        result = extract_fenced_commands(text)
        assert result == set()

    def test_plain_fenced_block_not_extracted(self):
        """A plain ``` block (no sh/bash tag) is NOT extracted (U-3)."""
        text = "```\ntrailhead doctor\n```"
        result = extract_fenced_commands(text)
        assert result == set()

    def test_lore_command_extracted(self):
        """A lore command in a ```bash block is extracted."""
        text = "```bash\nlore init ~/vault\n```"
        result = extract_fenced_commands(text)
        assert ("lore", "init") in result

    def test_multiple_commands_extracted(self):
        """Multiple commands across multiple fenced blocks are all extracted."""
        text = (
            "```sh\ntrailhead doctor\n```\n"
            "```bash\nlore recall --areas auth\n```\n"
        )
        result = extract_fenced_commands(text)
        assert ("trailhead", "doctor") in result
        assert ("lore", "recall") in result

    def test_result_is_set(self):
        """Duplicate commands in fenced blocks appear once (set semantics)."""
        text = "```sh\ntrailhead install\ntrailhead install\n```"
        result = extract_fenced_commands(text)
        assert result == {("trailhead", "install")}

    def test_sorted_result_is_deterministic(self):
        """sorted() on the result is stable across calls (R-5)."""
        text = "```sh\ntrailhead doctor\ntrailhead install\n```"
        r1 = sorted(extract_fenced_commands(text))
        r2 = sorted(extract_fenced_commands(text))
        assert r1 == r2


class TestExtractRelativeLinks:
    """extract_relative_links() grammar tests."""

    def test_extracts_dot_slash_link(self):
        """[label](./path) is extracted."""
        text = "See [guide](./docs/paths.md) for details."
        result = extract_relative_links(text)
        assert "./docs/paths.md" in result

    def test_extracts_dot_dot_slash_link(self):
        """[label](../path) is extracted."""
        text = "See [sibling](../lore) for details."
        result = extract_relative_links(text)
        assert "../lore" in result

    def test_absolute_https_link_not_extracted(self):
        """[label](https://...) is NOT extracted."""
        text = "[docs](https://example.com/docs)"
        result = extract_relative_links(text)
        assert result == set()

    def test_anchor_link_not_extracted(self):
        """[label](#section) is NOT extracted."""
        text = "[section](#install)"
        result = extract_relative_links(text)
        assert result == set()

    def test_non_relative_bare_path_not_extracted(self):
        """[label](LICENSE) without leading ./ is NOT extracted."""
        text = "[LICENSE](LICENSE)"
        result = extract_relative_links(text)
        assert result == set()

    def test_sorted_result_is_deterministic(self):
        """sorted() on the result is stable across calls (R-5)."""
        text = "[a](./a.md) [b](./b.md)"
        r1 = sorted(extract_relative_links(text))
        r2 = sorted(extract_relative_links(text))
        assert r1 == r2


class TestCheckInverseFixtures:
    """Fixture-driven inverse check tests (the Slice-2 contract)."""

    _FIXTURE_CLAIMS = [
        {
            "kind": "command",
            "tool": "trailhead",
            "ref": "doctor",
            "source": "root",
        },
        {
            "kind": "doc-link",
            "tool": "trailhead",
            "ref": "docs/paths.md",
            "source": "root",
        },
    ]

    def test_registered_command_and_link_pass(self, tmp_path):
        """A registered fenced command + registered doc-link → inverse check passes."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "paths.md").write_text("# paths")
        readme = tmp_path / "README.md"
        readme.write_text(
            "```sh\ntrailhead doctor\n```\n"
            "See [guide](./docs/paths.md).\n"
        )
        # must not raise
        check_inverse(readme, self._FIXTURE_CLAIMS, tmp_path)

    def test_unregistered_fenced_command_fails_named_assertion(self, tmp_path):
        """An unregistered fenced command raises with 'not registered' in the message."""
        readme = tmp_path / "README.md"
        readme.write_text("```sh\ntrailhead frobnicate\n```\n")
        with pytest.raises(AssertionError, match="not registered in landing_claims.toml"):
            check_inverse(readme, self._FIXTURE_CLAIMS, tmp_path)

    def test_unregistered_relative_link_fails(self, tmp_path):
        """A relative link to a nonexistent, unregistered path raises."""
        readme = tmp_path / "README.md"
        readme.write_text("[ghost](./docs/ghost.md)\n")
        with pytest.raises(AssertionError, match="does not resolve to"):
            check_inverse(readme, self._FIXTURE_CLAIMS, tmp_path)

    def test_traversal_link_fails_escapes_repo_root(self, tmp_path):
        """A path-traversal link escaping repo root fails with 'escapes repo root' (S-2)."""
        readme = tmp_path / "README.md"
        # ../../../etc/passwd escapes tmp_path — this must fail even if /etc/passwd exists
        readme.write_text("[evil](../../../etc/passwd)\n")
        with pytest.raises(AssertionError, match="escapes repo root"):
            check_inverse(readme, self._FIXTURE_CLAIMS, tmp_path)

    def test_allowlisted_example_does_not_fail(self, tmp_path):
        """A deliberately-wrong command in an allowlisted-example claim does not fail (R-4)."""
        allowlist_claims = [
            {
                "kind": "allowlisted-example",
                "tool": "trailhead",
                "ref": "trailhead frobnicate",
                "source": "root",
            }
        ]
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Don't do this:\n```sh\ntrailhead frobnicate\n```\n"
        )
        # must not raise
        check_inverse(readme, allowlist_claims, tmp_path)

    def test_bare_prose_not_flagged(self, tmp_path):
        """A bare inline prose mention is NOT extracted/flagged (U-3)."""
        readme = tmp_path / "README.md"
        readme.write_text("you can run trailhead frobnicate to get started\n")
        # frobnicate is not in claims, but prose mentions are not extracted → must not raise
        check_inverse(readme, self._FIXTURE_CLAIMS, tmp_path)

    def test_skill_claim_satisfies_fenced_command(self, tmp_path):
        """A kind=skill claim with ref=<subcommand> satisfies a fenced `lore <subcommand>`.

        The prefix rule matches ref==subcommand (and ref.startswith(subcommand + "/")),
        so a bare-name skill ref satisfies a fenced command of the same name.
        """
        claims = [
            {
                "kind": "skill",
                "tool": "lore",
                "ref": "recall",
                "source": "lore",
            }
        ]
        readme = tmp_path / "README.md"
        readme.write_text("```bash\nlore recall --areas auth\n```\n")
        # must not raise — the skill claim satisfies the lore recall command
        check_inverse(readme, claims, tmp_path)

    def test_existing_relative_link_passes(self, tmp_path):
        """A relative link that resolves to an existing file passes."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "paths.md").write_text("# paths")
        readme = tmp_path / "README.md"
        readme.write_text("See [paths](./docs/paths.md).\n")
        # must not raise
        check_inverse(readme, [], tmp_path)

    def test_sorted_extraction_determinism(self, tmp_path):
        """Extraction result sorted before assertion is stable (R-5)."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "```sh\ntrailhead doctor\ntrailhead install\n```\n"
        )
        claims = [
            {"kind": "command", "tool": "trailhead", "ref": "doctor", "source": "root"},
            {"kind": "command", "tool": "trailhead", "ref": "install", "source": "root"},
        ]
        # Call twice; should produce same result (no flake)
        check_inverse(readme, claims, tmp_path)
        check_inverse(readme, claims, tmp_path)

    def test_registered_doc_link_to_absent_file_passes_via_registration(self, tmp_path):
        """A registered doc-link claim is the ONLY thing that can pass a link to an
        absent file — proves the registration branch is live, not dead (I-1).

        The link target does not exist on disk, so existence cannot pass it; only the
        repo-root-relative doc-link claim match can. doc-link refs are repo-root-relative
        ("docs/future.md"), the extracted link is readme-dir-relative ("./docs/future.md")."""
        claims = [
            {"kind": "doc-link", "tool": "trailhead", "ref": "docs/future.md", "source": "root"}
        ]
        readme = tmp_path / "README.md"
        readme.write_text("See the [future guide](./docs/future.md) (not written yet).\n")
        # docs/future.md does not exist; must pass purely via the doc-link registration.
        check_inverse(readme, claims, tmp_path)

    def test_unregistered_absent_link_still_fails_after_normalization(self, tmp_path):
        """The I-1 normalization must not loosen the dangling-link guard: an absent,
        UN-registered link still fails."""
        claims = [
            {"kind": "doc-link", "tool": "trailhead", "ref": "docs/other.md", "source": "root"}
        ]
        readme = tmp_path / "README.md"
        readme.write_text("[ghost](./docs/ghost.md)\n")
        with pytest.raises(AssertionError, match="does not resolve to"):
            check_inverse(readme, claims, tmp_path)

    def test_link_with_title_attribute_does_not_leak_into_path(self, tmp_path):
        """A markdown title attribute — [label](./p "title") — must not leak into the
        extracted path (M-2)."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "paths.md").write_text("# paths")
        readme = tmp_path / "README.md"
        readme.write_text('See [paths](./docs/paths.md "the paths guide").\n')
        assert extract_relative_links(readme.read_text()) == {"./docs/paths.md"}
        # and the inverse check resolves it cleanly (no spurious title in the path)
        check_inverse(readme, [], tmp_path)


class TestRealReadmeInverseScan:
    """Real-README inverse scan over the four indexed READMEs: every fenced command
    and relative link in each README is registered in landing_claims.toml (D-5)."""

    def test_all_real_readmes_pass_inverse_check(self):
        """Every fenced command + relative link in the four READMEs is registered."""
        assert _CLAIMS_FILE.exists(), f"landing_claims.toml not found at {_CLAIMS_FILE}"
        with open(_CLAIMS_FILE, "rb") as f:
            data = tomllib.load(f)
        claims = data.get("claim", [])

        failures: list[str] = []
        for readme_path in README_INDEX:
            if not readme_path.exists():
                continue
            try:
                check_inverse(readme_path, claims, _REPO_ROOT)
            except AssertionError as e:
                failures.append(str(e))

        assert not failures, (
            f"{len(failures)} README(s) failed the inverse check:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


# ---------------------------------------------------------------------------
# Slice 3 — honesty guards (R-3, S-3)
# ---------------------------------------------------------------------------


_LORE_README = _REPO_ROOT / "tools" / "lore" / "README.md"
_TOOL_READMES: list[Path] = [
    _REPO_ROOT / "tools" / "lore" / "README.md",
    _REPO_ROOT / "tools" / "craft" / "README.md",
    _REPO_ROOT / "tools" / "camp" / "README.md",
]


class TestLoreRecallHonestyGuards:
    """D22 recall-fix honesty guards — positive (R-3) + negative.

    The lore README "How recall works" section must:
      - Contain `recall --areas` (the real mechanism — R-3 positive).
      - Not assert branch-keyword recall as a live mechanism (removed 2026-06-05).
      - Not present Tier-2 semantic/embedding recall as a built feature.
    """

    def test_lore_readme_contains_recall_areas_positive(self):
        """R-3 positive: the lore README must contain 'recall --areas' (the real mechanism)."""
        assert _LORE_README.exists(), f"lore README not found at {_LORE_README}"
        text = _LORE_README.read_text(encoding="utf-8")
        assert "recall --areas" in text, (
            "lore README must contain 'recall --areas' — the area-mediated recall mechanism "
            "(R-3 positive: ensures the real mechanism is documented, not just that the old "
            "oversell is absent)"
        )

    def test_lore_readme_no_branch_keyword_recall_as_live_feature(self):
        """Negative: lore README must not assert branch-keyword recall as a live mechanism.

        The branch-keyword recall was removed 2026-06-05 (every camp branch is
        worktree-<slug> so the keyword matched universally). The stale paragraph
        "When the current git branch contains any of those keywords" must be gone.

        Note: a historical mention ("was removed") is distinct from the stale *claim* phrasing
        and would not match this targeted grep — the test targets the live-claim form.
        """
        assert _LORE_README.exists(), f"lore README not found at {_LORE_README}"
        text = _LORE_README.read_text(encoding="utf-8")
        assert "git branch contains" not in text, (
            "lore README must not assert branch-keyword recall as a live mechanism — "
            "the 'When the current git branch contains any of those keywords' paragraph "
            "describes a removed feature (removed 2026-06-05)"
        )

    def test_lore_readme_no_semantic_recall_as_built_feature(self):
        """Negative regression sentinel: lore README must not present Tier-2 semantic/
        embedding recall as built.

        Tier-2 local embeddings are opt-in and NOT built (D23). This is a *phrase-pinned*
        regression guard: it triggers on the embedding/semantic vocabulary a reintroduction
        would most likely use, and only passes such a line if it carries a not-yet-built
        qualifier. It is dormant today (none of these phrases appear), and is a sentinel
        against a *future* edit reintroducing the oversell — it is NOT a general semantic-
        claim detector (a wholly-novel paraphrase could still slip past; the prose-honesty
        review is the backstop). We scan line-by-line to avoid variable-width lookbehind.
        """
        assert _LORE_README.exists(), f"lore README not found at {_LORE_README}"
        text = _LORE_README.read_text(encoding="utf-8")
        trigger_terms = ("semantic recall", "semantic search", "embedding", "vector search")
        qualifying_terms = ("planned", "not yet", "coming soon", "opt-in", "not built")
        unqualified_lines = []
        for line in text.splitlines():
            lower = line.lower()
            if any(t in lower for t in trigger_terms):
                if not any(q in lower for q in qualifying_terms):
                    unqualified_lines.append(line.strip())
        assert not unqualified_lines, (
            "lore README must not present Tier-2 semantic/embedding recall as a built feature. "
            "If mentioned at all, qualify explicitly as 'planned / not yet built'. "
            f"Unqualified occurrences: {unqualified_lines}"
        )


class TestNoToolReadmePypiLine:
    """S-3: none of the three tool READMEs may contain a 'pip install trailhead' line."""

    @pytest.mark.parametrize("readme", _TOOL_READMES, ids=lambda p: p.parent.name)
    def test_no_pip_install_trailhead_line(self, readme):
        """Tool README must not contain 'pip install trailhead' (name-squat exposure, S-3)."""
        assert readme.exists(), f"README not found at {readme}"
        text = readme.read_text(encoding="utf-8")
        assert "pip install trailhead" not in text, (
            f"{readme.parent.name}/README.md must not contain 'pip install trailhead' — "
            "the public PyPI install does not exist yet (lands with the org/repo-homing work); "
            "showing it implies a live install that would 404 (S-3)"
        )


# ---------------------------------------------------------------------------
# Slice 4 — root README honesty + structural guards
# ---------------------------------------------------------------------------

_ROOT_README = _REPO_ROOT / "README.md"


class TestRootReadmeNoPypiLine:
    """S-3 applied to the root README: no 'pip install trailhead' line allowed.

    The current root README has TWO such lines; the narrative landing must remove them.
    """

    def test_root_readme_no_pip_install_trailhead(self):
        """Root README must not contain 'pip install trailhead' (S-3, D-2 no-lie)."""
        assert _ROOT_README.exists(), f"root README not found at {_ROOT_README}"
        text = _ROOT_README.read_text(encoding="utf-8")
        assert "pip install trailhead" not in text, (
            "README.md must not contain 'pip install trailhead' — "
            "the public PyPI install does not exist yet (lands with the org/repo-homing work). "
            "Use the editable local install block ('Try it today') + the registry-future block "
            "instead (S-3 / D-2)."
        )


class TestRootReadmeNoSemanticRecallOversell:
    """Root README must not present Tier-2 semantic/embedding recall as a built feature (D-2).

    Mirrors the lore-README guard from Slice 3. Tier-2 local embeddings are opt-in and
    NOT built (D23). Any mention must carry a not-yet-built qualifier.
    """

    def test_root_readme_no_unqualified_semantic_recall(self):
        """Root README must not claim semantic/embedding recall as built.

        Phrase-pinned regression sentinel: triggers on embedding/semantic vocabulary a
        reintroduction would most likely use. A wholly-novel paraphrase could slip past;
        the prose-honesty review is the backstop.
        """
        assert _ROOT_README.exists(), f"root README not found at {_ROOT_README}"
        text = _ROOT_README.read_text(encoding="utf-8")
        trigger_terms = ("semantic recall", "semantic search", "embedding", "vector search")
        qualifying_terms = ("planned", "not yet", "coming soon", "opt-in", "not built")
        unqualified_lines = []
        for line in text.splitlines():
            lower = line.lower()
            if any(t in lower for t in trigger_terms):
                if not any(q in lower for q in qualifying_terms):
                    unqualified_lines.append(line.strip())
        assert not unqualified_lines, (
            "README.md must not present Tier-2 semantic/embedding recall as a built feature. "
            "If mentioned at all, qualify explicitly as 'planned / not yet built'. "
            f"Unqualified occurrences: {unqualified_lines}"
        )


class TestRootReadmeStructuralGuard:
    """A-3: the root README must NOT place a four-tool markdown table before the lore lead.

    The funnel rule: the lore use-case + first command must appear BEFORE any
    multi-tool table that names lore, camp, and craft together in a single row.
    This prevents the 'concept-map first' anti-pattern the plan warns against.

    Implementation: find the character-offset of the first fenced sh/bash block
    (the lore lead's two commands) and the first multi-tool table row (a Markdown
    table row containing at least two of: lore, camp, craft). Assert the fenced
    block comes first.
    """

    def test_lore_lead_appears_before_multi_tool_table(self):
        """The first fenced sh/bash block (lore lead) must precede any multi-tool table row.

        A multi-tool table row is any '| ... |' line that names at least two of the
        three tools (lore, camp, craft) — the signal that a 'What's included' concept
        map has started. The lore use-case must come first (A-3).
        """
        assert _ROOT_README.exists(), f"root README not found at {_ROOT_README}"
        text = _ROOT_README.read_text(encoding="utf-8")

        # Locate the first fenced sh/bash block
        fenced_match = _FENCED_BLOCK_RE.search(text)
        assert fenced_match is not None, (
            "README.md has no fenced sh/bash code block — "
            "the lore use-case lead must include at least one runnable command block"
        )
        first_fenced_offset = fenced_match.start()

        # Locate the first multi-tool table row: a '|' line naming ≥2 of lore/camp/craft
        multi_tool_table_offset: int | None = None
        for line_match in re.finditer(r"^\|.*\|.*$", text, re.MULTILINE):
            line_text = line_match.group(0).lower()
            named_tools = sum(1 for t in ("lore", "camp", "craft") if t in line_text)
            if named_tools >= 2:
                multi_tool_table_offset = line_match.start()
                break

        if multi_tool_table_offset is None:
            # No multi-tool table found — the guard is trivially satisfied
            return

        assert first_fenced_offset < multi_tool_table_offset, (
            "README.md places a multi-tool concept table before the lore lead. "
            f"First fenced block at offset {first_fenced_offset}; "
            f"first multi-tool table row at offset {multi_tool_table_offset}. "
            "The lore use-case + first command must appear before the 'What's included' "
            "table — lead with one use case, not the concept map (A-3)."
        )


# ---------------------------------------------------------------------------
# Slice 5 — mandatory landing leak gate (S-1) + no-D17-collapse scope guard
# ---------------------------------------------------------------------------

# The landing-surface files the gate must certify as clean.
# Slice 3 added tools/portage/README.md; Slice 4 added tools/landing/README.md.
_LANDING_FILES: list[Path] = [
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "tools" / "lore" / "README.md",
    _REPO_ROOT / "tools" / "craft" / "README.md",
    _REPO_ROOT / "tools" / "camp" / "README.md",
    _REPO_ROOT / "tools" / "portage" / "README.md",
    _REPO_ROOT / "tools" / "landing" / "README.md",
    _REPO_ROOT / "trailhead" / "landing_claims.toml",
]

# Path to the leak_gate.py script.
_LEAK_GATE = (
    _REPO_ROOT / "tools" / "craft" / "plugins" / "craft" / "scripts" / "leak_gate.py"
)

# Denylist token classes seeded into the ephemeral denylist (S-1).
#
# Regex design rationale:
#   brain/ path tokens — brain/ prefix catches vault path references in any context
#   ~/code/brain — absolute brain path (home-relative form)
#   localhost:7777 — an internal tooling URL (kept in the leak denylist defensively)
#   \bzenith\b — bare word; the company/product name; NOT "zenithhealth" (contained)
#   \bpenny\b — the internal product name
#   WS-\d+ — internal workstream IDs
#   /Users/tduffield — the author's absolute home path
#   5CC67114CCF2B7B5 — the zenith work GPG key (internal signing key ID)
#
# Words that must NOT false-positive (legitimate landing vocabulary checked below):
#   trailhead, lore, craft, camp, Claude Code, agent-native, recall, area, preset,
#   skill, agent, capability, install, doctor, config, update, minimal, standard, full
#
_DENYLIST_ENTRIES: list[str] = [
    # brain vault path references
    r"brain/",
    r"~/code/brain",
    # internal tooling URL
    r"localhost:7777",
    # internal product / company tokens (bare word only — not in compound identifiers)
    r"\bzenith\b",
    r"\bpenny\b",
    # internal workstream IDs
    r"WS-\d+",
    # author's absolute home path
    r"/Users/tduffield",
    # zenith work GPG key ID (internal)
    r"5CC67114CCF2B7B5",
]

_DENYLIST_COMMENT = (
    "# Slice-5 ephemeral landing-surface denylist — "
    "business-context strings, not secrets\n"
)


def _build_denylist(tmp_path: Path) -> Path:
    """Write an ephemeral denylist to tmp_path and return its path."""
    dl = tmp_path / "landing-denylist"
    dl.write_text(
        _DENYLIST_COMMENT + "\n".join(_DENYLIST_ENTRIES) + "\n",
        encoding="utf-8",
    )
    return dl


def _copy_landing_files_to_dir(dest: Path) -> Path:
    """Copy the landing-surface files into a flat directory under dest.

    leak_gate.py's _text_files() uses rglob("*") — it only yields files when
    scanning a DIRECTORY tree, not an individual file path. Copying into a
    directory ensures the scan is non-vacuous (the file is actually read and
    checked).

    Files are copied with a flat name derived from their tool context so
    distinct-enough for error messages:
      README.md          → root-README.md
      tools/lore/README.md   → lore-README.md
      tools/craft/README.md  → craft-README.md
      tools/camp/README.md   → camp-README.md
      trailhead/landing_claims.toml → landing_claims.toml
    """
    dest.mkdir(parents=True, exist_ok=True)
    name_map: list[tuple[Path, str]] = [
        (_REPO_ROOT / "README.md", "root-README.md"),
        (_REPO_ROOT / "tools" / "lore" / "README.md", "lore-README.md"),
        (_REPO_ROOT / "tools" / "craft" / "README.md", "craft-README.md"),
        (_REPO_ROOT / "tools" / "camp" / "README.md", "camp-README.md"),
        (_REPO_ROOT / "tools" / "portage" / "README.md", "portage-README.md"),
        (_REPO_ROOT / "tools" / "landing" / "README.md", "landing-README.md"),
        (_REPO_ROOT / "trailhead" / "landing_claims.toml", "landing_claims.toml"),
    ]
    for src, dst_name in name_map:
        assert src.exists(), f"landing file not found: {src}"
        shutil.copy2(src, dest / dst_name)
    return dest


def _run_gate(trees: list[Path], denylist: Path) -> subprocess.CompletedProcess:
    """Run leak_gate.py as a subprocess of the current Python interpreter."""
    cmd = [sys.executable, str(_LEAK_GATE), *[str(t) for t in trees], "--denylist", str(denylist)]
    return subprocess.run(cmd, capture_output=True, text=True)


class TestLandingSurfaceLeakGate:
    """S-1: mandatory, gating leak-gate run over the landing-surface files.

    Uses an ephemeral, repo-tracked denylist written to tmp_path so the gate
    runs identically on any checkout — never depends on ~/.claude/leak-gate.denylist.

    Test structure:
    1. Positive (GATING): scanning the real landing files with the denylist exits 0.
       This is the mandatory S-1 gate — a real leak makes the suite RED.
    2. Non-vacuous negative twin: a tmp_path file seeded with a denylist token exits 1,
       proving the denylist actually catches leaks (so the exit-0 is not vacuous).
    3. Fail-closed (exit 2): missing path and empty denylist.
    """

    def test_positive_gate_landing_surface_is_clean(self, tmp_path: Path) -> None:
        """S-1 GATING: the landing-surface files must exit 0 with the ephemeral denylist.

        A real leak (e.g. a brain/ path reference, a WS-\\d+ workstream ID, a bare
        'zenith' token) in any of the files makes this test RED — that is the
        intended behavior. Do NOT loosen the denylist to make it pass; fix the leak.

        The files are copied into a tmp_path directory so _text_files() scans them
        via rglob("*") — scanning a directory is non-vacuous; scanning a bare file
        path is vacuous (rglob on a file yields nothing).
        """
        scan_dir = _copy_landing_files_to_dir(tmp_path / "scan")
        denylist = _build_denylist(tmp_path)
        result = _run_gate([scan_dir], denylist)
        assert result.returncode == 0, (
            "Landing surface contains a forbidden token — leak-gate exited 1.\n"
            "DO NOT loosen the denylist to fix this; instead fix the leak in the "
            "offending landing file.\n"
            f"Gate output:\n{result.stdout}\n{result.stderr}"
        )

    def test_negative_twin_seeded_token_exits_1(self, tmp_path: Path) -> None:
        """Non-vacuous: a file seeded with a denylist token exits 1.

        This proves the denylist is actually enforced and the positive exit-0 above
        is not vacuous (the gate truly scanned the files, not nothing).
        """
        dirty_dir = tmp_path / "dirty"
        dirty_dir.mkdir()
        (dirty_dir / "leak.md").write_text(
            "This doc was part of the zenith project.\n",
            encoding="utf-8",
        )
        denylist = _build_denylist(tmp_path)
        result = _run_gate([dirty_dir], denylist)
        assert result.returncode == 1, (
            f"Expected exit 1 (leak found) for seeded token 'zenith', got {result.returncode}. "
            f"Output: {result.stdout}{result.stderr}"
        )

    def test_negative_twin_brain_path_exits_1(self, tmp_path: Path) -> None:
        """Non-vacuous: a brain/ path reference in a file exits 1."""
        dirty_dir = tmp_path / "dirty-brain"
        dirty_dir.mkdir()
        (dirty_dir / "leak.md").write_text(
            "See brain/areas/my-area.md for context.\n",
            encoding="utf-8",
        )
        denylist = _build_denylist(tmp_path)
        result = _run_gate([dirty_dir], denylist)
        assert result.returncode == 1, (
            f"Expected exit 1 (leak found) for 'brain/' path, got {result.returncode}. "
            f"Output: {result.stdout}{result.stderr}"
        )

    def test_negative_twin_ws_id_exits_1(self, tmp_path: Path) -> None:
        """Non-vacuous: a WS-N internal workstream ID in a file exits 1."""
        dirty_dir = tmp_path / "dirty-ws"
        dirty_dir.mkdir()
        (dirty_dir / "leak.md").write_text(
            "This feature was tracked as WS-8 internally.\n",
            encoding="utf-8",
        )
        denylist = _build_denylist(tmp_path)
        result = _run_gate([dirty_dir], denylist)
        assert result.returncode == 1, (
            f"Expected exit 1 (leak found) for 'WS-8', got {result.returncode}. "
            f"Output: {result.stdout}{result.stderr}"
        )

    def test_fail_closed_missing_path_exits_2(self, tmp_path: Path) -> None:
        """Fail-closed: a non-existent tree path exits 2 (cannot certify clean)."""
        denylist = _build_denylist(tmp_path)
        result = _run_gate([tmp_path / "does-not-exist"], denylist)
        assert result.returncode == 2, (
            f"Expected exit 2 (fail-closed) for missing path, got {result.returncode}. "
            f"Output: {result.stdout}{result.stderr}"
        )

    def test_fail_closed_empty_denylist_exits_2(self, tmp_path: Path) -> None:
        """Fail-closed: an empty denylist exits 2 (vacuous certification refused)."""
        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "harmless.md").write_text("nothing here\n", encoding="utf-8")
        empty_dl = tmp_path / "empty.denylist"
        empty_dl.write_text("# only a comment\n\n", encoding="utf-8")
        result = _run_gate([scan_dir], empty_dl)
        assert result.returncode == 2, (
            f"Expected exit 2 (fail-closed) for empty denylist, got {result.returncode}. "
            f"Output: {result.stdout}{result.stderr}"
        )

    def test_legitimate_vocabulary_not_flagged(self, tmp_path: Path) -> None:
        """Denylist must NOT false-positive on legitimate landing vocabulary.

        Words that are valid in a public-facing landing: trailhead, lore, craft,
        camp, Claude Code, agent-native, recall, area, preset, skill, agent,
        capability, install, doctor, config, update, minimal, standard, full.
        """
        clean_dir = tmp_path / "clean-vocab"
        clean_dir.mkdir()
        (clean_dir / "vocab.md").write_text(
            "# trailhead\n\n"
            "Agent-native project memory that works with your existing setup.\n\n"
            "lore, craft, and camp are the three plugins.\n"
            "Run `trailhead install` to get started with the minimal or standard preset.\n"
            "Use `lore recall --areas <topic>` to load area memory.\n"
            "Claude Code is the agent runtime. craft:execute is a skill.\n"
            "Run `trailhead doctor` or `trailhead config` or `trailhead update`.\n"
            "The full preset wires all capabilities.\n",
            encoding="utf-8",
        )
        denylist = _build_denylist(tmp_path)
        result = _run_gate([clean_dir], denylist)
        assert result.returncode == 0, (
            "Denylist false-positived on legitimate landing vocabulary.\n"
            f"Gate output:\n{result.stdout}\n{result.stderr}"
        )


class TestNoDivCollapseGuard:
    """D17 scope guard: asserts that Slice 5 did NOT wire the D17 group-workspace-config.

    The D17 collapse (wiring trailhead as the group's camp workspace + adding a
    group CLAUDE.md/AGENTS.md) is explicitly deferred as a tracked follow-up, not
    built in WS-8 (D-3). This parallels Step-6's no-cutover guard: the boundary is
    a tested contract, not just a comment.

    Two negative assertions (kept simple and meaningful, not brittle):
    1. The root README.md does NOT claim "this repo is the group's workspace" /
       "camp workspace" (the D-3 omit branch: if D17 wiring isn't in, the sentence
       must not be in either — the claim would be link-dead).
    2. No group coordination file (CLAUDE.md or AGENTS.md) was added to the repo root
       by WS-8 work.
    """

    def test_root_readme_does_not_claim_group_workspace(self) -> None:
        """Root README must not claim 'this repo is the group's workspace' (D-3 omit).

        If D17 hasn't landed, the link-dead sentence must not be in the README.
        The guard targets the specific vocabulary the workspace-config feature would
        introduce: 'group's workspace', 'camp workspace', 'workspace = <path>' wiring.
        """
        assert _ROOT_README.exists(), f"root README not found at {_ROOT_README}"
        text = _ROOT_README.read_text(encoding="utf-8")
        # None of these phrases should appear in a landing that hasn't wired D17
        forbidden_phrases = (
            "group's workspace",
            "group workspace",
            "camp workspace",
            "workspace = ",
        )
        for phrase in forbidden_phrases:
            assert phrase.lower() not in text.lower(), (
                f"README.md contains '{phrase}', which implies D17 group-workspace-config "
                "is wired. D17 is deferred (D-3) — this claim is link-dead until D17 lands. "
                "Either wire D17 properly (out of WS-8 scope) or remove the sentence."
            )

    # NOTE: the former `test_no_group_coordination_file_at_repo_root` guard was
    # removed. It used the mere existence of CLAUDE.md at the repo root as a proxy
    # for "D17 group-workspace-config was wired". That proxy was invalidated when
    # the project deliberately adopted a repo-root CLAUDE.md to load the vision
    # axioms (it imports docs/vision.md) — a purpose unrelated to D17. D17 wiring
    # is still guarded above by test_root_readme_does_not_claim_group_workspace,
    # which checks for the actual workspace-config vocabulary rather than a file's
    # presence.
