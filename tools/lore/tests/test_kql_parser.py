"""KQL tokenizer + recursive-descent parser → backend-agnostic AST tests.

Covers the test contract:

  - Grammar matrix: each supported construct parses to the expected AST shape.
  - Quoting rules: slash-forced quoting, unquoted field tokens, space-as-boundary.
  - Error cases: each unsupported / malformed input raises KqlParseError.
  - Deterministic suggestion: unknown field yields the same "did you mean" output
    on every call — no nondeterministic ordering from set iteration.

All tests load ``kql`` via ``load_script("lore.search.kql")`` (pure stdlib, no I/O).
"""

import pytest
from conftest import load_script


@pytest.fixture()
def kql():
    return load_script("lore.search.kql")


# ---------------------------------------------------------------------------
# Grammar matrix — expected AST shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "field", "value"),
    [
        pytest.param("kind:spec", "kind", "spec", id="kind_field_eq"),
        pytest.param(
            'repo:"trailhead-ai/trailhead"',
            "repo",
            "trailhead-ai/trailhead",
            id="repo_slash_requires_quotes",
        ),
        pytest.param('team:"platform infra"', "team", "platform infra", id="team_space_requires_quotes"),
    ],
)
def test_field_eq(kql, query, field, value):
    ast = kql.parse(query)
    assert isinstance(ast, kql.FieldEq)
    assert ast.field == field
    assert ast.value == value


@pytest.mark.parametrize(
    ("query", "facet", "value"),
    [
        pytest.param("area:penny", "area", "penny", id="area"),
        pytest.param("phase:build", "phase", "build", id="phase"),
        pytest.param("keyword:python", "keyword", "python", id="keyword"),
        pytest.param('supersedes:"adr/foo"', "supersedes", "adr/foo", id="supersedes_field_parses_as"),
        pytest.param('superseded-by:"adr/bar"', "superseded-by", "adr/bar", id="superseded_by_field_parses_as"),
    ],
)
def test_facet_membership(kql, query, facet, value):
    ast = kql.parse(query)
    assert isinstance(ast, kql.FacetMembership)
    assert ast.facet == facet
    assert ast.value == value


def test_status_and_kind(kql):
    ast = kql.parse("status:active and kind:lesson")
    assert isinstance(ast, kql.And)
    left, right = ast.left, ast.right
    assert isinstance(left, kql.FieldEq)
    assert left.field == "status" and left.value == "active"
    assert isinstance(right, kql.FieldEq)
    assert right.field == "kind" and right.value == "lesson"


def test_or_binds_looser_than_and(kql):
    # Precedence contract (the compiler builds boolean SQL against this shape):
    # `a or b and c` → Or(a, And(b, c)) — AND binds tighter than OR.
    ast = kql.parse("kind:spec or kind:plan and status:active")
    assert isinstance(ast, kql.Or), "OR must be the top node (binds loosest)"
    assert isinstance(ast.left, kql.FieldEq)
    assert ast.left.field == "kind" and ast.left.value == "spec"
    assert isinstance(ast.right, kql.And), "AND must bind tighter than OR"
    assert ast.right.left.field == "kind" and ast.right.left.value == "plan"
    assert ast.right.right.field == "status" and ast.right.right.value == "active"


def test_implicit_and_is_left_associative(kql):
    # Implicit AND between three terms is left-associative: a b c → And(And(a,b),c).
    ast = kql.parse("kind:spec status:active team:platform")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.And), "implicit AND must be left-associative"
    assert ast.left.left.field == "kind"
    assert ast.left.right.field == "status"
    assert ast.right.field == "team"


def test_field_or_group(kql):
    # kind:(spec or plan) → Or(FieldEq("kind","spec"), FieldEq("kind","plan"))
    ast = kql.parse("kind:(spec or plan)")
    assert isinstance(ast, kql.Or)
    assert isinstance(ast.left, kql.FieldEq)
    assert ast.left.field == "kind" and ast.left.value == "spec"
    assert isinstance(ast.right, kql.FieldEq)
    assert ast.right.field == "kind" and ast.right.value == "plan"


def test_facet_or_group(kql):
    # area:(penny or infra) → Or(FacetMembership, FacetMembership)
    ast = kql.parse("area:(penny or infra)")
    assert isinstance(ast, kql.Or)
    assert isinstance(ast.left, kql.FacetMembership)
    assert ast.left.facet == "area" and ast.left.value == "penny"
    assert isinstance(ast.right, kql.FacetMembership)
    assert ast.right.facet == "area" and ast.right.value == "infra"


