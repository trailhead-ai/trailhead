"""Each prose surface documenting `[release].merge_method` must name the same
strategy vocabulary and the same resolved unset-default as the loader itself.

The four surfaces below all asserted a squashing default before automatic
selection shipped. This is the code-versus-document consistency check the
repository's conventions sanction: the expected values are derived from
`trailhead.vcs.github` — never retyped here — so a future vocabulary change
that this test doesn't get updated for fails loudly instead of leaving a
surface asserting something untrue.

Each surface is checked individually (four separate tests) so a fix to one
does not mask a still-stale one, and each surface's check is scoped to its
own `merge_method`-specific span rather than the whole document — the word
"automatic" already appears elsewhere in some of these docs as a substring of
"automatically" (unrelated `auto_merge` prose), so a whole-document plain
substring search would pass on a decoy occurrence instead of the real one.
Matching on `\bautomatic\b` sidesteps that decoy: the word-boundary after
"automatic" fails inside "automatically" (the next character, "a", is a word
character), so only a standalone "automatic" token counts.
"""

from __future__ import annotations

import re

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from trailhead.vcs.github import AUTOMATIC_MERGE_METHOD, _MERGE_METHOD_VALUES, _RESTORE_SQUASH_METHOD

_REPO_ROOT = _portage_cli.PLUGIN_ROOT.parents[3]
_README = _portage_cli.PLUGIN_ROOT.parents[1] / "README.md"
_MONITOR_MD = _portage_cli.PLUGIN_ROOT / "agents" / "monitor.md"
_RITUALS_MD = _portage_cli.PLUGIN_ROOT / "docs" / "pr-merge-rituals.md"
_SKILL_MD = _portage_cli.PLUGIN_ROOT / "skills" / "pull_request" / "SKILL.md"

# Each entry locates the merge_method-specific span in its document by a
# stable start/end anchor pair (text on either side of the span that these
# docs cover under a different task's scope, so it stays fixed) rather than
# retyping the span's content.
_SPANS = {
    "README.md": (_README, r"- `merge_method` —.*?(?=\n- `review_bot_login`)"),
    "monitor.md": (
        _MONITOR_MD,
        r"`portage merge` also reads `merge_method`.*?(?=\n\n`portage merge` exits nonzero)",
    ),
    "pr-merge-rituals.md": (
        _RITUALS_MD,
        r"merge_method = \"squash\".*?(?=\n\n## Error convention split)",
    ),
    "SKILL.md": (
        _SKILL_MD,
        r"- Merge method: configured.*?(?=\n- No issue tracker configured)",
    ),
}


def _span(path, pattern) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.DOTALL)
    assert match, f"{path}: could not locate the merge_method span via {pattern!r}"
    return match.group(0)


@pytest.mark.parametrize("name", sorted(_SPANS))
def test_surface_names_the_full_accepted_vocabulary(name: str) -> None:
    """Every accepted merge_method value is named, as a whole word, in the span."""
    path, pattern = _SPANS[name]
    span = _span(path, pattern)
    missing = [
        value
        for value in sorted(_MERGE_METHOD_VALUES)
        if not re.search(rf"\b{re.escape(value)}\b", span)
    ]
    assert not missing, (
        f"{name}'s merge_method span is missing value(s) {missing} present in "
        f"trailhead.vcs.github._MERGE_METHOD_VALUES:\n{span}"
    )


# `default`/`defaults` followed, within a short run of characters, by the
# resolved unset value — proximity rather than a whole-span search, so a
# surface that lists `automatic` in its vocabulary but still calls
# `_RESTORE_SQUASH_METHOD` the default (leaving the two disagreeing) is
# caught rather than passing on the vocabulary mention alone.
_DEFAULT_NAMES_RESOLUTION = re.compile(
    rf"default[s]?\b.{{0,40}}?\b{re.escape(AUTOMATIC_MERGE_METHOD)}\b", re.IGNORECASE | re.DOTALL
)


@pytest.mark.parametrize("name", sorted(_SPANS))
def test_surface_names_the_resolved_unset_default(name: str) -> None:
    """The default the loader resolves to (`_load_merge_method`'s unset case) is
    named as `AUTOMATIC_MERGE_METHOD`, not `_RESTORE_SQUASH_METHOD`."""
    path, pattern = _SPANS[name]
    span = _span(path, pattern)
    assert _DEFAULT_NAMES_RESOLUTION.search(span), (
        f"{name}'s merge_method span never names '{AUTOMATIC_MERGE_METHOD}' near "
        f"'default' — trailhead.vcs.github._load_merge_method resolves an unset "
        f"[release].merge_method to {AUTOMATIC_MERGE_METHOD!r}, not "
        f"{_RESTORE_SQUASH_METHOD!r}:\n{span}"
    )
