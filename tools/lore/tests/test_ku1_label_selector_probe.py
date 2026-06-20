"""KU1 assumption probe — label selector syntax for KQL (Slice 4 gate).

THROWAWAY: This file is an ephemeral assumption prover; delete it after Slice 4
is built and the real behavioral tests replace it.

**What we are proving (KU1):**
  (a) `label.worktree` tokenizes as a SINGLE word token under the existing lexer
      (`.` is already a word char at kql.py:290-291).
  (b) Without modification, `label.worktree:s5` reaches `_parse_field_rhs` and
      hits the `_ALL_FIELDS` guard at kql.py:465 with "unknown field" — this is
      the wall we must route around, not past.
  (c) A MINIMAL routing patch (intercepting `label.`-prefixed fields in
      `_parse_field_rhs` BEFORE the `_ALL_FIELDS` check) routes to a
      `LabelEq(key, value)` node and does NOT hit the guard.
  (d) The `has:` path: without modification, `has:label.worktree` also hits the
      guard (on the field name `has`). With a routing patch at the same hook
      point, `has:label.<key>` routes to `LabelExists(key)`.
  (e) Disallowed chars (`*`, `~`, bare `/`) are STILL rejected.
  (f) The old wrong form `label:worktree=s5` — using `=` — is rejected at the
      LEXER level (unexpected char), confirming why the dot-form is necessary.

**Proof strategy:**
  - Steps (a)–(b) are pure code observations proved via the real tokenizer/parser.
  - Steps (c)–(d) prototype a minimal patch: we subclass `_Parser` to override
    `_parse_field_rhs`, inject `LabelEq`/`LabelExists` as minimal dataclasses,
    and call `parse()` via a monkey-patched module to show the routing produces
    the expected AST nodes. We do NOT implement the full Slice 4 here.
  - Steps (e)–(f) use the unmodified parser/tokenizer.

**Files to clean up after Slice 4:**
  tools/lore/tests/test_ku1_label_selector_probe.py  (this entire file)
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ by stem, freshly each call."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if name in sys.modules:
        del sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def kql():
    return load_script("kql")


# ---------------------------------------------------------------------------
# (a) Tokenizer: label.worktree is a single WORD token
# ---------------------------------------------------------------------------

class TestTokenizer:
    """Prove `.` is a word char — `label.worktree` is one token, not two."""

    def test_label_dot_worktree_is_single_word_token(self, kql):
        """label.worktree tokenizes as a single WORD (dot is a word char)."""
        tokens = kql._tokenize("label.worktree:s5")
        # Expected: WORD("label.worktree"), COLON(":"), WORD("s5"), EOF
        kinds = [t[0] for t in tokens]
        values = [t[1] for t in tokens]
        assert kinds == ["WORD", "COLON", "WORD", "EOF"], (
            f"unexpected token kinds: {list(zip(kinds, values))}"
        )
        assert values[0] == "label.worktree", (
            f"first token value should be 'label.worktree', got {values[0]!r}"
        )
        assert values[2] == "s5", f"value token should be 's5', got {values[2]!r}"

    def test_has_label_dot_worktree_tokenizes_correctly(self, kql):
        """has:label.worktree tokenizes as WORD('has') COLON WORD('label.worktree') EOF."""
        tokens = kql._tokenize("has:label.worktree")
        kinds = [t[0] for t in tokens]
        values = [t[1] for t in tokens]
        assert kinds == ["WORD", "COLON", "WORD", "EOF"], (
            f"unexpected: {list(zip(kinds, values))}"
        )
        assert values[0] == "has"
        assert values[2] == "label.worktree"

    def test_dot_is_already_a_word_char_not_a_separator(self, kql):
        """Sanity: a dotted name like `phi-scrubber.v2` is also one token."""
        tokens = kql._tokenize("phi-scrubber.v2")
        kinds = [t[0] for t in tokens]
        assert kinds == ["WORD", "EOF"]
        assert tokens[0][1] == "phi-scrubber.v2"


# ---------------------------------------------------------------------------
# (b) Parser (unmodified): label.worktree:s5 hits _ALL_FIELDS guard at :465
# ---------------------------------------------------------------------------

class TestUnmodifiedParserRejectsLabelDotForm:
    """Without modification the parser hard-rejects label.worktree:s5 at :465."""

    def test_label_dot_worktree_eq_hits_unknown_field_guard(self, kql):
        """label.worktree:s5 raises KqlParseError naming 'unknown field'."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql.parse("label.worktree:s5")
        msg = str(exc_info.value)
        assert "unknown field" in msg, (
            f"expected 'unknown field' in error, got: {msg!r}"
        )
        assert "label.worktree" in msg, (
            f"expected field name in error, got: {msg!r}"
        )

    def test_has_label_dot_worktree_hits_unknown_field_guard_on_has(self, kql):
        """has:label.worktree raises KqlParseError — 'has' is not in _ALL_FIELDS."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql.parse("has:label.worktree")
        msg = str(exc_info.value)
        assert "unknown field" in msg, (
            f"expected 'unknown field' in error, got: {msg!r}"
        )
        # The guard fires on 'has', not on 'label.worktree'
        assert "has" in msg, f"expected 'has' in error message, got: {msg!r}"


# ---------------------------------------------------------------------------
# (c)+(d) Prototyped routing patch: intercept label. prefix in _parse_field_rhs
# ---------------------------------------------------------------------------

# Minimal placeholder AST nodes for the probe (Slice 4 will add these to kql.py)
@dataclass(frozen=True)
class LabelEq:
    """label.<key>:<value> — exact label match (KU1 candidate node)."""
    key: str
    value: str


@dataclass(frozen=True)
class LabelExists:
    """has:label.<key> — label key existence (KU1 candidate node)."""
    key: str


class TestRoutingPatch:
    """Prototype a minimal routing hook and prove the parse path works.

    We subclass _Parser and override _parse_field_rhs to intercept:
      - fields starting with 'label.' → LabelEq(key, value)
      - field == 'has', value starts with 'label.' → LabelExists(key)

    Then we wire it through a patched `parse()` wrapper and confirm:
      1. label.worktree:s5 → LabelEq(key='worktree', value='s5')
      2. has:label.worktree → LabelExists(key='worktree')
      3. namespaced key: label.claude-code/model:x → LabelEq('claude-code/model', 'x')
         NOTE: '/' is a disallowed bare char, so namespaced keys must be quoted values,
         see surprises section below.
      4. Existing non-label fields (kind:spec) still work via the original guard.
      5. Unknown non-label field (bogus:x) still raises KqlParseError.
    """

    def _make_patched_parse(self, kql_mod):
        """Return a parse() function that routes label. fields to LabelEq/LabelExists."""
        _TK_COLON = kql_mod._TK_COLON
        _TK_WORD = kql_mod._TK_WORD
        _TK_QUOTED = kql_mod._TK_QUOTED
        _TK_EOF = kql_mod._TK_EOF
        _tokenize = kql_mod._tokenize
        KqlParseError = kql_mod.KqlParseError
        _ALL_FIELDS = kql_mod._ALL_FIELDS

        # Subclass the real _Parser, overriding only _parse_field_rhs
        class PatchedParser(kql_mod._Parser):
            def _parse_field_rhs(self, field):
                # Route label.<key>:value → LabelEq(key, value)
                if field.startswith("label."):
                    key = field[len("label."):]
                    value = self._parse_value()
                    return LabelEq(key=key, value=value)
                # Route has:label.<key> → LabelExists(key)
                if field == "has":
                    # The RHS should be label.<key>
                    if self._at(_TK_WORD):
                        _, rhs = self._peek()
                        if rhs.startswith("label."):
                            self._consume()
                            key = rhs[len("label."):]
                            return LabelExists(key=key)
                    raise KqlParseError(
                        "has: expects has:label.<key> (e.g. has:label.worktree)"
                    )
                # All other fields go through the original guard
                return super()._parse_field_rhs(field)

        def patched_parse(query):
            stripped = query.strip()
            if not stripped:
                raise KqlParseError("empty query: a non-empty query string is required")
            tokens = _tokenize(stripped)
            parser = PatchedParser(tokens)
            return parser.parse_query()

        return patched_parse

    def test_label_dot_worktree_routes_to_label_eq(self, kql):
        """label.worktree:s5 → LabelEq(key='worktree', value='s5')."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse("label.worktree:s5")
        assert isinstance(node, LabelEq), f"expected LabelEq, got {type(node).__name__}: {node}"
        assert node.key == "worktree", f"expected key='worktree', got {node.key!r}"
        assert node.value == "s5", f"expected value='s5', got {node.value!r}"

    def test_has_label_worktree_routes_to_label_exists(self, kql):
        """has:label.worktree → LabelExists(key='worktree')."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse("has:label.worktree")
        assert isinstance(node, LabelExists), (
            f"expected LabelExists, got {type(node).__name__}: {node}"
        )
        assert node.key == "worktree", f"expected key='worktree', got {node.key!r}"

    def test_label_eq_composes_with_boolean_and(self, kql):
        """label.worktree:s5 kind:spec → And(LabelEq(...), FieldEq(...))."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse("label.worktree:s5 kind:spec")
        assert isinstance(node, kql.And), f"expected And, got {type(node).__name__}: {node}"
        left, right = node.left, node.right
        assert isinstance(left, LabelEq), f"left should be LabelEq, got {type(left).__name__}"
        assert isinstance(right, kql.FieldEq), f"right should be FieldEq, got {type(right).__name__}"
        assert left.key == "worktree"
        assert right.field == "kind"
        assert right.value == "spec"

    def test_label_exists_composes_with_boolean_and(self, kql):
        """has:label.worktree kind:spec → And(LabelExists(...), FieldEq(...))."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse("has:label.worktree kind:spec")
        assert isinstance(node, kql.And)
        assert isinstance(node.left, LabelExists)
        assert isinstance(node.right, kql.FieldEq)

    def test_existing_field_still_works_through_patch(self, kql):
        """kind:spec still routes correctly through the patched parser."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse("kind:spec")
        assert isinstance(node, kql.FieldEq), (
            f"expected FieldEq, got {type(node).__name__}: {node}"
        )
        assert node.field == "kind"
        assert node.value == "spec"

    def test_unknown_non_label_field_still_raises(self, kql):
        """bogus:x still raises KqlParseError — non-label unknown fields are NOT routed."""
        patched_parse = self._make_patched_parse(kql)
        with pytest.raises(kql.KqlParseError) as exc_info:
            patched_parse("bogus:x")
        assert "unknown field" in str(exc_info.value)

    def test_label_eq_with_quoted_value(self, kql):
        """label.worktree:"s5 env" → LabelEq(key='worktree', value='s5 env')."""
        patched_parse = self._make_patched_parse(kql)
        node = patched_parse('label.worktree:"s5 env"')
        assert isinstance(node, LabelEq)
        assert node.key == "worktree"
        assert node.value == "s5 env"

    def test_has_label_bare_word_suffix_no_label_prefix_raises(self, kql):
        """has:worktree (without label. prefix) raises — has: only routes label. form."""
        patched_parse = self._make_patched_parse(kql)
        with pytest.raises(kql.KqlParseError) as exc_info:
            patched_parse("has:worktree")
        msg = str(exc_info.value)
        # Should name the expected form
        assert "label" in msg.lower(), f"error should mention 'label', got: {msg!r}"