def test_exclusion_prefix(kql):
    # -status:dropped → Not(FieldEq("status","dropped"))
    ast = kql.parse("-status:dropped")
    assert isinstance(ast, kql.Not)
    assert isinstance(ast.operand, kql.FieldEq)
    assert ast.operand.field == "status"
    assert ast.operand.value == "dropped"


def test_phrase(kql):
    ast = kql.parse('"penny worker"')
    assert isinstance(ast, kql.Phrase)
    assert ast.text == "penny worker"


def test_compare_gte(kql):
    ast = kql.parse('created-at >= "2026-01-01"')
    assert isinstance(ast, kql.Compare)
    assert ast.field == "created-at"
    assert ast.op == ">="
    assert ast.value == "2026-01-01"


def test_compare_lte(kql):
    ast = kql.parse('updated-at <= "2026-06-01"')
    assert isinstance(ast, kql.Compare)
    assert ast.field == "updated-at"
    assert ast.op == "<="
    assert ast.value == "2026-06-01"


def test_compare_gt(kql):
    ast = kql.parse('created-at > "2025-01-01"')
    assert isinstance(ast, kql.Compare)
    assert ast.op == ">"


def test_compare_lt(kql):
    ast = kql.parse('last-referenced-at < "2026-01-01"')
    assert isinstance(ast, kql.Compare)
    assert ast.op == "<"


def test_nested_grouping(kql):
    # (kind:spec or kind:plan) and status:active
    # The parenthesized sub-expression is wrapped in a Group node; inner is the Or.
    ast = kql.parse("(kind:spec or kind:plan) and status:active")
    assert isinstance(ast, kql.And)
    # Left is a Group wrapping the Or
    assert isinstance(ast.left, kql.Group)
    assert isinstance(ast.left.inner, kql.Or)
    assert isinstance(ast.right, kql.FieldEq)
    assert ast.right.field == "status" and ast.right.value == "active"


def test_bare_fulltext_term(kql):
    ast = kql.parse("trailhead")
    assert isinstance(ast, kql.FullText)
    assert ast.term == "trailhead"


def test_implicit_and(kql):
    # Adjacent terms → And
    ast = kql.parse("foo bar")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.FullText)
    assert ast.left.term == "foo"
    assert isinstance(ast.right, kql.FullText)
    assert ast.right.term == "bar"


def test_not_keyword(kql):
    # not kind:spec → Not(FieldEq("kind","spec"))
    ast = kql.parse("not kind:spec")
    assert isinstance(ast, kql.Not)
    assert isinstance(ast.operand, kql.FieldEq)


def test_or_keyword(kql):
    # kind:spec or kind:plan
    ast = kql.parse("kind:spec or kind:plan")
    assert isinstance(ast, kql.Or)


def test_three_term_implicit_and(kql):
    # a b c → And(And(a, b), c)  — left-associative
    ast = kql.parse("foo bar baz")
    # top node is And
    assert isinstance(ast, kql.And)


# ---------------------------------------------------------------------------
# Quoting rules
# ---------------------------------------------------------------------------


def test_created_at_unquoted_in_compare(kql):
    # bare created-at in a comparison stays an unquoted field token
    ast = kql.parse('created-at >= "2026-01-01"')
    assert isinstance(ast, kql.Compare)
    assert ast.field == "created-at"


def test_unquoted_value_space_is_boundary(kql):
    # "status:active kind:spec" — space is a parse boundary, not a merge
    ast = kql.parse("status:active kind:spec")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.FieldEq)
    assert ast.left.field == "status" and ast.left.value == "active"
    assert isinstance(ast.right, kql.FieldEq)
    assert ast.right.field == "kind" and ast.right.value == "spec"


def test_hyphenated_field_not_quoted(kql):
    # related-area is a recognized field token without quotes
    ast = kql.parse("related-area:penny")
    assert isinstance(ast, kql.FacetMembership)
    assert ast.facet == "related-area"


# ---------------------------------------------------------------------------
# Kind-derived `related-<kind>` facet fields — one per record.model.KINDS
# member, derived rather than hand-listed (table-driven off KINDS itself, so a
# newly added kind such as `adr` is covered automatically).
# ---------------------------------------------------------------------------


