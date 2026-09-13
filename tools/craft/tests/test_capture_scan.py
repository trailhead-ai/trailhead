"""Tests for capture_scan.py — the credential-pattern scan over committed eval
captures.

The pattern set mirrors the seven named classes in the credential-pattern scrub
list (`tools/craft/plugins/craft/skills/_shared/security.md` — "Credential-pattern
scrub"): key-like tokens, vendor fixed-prefix tokens, bearer shapes, api-key
shapes, high-entropy base64, high-entropy hex, and PEM private-key blocks. These
tests are derived from that list and from the task's test contract, never from
the scanner's own implementation.

Exit-code contract (mirrors `leak_gate.py` and `footprint_guard.py`):
  0 → clean (no credential-class finding in the scanned tree)
  1 → finding(s) — commit blocked (prints `relpath:lineno:class:token` per hit)
  2 → error — fail-closed (a given tree path does not exist)

The known false-positive class: `/` is in the base64 alphabet, so the
high-entropy pattern also matches long slash-separated paths and record URLs.
The scanner must classify that shape distinctly rather than either silently
dropping it or reporting it as a credential — `test_a_record_url_and_a_real_
credential_are_classified_differently` pins that behaviour directly.
"""

from __future__ import annotations

import base64
import random
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCANNER = REPO_ROOT / "plugins" / "craft" / "scripts" / "capture_scan.py"
EVALS_ROOT = REPO_ROOT / "plugins" / "craft" / "evals"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write(tmp_path: Path, name: str, body: str) -> Path:
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    return f


# ---- one test per pattern class: planted secret flags, removal clears ------

CLASSES = {
    "key-like": (
        "key-like",
        "AWS_SECRET_ACCESS_KEY=zQ9pLxR2vT8mN0kW\n",
        "just a normal line of transcript output\n",
    ),
    "vendor-prefix": (
        "vendor-prefix",
        "leaked: ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "\n",
        "leaked nothing, a github personal access thing was rotated\n",
    ),
    "bearer": (
        "bearer",
        "Authorization: Bearer abc123.def456-ghi789\n",
        "Authorization: (redacted, see runbook)\n",
    ),
    "api-key-shape": (
        "api-key-shape",
        'api_key="zx9Kq2mN7vLp4Rt6Ws8Y"\n',
        "api key rotation happens quarterly\n",
    ),
    "high-entropy-base64": (
        "high-entropy-base64",
        "leaked=Tf3kQzP9mVnB7xRcYjLhWaEoIuGdSbNtMkXpZq1s\n",
        "leaked=nothing-here-just-prose\n",
    ),
    "high-entropy-hex": (
        "high-entropy-hex",
        "hash=a3f5c9e1b2d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2b4\n",
        "hash=not-a-hex-string\n",
    ),
    "pem-private-key": (
        "pem-private-key",
        "-----BEGIN RSA PRIVATE KEY-----\n",
        "the private key rotation ran on schedule\n",
    ),
}


@pytest.mark.parametrize("cls,planted,clean", CLASSES.values(), ids=list(CLASSES))
def test_class_is_flagged_when_planted_and_clear_when_removed(tmp_path, cls, planted, clean):
    dirty = _write(tmp_path, "dirty.txt", planted)
    result = _run(dirty)
    assert result.returncode == 1, f"expected a finding, got: {result.stdout}{result.stderr}"
    assert f":{cls}:" in result.stdout, (
        f"expected a {cls!r} finding in stdout:\n{result.stdout}"
    )

    clean_file = _write(tmp_path, "clean.txt", clean)
    clean_result = _run(clean_file)
    assert clean_result.returncode == 0, (
        f"removing the secret should leave the file clean: {clean_result.stdout}"
    )
    assert f":{cls}:" not in clean_result.stdout


# ---- the known false-positive class -----------------------------------------


def test_a_record_url_and_a_real_credential_are_classified_differently(tmp_path):
    text = (
        "See http://127.0.0.1:7313/records/trailhead/task/"
        "both-slice-termination-outcomes-are-measured-not-assumed for details.\n"
        "leaked=Tf3kQzP9mVnB7xRcYjLhWaEoIuGdSbNtMkXpZq1s\n"
    )
    f = _write(tmp_path, "mixed.txt", text)
    result = _run(f)

    lines = result.stdout.splitlines()
    url_lines = [ln for ln in lines if "7313/records/trailhead/task/both" in ln]
    secret_lines = [ln for ln in lines if "Tf3kQzP9mVnB7xRcYjLhWaEoIuGdSbNtMkXpZq1s" in ln]

    assert url_lines, f"expected the record-url shape to be reported:\n{result.stdout}"
    assert secret_lines, f"expected the credential to be reported:\n{result.stdout}"

    url_class = url_lines[0].split(":")[2]
    secret_class = secret_lines[0].split(":")[2]
    assert url_class != secret_class, (
        f"record-url shape and a real credential must classify differently, "
        f"got {url_class!r} for both:\n{result.stdout}"
    )
    assert url_class != "high-entropy-base64", (
        "the record-url shape must not be reported as a raw credential class"
    )
    # Only the real credential should trip the exit code.
    assert result.returncode == 1