# ---------------------------------------------------------------------------
# (e) Disallowed chars are STILL rejected (unmodified tokenizer)
# ---------------------------------------------------------------------------

class TestDisallowedCharsStillRejected:
    """Disallowed chars must still be rejected — the routing must not loosen guards."""

    def test_wildcard_in_value_rejected(self, kql):
        """label.worktree:s5* raises KqlParseError (wildcard unsupported)."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql._tokenize("label.worktree:s5*")
        assert "wildcard" in str(exc_info.value).lower(), str(exc_info.value)

    def test_fuzzy_tilde_rejected(self, kql):
        """A bare ~ raises KqlParseError (fuzzy unsupported)."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql._tokenize("label.worktree:s5~")
        assert "fuzzy" in str(exc_info.value).lower(), str(exc_info.value)

    def test_bare_slash_rejected(self, kql):
        """A bare / raises KqlParseError (regex/unquoted-slash unsupported)."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql._tokenize("label.claude-code/model:x")
        # The '/' in a namespace key is rejected unless the value is quoted
        assert "/" in str(exc_info.value) or "unexpected" in str(exc_info.value).lower(), (
            str(exc_info.value)
        )

    def test_wildcard_in_label_key_portion_rejected(self, kql):
        """label.worktree* is treated as a word with * → wildcard error."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql._tokenize("label.worktree*:s5")
        assert "wildcard" in str(exc_info.value).lower(), str(exc_info.value)


