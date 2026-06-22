"""KU-2 assumption probe: scan live vault, assert the reader design covers all shapes.

This test is EPHEMERAL — delete after KU-2 is resolved.
Files to clean up: tools/lore/tests/test_ku2_vault_shape_probe.py (entire file)

Checks:
1. Every top-level vault directory maps to a known disposition (kind-consolidation,
   unmapped-directory, pass-through, DROP, or a non-record directory we skip).
2. Every frontmatter key found in any .md file is in the known allowlist.
3. Every distinct `status` value is catalogued (no assertion — informational, but
   unexpected values are surfaced as test failures only if truly unmapped).
4. Every wikilink shape in `related:` and inline [[...]] matches one of the 4
   known variants; any 5th variant is flagged.
5. Sessions missing `session_id` are counted (drives abort-gate decision).
6. `post-merge-incidents/` record count (drives manual pre-cutover work).
7. Adversarial boundary shapes: ]] in flow-sequence, nested {}, block scalars,
   unusual indentation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from collections import defaultdict

import pytest

VAULT = Path.home() / "code" / "lore-vault"

# ---------------------------------------------------------------------------
# Known directory dispositions
# ---------------------------------------------------------------------------

# kind-consolidation (map to a new kind)
KIND_CONSOLIDATION = {
    "dead-ends":   "lesson",
    "gotchas":     "lesson",
    "lessons":     "lesson",
    "lesson":      "lesson",       # singular form already present
    "deferred":    "backlog",
    "follow-ups":  "backlog",
    "inbox":       "backlog",
    # no "tracking" in top-level dirs, but covered below
    "tools":       "area",
    "areas":       "area",
}

# pass-through kinds (keep kind as-is)
PASS_THROUGH_KINDS = {
    "decision",       # singular
    "decisions",      # plural variant
    "plans",
    "sessions",
    "specs",
    "collaboration",
    "briefings",      # check if present
    "backlog",
}

# unmapped-directory → blob
UNMAPPED_TO_BLOB = {
    "designs",
    "audits",
    "reviews",
    "ops",
}

# DROP dispositions
DROP_DIRS = {
    "reports",
    "post-merge-incidents",  # not DROP — manual extraction, abort-gate
}

# Non-record support directories we can safely skip
NON_RECORD_DIRS = {
    ".git",
    ".github",
    ".claude",
    ".obsidian",
    ".templates",
    "bin",
    "docs",
    "memory",
    "chrome",
    "capabilities",
    "templates",
}

# post-merge-incidents is a special abort-gate (not DROP, not blob)
# We count it separately.
ABORT_GATE_DIRS = {"post-merge-incidents"}

# All known dispositions for the assertion
ALL_MAPPED_DIRS = (
    set(KIND_CONSOLIDATION)
    | PASS_THROUGH_KINDS
    | UNMAPPED_TO_BLOB
    | DROP_DIRS
    | NON_RECORD_DIRS
    | ABORT_GATE_DIRS
)

# ---------------------------------------------------------------------------
# Known frontmatter key allowlist (from plan + spec)
# ---------------------------------------------------------------------------

KNOWN_KEYS = {
    # core record keys
    "type", "group", "date", "areas", "phases", "related",
    "raised-in", "source-spec", "source-plan", "last-reviewed",
    "severity", "closure-reason", "status", "revive-condition",
    # session-specific keys
    "session_id", "worktree", "branch", "started", "ended", "phase",
    # plan/spec keys found in the vault's plan dir
    "project", "slug", "created", "related-areas", "related-spec",
    # misc keys observed in older records
    "title", "tool", "subsystems", "surfaces",
}

# ---------------------------------------------------------------------------
# Known status vocabulary (legacy, from status_validator.py)
# ---------------------------------------------------------------------------

# We'll read status_validator.py's CANONICAL set to compare
STATUS_VALIDATOR_PATH = (
    Path(__file__).parent.parent
    / "plugins" / "lore" / "scripts" / "status_validator.py"
)


def load_canonical_statuses() -> set[str]:
    """Load the CANONICAL status set from status_validator.py."""
    if not STATUS_VALIDATOR_PATH.exists():
        return set()
    text = STATUS_VALIDATOR_PATH.read_text()
    # Find CANONICAL = {...} block
    m = re.search(r'CANONICAL\s*=\s*\{([^}]+)\}', text, re.DOTALL)
    if not m:
        return set()
    raw = m.group(1)
    return {s.strip().strip('"').strip("'") for s in raw.split(",") if s.strip().strip('"').strip("'")}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

FM_RE = re.compile(r'^---\n(.*?)\n---', re.DOTALL)
WIKILINK_INLINE_RE = re.compile(r'\[\[([^\]]+)\]\]')


def extract_frontmatter_text(text: str) -> str | None:
    """Return the raw frontmatter block text (between --- delimiters), or None."""
    m = FM_RE.match(text)
    return m.group(1) if m else None


def parse_raw_keys_and_values(fm_text: str) -> dict[str, str]:
    """Extract key→raw_value from frontmatter text (flat, no nesting).
    Returns raw string for each key — not further parsed.
    """
    result: dict[str, str] = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line or line.startswith(" ") or line.startswith("\t"):
            i += 1
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        if not k:
            i += 1
            continue
        v = v.strip()
        result[k] = v
        i += 1
    return result


def classify_wikilink_shape(raw_value: str) -> str:
    """Classify a `related:` field value into one of the 4 known shapes."""
    v = raw_value.strip()
    # Map-typed value: {target: kind} or { target: kind }
    if v.startswith("{") and ":" in v:
        return "map-typed"
    # In-flow-sequence-string: ["[[target]]"] or ["target"]
    if v.startswith("["):
        return "flow-sequence"
    # Bare wikilink: [[target]] or [[target|alias]]
    if v.startswith("[["):
        if "|" in v:
            return "pipe-alias"
        return "bare-wikilink"
    # Bare scalar (no brackets at all)
    if v and not v.startswith("[") and not v.startswith("{"):
        return "bare-scalar"
    if v == "":
        return "empty"
    return f"UNKNOWN:{v[:60]}"


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_vault():
    """Scan all .md files in the vault; return aggregated findings."""
    if not VAULT.exists():
        pytest.skip(f"Live vault not found at {VAULT}")

    # Gather top-level dirs
    top_dirs = {p.name for p in VAULT.iterdir() if p.is_dir()}

    # Per-record findings
    unknown_keys: dict[str, list[str]] = defaultdict(list)   # key → [file examples]
    all_status_values: dict[str, list[str]] = defaultdict(list)  # status → [file examples]
    wikilink_shapes: dict[str, list[str]] = defaultdict(list)    # shape → [file examples]
    sessions_missing_id: list[str] = []
    post_merge_count: int = 0
    block_scalar_files: list[str] = []
    nested_brace_files: list[str] = []
    bracket_in_flow_seq_files: list[str] = []
    unusual_indent_files: list[str] = []

    # Walk every .md file
    for md_path in sorted(VAULT.rglob("*.md")):
        # Skip non-record root files
        if md_path.parent == VAULT:
            continue
        top_dir = md_path.relative_to(VAULT).parts[0]
        if top_dir in NON_RECORD_DIRS:
            continue

        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        fm_text = extract_frontmatter_text(text)
        if fm_text is None:
            continue

        rel = str(md_path.relative_to(VAULT))

        # --- Key scan ---
        kv = parse_raw_keys_and_values(fm_text)
        for k in kv:
            if k not in KNOWN_KEYS:
                if len(unknown_keys[k]) < 5:
                    unknown_keys[k].append(rel)

        # --- Status scan ---
        if "status" in kv:
            sv = kv["status"]
            if len(all_status_values[sv]) < 3:
                all_status_values[sv].append(rel)

        # --- Wikilink shape scan (related: field) ---
        if "related" in kv:
            shape = classify_wikilink_shape(kv["related"])
            if len(wikilink_shapes[shape]) < 5:
                wikilink_shapes[shape].append(rel)

        # --- Session id scan ---
        if top_dir in ("sessions",):
            if "session_id" not in kv:
                sessions_missing_id.append(rel)

        # --- post-merge-incidents count ---
        if top_dir == "post-merge-incidents":
            post_merge_count += 1

        # --- Adversarial boundary shapes ---
        fm_lines = fm_text.splitlines()
        for j, line in enumerate(fm_lines):
            stripped = line.lstrip()
            # Block scalar: key: | or key: >
            if re.match(r'^[a-z_-]+:\s*[|>]', stripped):
                if len(block_scalar_files) < 5:
                    block_scalar_files.append(f"{rel}:{j+1}: {line}")
            # Nested braces: { ... { ... } }
            if line.count("{") > 1 or (line.count("{") == 1 and re.search(r'\{[^}]*\{', line)):
                if len(nested_brace_files) < 5:
                    nested_brace_files.append(f"{rel}:{j+1}: {line}")

        # ]] inside a flow-sequence element
        if "related" in kv:
            related_raw = kv["related"]
            if related_raw.startswith("["):
                # Check for ]] inside elements
                inner = related_raw[1:-1] if related_raw.endswith("]") else related_raw[1:]
                for elem in inner.split(","):
                    elem = elem.strip().strip('"').strip("'")
                    if "]]" in elem and not (elem.startswith("[[") and elem.endswith("]]")):
                        if len(bracket_in_flow_seq_files) < 5:
                            bracket_in_flow_seq_files.append(f"{rel}: {related_raw[:80]}")

        # Unusual indentation: value on next line (indented) rather than same line
        for j, line in enumerate(fm_lines):
            if re.match(r'^[a-z_-]+:\s*$', line.strip()):
                # Empty-value line — check if next line is indented but NOT a list item
                if j + 1 < len(fm_lines):
                    nxt = fm_lines[j + 1]
                    if nxt.startswith("  ") and not nxt.strip().startswith("-"):
                        if len(unusual_indent_files) < 5:
                            unusual_indent_files.append(f"{rel}:{j+1}: {line!r} -> {nxt!r}")

    return {
        "top_dirs": top_dirs,
        "unknown_keys": dict(unknown_keys),
        "all_status_values": dict(all_status_values),
        "wikilink_shapes": dict(wikilink_shapes),
        "sessions_missing_id": sessions_missing_id,
        "post_merge_count": post_merge_count,
        "block_scalar_files": block_scalar_files,
        "nested_brace_files": nested_brace_files,
        "bracket_in_flow_seq_files": bracket_in_flow_seq_files,
        "unusual_indent_files": unusual_indent_files,
    }


# Cache results so multiple test functions share one scan pass
_RESULTS: dict | None = None


@pytest.fixture(scope="module")
def vault_findings():
    global _RESULTS
    if _RESULTS is None:
        _RESULTS = scan_vault()
    return _RESULTS


# ===========================================================================
# Test 1: Every top-level directory maps to a known disposition
# ===========================================================================

def test_all_top_dirs_are_mapped(vault_findings):
    """Every top-level vault dir must map to a known disposition or be a non-record dir."""
    top_dirs = vault_findings["top_dirs"]
    unmapped = top_dirs - ALL_MAPPED_DIRS
    assert not unmapped, (
        f"UNMAPPED top-level directories found — extend the disposition table:\n"
        + "\n".join(f"  {d}" for d in sorted(unmapped))
    )


# ===========================================================================
# Test 2: Every frontmatter key is in the known allowlist
# ===========================================================================

def test_all_frontmatter_keys_are_known(vault_findings):
    """Every frontmatter key found in vault .md files must be in the known allowlist."""
    unknown = vault_findings["unknown_keys"]
    if unknown:
        lines = []
        for key, examples in sorted(unknown.items()):
            lines.append(f"  key={key!r}  examples: {examples[:3]}")
        pytest.fail(
            f"Unknown frontmatter keys found — add to allowlist or handle in reader:\n"
            + "\n".join(lines)
        )


# ===========================================================================
# Test 3: Status values catalogue (informational — report, assert known
#         base values pass through status_validator CANONICAL)
# ===========================================================================

def test_status_values_catalogue(vault_findings):
    """Print all distinct status values. Fail only if a value has no
    recognizable base prefix in the canonical set OR the compound-value pattern."""
    all_statuses = vault_findings["all_status_values"]
    canonical = load_canonical_statuses()

    # Compound-value pattern from the spec: "base | suffix — prose"
    # Base values like "active", "shelved", "superseded", etc.
    unknown_statuses = {}
    for sv, examples in sorted(all_statuses.items()):
        # Strip compound suffix: "active | superseded — some prose" → "active"
        base = re.split(r'\s*\|', sv)[0].strip()
        # Also handle "execute-active", "plan-active", "spec-active" etc.
        if base not in canonical and not re.match(
            r'^(execute|plan|spec|review|draft|shelved|active|open|done|'
            r'closed|complete|completed|conditional|pending|cancelled|'
            r'archived|superseded|blocked|wont-fix|in-progress)-?', base
        ):
            unknown_statuses[sv] = examples

    # Print all for informational purposes (visible with -s flag)
    print("\n\n=== All distinct status values in vault ===")
    for sv in sorted(all_statuses):
        print(f"  {sv!r}  ({len(all_statuses[sv])} occurrences, e.g. {all_statuses[sv][0]})")
    print(f"  Total distinct values: {len(all_statuses)}")

    if unknown_statuses:
        lines = [f"  {sv!r}: {exs[:2]}" for sv, exs in sorted(unknown_statuses.items())]
        pytest.fail(
            "Status values with no recognizable base prefix:\n" + "\n".join(lines)
        )


# ===========================================================================
# Test 4: Wikilink shapes — only 4 variants allowed
# ===========================================================================

KNOWN_WIKILINK_SHAPES = {"map-typed", "flow-sequence", "pipe-alias", "bare-wikilink", "bare-scalar", "empty"}


def test_wikilink_shapes_are_covered(vault_findings):
    """Every wikilink shape in `related:` must match one of the 4 known variants."""
    shapes = vault_findings["wikilink_shapes"]
    unknown_shapes = {s: v for s, v in shapes.items() if s not in KNOWN_WIKILINK_SHAPES}

    print("\n\n=== Wikilink shapes found in `related:` ===")
    for shape, examples in sorted(shapes.items()):
        print(f"  {shape}: {len(examples)} occurrences, e.g. {examples[0]}")

    assert not unknown_shapes, (
        f"Unknown wikilink shapes found:\n"
        + "\n".join(f"  {s}: {e[:3]}" for s, e in sorted(unknown_shapes.items()))
    )


# ===========================================================================
# Test 5: Sessions missing session_id (informational count, not a hard fail)
# ===========================================================================

def test_sessions_missing_session_id_count(vault_findings):
    """Count sessions/ records missing session_id. Printed for abort-gate sizing."""
    missing = vault_findings["sessions_missing_id"]
    print(f"\n\n=== Sessions missing session_id: {len(missing)} ===")
    for f in missing[:10]:
        print(f"  {f}")
    if len(missing) > 10:
        print(f"  ... and {len(missing)-10} more")
    # This is informational — the abort gate will handle them; we don't fail
    # the assumption test for this, we just report.
    # But we do assert we could count them (i.e. scan succeeded).
    assert isinstance(missing, list)


# ===========================================================================
# Test 6: post-merge-incidents count
# ===========================================================================

def test_post_merge_incidents_count(vault_findings):
    """Count post-merge-incidents records. Manual pre-cutover extraction needed."""
    count = vault_findings["post_merge_count"]
    print(f"\n\n=== post-merge-incidents record count: {count} ===")
    assert isinstance(count, int)


# ===========================================================================
# Test 7: Adversarial boundary shapes
# ===========================================================================

def test_no_block_scalars_in_frontmatter(vault_findings):
    """Frontmatter block scalars (key: | or key: >) need explicit reader support."""
    found = vault_findings["block_scalar_files"]
    if found:
        print(f"\n\n=== Block scalars in frontmatter ({len(found)} found) ===")
        for f in found:
            print(f"  {f}")
        pytest.fail(
            f"Block scalar values found in frontmatter ({len(found)} occurrences).\n"
            f"The bespoke reader must handle these or flag them for review.\n"
            f"First few:\n" + "\n".join(f"  {f}" for f in found[:5])
        )


def test_no_deeply_nested_braces_in_frontmatter(vault_findings):
    """Nested {} in frontmatter values (beyond simple map-typed wikilinks) need reader support."""
    found = vault_findings["nested_brace_files"]
    if found:
        print(f"\n\n=== Deeply nested braces in frontmatter ({len(found)} found) ===")
        for f in found:
            print(f"  {f}")
        pytest.fail(
            f"Nested brace values found ({len(found)}):\n"
            + "\n".join(f"  {f}" for f in found[:5])
        )


def test_no_bracket_in_flow_sequence_elements(vault_findings):
    """Flow-sequence elements containing ]] (beyond simple [[target]]) need reader support."""
    found = vault_findings["bracket_in_flow_seq_files"]
    if found:
        print(f"\n=== ]] inside flow-sequence elements ({len(found)}) ===")
        for f in found[:5]:
            print(f"  {f}")
        pytest.fail(
            f"Flow-sequence elements containing ]] found ({len(found)}):\n"
            + "\n".join(f"  {f}" for f in found[:5])
        )


def test_unusual_indentation_shapes(vault_findings):
    """Indented (non-list) values after a key: line need explicit reader support."""
    found = vault_findings["unusual_indent_files"]
    if found:
        print(f"\n=== Unusual indentation (indented value, not list) ({len(found)}) ===")
        for f in found[:5]:
            print(f"  {f}")
        pytest.fail(
            f"Unusual indented value shapes found ({len(found)}):\n"
            + "\n".join(f"  {f}" for f in found[:5])
        )
