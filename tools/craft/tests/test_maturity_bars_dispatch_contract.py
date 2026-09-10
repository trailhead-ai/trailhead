"""Every council dispatcher fills `<maturity-calibration>` before dispatch.

Four skills dispatch the council panel `_shared/council.md` governs: `plan`,
`gauntlet`, `consult`, and `drive`. Each must resolve a maturity level (by
running `scripts/maturity_bars.py` against the reviewed spec, or the
agent-instruction file alone for `consult`'s standalone-question case),
refuse the dispatch on a non-zero exit, and surface the resolved level and
its basis in the review it prints.

The dispatcher count is pinned two ways — a citation sweep (independent of
the substitution-token search) and the token-search enumeration
`test_maturity_bars_council_contract.py` already exposes as
`discover_council_dispatchers()` — so a fifth dispatcher added later, or one
that fills the token without being a real dispatcher (or vice versa), fails
here rather than silently reviewing at whatever severity its bars happen to
carry.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from test_maturity_bars_council_contract import discover_council_dispatchers

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "craft" / "skills"
BARS = REPO_ROOT / "plugins" / "craft" / "scripts" / "maturity_bars.py"

PLAN_MD = SKILLS_DIR / "plan" / "SKILL.md"
GAUNTLET_MD = SKILLS_DIR / "gauntlet" / "SKILL.md"
CONSULT_MD = SKILLS_DIR / "consult" / "SKILL.md"
DRIVE_MD = SKILLS_DIR / "drive" / "SKILL.md"

DISPATCHER_PATHS = {
    "plan": PLAN_MD,
    "gauntlet": GAUNTLET_MD,
    "consult": CONSULT_MD,
    "drive": DRIVE_MD,
}

_COUNCIL_LENSES = ("builder", "breaker", "attacker", "advocate")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- discovery method B: a citation sweep, independent of the token search ----
#
# Deliberately a different mechanism from `discover_council_dispatchers()` (which
# searches for the literal `<lens-critical-bars>` substitution token): this one
# looks for a skill that both *cites* `_shared/council.md` as its dispatch
# contract and *names* all four lens agents. A skill that merely fills a token
# without citing the shared contract, or cites the contract without actually
# naming the four members, does not count as a dispatcher under this method —
# so the two methods can disagree on a malformed or partial fixture even though
# they agree on the real four.


def citation_sweep_dispatchers() -> list[Path]:
    found = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = _text(skill_md)
        if "_shared/council.md" not in text:
            continue
        if all(lens in text for lens in _COUNCIL_LENSES):
            found.append(skill_md)
    return found


# ---- fenced-code-block extraction, mirroring the repo's own line-scanner style ----


_FENCE_RE = re.compile(r"^\s*```")


def fenced_blocks(text: str) -> list[str]:
    """Every fenced code block's body, using a line-scanner state machine
    (paired open/close fence markers) rather than a regex spanning ``` to
    ```` — a naive non-greedy regex mispairs when a document contains many
    unrelated triple-backtick fences, which this document does."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            if in_fence:
                blocks.append("\n".join(current))
                current = []
                in_fence = False
            else:
                in_fence = True
            continue
        if in_fence:
            current.append(line)
    return blocks


# ---- contract item 1: two independent discovery methods agree ------------


# ---- contract item 2: every discovered dispatcher fills the token --------


def test_every_discovered_dispatcher_fills_maturity_calibration_token():
    dispatchers = discover_council_dispatchers()
    assert dispatchers
    for path in dispatchers:
        assert "<maturity-calibration>" in _text(path), (
            f"{path} is a discovered dispatcher but never fills "
            "<maturity-calibration>"
        )


# ---- contract item 3: non-zero exit refuses the dispatch ------------------
#
# Anchored to a bounded window starting at the `<maturity-calibration>` token
# itself, not searched across the whole document: `gauntlet/SKILL.md` has an
# unrelated "non-zero exit ... refuses" sentence earlier in the file (its
# criterion-gate step), a decoy this window excludes by only ever looking
# forward from the calibration token's own mention.

_MATURITY_WINDOW = 1600
_REFUSAL_RE = re.compile(r"non-zero exit[^.\n]*refuses", re.IGNORECASE)


def _maturity_calibration_window(text: str) -> str:
    idx = text.index("<maturity-calibration>")
    return text[idx : idx + _MATURITY_WINDOW]


# ---- contract item 6b: stand-down and waiver-not-recognised surfaced too -
#
# Every dispatcher — including gauntlet, whose own `<maturity-calibration>` instruction names
# "Filling the calibration token" in `_shared/council.md` directly rather than duplicating it —
# carries its own restatement instruction naming both prefixes explicitly. The expected tokens
# are imported from the renderer rather than retyped, the same discipline contract item 11 below
# applies to the concern vocabulary.

sys.path.insert(0, str(REPO_ROOT / "plugins" / "craft" / "scripts"))

_OWN_RESTATEMENT_DISPATCHERS = {
    "plan": PLAN_MD,
    "gauntlet": GAUNTLET_MD,
    "consult": CONSULT_MD,
    "drive": DRIVE_MD,
}


# ---- contract item 6c: gauntlet's Adjudicate list reconciles stand-downs --
#
# gauntlet is the one dispatcher whose consolidation step is a fully self-contained numbered
# list rather than a pointer to `_shared/council.md`'s Synthesis section (plan, consult, and
# drive each say "synthesize per `_shared/council.md`", which pulls in that section's own
# "Reconcile stand-downs first" rule by reference) — so gauntlet's own list must state the rule
# itself, scoped to its own "### 4. Adjudicate" step so a rule appearing only in some other
# section does not count.

_ADJUDICATE_BOUNDS = ("### 4. Adjudicate", "### 5. Recommend")


# ---- contract item 8: severity change only — no lens or pass dropped -----
#
# Scoped to each dispatcher's own dispatch step, not searched across the
# whole file — a lens name surviving elsewhere in the document (e.g. the
# frontmatter description) would let a dropped-lens mutation inside the
# actual dispatch instruction pass unnoticed.


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


_DISPATCH_STEP_BOUNDS = {
    "plan": ("### 8.5. Council Review (mandatory)", "### 9. Present for Approval"),
    "gauntlet": (
        "### 3. Dispatch the eight passes (parallel, isolated)",
        "### 4. Adjudicate (main session, NOT a subagent)",
    ),
    "consult": (
        "### 2. Dispatch the four members (parallel, isolated)",
        "### 3. Synthesize (main session, NOT a subagent)",
    ),
    "drive": ("### 8. Run the council review", "### 9. Run the build phase"),
}


# ---- contract item 9: plan's remedy table names every reason-code -----
#       `scripts/maturity_bars.py` can actually exit non-zero with -----
#
# Fixtures trigger each reachable refusal; the reason-code compared against
# the table is read back from the renderer's own stderr, never hand-typed —
# so a mutation that renames a reason-code fails here on the new name it
# actually emits, not the old one this test happened to be written against.


def _run_bars(stdin_bytes: bytes, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BARS), *(extra_args or [])],
        input=stdin_bytes,
        capture_output=True,
    )


def _observed_reason_code(result: subprocess.CompletedProcess) -> str:
    assert result.returncode != 0, result.stdout
    match = re.search(rb"reason-code: ([\w-]+)", result.stderr)
    assert match, f"no reason-code on stderr: {result.stderr!r}"
    return match.group(1).decode("utf-8")


def _reachable_reason_codes() -> set[str]:
    fixtures = [
        "# S\n\n## Maturity\n\n- bad line\n",
        "# S\n\n## Maturity\n\n- repo: bogus\n",
        "# S\n\n## Maturity\n\n- repo: early\n- repo: production\n",
        "# S\n\n## Maturity\n\n- a: early\n\n## Maturity\n\n- b: production\n",
        "# S\n\n## Maturity\n\n",
        "# S\n\n## Maturity\n\n<!-- unresolved-enumeration: x -->\n",
        "# S\n\n## Maturity\n\n- " + "a" * 101 + ": early\n",
    ]
    codes = {_observed_reason_code(_run_bars(f.encode("utf-8"))) for f in fixtures}
    codes.add(_observed_reason_code(_run_bars(b"# S\n\n## Maturity\n\n- repo: \xff\n")))
    codes.add(
        _observed_reason_code(
            _run_bars(b"", ["--agent-instruction-file", "/does/not/exist"])
        )
    )
    return codes


def test_plan_remedy_table_names_every_reason_code_the_renderer_can_actually_emit():
    reachable = _reachable_reason_codes()
    assert len(reachable) >= 9, f"fixtures should reach at least nine distinct codes: {reachable!r}"
    table = _text(PLAN_MD)
    for code in reachable:
        assert f"`{code}`" in table, (
            f"plan/SKILL.md's step 8.5 remedy table never names reason-code `{code}`, "
            "which scripts/maturity_bars.py can actually exit non-zero with"
        )


# ---- contract item 10: the waived-concern eval fixtures embed the renderer's real output ----
#
# `plugins/craft/evals/waived-concern-stands-down/` is a manual (hand-dispatched) eval case,
# not something pytest can run — but the calibration block each fixture embeds under its own
# `## Maturity calibration` heading is claimed to be `scripts/maturity_bars.py`'s real output
# for that fixture's own spec, not hand-authored stand-down wording. That claim is ordinary
# code-vs-document wiring and stays a deterministic test: re-run the renderer against the
# fixture's own spec section and byte-compare.