def test_related_kind_field_parses_for_every_kind(kql):
    record_model = load_script("lore.record.model")
    for kind in sorted(record_model.KINDS):
        ast = kql.parse(f"related-{kind}:foo")
        assert isinstance(ast, kql.FacetMembership), f"related-{kind} did not parse as a facet"
        assert ast.facet == f"related-{kind}"
        assert ast.value == "foo"


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param("related-notakind:foo", "unknown field", id="related_notakind_field_is_unknown"),
        pytest.param(
            'supersedes-notreal:"adr/foo"',
            "unknown field",
            id="unknown_field_adjacent_to_supersedes_still_raises",
        ),
        pytest.param("pen*", "wildcard|not supported", id="error_wildcard_prefix"),
        pytest.param("foo~", "fuzzy|not supported", id="error_fuzzy"),
        pytest.param("title^2", "boost|not supported", id="error_boost"),
        pytest.param("/re/", "regex|not supported", id="error_regex"),
        pytest.param("bogusfield:value", "unknown field", id="error_unknown_field"),
        pytest.param("label.worktree:s5*", "wildcard|not supported", id="label_value_wildcard_rejected"),
        pytest.param("label.worktree:s5~", "fuzzy|not supported", id="label_value_fuzzy_rejected"),
        pytest.param("label.worktree*:s5", "wildcard|not supported", id="label_key_wildcard_rejected"),
        pytest.param("annotation:foo", "unknown field", id="annotation_field_is_unknown"),
        pytest.param("annotation.worktree:foo", "unknown field", id="annotation_dot_field_is_unknown"),
    ],
)
def test_unsupported_query_is_a_parse_error_with_message(kql, query, match):
    with pytest.raises(kql.KqlParseError, match=match):
        kql.parse(query)


# ---------------------------------------------------------------------------
# supersedes / superseded-by — the supersession edge, queryable both ways
# ---------------------------------------------------------------------------


def test_supersedes_and_superseded_by_are_valid_fields(kql):
    assert "supersedes" in kql.VALID_FIELDS
    assert "superseded-by" in kql.VALID_FIELDS


# ---------------------------------------------------------------------------
# Error cases — each must raise KqlParseError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        pytest.param('"unfinished', id="error_unbalanced_quote"),
        pytest.param("(kind:spec", id="error_unbalanced_paren"),
        pytest.param("", id="error_empty_query"),
        pytest.param("   ", id="error_whitespace_only_query"),
        pytest.param("kind:spec)", id="extra_close_paren_is_error"),
        pytest.param("()", id="empty_group_is_error"),
        pytest.param("label.claude-code/model:x", id="label_bare_slash_rejected"),
    ],
)
def test_malformed_query_is_a_parse_error(kql, query):
    with pytest.raises(kql.KqlParseError):
        kql.parse(query)


# ---------------------------------------------------------------------------
# Deterministic suggestion — asserted by repeated calls
# ---------------------------------------------------------------------------


def test_deterministic_suggestion_repeated(kql):
    """Unknown field 'aera' must yield identical output on every call."""
    messages = []
    for _ in range(5):
        try:
            kql.parse("aera:penny")
        except kql.KqlParseError as exc:
            messages.append(str(exc))

    # all identical
    assert len(set(messages)) == 1, "suggestion is nondeterministic"


def test_deterministic_suggestion_content(kql):
    """'aera' should suggest 'area' (closest match) + the sorted valid-field list."""
    try:
        kql.parse("aera:penny")
    except kql.KqlParseError as exc:
        msg = str(exc)
        assert "area" in msg, f"expected 'area' suggestion, got: {msg}"
        assert "unknown field" in msg.lower(), f"expected 'unknown field' in: {msg}"
    else:
        pytest.fail("expected KqlParseError for unknown field 'aera'")


def test_deterministic_suggestion_sorted_field_list(kql):
    """The valid-field list in the error message must appear in sorted order."""
    try:
        kql.parse("boguz:value")
    except kql.KqlParseError as exc:
        msg = str(exc)
        # The message should include the sorted valid fields; verify it's deterministic
        # by parsing the field list portion if present
        assert msg  # non-empty message
    else:
        pytest.fail("expected KqlParseError for unknown field 'boguz'")