# ---------------------------------------------------------------------------
# (f) Old wrong form `label:worktree=s5` is rejected at lexer level
# ---------------------------------------------------------------------------

class TestOldWrongFormRejected:
    """The =form is tokenizer-level rejected — = is not a word char."""

    def test_equal_sign_in_value_is_unexpected_char(self, kql):
        """label:worktree=s5 → unexpected character '=' (= is not a word char)."""
        with pytest.raises(kql.KqlParseError) as exc_info:
            kql._tokenize("label:worktree=s5")
        msg = str(exc_info.value)
        assert "unexpected" in msg.lower() or "=" in msg, (
            f"expected rejection of '=', got: {msg!r}"
        )

    def test_equal_sign_is_not_in_word_chars(self, kql):
        """Sanity: '=' is not alphanumeric and not in the _.* or ~^ sets."""
        # This confirms WHY the dot-form is necessary vs the =form
        # Test by verifying that a token with = mid-word is split at =
        tokens = kql._tokenize("label:worktree")  # plain field: should work as tokens
        # WORD("label") COLON(":") WORD("worktree")
        assert tokens[0] == ("WORD", "label")
        assert tokens[1] == ("COLON", ":")
        assert tokens[2] == ("WORD", "worktree")
        # Now confirm '=' would break the token stream (can't be in a word)
        # We already tested the full form above; this confirms the components
        # separately so the reason is clear.
        char_is_word = '='.isalnum() or '=' in '_.' or '=' in '*~^'
        assert not char_is_word, "= should NOT be a word char (this is the reason dot-form is needed)"

    def test_namespaced_key_slash_requires_quoting(self, kql):
        """namespace/name keys can't appear bare — / is rejected; must be quoted value."""
        # label.claude-code/model:x is rejected because / is a bare disallowed char
        # The namespaced key must come from the label key's prefix, not via / in tokenizer
        # This surfaces the constraint: label.<key> only supports simple (no-slash) keys
        # bare; namespace/name keys need a different approach (quoted value or
        # flattening namespace into the dot-key — e.g. label.claude-code.model)
        with pytest.raises(kql.KqlParseError):
            kql._tokenize("label.claude-code/model:x")


