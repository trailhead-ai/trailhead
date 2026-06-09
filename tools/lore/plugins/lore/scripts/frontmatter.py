"""Frontmatter parser and byte-careful edit primitives for vault notes.

`parse_frontmatter` is a minimal reader (key: value, flat [a, b, c] inline
lists, and block-style list fields) — not a general YAML parser, only the
shapes this vault uses.

`set_status` and `patch_section` are the two ergonomic write operations the
CLI exposes: a frontmatter status flip and a section-targeted append. Both
leave everything they don't touch byte-identical.
"""
from __future__ import annotations

from pathlib import Path

# Cross-reference keys whose wikilink values are slug-reduced (prefix
# stripped) so `[[areas/foo]]` and `foo` compare equal for index grouping.
_SLUG_REDUCED_KEYS = frozenset({"surfaces", "areas", "related-areas"})

# Path prefixes stripped when slug-reducing overlap-key wikilink values.
_SLUG_PREFIXES = ("areas/", "tools/", "plans/")


def _unwrap_wikilink(value: str) -> str:
    """Strip [[ and ]] from a wikilink, returning the inner target."""
    if value.startswith("[[") and value.endswith("]]"):
        return value[2:-2]
    return value


def _strip_slug_prefix(value: str) -> str:
    """Remove a known path prefix from a wikilink target (slug-reduce)."""
    for prefix in _SLUG_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
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
    related-areas), the path prefix (areas/, tools/, plans/) is
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
            while j < len(lines) and (
                lines[j].startswith("  - ") or lines[j].startswith("- ")
            ):
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


def _split(text: str) -> tuple[str, str] | None:
    """Split a document into (frontmatter_body, rest) including delimiters.

    Returns (fm_text, body) where fm_text is the content between the opening
    and closing ``---`` lines, and body is everything from the closing
    delimiter onward (``\\n---`` included). Returns None when there is no
    frontmatter block.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    return text[3:end], text[end:]


def set_status(path: Path, value: str) -> bool:
    """Set or replace the ``status:`` frontmatter key.

    Replaces the existing status line in place if present, otherwise appends
    one to the end of the frontmatter block. All other lines stay
    byte-identical. Returns True on write.
    """
    path = Path(path)
    text = path.read_text()
    split = _split(text)
    if split is None:
        return False
    fm_text, body = split
    fm_lines = fm_text.splitlines()

    new_line = f"status: {value}"
    replaced = False
    for i, line in enumerate(fm_lines):
        if line.strip() == "status:" or line.startswith("status:"):
            fm_lines[i] = new_line
            replaced = True
            break
    if not replaced:
        fm_lines.append(new_line)

    new_text = "---" + "\n".join(fm_lines) + body
    path.write_text(new_text)
    return True


def patch_section(path: Path, section: str, text_to_add: str) -> bool:
    """Append ``text_to_add`` under the ``## <section>`` heading.

    Inserts the text immediately before the next sibling heading (or at the
    end of the section's content) so every other section stays byte-identical.
    If the section heading is absent, appends a fresh ``## <section>`` block
    at the end of the document. Returns True on write.
    """
    path = Path(path)
    content = path.read_text()
    lines = content.split("\n")
    heading = f"## {section}"

    heading_idx = None
    for i, line in enumerate(lines):
        if line.rstrip() == heading:
            heading_idx = i
            break

    addition = text_to_add.rstrip("\n")

    if heading_idx is None:
        # Section missing — append it at the end of the document.
        suffix = "" if content.endswith("\n") else "\n"
        new_content = content + suffix + f"\n## {section}\n{addition}\n"
        path.write_text(new_content)
        return True

    # Find the end of this section: the next line starting with "## " (a
    # sibling-or-higher heading) after the heading, else end of document.
    end_idx = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break

    # Walk back over trailing blank lines so the append sits flush against
    # the section's content, then re-pad with one blank line before the next
    # heading.
    insert_at = end_idx
    while insert_at > heading_idx + 1 and lines[insert_at - 1] == "":
        insert_at -= 1

    new_lines = lines[:insert_at] + [addition] + lines[insert_at:]
    path.write_text("\n".join(new_lines))
    return True