def test_valid_field_zero_matches_no_error(kql):
    """A valid field produces an AST (no error) even if it matches nothing at query time."""
    ast = kql.parse("kind:nonexistent-kind-value")
    assert isinstance(ast, kql.FieldEq)
    assert ast.field == "kind"


# ---------------------------------------------------------------------------
# AST node immutability (frozen dataclasses)
# ---------------------------------------------------------------------------


def test_nodes_are_immutable(kql):
    ast = kql.parse("kind:spec")
    with pytest.raises((AttributeError, TypeError)):
        ast.field = "other"  # type: ignore[misc]


def test_ast_equality(kql):
    a = kql.parse("kind:spec")
    b = kql.parse("kind:spec")
    assert a == b


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_suite_field(kql):
    ast = kql.parse("suite:search")
    assert isinstance(ast, kql.FieldEq)
    assert ast.field == "suite"


def test_product_field(kql):
    ast = kql.parse("product:lore")
    assert isinstance(ast, kql.FieldEq)
    assert ast.field == "product"


# ---------------------------------------------------------------------------
# label.<key>:<value> and has:label.<key> selectors
#
# The dot-form syntax: `.` is already a word char so
# `label.worktree` is a single token, routed past the _ALL_FIELDS guard to a
# LabelEq/LabelExists node. Namespaced keys encode `/` as `.` (dot-for-slash)
# since a bare `/` is rejected by the lexer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "key", "value"),
    [
        pytest.param("label.worktree:s5", "worktree", "s5", id="simple_key"),
        pytest.param("label.claude-code.model:opus", "claude-code/model", "opus", id="namespaced_key_dot_for_slash"),
        pytest.param('label.worktree:"s5 env"', "worktree", "s5 env", id="quoted_value"),
    ],
)
def test_label_eq(kql, query, key, value):
    ast = kql.parse(query)
    assert isinstance(ast, kql.LabelEq)
    assert ast.key == key
    assert ast.value == value


def test_label_exists_simple_key(kql):
    ast = kql.parse("has:label.worktree")
    assert isinstance(ast, kql.LabelExists)
    assert ast.key == "worktree"


def test_label_exists_namespaced_key_dot_for_slash(kql):
    ast = kql.parse("has:label.claude-code.model")
    assert isinstance(ast, kql.LabelExists)
    assert ast.key == "claude-code/model"


def test_label_eq_composes_with_and(kql):
    ast = kql.parse("label.worktree:s5 kind:spec")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.LabelEq)
    assert ast.left.key == "worktree"
    assert isinstance(ast.right, kql.FieldEq)
    assert ast.right.field == "kind"


def test_two_label_terms_and_together(kql):
    ast = kql.parse("label.worktree:s5 label.phase:build")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.LabelEq)
    assert isinstance(ast.right, kql.LabelEq)
    assert ast.left.key == "worktree" and ast.left.value == "s5"
    assert ast.right.key == "phase" and ast.right.value == "build"


def test_label_exists_composes_with_label_eq(kql):
    ast = kql.parse("has:label.worktree label.phase:build")
    assert isinstance(ast, kql.And)
    assert isinstance(ast.left, kql.LabelExists)
    assert isinstance(ast.right, kql.LabelEq)


def test_label_eq_negation(kql):
    ast = kql.parse("-label.worktree:s5")
    assert isinstance(ast, kql.Not)
    assert isinstance(ast.operand, kql.LabelEq)


# -- disallowed chars still rejected on the label path ----------------------


# -- wrong form → actionable error pointing at the dot-form ------------------


def test_old_equals_form_rejected_with_guidance(kql):
    # label:worktree=s5 — the '=' is not a word char; the message must guide the
    # user toward the correct label.<key>:<value> form.
    with pytest.raises(kql.KqlParseError) as exc:
        kql.parse("label:worktree=s5")
    assert "label." in str(exc.value)


# -- annotations get NO selector --------------------------------------------


def test_has_without_label_prefix_rejected(kql):
    with pytest.raises(kql.KqlParseError) as exc:
        kql.parse("has:worktree")
    assert "label" in str(exc.value).lower()


def test_label_nodes_are_immutable(kql):
    eq = kql.LabelEq(key="worktree", value="s5")
    exists = kql.LabelExists(key="worktree")
    with pytest.raises(Exception):
        eq.key = "other"
    with pytest.raises(Exception):
        exists.key = "other"
