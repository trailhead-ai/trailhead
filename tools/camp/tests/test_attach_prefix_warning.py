"""Tests for `camp.attach.prefix_warning` — the nested-multiplexer
prefix-conflict warning.

Test contract:
- Inside a multiplexer, the warning is emitted; outside one, it is not.
- On a remote attach launched from inside a local multiplexer, the warning
  is emitted — the decision is made before the handoff and does not consult
  the target machine.
- On a remote attach launched from outside one, the warning is absent even
  when the target machine's own environment would suggest otherwise.
- The warning goes to stderr and does not change the exit status or prevent
  the handoff.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.attach import prefix_warning  # noqa: E402


# ---------------------------------------------------------------------------
# Inside a multiplexer, warning emitted; outside, it is not.
# ---------------------------------------------------------------------------


def test_warning_emitted_inside_a_multiplexer(capsys):
    prefix_warning.warn_if_nested(env={"TMUX": "/tmp/tmux-1000/default,1234,0"})

    captured = capsys.readouterr()
    assert prefix_warning.MESSAGE in captured.err
    assert captured.out == ""


def test_warning_absent_outside_a_multiplexer(capsys):
    prefix_warning.warn_if_nested(env={})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Remote attach from inside a local multiplexer — warning emitted, decided
# before the handoff, not from the target.
# ---------------------------------------------------------------------------

_REMOTE_ATTACH_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {plugin_dir!r})
    from camp.attach import prefix_warning
    from camp.host import handoff
    from camp.host.config import Host

    local_env = {local_env!r}
    # The Host declaration is all a remote attach carries about the target —
    # it has no environment field, so there is structurally nothing here for
    # the decision to read from the target machine.
    host = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")

    prefix_warning.warn_if_nested(env=local_env)
    argv = handoff.remote_argv(host, "some-session-ref")
    handoff.handoff(["echo", "REMOTE-HANDOFF-RAN"], exec_seam=lambda a: print("EXEC:", a))
    """
)


def _run_remote_attach_script(tmp_path: Path, local_env: dict) -> subprocess.CompletedProcess:
    script = tmp_path / "remote_attach.py"
    script.write_text(
        _REMOTE_ATTACH_SCRIPT.format(plugin_dir=str(_PLUGIN_DIR), local_env=local_env)
    )
    return subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10
    )


def test_remote_attach_from_inside_local_multiplexer_warns(tmp_path):
    result = _run_remote_attach_script(
        tmp_path, {"TMUX": "/tmp/tmux-1000/default,1234,0"}
    )

    assert prefix_warning.MESSAGE in result.stderr
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Remote attach from outside a local multiplexer — warning absent, even
# when a decoy resembling the target's own multiplexer state is present.
# ---------------------------------------------------------------------------


def test_remote_attach_from_outside_local_multiplexer_does_not_warn_even_with_target_decoy(
    tmp_path,
):
    # A decoy meant to model "the target machine's own environment would
    # suggest otherwise": an unrelated key that looks multiplexer-ish but is
    # not the local operator's own TMUX variable. The decision must ignore
    # it entirely, because the function has no target-environment input at
    # all — only the one env mapping passed here, which has no TMUX key.
    local_env = {"CAMP_REMOTE_TMUX": "/tmp/tmux-1000/default,9999,0"}

    result = _run_remote_attach_script(tmp_path, local_env)

    assert prefix_warning.MESSAGE not in result.stderr
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# stderr, no exit-status change, does not prevent the handoff.
# ---------------------------------------------------------------------------

_WARN_THEN_HANDOFF_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {plugin_dir!r})
    from camp.attach import prefix_warning
    from camp.host import handoff

    prefix_warning.warn_if_nested(env={{"TMUX": "/tmp/tmux-1000/default,1,0"}})
    handoff.handoff(["echo", "HANDOFF-MARKER"])
    """
)


def test_warning_goes_to_stderr_and_does_not_prevent_the_handoff(tmp_path):
    script = tmp_path / "warn_then_handoff.py"
    script.write_text(_WARN_THEN_HANDOFF_SCRIPT.format(plugin_dir=str(_PLUGIN_DIR)))

    result = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0
    assert result.stdout == "HANDOFF-MARKER\n"
    assert prefix_warning.MESSAGE in result.stderr
