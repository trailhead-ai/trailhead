"""KQL-subset tokenizer + recursive-descent parser.

Turns a KQL-subset query string into a **backend-agnostic AST** of frozen
dataclass nodes. No I/O, no SQL, no sqlite — pure string → AST.

NOTE: This module intentionally omits ``from __future__ import annotations``.
Python 3.14's ``@dataclass`` implementation looks up ``cls.__module__`` in
``sys.modules`` when annotations are stored as strings (which ``from __future__
import annotations`` forces). The ``load_script`` test harness loads this module via
``importlib.util`` without registering it in ``sys.modules``, so string-annotation
mode would crash. Using concrete runtime type annotations avoids that.

**Alias-resolution-location decision (SURFACE field on the node):**
The parser keeps the SURFACE field name on every AST node (e.g. ``area``,
``phase``, ``keyword``, ``related-area``). Alias resolution to the real index key
(``area`` → ``related-area``, ``phase`` → ``related-phases``,
``keyword`` → ``keywords``) is deferred to ``kql_compile.py`` so this
module stays backend-agnostic. A ``FacetMembership`` node is emitted whenever the
field name is a known facet alias or its resolved real key; a ``FieldEq`` node is
emitted for direct scalar fields; a ``Compare`` node is emitted for comparison
operators.

**AST node types:**
- ``FieldEq(field, value)`` — scalar field equality (``kind:spec``, ``status:active``,
  ``repo:"trailhead-ai/trailhead"``). ``field`` is the surface field name.
- ``FacetMembership(facet, value)`` — membership in a list-valued facet (``area``,
  ``phase``, ``keyword``, ``related-area``, ``related-phases``, ``keywords``).
  ``facet`` is the surface field name.
- ``LabelEq(key, value)`` — exact indexed-label match (``label.worktree:s5``).
  Namespaced keys use the **dot-for-slash** convention: ``label.claude-code.model:opus``
  selects the stored key ``claude-code/model`` (the lexer rejects a bare ``/``, so
  the selector encodes ``/`` as ``.`` and the parser decodes it back). ``key`` is the
  REAL stored key. ``annotations`` deliberately have NO selector.
- ``LabelExists(key)`` — indexed-label key existence (``has:label.worktree``); same
  dot-for-slash decoding on ``key``.
- ``FullText(term)`` — a bare full-text term (``trailhead``).
- ``Phrase(text)`` — a quoted adjacent-phrase (``"penny worker"``).
- ``Compare(field, op, value)`` — range comparison on a date/number column;
  ``op ∈ {">=", "<=", ">", "<"}``.
- ``And(left, right)``, ``Or(left, right)``, ``Not(operand)`` — boolean composition.
  Implicit AND between adjacent terms (left-associative).
- ``Group(node)`` — explicit ``()`` grouping. The inner node is wrapped so the
  compiler can see the grouping structure when needed; in most cases the compiler
  will unwrap it via ``node.inner``.
- ``Not`` is also used for the ``-exclusion`` prefix form (``-status:dropped`` →
  ``Not(FieldEq("status","dropped"))``). There is no separate ``Exclude`` node;
  ``-x`` is canonically represented as ``Not(x)``.

**``field:(a or b)`` expansion:**
``kind:(spec or task)`` is expanded at parse time to
``Or(FieldEq("kind","spec"), FieldEq("kind","task"))`` — one leaf per value,
distributed over the OR. This is the simplest form for the compiler to pattern-match.

**Known fields (hard error on anything else):**
- Facet aliases: ``area``, ``phase``, ``keyword``
- Facet real keys: ``related-area``, ``related-phases``, ``keywords``
- Scalar direct: ``kind``, ``status``, ``repo``, ``team``, ``product``, ``suite``
- Comparison: ``created-at``, ``updated-at``, ``last-referenced-at``

**Hard errors → ``KqlParseError``:**
- Unbalanced quotes / parentheses.
- Empty / whitespace-only query.
- Unsupported features: wildcard ``*``, fuzzy ``~``, boost ``^``, regex ``/.../``.
- Unknown field/alias — hard error with a deterministic "did you mean 'X'?"
  suggestion and the sorted valid-field list.

The module raises ``KqlParseError`` and never prints or calls ``sys.exit``.
"""

