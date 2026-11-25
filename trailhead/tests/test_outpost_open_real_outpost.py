"""`trailhead outpost open` against the real Outpost that predates the one-time sign-in code.

Opt-in tier (``seam_real``): the Outpost under test is a checkout of the release before the sign-in code route
existed (``OLD_OUTPOST_REV`` in the ``../outpost`` repository), added as a temporary worktree with the current
checkout's ``node_modules`` linked in, started on a free port with a temp state directory.

    .venv/bin/python -m pytest -m seam_real -n 0 trailhead/tests/test_outpost_open_real_outpost.py
"""

from __future__ import annotations

import subprocess

import pytest

from trailhead import outpost_lifecycle

from ._gateway_e2e_support import make_camp, outpost_checkout, real_outpost

pytestmark = pytest.mark.seam_real

OLD_OUTPOST_REV = "8182b021"


class _Opener:
    def __init__(self):
        self.args = []

    def __call__(self, path):
        self.args.append(path)
        return True


@pytest.fixture(scope="module")
def old_outpost(tmp_path_factory):
    current = outpost_checkout()
    root = tmp_path_factory.mktemp("old-outpost")
    checkout = root / "checkout"
    subprocess.run(
        ["git", "-C", str(current), "worktree", "add", "--detach", str(checkout), OLD_OUTPOST_REV],
        check=True,
        capture_output=True,
    )
    try:
        (checkout / "node_modules").symlink_to(current / "node_modules")
        mp = pytest.MonkeyPatch()
        mp.setenv("TRAILHEAD_GATEWAY_E2E_OUTPOST", str(checkout))
        try:
            with real_outpost(root, make_camp(root)) as outpost:
                yield outpost
        finally:
            mp.undo()
    finally:
        subprocess.run(
            ["git", "-C", str(current), "worktree", "remove", "--force", str(checkout)],
            check=False,
            capture_output=True,
        )


def test_open_falls_back_to_the_bare_ui_url_when_the_older_outpost_wants_the_cookie_for_the_code_route(
    old_outpost, capsys
):
    opener = _Opener()
    env = {"OUTPOST_STATE_DIR": str(old_outpost.state)}

    rc = outpost_lifecycle.open_ui(env=env, port=old_outpost.port, opener=opener, settle=0)

    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert opener.args == [f"http://127.0.0.1:{old_outpost.port}/"]
    assert len(out) == 1 and "older" in out[0]
