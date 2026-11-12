"""Frontmatter parser and byte-careful edit primitives for vault notes.

`parse_frontmatter` is a minimal reader (key: value, flat [a, b, c] inline
lists, and block-style list fields) — not a general YAML parser, only the
shapes this vault uses.

This module only reads: every write into a vault goes through ``record.store``
under the vault write lock.
"""

from __future__ import annotations

from pathlib import Path

# Cross-reference keys whose wikilink values are slug-reduced (prefix
# stripped) so `[[area/foo]]` and `foo` compare equal for index grouping.
_SLUG_REDUCED_KEYS = frozenset({"surfaces", "areas", "related-areas"})

# Path prefixes stripped when slug-reducing overlap-key wikilink values.
# Singular kind dirs: vault directories are singular, so a wikilink
# like ``[[area/foo]]`` / ``[[task/foo]]`` slug-reduces to ``foo``.
_SLUG_PREFIXES = ("area/", "tools/", "task/")


def _unwrap_wikilink(value: str) -> str:
    """Strip [[ and ]] from a wikilink, returning the inner target."""
    if value.startswith("[[") and value.endswith("]]"):
        return value[2:-2]
    return value


def _strip_slug_prefix(value: str) -> str:
    """Remove a known path prefix from a wikilink target (slug-reduce)."""
    for prefix in _SLUG_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _process_item(raw: str, key: str) -> str:
    """Strip quotes, unwrap wikilink, and (for overlap keys) slug-reduce."""
    item = raw.strip().strip('"').strip("'")
    item = _unwrap_wikilink(item)
    if key in _SLUG_REDUCED_KEYS:
        item = _strip_slug_prefix(item)
    return item


def _parse_fm_text(text: str) -> dict:
    """Parse frontmatter from raw text. Returns {} when no frontmatter block found.

    Handles three value shapes:
    - Scalar:      key: value
    - Inline list: key: [a, b, c]
    - Block list:  key:\n  - item\n  - item

    For every list item (inline or block): strips surrounding quotes and
    unwraps [[wikilinks]]. For overlap keys (surfaces, areas,
    related-areas), the path prefix (area/, tools/, task/) is
    stripped to a bare slug. For all other keys, the full wikilink target
    is kept verbatim.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, object] = {}
    lines = text[3:end].strip().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            # Inline list: key: [a, b, c]
            inner = v[1:-1]
            items = [_process_item(p, k) for p in inner.split(",") if p.strip()]
            fm[k] = items
            i += 1
        elif v == "":
            # Possible block list: collect following "  - item" or "- item" lines
            block_items: list[str] = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("  - ") or lines[j].startswith("- ")):
                entry = lines[j]
                if entry.startswith("  - "):
                    raw = entry[4:]
                else:
                    raw = entry[2:]
                block_items.append(_process_item(raw, k))
                j += 1
            if block_items:
                fm[k] = block_items
                i = j
            else:
                # Empty value, no block items — store as empty string
                fm[k] = ""
                i += 1
        else:
            # Scalar: strip quotes, unwrap wikilink (no slug-reduce for scalars)
            scalar = v.strip('"').strip("'")
            scalar = _unwrap_wikilink(scalar)
            fm[k] = scalar
            i += 1
    return fm


def parse_frontmatter(path: Path) -> dict:
    """Minimal frontmatter parser — key: value and flat [a, b, c] lists.

    Not a general YAML parser; only handles the shapes this vault uses.
    Returns {} when the file is missing or has no frontmatter.
    """
    try:
        text = Path(path).read_text()
    except Exception:
        return {}
    return _parse_fm_text(text)


def parse_frontmatter_str(text: str) -> dict:
    """Parse frontmatter from a raw string instead of a file path.

    Same semantics as parse_frontmatter but takes text directly. Useful
    for validating rendered templates before writing to disk.
    """
    return _parse_fm_text(text)