import difflib
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Known fields
# ---------------------------------------------------------------------------

_FACET_ALIASES = frozenset(
    {
        "area",
        "phase",
        "keyword",
        "related-area",
        "related-phases",
        "keywords",
    }
)

_SCALAR_FIELDS = frozenset(
    {
        "kind",
        "status",
        "repo",
        "team",
        "product",
        "suite",
    }
)

_COMPARE_FIELDS = frozenset(
    {
        "created-at",
        "updated-at",
        "last-referenced-at",
    }
)

VALID_FIELDS = tuple(sorted(_FACET_ALIASES | _SCALAR_FIELDS | _COMPARE_FIELDS))

_ALL_FIELDS = _FACET_ALIASES | _SCALAR_FIELDS | _COMPARE_FIELDS


# ---------------------------------------------------------------------------
# AST node types (frozen dataclasses — no from __future__ import annotations)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldEq:
    """Scalar field equality: ``kind:spec``."""

    field: str
    value: str


@dataclass(frozen=True)
class FacetMembership:
    """List-valued facet membership: ``area:penny``, ``phase:build``."""

    facet: str
    value: str


@dataclass(frozen=True)
class LabelEq:
    """Exact indexed-label match: ``label.worktree:s5`` → ``LabelEq("worktree", "s5")``.

    ``key`` is the REAL stored label key (dot-for-slash already decoded — see
    ``_parse_field_rhs``). Only ``labels`` have a selector; ``annotations`` do not.
    """

    key: str
    value: str


@dataclass(frozen=True)
class LabelExists:
    """Indexed-label key existence: ``has:label.worktree`` → ``LabelExists("worktree")``.

    ``key`` is the REAL stored label key (dot-for-slash already decoded).
    """

    key: str


@dataclass(frozen=True)
class FullText:
    """Bare full-text term: ``trailhead``."""

    term: str


@dataclass(frozen=True)
class Phrase:
    """Quoted adjacent phrase: ``"penny worker"``."""

    text: str


@dataclass(frozen=True)
class Compare:
    """Range comparison: ``created-at >= "2026-01-01"``."""

    field: str
    op: str
    value: str


@dataclass(frozen=True)
class And:
    """Boolean AND (explicit ``and`` or implicit adjacency)."""

    left: object
    right: object


@dataclass(frozen=True)
class Or:
    """Boolean OR."""

    left: object
    right: object


@dataclass(frozen=True)
class Not:
    """Boolean NOT — also used for the ``-exclusion`` prefix form."""

    operand: object


@dataclass(frozen=True)
class Group:
    """Explicit parenthesized grouping."""

    inner: object


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class KqlParseError(Exception):
    """Raised for any parse error (malformed input, unsupported feature, unknown field).

    The message is human-readable and stable for identical inputs (deterministic).
    Never prints or calls sys.exit — callers catch and surface this.
    """


# ---------------------------------------------------------------------------
# Deterministic "did you mean" suggestion
# ---------------------------------------------------------------------------


def _suggest_field(name):
    """Return a deterministic 'did you mean X?' error string for an unknown field name.

    Uses ``difflib.get_close_matches`` over the *sorted* ``VALID_FIELDS`` tuple
    (sorted so iteration order is stable regardless of Python version or set hashing).
    On ties, the first candidate in lexicographic order wins (``VALID_FIELDS`` is
    already sorted, so ``get_close_matches`` sees a stable input and returns a stable
    result). ``n=1`` ensures at most one suggestion.
    """
    matches = difflib.get_close_matches(name, VALID_FIELDS, n=1, cutoff=0.6)
    suggestion = f"did you mean '{matches[0]}'? " if matches else ""
    all_fields = ", ".join(VALID_FIELDS)
    return f"unknown field '{name}': {suggestion}valid fields: {all_fields}"


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Token kinds
_TK_WORD = "WORD"  # bare word (identifier or value)
_TK_QUOTED = "QUOTED"  # "..."
_TK_COLON = "COLON"  # :
_TK_LPAREN = "LPAREN"  # (
_TK_RPAREN = "RPAREN"  # )
_TK_GTE = "GTE"  # >=
_TK_LTE = "LTE"  # <=
_TK_GT = "GT"  # >
_TK_LT = "LT"  # <
_TK_MINUS = "MINUS"  # -  (exclusion prefix)
_TK_EOF = "EOF"