def test_a_slash_heavy_path_alone_does_not_trip_the_exit_code(tmp_path):
    f = _write(
        tmp_path,
        "path-only.txt",
        "Read tools/craft/plugins/craft/skills/slice/SKILL.md for the ritual.\n",
    )
    result = _run(f)
    assert result.returncode == 0, (
        f"a bare slash-heavy path must not be reported as a credential finding: "
        f"{result.stdout}{result.stderr}"
    )


def _generated_slashy_secrets(seed: int, count: int, min_slashes: int, max_slashes: int):
    """Deterministically generate real base64(32 random bytes) secrets whose
    matched token carries a slash count in [min_slashes, max_slashes] — the
    band a naive "2 or more slashes ⇒ path" rule would misclassify as safe,
    since `/` is a base64-alphabet character a genuine secret can carry by
    chance, not just a path separator.
    """
    rng = random.Random(seed)
    found = []
    while len(found) < count:
        token = base64.b64encode(rng.randbytes(32)).decode()
        slashes = token.count("/")
        if min_slashes <= slashes <= max_slashes:
            found.append(token)
    return found


def test_generated_slashy_base64_secrets_are_flagged_as_credentials(tmp_path):
    # Real secrets, not a hand-picked example: base64(os.urandom(32))-shaped
    # tokens whose slash count (2-3) sits in the band a slash-count-only
    # allowlist rule used to treat as "path-shaped" and wave through.
    secrets = _generated_slashy_secrets(seed=42, count=10, min_slashes=2, max_slashes=3)
    body = "".join(f"line_{i}={tok}\n" for i, tok in enumerate(secrets))
    f = _write(tmp_path, "generated-secrets.txt", body)
    result = _run(f)

    assert result.returncode == 1, (
        f"a generated base64 secret with 2-3 slashes must be reported as a "
        f"credential finding (exit 1), not waved through as a known-safe "
        f"path/URL shape: {result.stdout}{result.stderr}"
    )
    for tok in secrets:
        # The scanner's trailing `={0,2}` requires a following word/non-word
        # boundary it cannot find at end-of-line, so the printed match omits
        # any base64 padding — compare against the unpadded token.
        unpadded = tok.rstrip("=")
        matching = [ln for ln in result.stdout.splitlines() if unpadded in ln]
        assert matching, f"expected {tok!r} to be reported at all:\n{result.stdout}"
        assert ":path-or-url-shape:" not in matching[0], (
            f"generated secret {tok!r} was misclassified as the known-safe "
            f"path/URL shape:\n{matching[0]}"
        )


def test_generated_secrets_at_or_above_the_slash_threshold_are_still_flagged(tmp_path):
    # Real secrets whose slash count (4-6) sits at or above the current
    # slash-only threshold — the band a slash-count-only rule waves through
    # as path-shaped even though these tokens have no path-like structure
    # (no segment is a plain word; the run mixes upper/lower/digit throughout).
    secrets = _generated_slashy_secrets(seed=99, count=10, min_slashes=4, max_slashes=6)
    body = "".join(f"line_{i}={tok}\n" for i, tok in enumerate(secrets))
    f = _write(tmp_path, "generated-secrets-heavy.txt", body)
    result = _run(f)

    assert result.returncode == 1, (
        f"a generated base64 secret with 4+ slashes must be reported as a "
        f"credential finding (exit 1), not waved through as a known-safe "
        f"path/URL shape on slash count alone: {result.stdout}{result.stderr}"
    )
    for tok in secrets:
        # As above, the scanner's `\b` word boundary can't match at a
        # leading `/` either, so a token that happens to start with `/`
        # prints without it — strip both ends before comparing.
        unpadded = tok.rstrip("=").lstrip("/")
        matching = [ln for ln in result.stdout.splitlines() if unpadded in ln]
        assert matching, f"expected {tok!r} to be reported at all:\n{result.stdout}"
        assert ":path-or-url-shape:" not in matching[0], (
            f"generated secret {tok!r} was misclassified as the known-safe "
            f"path/URL shape:\n{matching[0]}"
        )


# ---- CLI plumbing ------------------------------------------------------------


def test_a_missing_tree_fails_closed(tmp_path):
    result = _run(tmp_path / "does-not-exist")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_a_clean_multi_line_capture_exits_zero(tmp_path):
    f = _write(
        tmp_path,
        "capture.txt",
        "user: run the eval\nassistant: sure, here is the plan...\n",
    )
    result = _run(f)
    assert result.returncode == 0
    assert result.stdout == ""


# ---- CI enforcement: the committed capture batch is scanned ----------------


def test_the_committed_eval_captures_pass_the_scan():
    runs_dirs = sorted(EVALS_ROOT.glob("*/runs"))
    assert runs_dirs, "expected at least one committed evals/**/runs directory"
    result = _run(*runs_dirs)
    assert result.returncode == 0, (
        f"a committed capture tripped the credential scan — this is a real "
        f"finding on already-committed evidence, report it, do not edit the "
        f"capture:\n{result.stdout}{result.stderr}"
    )
