"""Tests for the maturity calibration-block renderer.

The renderer resolves exactly one maturity level for a spec under review and
emits the calibration block the council dispatch substitutes. It reads the
spec body on stdin and accepts an optional `--agent-instruction-file <path>`.

Resolution order:
  - a `## Maturity` section naming exactly one repository -> that
    repository's level, basis `stamp`
  - a section naming more than one repository -> a concern-by-repository
    matrix, each stamped repository rated at its own declared level, basis
    `highest-stamped`, with the highest stamped level reported as the
    fallback for a finding no repository can be attributed to
  - a section absent (including empty stdin) -> `maturity_resolve.resolve()`
    against the agent-instruction file, basis `agent-instruction-file`; with
    no such flag, `production`, basis `default`
  - any other stamp violation refuses, carrying the stamp reader's own
    reason-code

Stdout on success (exit 0) is the calibration block: the resolved level and
its basis, plus all five maturity-sensitive concerns each rated at the
severity the resolved level maps to (Critical at production, Important at
early, Minor at prototype) — or, at basis `highest-stamped`, one severity
per stamped repository for each of those concerns.

Exit codes:
  0 -> resolved, block printed
  2 -> fail-closed, reason-code on stderr, no block on stdout
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "craft" / "scripts"
BARS = SCRIPTS_DIR / "maturity_bars.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import maturity_bars  # noqa: E402

CONCERNS = (
    "backwards compatibility",
    "migration and backfill",
    "rollback and reversibility",
    "production failure visibility",
    "cross-consumer blast radius",
)

SEVERITIES = ("Critical", "Important", "Minor")

SINGLE_REPO_PROTOTYPE = """\
# Some Spec

## Maturity

- lookout: prototype
"""

SINGLE_REPO_EARLY = """\
# Some Spec

## Maturity

- lookout: early
"""

SINGLE_REPO_PRODUCTION = """\
# Some Spec

## Maturity

- lookout: production
"""

NO_MATURITY_SECTION = """\
# Some Spec

Just prose, no stamp at all.
"""

MALFORMED_ENTRY = """\
# Some Spec

## Maturity

- lookout production
"""

INVALID_LEVEL = """\
# Some Spec

## Maturity

- lookout: XTREME_HAXOR_MARKER_9f3a
"""

DUPLICATE_MEMBER = """\
# Some Spec

## Maturity

- lookout: prototype
- lookout: early
"""

DUPLICATE_SECTION = """\
# Some Spec

## Maturity

- lookout: prototype

## Maturity

- trailhead: production
"""

EMPTY_SECTION = """\
# Some Spec

## Maturity

"""

UNRESOLVED_ENUMERATION = """\
# Some Spec

## Maturity

<!-- unresolved-enumeration: cannot enumerate repos touched -->
"""

AGENT_FILE_PROTOTYPE = """\
# Some Repo

## Project Maturity

prototype
"""

AGENT_FILE_EARLY = """\
# Some Repo

## Project Maturity

early
"""

AGENT_FILE_PRODUCTION = """\
# Some Repo

## Project Maturity

production
"""

AGENT_FILE_NO_DECLARATION = """\
# Some Repo