def _tokenize(text):
    """Return a list of (kind, value) token pairs.

    Handles:
    - Quoted strings "..." (values or phrases). Unbalanced quote → error.
    - Hyphenated field names and words (hyphens mid-word are NOT boundaries).
    - Comparison operators >=, <=, >, <.
    - Rejection of unsupported characters in bare words: *, ~, ^, /.

    A hyphen is treated as a word character only when preceded by another word
    character (so -status is MINUS + WORD but created-at is one WORD).
    """
    tokens = []
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        # whitespace
        if c in " \t\n\r":
            i += 1
            continue

        # quoted string
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 1
            if j >= n:
                raise KqlParseError("unbalanced quote: missing closing '\"'")
            tokens.append((_TK_QUOTED, text[i + 1 : j]))
            i = j + 1
            continue

        # bare '/' — rejected. Either an unsupported regex literal (/re/) or an
        # unquoted value containing a slash; both are errors, but the common case
        # is a value (e.g. a repo path) that needs quoting, so say so.
        if c == "/":
            raise KqlParseError(
                "unexpected '/': regex (/re/) is not supported, and a value "
                "containing '/' must be quoted "
                '(e.g. repo:"trailhead-ai/trailhead")'
            )

        # operators >=, <=, >, <
        if c == ">" and i + 1 < n and text[i + 1] == "=":
            tokens.append((_TK_GTE, ">="))
            i += 2
            continue
        if c == "<" and i + 1 < n and text[i + 1] == "=":
            tokens.append((_TK_LTE, "<="))
            i += 2
            continue
        if c == ">":
            tokens.append((_TK_GT, ">"))
            i += 1
            continue
        if c == "<":
            tokens.append((_TK_LT, "<"))
            i += 1
            continue

        # colon
        if c == ":":
            tokens.append((_TK_COLON, ":"))
            i += 1
            continue

        # parens
        if c == "(":
            tokens.append((_TK_LPAREN, "("))
            i += 1
            continue
        if c == ")":
            tokens.append((_TK_RPAREN, ")"))
            i += 1
            continue

        # minus — leading exclusion prefix (emitted as its own token)
        if c == "-":
            tokens.append((_TK_MINUS, "-"))
            i += 1
            continue

        # bare word (letters, digits, underscore, dot; hyphen is mid-word only)
        if c.isalnum() or c in "_." or c in "*~^":
            j = i
            while j < n and (
                text[j].isalnum()
                or text[j] in "_."
                or text[j] in "*~^"
                # hyphen as mid-word char only (not at the start position)
                or (text[j] == "-" and j > i)
            ):
                j += 1

            word = text[i:j]

            # Trim trailing hyphens (e.g. "created-" where a MINUS follows)
            while word.endswith("-"):
                word = word[:-1]
                j -= 1

            if "*" in word:
                raise KqlParseError(
                    f"wildcard queries ('{word}') are not supported in this KQL subset"
                )
            if "~" in word:
                raise KqlParseError(
                    f"fuzzy queries ('{word}') are not supported in this KQL subset"
                )
            if "^" in word:
                raise KqlParseError(
                    f"boost queries ('{word}') are not supported in this KQL subset"
                )

            tokens.append((_TK_WORD, word))
            i = j
            continue

        if c == "=":
            # The old/wrong label form ``label:worktree=s5`` lands here ('=' is not
            # a word char). Point users at the correct dot-form selector.
            raise KqlParseError(
                "unexpected character '=' in query: for labels use "
                "label.<key>:<value> (e.g. label.worktree:s5) — "
                "namespaced keys encode '/' as '.' (label.claude-code.model:opus)"
            )

        raise KqlParseError(f"unexpected character '{c}' in query")

    tokens.append((_TK_EOF, ""))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _Parser:
    """Recursive-descent parser over the token list produced by ``_tokenize``."""

    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    # -- token navigation ----------------------------------------------------

    def _peek(self):
        return self._tokens[self._pos]

    def _consume(self):
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _at(self, kind):
        return self._peek()[0] == kind

    def _at_word(self, value):
        tk, val = self._peek()
        return tk == _TK_WORD and val.lower() == value

    # -- grammar rules -------------------------------------------------------

    def parse_query(self):
        if self._at(_TK_EOF):
            raise KqlParseError("empty query: a non-empty query string is required")
        ast = self._parse_or()
        if not self._at(_TK_EOF):
            _, val = self._peek()
            raise KqlParseError(f"unexpected token '{val}' at end of query")
        return ast

    def _parse_or(self):
        left = self._parse_and()
        while self._at_word("or"):
            self._consume()  # eat 'or'
            right = self._parse_and()
            left = Or(left, right)
        return left

    def _parse_and(self):
        left = self._parse_not()
        while True:
            if self._at_word("and"):
                self._consume()  # eat explicit 'and'
                right = self._parse_not()
                left = And(left, right)
            elif not self._at(_TK_EOF) and not self._at(_TK_RPAREN) and not self._at_word("or"):
                # Implicit AND: next token starts a new primary
                right = self._parse_not()
                left = And(left, right)
            else:
                break
        return left

    def _parse_not(self):
        if self._at_word("not"):
            self._consume()
            operand = self._parse_primary()
            return Not(operand)
        if self._at(_TK_MINUS):
            self._consume()
            operand = self._parse_primary()
            return Not(operand)
        return self._parse_primary()

    def _parse_primary(self):
        # Grouped expression
        if self._at(_TK_LPAREN):
            self._consume()  # eat '('
            if self._at(_TK_RPAREN):
                raise KqlParseError("empty group '()' is not valid")
            inner = self._parse_or()
            if not self._at(_TK_RPAREN):
                raise KqlParseError("unbalanced parenthesis: missing closing ')'")
            self._consume()  # eat ')'
            return Group(inner)

        # Quoted phrase (standalone, no field:)
        if self._at(_TK_QUOTED):
            _, text = self._consume()
            return Phrase(text)

        # Word — could be a field name (field:...) or a bare term
        if self._at(_TK_WORD):
            _, word = self._peek()
            word_lower = word.lower()

            # Lookahead: peek at the token AFTER this word
            next_pos = self._pos + 1
            next_tok = self._tokens[next_pos] if next_pos < len(self._tokens) else (_TK_EOF, "")

            if next_tok[0] == _TK_COLON:
                self._consume()  # eat field word
                self._consume()  # eat ':'
                return self._parse_field_rhs(word_lower)

            if next_tok[0] in (_TK_GTE, _TK_LTE, _TK_GT, _TK_LT):
                self._consume()  # eat field word
                _, op = self._consume()  # eat operator
                return self._parse_compare_rhs(word_lower, op)

            # bare word — reject reserved Boolean operators used alone
            self._consume()
            if word_lower in ("and", "or", "not"):
                raise KqlParseError(f"unexpected operator '{word}' without operands")
            return FullText(word)

        raise KqlParseError(f"unexpected token '{self._peek()[1]}' in query")

    def _parse_field_rhs(self, field):
        """Parse the RHS of a field:value expression.

        Routes the indexed-label selectors (``label.<key>:<value>`` and
        ``has:label.<key>``) to ``LabelEq`` / ``LabelExists`` BEFORE the static
        ``_ALL_FIELDS`` guard — those fields are deliberately NOT in the static set.
        Then validates the field name; emits FacetMembership for facet fields,
        FieldEq for scalar fields, and Compare for comparison fields (the latter
        only when the field name appears in _COMPARE_FIELDS).

        **Dot-for-slash key convention (labels only):** a namespaced label key
        such as ``claude-code/model`` contains a ``/``, which the lexer rejects in a
        bare token. The selector therefore encodes ``/`` as ``.`` —
        ``label.claude-code.model:opus`` — and this routing decodes it back to the
        real stored key by replacing ``.`` with ``/``. This is unambiguous because
        stored keys are kebab-only (``[a-z0-9-]``, no dots) with at most one
        namespace segment, so the post-prefix portion has at most one ``.``. Simple
        keys (``label.worktree`` → ``worktree``) have no dot and are unaffected.
        ``annotations`` deliberately have no selector.
        """
        if field.startswith("label."):
            key = field[len("label.") :].replace(".", "/")
            value = self._parse_value()
            return LabelEq(key=key, value=value)

        if field == "has":
            if self._at(_TK_WORD):
                _, rhs = self._peek()
                if rhs.startswith("label."):
                    self._consume()  # eat the label.<key> word
                    key = rhs[len("label.") :].replace(".", "/")
                    return LabelExists(key=key)
            raise KqlParseError("has: expects has:label.<key> (e.g. has:label.worktree)")

        if field not in _ALL_FIELDS:
            raise KqlParseError(_suggest_field(field))

        is_facet = field in _FACET_ALIASES

        # field:(a or b) group — expanded to Or tree
        if self._at(_TK_LPAREN):
            self._consume()  # eat '('
            if self._at(_TK_RPAREN):
                raise KqlParseError("empty group in field:(…) is not valid")
            nodes = [self._make_field_node(field, self._parse_value(), is_facet)]
            while self._at_word("or"):
                self._consume()  # eat 'or'
                nodes.append(self._make_field_node(field, self._parse_value(), is_facet))
            if not self._at(_TK_RPAREN):
                raise KqlParseError("unbalanced parenthesis in field:(…)")
            self._consume()  # eat ')'
            # Expand to left-associative Or tree
            result = nodes[0]
            for node in nodes[1:]:
                result = Or(result, node)
            return result

        value = self._parse_value()
        return self._make_field_node(field, value, is_facet)

    def _make_field_node(self, field, value, is_facet):
        if is_facet:
            return FacetMembership(facet=field, value=value)
        return FieldEq(field=field, value=value)

    def _parse_compare_rhs(self, field, op):
        """Parse the RHS of a field <op> value comparison."""
        if field not in _ALL_FIELDS:
            raise KqlParseError(_suggest_field(field))
        if field not in _COMPARE_FIELDS and field not in _SCALAR_FIELDS:
            raise KqlParseError(
                f"field '{field}' does not support comparison operators; "
                f"use '{field}:value' for equality"
            )
        value = self._parse_value()
        return Compare(field=field, op=op, value=value)

    def _parse_value(self):
        """Parse a single value: quoted string or bare word."""
        if self._at(_TK_QUOTED):
            _, text = self._consume()
            return text
        if self._at(_TK_WORD):
            _, word = self._consume()
            return word
        raise KqlParseError(f"expected a value but got '{self._peek()[1]}'")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse(query):
    """Parse a KQL-subset query string and return the AST root node.

    Args:
        query: The raw query string (e.g. ``"kind:spec and area:penny"``).

    Returns:
        An AST root node (one of the frozen dataclass types defined above).

    Raises:
        KqlParseError: For any parse error — unbalanced quotes/parens, empty query,
            unsupported feature, or unknown field/alias.
    """
    stripped = query.strip()
    if not stripped:
        raise KqlParseError("empty query: a non-empty query string is required")
    tokens = _tokenize(stripped)
    parser = _Parser(tokens)
    return parser.parse_query()
