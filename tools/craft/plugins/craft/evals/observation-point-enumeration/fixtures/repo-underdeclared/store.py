"""Toy persistence layer."""

ROWS = []


def persist(kind, payload):
    ROWS.append((kind, dict(payload)))
    return len(ROWS)
