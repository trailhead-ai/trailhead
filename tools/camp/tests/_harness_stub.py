"""A hermetic stand-in for the harness seams the `camp rm` teardown guard reads.

The guard asks the harness two questions on every non-`--force` removal — which
transcripts it keeps and which sessions are live — and refuses when it cannot
get an answer. A test environment that leaves either seam pointing at the real
machine therefore either answers from the developer's own `~/.claude` or, with
a stripped PATH, refuses every removal. Both are noise, so every `camp rm` test
environment pins both seams here.

Path-loaded rather than imported (see the reuse in ``test_statelessness.py``):
the tests directory is addressed by path so the reuse is unambiguous under any
invocation.
"""

from __future__ import annotations

import os
from pathlib import Path

#: A `claude` stand-in. The only subcommand camp reaches for on this surface is
#: the live-session enumeration, whose answer each test writes into a file; the
#: failure branch exists so a test can pin what camp does when the probe fails
#: rather than returns an empty list.
CLAUDE_STUB = """#!/usr/bin/env python3
import os, sys

if sys.argv[1:3] == ["agents", "--json"]:
    if os.environ.get("CAMP_FAKE_AGENTS_FAIL"):
        sys.stderr.write("claude: not logged in")
        sys.exit(1)
    path = os.environ.get("CAMP_FAKE_AGENTS_FILE")
    sys.stdout.write(open(path).read() if path else "[]")
    sys.exit(0)
sys.exit(0)
"""


def fake_agents_file(tmp_path: Path) -> Path:
    """The file the stand-in reports as its live-session listing."""
    path = tmp_path / "agents.json"
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
    return path


def fake_harness_bin(tmp_path: Path) -> Path:
    """A bin dir holding the `claude` stand-in, created once per tmp_path."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "claude"
    if not stub.exists():
        stub.write_text(CLAUDE_STUB, encoding="utf-8")
        stub.chmod(0o755)
    return bin_dir


def harness_env(tmp_path: Path, *, path: str | None = None) -> dict[str, str]:
    """The environment fragment that pins both harness seams under *tmp_path*."""
    return {
        "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude"),
        "CAMP_FAKE_AGENTS_FILE": str(fake_agents_file(tmp_path)),
        "PATH": f"{fake_harness_bin(tmp_path)}{os.pathsep}"
        f"{path if path is not None else os.environ.get('PATH', '')}",
    }