# ---------------------------------------------------------------------------
# Summary marker (not a test — just documents what was proved vs prototyped)
# ---------------------------------------------------------------------------

class TestProofSummary:
    """Documents what was PROVED (ran against real code) vs PROTOTYPED (patched)."""

    def test_proved_tokenization(self, kql):
        """PROVED: label.worktree tokenizes as one WORD; = is rejected at lexer level."""
        tokens = kql._tokenize("label.worktree:s5")
        assert tokens[0] == ("WORD", "label.worktree")

    def test_proved_unmodified_parser_guard(self, kql):
        """PROVED: unmodified parser raises on label.worktree:s5 at _ALL_FIELDS guard."""
        with pytest.raises(kql.KqlParseError):
            kql.parse("label.worktree:s5")

    def test_prototyped_label_eq_routing(self, kql):
        """PROTOTYPED: patched _parse_field_rhs routes label. → LabelEq without hitting guard."""
        # This test is identical to TestRoutingPatch.test_label_dot_worktree_routes_to_label_eq
        # but is here to make the proof-vs-prototype distinction explicit.
        patched_parse = TestRoutingPatch()._make_patched_parse(kql)
        node = patched_parse("label.worktree:s5")
        assert isinstance(node, LabelEq)
        assert node.key == "worktree"
        assert node.value == "s5"

    def test_prototyped_label_exists_routing(self, kql):
        """PROTOTYPED: patched _parse_field_rhs routes has:label. → LabelExists."""
        patched_parse = TestRoutingPatch()._make_patched_parse(kql)
        node = patched_parse("has:label.worktree")
        assert isinstance(node, LabelExists)
        assert node.key == "worktree"