Nothing declared here.
"""

_LEVEL_ORDER = ("prototype", "early", "production")
_SEVERITY_BY_LEVEL = {
    "production": "Critical",
    "early": "Important",
    "prototype": "Minor",
}


def _run(stdin_bytes: bytes, args: list[str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BARS), *(args or [])],
        input=stdin_bytes,
        capture_output=True,
    )


def _stdout(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode("utf-8")


def _stderr(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8")


# ---- per-level severity rendering ----------------------------------------


def test_production_level_rates_every_concern_critical():
    result = _run(SINGLE_REPO_PRODUCTION.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Critical" in out


def test_early_level_rates_every_concern_important():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Important" in out


def test_prototype_level_rates_every_concern_minor():
    result = _run(SINGLE_REPO_PROTOTYPE.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert f"{concern}: Minor" in out


def test_all_five_concerns_appear_at_every_level():
    for fixture in (SINGLE_REPO_PROTOTYPE, SINGLE_REPO_EARLY, SINGLE_REPO_PRODUCTION):
        out = _stdout(_run(fixture.encode("utf-8")))
        for concern in CONCERNS:
            assert concern in out, f"{concern!r} missing from block for {fixture!r}"


def test_severity_vocabulary_is_exactly_three_words_no_fourth_tier():
    for fixture in (SINGLE_REPO_PROTOTYPE, SINGLE_REPO_EARLY, SINGLE_REPO_PRODUCTION):
        out = _stdout(_run(fixture.encode("utf-8")))
        found = {word for word in ("Critical", "Important", "Minor", "Major", "Severe", "Blocker") if word in out}
        assert found <= set(SEVERITIES)
        # every concern line's severity is one of the three
        for concern in CONCERNS:
            for line in out.splitlines():
                if line.strip().startswith(f"- {concern}:"):
                    severity = line.split(":", 1)[1].strip()
                    assert severity in SEVERITIES


# ---- --level flag: preview any level's block, default output untouched ----


@pytest.mark.parametrize("level", ["prototype", "early", "production"])
def test_level_flag_renders_that_levels_block_regardless_of_stdin(level):
    result = _run(b"", ["--level", level])
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert f"maturity: {level} (basis: requested)" in out
    for concern in CONCERNS:
        assert f"{concern}: {_SEVERITY_BY_LEVEL[level]}" in out


def test_level_flag_ignores_a_conflicting_maturity_stamp_on_stdin():
    result = _run(SINGLE_REPO_PROTOTYPE.encode("utf-8"), ["--level", "production"])
    assert result.returncode == 0, _stderr(result)
    out = _stdout(result)
    assert "maturity: production (basis: requested)" in out
    for concern in CONCERNS:
        assert f"{concern}: Critical" in out


def test_default_output_is_byte_for_byte_unchanged_without_the_level_flag():
    with_flag_absent = _stdout(_run(b""))
    assert with_flag_absent == "maturity: production (basis: default)\n\n" + "".join(
        f"- {concern}: Critical\n" for concern in CONCERNS
    ) + (
        "\nEvery concern above is reported at its mapped severity and is "
        "never filtered out.\nWhere a concern above also appears in your "
        "per-lens Critical bars, the severity above governs — the bars say "
        "what to look for, this block says how severely to rate it.\n"
    )


# ---- basis: stamp ----------------------------------------------------------


def test_single_repository_stamp_resolves_to_that_level_basis_stamp():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "early" in out
    assert "(basis: stamp)" in out


def test_single_repository_stamp_wins_over_conflicting_agent_instruction_file(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PRODUCTION, encoding="utf-8")
    result = _run(
        SINGLE_REPO_PROTOTYPE.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "prototype" in out
    assert "(basis: stamp)" in out
    for concern in CONCERNS:
        assert f"{concern}: Minor" in out


# ---- basis: highest-stamped -------------------------------------------------


def _two_repo_stamp(level_a: str, level_b: str) -> str:
    return (
        "# Some Spec\n\n## Maturity\n\n"
        f"- repo-a: {level_a}\n"
        f"- repo-b: {level_b}\n"
    )


MIXED_TWO_REPO_STAMP = "# S\n\n## Maturity\n\n- lookout: prototype\n- trailhead: production\n"

THREE_REPO_STAMP = (
    "# Some Spec\n\n## Maturity\n\n"
    "- alpha: prototype\n"
    "- mango: early\n"
    "- zebra: production\n"
)

SEVERITY_COLLISION_STAMP = (
    "# S\n\n## Maturity\n\n- lookout: production\n- Critical: prototype\n"
)


@pytest.mark.parametrize("level_a,level_b", list(itertools.permutations(_LEVEL_ORDER, 2)))
def test_two_repo_stamp_renders_each_repositorys_own_severity_basis_highest_stamped(level_a, level_b):
    result = _run(_two_repo_stamp(level_a, level_b).encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    higher = level_a if _LEVEL_ORDER.index(level_a) > _LEVEL_ORDER.index(level_b) else level_b
    assert f"maturity: {higher} (basis: highest-stamped)" in out
    for concern in CONCERNS:
        # This exact-cell assertion already pins that the two repositories'
        # rendered severities differ whenever `_SEVERITY_BY_LEVEL[level_a]`
        # and `_SEVERITY_BY_LEVEL[level_b]` differ — a separate assertion on
        # the two dict lookups themselves would prove only a property of
        # this parametrization, never of the renderer's output.
        assert (
            f"- {concern}: `repo-a`={_SEVERITY_BY_LEVEL[level_a]}, "
            f"`repo-b`={_SEVERITY_BY_LEVEL[level_b]}"
        ) in out


def test_two_repo_stamp_at_same_level_still_renders_the_full_matrix():
    spec = "# Some Spec\n\n## Maturity\n\n- repo-a: early\n- repo-b: early\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: early (basis: highest-stamped)" in out
    assert "concern x repository: `repo-a`, `repo-b`" in out
    for concern in CONCERNS:
        assert f"- {concern}: `repo-a`=Important, `repo-b`=Important" in out


def test_three_repo_stamp_orders_columns_by_member_name_not_write_order():
    spec = (
        "# Some Spec\n\n## Maturity\n\n"
        "- zebra: production\n"
        "- alpha: prototype\n"
        "- mango: early\n"
    )
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "concern x repository: `alpha`, `mango`, `zebra`" in out
    for concern in CONCERNS:
        assert (
            f"- {concern}: `alpha`=Minor, `mango`=Important, `zebra`=Critical"
        ) in out


def test_highest_stamped_names_the_fallback_level_explicitly():
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "maturity: production (basis: highest-stamped)" in out
    assert "not a general highest-wins rule" in out


@pytest.mark.parametrize("level_a,level_b", list(itertools.permutations(_LEVEL_ORDER, 2)))
def test_highest_stamped_block_states_the_fallback_severity_itself(level_a, level_b):
    """The header names a level (`maturity: <level> (basis: highest-stamped)`),
    never a severity — a lens rating an unattributable finding needs the
    fallback severity spelled out in the block itself, not derived from a
    ladder table (`_shared/council.md`) it is never shown."""
    out = _stdout(_run(_two_repo_stamp(level_a, level_b).encode("utf-8")))
    higher = level_a if _LEVEL_ORDER.index(level_a) > _LEVEL_ORDER.index(level_b) else level_b
    assert f"maturity: {higher} (basis: highest-stamped)" in out
    fallback_line = next(line for line in out.splitlines() if "sanctioned fallback" in line)
    assert _SEVERITY_BY_LEVEL[higher] in fallback_line


def test_highest_stamped_block_states_the_path_attribution_rule():
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "leading camp member name segment" in out
    assert "path" in out


def test_attribution_pointer_names_the_columns_below_not_above():
    """The attribution rule renders before the matrix's column header in
    every rendered block (the rule is fixed prose that always precedes the
    per-repository section), so a pointer reading "above" is wrong as
    rendered no matter which repository the columns name."""
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    lines = out.splitlines()
    rule_idx = next(i for i, line in enumerate(lines) if "against the columns" in line)
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("concern x repository:"))
    assert rule_idx < header_idx, "attribution rule must render before the columns it points at"
    assert "against the columns below" in lines[rule_idx]
    assert "against the columns above" not in out


def test_attribution_rule_states_the_multi_match_trigger_over_multiple_cited_paths():
    """A single cited path's leading segment can never produce "two or more
    distinct matches" — only multiple cited paths can. The block's own text
    must carry that plural qualifier (mirroring `_shared/council.md`'s
    "across the finding's cited paths") so the multi-match case is reachable
    as written, not merely as intended."""
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "the paths it cites" in out


def test_highest_stamped_block_states_all_four_attribution_cases_decidably():
    """The rendered block is the only maturity text a lens subagent ever
    sees, so it — not just council.md — must decide all four cases a
    finding's cited paths can present, using AC8's own term "single" to
    settle the two-or-more-match case unambiguously."""
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "single repository" in out
    assert "two or more distinct matches" in out
    assert "no cited path at all" in out


def test_three_repo_stamp_carries_all_five_concerns_for_every_repository():
    lines = _stdout(_run(THREE_REPO_STAMP.encode("utf-8"))).splitlines()
    for concern in CONCERNS:
        assert (
            f"- {concern}: `alpha`=Minor, `mango`=Important, `zebra`=Critical"
        ) in lines, f"{concern!r} missing its full per-repository row"


def test_highest_stamped_severity_vocabulary_is_exactly_three_words():
    out = _stdout(_run(THREE_REPO_STAMP.encode("utf-8")))
    found = {word for word in ("Critical", "Important", "Minor", "Major", "Severe", "Blocker") if word in out}
    assert found <= set(SEVERITIES)
    for concern in CONCERNS:
        for line in out.splitlines():
            if line.strip().startswith(f"- {concern}:"):
                for cell in line.split(":", 1)[1].split(","):
                    severity = cell.split("=", 1)[1].strip()
                    assert severity in SEVERITIES


def test_highest_stamped_downgrade_restatement_names_repository_and_level():
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "downgraded by `lookout`'s prototype maturity level" in out


def test_single_repository_stamp_renders_byte_for_byte_unchanged():
    out = _stdout(_run(SINGLE_REPO_EARLY.encode("utf-8")))
    assert out == (
        "maturity: early (basis: stamp)\n\n"
        + "".join(f"- {concern}: Important\n" for concern in CONCERNS)
        + "\nEvery concern above is reported at its mapped severity and is "
        "never filtered out.\nWhere a concern above also appears in your "
        "per-lens Critical bars, the severity above governs — the bars say "
        "what to look for, this block says how severely to rate it.\n"
        "A finding downgraded by this calibration restates the concern "
        "and the deciding level in its own text (for example "
        "\"migration and backfill — Important, downgraded by this spec's "
        "early maturity level\"), so the operator can tell a calibrated "
        "downgrade from noise and has something concrete to override.\n"
    )


def test_matrix_header_and_per_repository_line_shape_are_pinned():
    lines = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8"))).splitlines()
    assert "concern x repository: `lookout`, `trailhead`" in lines
    assert "- backwards compatibility: `lookout`=Minor, `trailhead`=Critical" in lines


# ---- a member name colliding with the severity vocabulary --------------


def test_severity_colliding_member_name_renders_delimited_in_the_header():
    out = _stdout(_run(SEVERITY_COLLISION_STAMP.encode("utf-8")))
    assert "concern x repository: `Critical`, `lookout`" in out


def test_severity_colliding_member_name_renders_delimited_not_as_a_bare_token():
    out = _stdout(_run(SEVERITY_COLLISION_STAMP.encode("utf-8")))
    assert "`Critical`=Minor" in out
    assert "Critical=Minor" not in out


def test_severity_colliding_member_name_stays_delimited_in_the_downgrade_example():
    out = _stdout(_run(SEVERITY_COLLISION_STAMP.encode("utf-8")))
    assert "migration and backfill — `Critical`, Minor, downgraded by " in out
    assert "downgraded by `Critical`'s prototype maturity level" in out


# ---- treat-as-data framing for interpolated repository names -----------


def test_matrix_block_states_repository_names_are_labels_not_instructions():
    out = _stdout(_run(MIXED_TWO_REPO_STAMP.encode("utf-8")))
    assert "labels" in out.lower()
    assert "never instructions" in out.lower() or "not instructions" in out.lower()


@pytest.mark.parametrize(
    "fixture,reason_code",
    [
        (MALFORMED_ENTRY, "malformed-entry"),
        (INVALID_LEVEL, "invalid-level"),
        (DUPLICATE_MEMBER, "duplicate-member"),
        (DUPLICATE_SECTION, "duplicate-section"),
        (EMPTY_SECTION, "empty-section"),
        (UNRESOLVED_ENUMERATION, "unresolved-enumeration"),
    ],
)
def test_fail_closed_stamp_violations_still_write_no_block_after_matrix_rendering(fixture, reason_code):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode == 2
    err = _stderr(result)
    assert f"reason-code: {reason_code}" in err
    assert _stdout(result) == ""


# ---- basis: agent-instruction-file / default -------------------------------


def test_absent_section_with_agent_instruction_file_resolves_its_declared_level(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_EARLY, encoding="utf-8")
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "early" in out
    assert "agent-instruction-file" in out


def test_absent_section_with_no_flag_resolves_production_basis_default():
    result = _run(NO_MATURITY_SECTION.encode("utf-8"))
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: default)" in out
    for concern in CONCERNS:
        assert f"{concern}: Critical" in out


def test_empty_stdin_resolves_identically_to_absent_section_exit_zero():
    result = _run(b"")
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: default)" in out


def test_empty_stdin_with_agent_instruction_file_uses_it(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PROTOTYPE, encoding="utf-8")
    result = _run(b"", ["--agent-instruction-file", str(agent_file)])
    assert result.returncode == 0
    out = _stdout(result)
    assert "prototype" in out
    assert "agent-instruction-file" in out


def test_absent_section_agent_file_declares_nothing_resolves_production(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_NO_DECLARATION, encoding="utf-8")
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: agent-instruction-file)" in out


def test_absent_section_agent_file_out_of_vocabulary_resolves_production(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(
        "# Some Repo\n\n## Project Maturity\n\nbanana\n", encoding="utf-8"
    )
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(agent_file)],
    )
    assert result.returncode == 0
    out = _stdout(result)
    assert "maturity: production (basis: agent-instruction-file)" in out


# ---- unreadable agent-instruction-file -------------------------------------


def test_missing_agent_instruction_file_exits_nonzero_with_distinct_reason_code():
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", "/does/not/exist/CLAUDE.md"],
    )
    assert result.returncode != 0
    err = _stderr(result)
    assert "reason-code: agent-instruction-file-unreadable" in err


def test_directory_as_agent_instruction_file_exits_nonzero_same_reason_code(tmp_path):
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(tmp_path)],
    )
    assert result.returncode != 0
    err = _stderr(result)
    assert "reason-code: agent-instruction-file-unreadable" in err


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses mode bits, so chmod(0o000) leaves this test "
    "unable to observe its own subject and it would pass vacuously",
)
def test_unreadable_agent_instruction_file_exits_nonzero_same_reason_code(tmp_path):
    agent_file = tmp_path / "CLAUDE.md"
    agent_file.write_text(AGENT_FILE_PRODUCTION, encoding="utf-8")
    agent_file.chmod(0o000)
    try:
        result = _run(
            NO_MATURITY_SECTION.encode("utf-8"),
            ["--agent-instruction-file", str(agent_file)],
        )
        assert result.returncode != 0
        err = _stderr(result)
        assert "reason-code: agent-instruction-file-unreadable" in err
    finally:
        agent_file.chmod(0o644)


def test_unreadable_agent_instruction_file_raises_no_traceback(tmp_path):
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", "/does/not/exist/CLAUDE.md"],
    )
    assert "Traceback" not in _stderr(result)


# ---- render() invariant: highest-stamped basis requires entries -----------


def test_render_raises_on_highest_stamped_basis_with_no_entries():
    """`render()` must never silently fall back to the flat highest-wins
    block for `highest-stamped` — the matrix output this change exists to
    produce. A caller that loses `entries` for this basis is an invariant
    violation, not a degraded-but-valid output."""
    with pytest.raises(AssertionError):
        maturity_bars.render("production", "highest-stamped", None)


# ---- member-name length bound reaches this renderer too -------------------


def test_member_name_at_the_length_bound_renders_through_this_renderer():
    name = "a" * 100
    spec = f"# Some Spec\n\n## Maturity\n\n- {name}: production\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode == 0
    assert "maturity: production (basis: stamp)" in _stdout(result)


def test_member_name_over_the_length_bound_refuses_with_its_own_reason_code():
    name = "a" * 101
    spec = f"# Some Spec\n\n## Maturity\n\n- {name}: production\n"
    result = _run(spec.encode("utf-8"))
    assert result.returncode != 0
    assert "reason-code: member-name-too-long" in _stderr(result)
    assert _stdout(result) == ""
    assert name not in _stderr(result)


# ---- remaining stamp violations pass through their own reason-code --------


@pytest.mark.parametrize(
    "fixture,reason_code",
    [
        (MALFORMED_ENTRY, "malformed-entry"),
        (INVALID_LEVEL, "invalid-level"),
        (DUPLICATE_MEMBER, "duplicate-member"),
        (DUPLICATE_SECTION, "duplicate-section"),
        (EMPTY_SECTION, "empty-section"),
        (UNRESOLVED_ENUMERATION, "unresolved-enumeration"),
    ],
)
def test_each_remaining_stamp_violation_exits_nonzero_with_stamp_readers_reason_code(fixture, reason_code):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode != 0
    err = _stderr(result)
    assert f"reason-code: {reason_code}" in err


# ---- no offending value ever echoed ----------------------------------------


def test_no_refusal_writes_offending_value_to_stdout_or_stderr():
    result = _run(INVALID_LEVEL.encode("utf-8"))
    assert result.returncode != 0
    assert "XTREME_HAXOR_MARKER_9f3a" not in _stdout(result)
    assert "XTREME_HAXOR_MARKER_9f3a" not in _stderr(result)


# ---- no block on non-zero exit ---------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        MALFORMED_ENTRY,
        INVALID_LEVEL,
        DUPLICATE_MEMBER,
        DUPLICATE_SECTION,
        EMPTY_SECTION,
        UNRESOLVED_ENUMERATION,
    ],
)
def test_nonzero_exit_never_writes_a_calibration_block_to_stdout(fixture):
    result = _run(fixture.encode("utf-8"))
    assert result.returncode != 0
    out = _stdout(result)
    for concern in CONCERNS:
        assert concern not in out
    assert out == ""


# ---- resolved level and basis are surfaced ---------------------------------


def test_block_states_resolved_level_and_basis():
    result = _run(SINGLE_REPO_EARLY.encode("utf-8"))
    out = _stdout(result)
    assert "early" in out
    assert "(basis: stamp)" in out


# ---- undecodable stdin fails closed ----------------------------------------


def test_non_utf8_stdin_refuses_with_the_stamp_readers_own_reason_code():
    """Undecodable stdin is unreadable input, and unreadable input fails
    closed — it is never silently read as an absent `## Maturity` section.
    The reason-code is the one `maturity_stamp.py` already raises for this
    input rather than a second vocabulary."""
    result = _run(b"\xff\xfe\x00garbage")
    assert result.returncode != 0
    assert "reason-code: invalid-utf8-stdin" in _stderr(result)


def test_non_utf8_stdin_refuses_even_with_an_agent_instruction_file():
    """The fallback to the agent-instruction file is for a spec that carries
    no section — not for one whose bytes could not be read at all."""
    result = _run(
        b"\xff\xfe\x00garbage",
        ["--agent-instruction-file", str(BARS)],
    )
    assert result.returncode != 0
    assert "reason-code: invalid-utf8-stdin" in _stderr(result)


def test_non_utf8_stdin_writes_no_calibration_block_to_stdout():
    result = _run(b"\xff\xfe\x00garbage")
    assert result.returncode != 0
    assert _stdout(result) == ""


def test_unreadable_agent_instruction_file_writes_no_block_to_stdout(tmp_path):
    """Completes the "at any reason-code" property for the seventh
    reason-code — the one this renderer owns rather than forwards."""
    directory = tmp_path / "a-directory"
    directory.mkdir()
    result = _run(
        NO_MATURITY_SECTION.encode("utf-8"),
        ["--agent-instruction-file", str(directory)],
    )
    assert result.returncode != 0
    assert _stdout(result) == ""


# ---- the block carries the rules its reader must follow --------------------
#
# The rendered block is the ONLY maturity text a lens subagent ever sees. A
# rule that lives solely in `_shared/council.md` never reaches the actor that
# writes findings, so the two rules below have to travel in the block itself.


@pytest.mark.parametrize("level", ["prototype", "early", "production"])
def test_block_states_the_calibration_governs_severity_over_the_lens_bars(level):
    """Three of the five concerns also appear verbatim as per-lens Critical
    bars. Without a stated tiebreak the lens receives two contradictory
    severities at `prototype` and `early`."""
    spec = f"# S\n\n## Maturity\n\n- lookout: {level}\n"
    out = _stdout(_run(spec.encode("utf-8")))
    assert "severity" in out.lower()
    assert "Critical bars" in out or "critical bars" in out


@pytest.mark.parametrize("level", ["prototype", "early"])
def test_block_instructs_a_downgraded_finding_to_restate_concern_and_level(level):
    """The operator override this slice exists to enable needs the finding to
    say which level downgraded it."""
    spec = f"# S\n\n## Maturity\n\n- lookout: {level}\n"
    out = _stdout(_run(spec.encode("utf-8")))
    assert "downgrad" in out.lower()
    assert level in out


@pytest.mark.parametrize(
    "fixture",
    [MALFORMED_ENTRY, INVALID_LEVEL, DUPLICATE_MEMBER],
)
def test_no_refusal_echoes_the_offending_value_at_any_reason_code(fixture):
    """The contract says "at any reason-code" — `malformed-entry` and
    `duplicate-member` carry offending text through `StampError` too."""
    marked = fixture.replace("lookout", "MARKER_7c1e").replace(
        "XTREME_HAXOR_MARKER_9f3a", "MARKER_7c1e"
    )
    result = _run(marked.encode("utf-8"))
    assert result.returncode != 0
    assert "MARKER_7c1e" not in _stdout(result)
    assert "MARKER_7c1e" not in _stderr(result)
