#!/usr/bin/env python3
"""Confirmation codes: proof that a confirmation was displayed before the write it gates.

A preview prints a short code; the matching write takes `--confirm <code>`, recomputes the
code from its own input, and refuses when the code is missing or differs. The code is a
digest of three things: the confirmation `kind`, the `spec` name, and a `payload`, the
exact content the write will act on (names with their revisions, the reason, merge
intents, the included set).

    code_for(kind, spec, payload) -> str     4 characters from ALPHABET
    matches(kind, spec, payload, typed) -> bool

The payload is JSON-serialisable. It is canonicalised with `json.dumps(..., sort_keys=True)`
over a structure whose lists are ordered by their own canonical form, so key order and the
order of any list (every list in a payload is a set of members) do not change the code,
while any changed name, revision, reason, intent or member does. Fields are never joined
with a delimiter, so one field cannot borrow text from its neighbour.

`ALPHABET` is lowercase letters and digits without the look-alikes `0 o 1 l i`, so a code
read off a screen and typed back is unambiguous. `matches` ignores case and surrounding
whitespace and compares in constant time; an absent or blank `typed` never matches.

Limit: the code is stateless, short and unkeyed, and the same scripts print and check it.
It proves the confirmation was shown against the shortcut of treating an answer given up
front as the confirmation; it is neither authorization nor a trust boundary, and it does
not stop a caller that deliberately copies a code out of script output without showing it.

The module imports neither `followup_parent` nor `followup_dispose`, so both can use it.
"""

from __future__ import annotations

import hashlib
import hmac
import json

ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
CODE_LENGTH = 4


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return sorted((_canonical(item) for item in value), key=_dump)
    return value


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def code_for(kind: str, spec: str, payload) -> str:
    """The confirmation code for `payload` as confirmed under `kind` for `spec`."""
    digest = hashlib.sha256(
        _dump({"kind": kind, "spec": spec, "payload": _canonical(payload)}).encode("ascii")
    ).digest()
    return "".join(ALPHABET[byte % len(ALPHABET)] for byte in digest[:CODE_LENGTH])


def matches(kind: str, spec: str, payload, typed: str | None) -> bool:
    """Whether `typed` is the code for `payload`; case and surrounding whitespace ignored."""
    if typed is None:
        return False
    return hmac.compare_digest(typed.strip().lower().encode("utf-8"), code_for(kind, spec, payload).encode("ascii"))
