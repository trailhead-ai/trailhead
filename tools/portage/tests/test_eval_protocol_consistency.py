"""The eval protocol must name the version and the not-run reasons the eval command actually has.

The expected values come from ``portage.eval_command.request``; nothing is retyped here. This is a
code-vs-document consistency check (wiring), not a behavioural claim about the prose.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "portage" / "plugins" / "portage"))

from portage.eval_command import request  # noqa: E402

PROTOCOL = ROOT / "docs" / "eval-protocol.md"
REQUIRE_VERSION = re.compile(r"--require-version[ =](\d+)")


def _protocol() -> str:
    return PROTOCOL.read_text()


def test_protocol_requires_the_eval_command_version():
    required = {int(n) for n in REQUIRE_VERSION.findall(_protocol())}
    assert required == {request.EVAL_COMMAND_VERSION}


@pytest.mark.parametrize("reasons", [request.STOP_AND_REPORT_REASONS, request.RESUBMIT_REASONS],
                         ids=["stop-and-report", "resubmit"])
def test_protocol_names_every_reason(reasons):
    text = _protocol()
    missing = [r for r in reasons if f"`{r}`" not in text]
    assert not missing