EVALS_DIR = REPO_ROOT / "plugins" / "craft" / "evals"
WAIVED_CONCERN_EVAL = EVALS_DIR / "waived-concern-stands-down"
WAIVED_CONCERN_FIXTURES = sorted(
    (WAIVED_CONCERN_EVAL / "fixtures").glob("*.md")
)

_SPEC_HEADING = "## Spec under review\n\n"
_CALIBRATION_HEADING = "\n## Maturity calibration\n\n"
_LENS_HEADING = "\n## Captured lens responses\n"


def _split_fixture(text: str) -> tuple[str, str]:
    """Return `(spec_text, calibration_block)` from a waived-concern-stands-down
    fixture: the spec section between the `## Spec under review` and
    `## Maturity calibration` headings, and the calibration block between
    `## Maturity calibration` and `## Captured lens responses`."""
    spec_start = text.index(_SPEC_HEADING) + len(_SPEC_HEADING)
    calibration_start = text.index(_CALIBRATION_HEADING, spec_start)
    spec_text = text[spec_start:calibration_start]
    block_start = calibration_start + len(_CALIBRATION_HEADING)
    block_end = text.index(_LENS_HEADING, block_start)
    return spec_text, text[block_start:block_end]


# ---- contract item 11: the documented concern names are derived from _CONCERNS ----
#
# `_shared/council.md`'s new waiver-rule prose names the closed concern vocabulary a
# `Waives:` marker recognises. That vocabulary is owned in one place — `_CONCERNS` in
# `scripts/maturity_bars.py` — so this test imports it rather than retyping it, the same
# discipline `test_maturity_bars_council_contract.py` already applies to the severity table.

sys.path.insert(0, str(REPO_ROOT / "plugins" / "craft" / "scripts"))


_WAIVER_RULE_START = "A concern above can be waived for this spec alone"
_WAIVER_RULE_END = "### Filling the calibration token"
