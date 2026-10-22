import tempfile

import pytest


@pytest.fixture(autouse=True)
def _contained_tempdir(tmp_path, monkeypatch):
    """Keep every ``tempfile`` default (the eval-gate state dir) under the test's own tmp_path."""
    scratch = tmp_path / "system-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
