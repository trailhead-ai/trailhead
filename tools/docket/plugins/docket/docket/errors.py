"""The error every docket module raises for a failure the operator should read, not debug."""
from __future__ import annotations


class DocketError(Exception):
    """A named failure. ``docket.cli.main`` prints it as ``docket: <message>`` and exits 1."""
