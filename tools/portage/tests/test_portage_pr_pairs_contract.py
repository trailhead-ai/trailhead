"""The `pr_pairs` templates the portage docs spell out parse as 3-field pairs.

`updater` produces the `pr_pairs` line; `pull_request/SKILL.md` (the caller) and
`monitor` (the consumer) pass it along to `portage merge`, which splits it with
`portage.pairs.split_pair`. A doc that drifts back to a 2-field `repo:pr` form
reads fine and then starves `merge_order` keying of the member name.

The documents are the INPUT here: every pair template they spell out is expanded
to concrete values and fed to the real parser. Nothing below asserts that a
sentence appears in a document.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.pairs import split_pair

_PLUGIN_DIR = _portage_cli.PLUGIN_ROOT

_SURFACES = {
    "updater.md": _PLUGIN_DIR / "agents" / "updater.md",
    "monitor.md": _PLUGIN_DIR / "agents" / "monitor.md",
    "pull_request/SKILL.md": _PLUGIN_DIR / "skills" / "pull_request" / "SKILL.md",
}

# A pair template as the docs write it: colon-joined `<placeholder>` fields.
_TEMPLATE = re.compile(r"<[a-z0-9_]+>(?::<[a-z0-9_]+>)+")
_FIELD = re.compile(r"<([a-z0-9_]+)>")


def _concrete(field: str) -> str:
    """Substitute a placeholder with a value of the shape it names."""
    return "42" if field.startswith("pr") and "path" not in field else f"x-{field}"


def _pr_pairs_templates(path: Path) -> list[list[str]]:
    """Every pair template the doc spells out *in a pr_pairs context*.

    A template's context is its own line plus the one above it, because the docs
    routinely introduce the contract on one line ("a `pr_pairs:` line listing
    each") and spell the template on the next. Templates outside that context
    belong to other contracts — the prs.json sidecar spells a 4-field
    `<repo>:<pr_number>:<url>:<branch>` — and are not this file's subject.
    """
    lines = path.read_text().splitlines()
    found: list[list[str]] = []
    for i, line in enumerate(lines):
        context = lines[i - 1] + line if i else line
        if "pr_pairs" not in context:
            continue
        for template in _TEMPLATE.findall(line):
            found.append(_FIELD.findall(template))
    return found


@pytest.mark.parametrize("surface", sorted(_SURFACES))
def test_every_documented_pair_template_parses_into_three_fields(surface: str) -> None:
    templates = _pr_pairs_templates(_SURFACES[surface])
    assert templates, (
        f"{surface} spells out no pr_pairs template at all — the extraction has "
        "stopped reading the doc, so it would pass over any drift"
    )
    for fields in templates:
        token = ":".join(_concrete(f) for f in fields)
        parts = split_pair(token, max_parts=3)
        assert len(parts) == 3, (
            f"{surface}: {token!r} parsed to {parts} — a pr_pairs template must "
            "carry repo, pr_number and member_name"
        )
        assert parts[2], f"{surface}: {token!r} parsed with an empty member field"


def test_a_two_field_pair_is_refused_by_the_command_the_docs_feed(capsys) -> None:
    """The check above has teeth only while 3 fields are actually required.

    `split_pair` itself accepts a 2-field token; `merge` is what rejects one, so
    the refusal is asserted where it lives — nothing is merged and the member
    name is never guessed from a repo-path basename.
    """
    from portage.cli import dispatch

    parser = dispatch.build_parser()
    args = parser.parse_args(["merge", "--manifest", "m.json", "x-repo_path:42"])
    assert args.func(args) == 2
    assert "missing member_name" in capsys.readouterr().err
