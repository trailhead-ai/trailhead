"""Unit-level tests for camp.cli.session — collaborators mocked directly.

Complements test_session_cli.py's end-to-end subprocess coverage with two
seams that are awkward to exercise through the real CLI binary:

- `wait_for_provisioning` degrading a corrupt/missing manifest (ManifestError)
  into the same one-line refusal shape as any other provisioning failure,
  rather than letting the exception escape as a raw traceback.
- `camp sessions <slug>` scoping enumeration by the same resolved workspace
  directory the launch engine spawns into, so a symlinked workspace root
  doesn't make a just-launched session invisible to a slug-scoped query.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}


class TestWaitForProvisioningManifestError:
    def test_a_manifest_error_degrades_to_a_camp_launch_refusal_line(self, monkeypatch, capsys):
        import camp.cli.session as cli_session
        from camp.group.manifest import ManifestError

        def boom(*a, **k):
            raise ManifestError("manifest for 'feat-x' is corrupt: not valid JSON")

        monkeypatch.setattr(
            "camp.provision.lifecycle.wait_for_provisioning_ready", boom
        )

        result = cli_session.wait_for_provisioning(GROUP, "feat-x", env={})

        assert result is False
        err = capsys.readouterr().err
        assert "camp launch:" in err
        assert "not valid JSON" in err

    def test_a_manifest_error_does_not_propagate_as_a_traceback(self, monkeypatch):
        import camp.cli.session as cli_session
        from camp.group.manifest import ManifestError

        def boom(*a, **k):
            raise ManifestError("no manifest found")

        monkeypatch.setattr(
            "camp.provision.lifecycle.wait_for_provisioning_ready", boom
        )

        # No exception should escape — the function returns a plain bool.
        assert cli_session.wait_for_provisioning(GROUP, "feat-x", env={}) is False


class TestSessionsSlugScopingResolvesTheWorkspace:
    def test_slug_scoped_enumeration_uses_the_resolved_workspace_dir(
        self, monkeypatch, tmp_path
    ):
        import camp.cli.session as cli_session

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real_dir)

        monkeypatch.setattr(
            "camp.group.manifest.workspace_dir", lambda group, slug, env=None: link
        )
        monkeypatch.setattr(
            "camp.cli.dispatch._slug_from_args_or_cwd",
            lambda *a, **k: "feat-x",
        )

        seen: dict[str, Path | None] = {}

        def fake_enumerate(group, workspace, env):
            seen["workspace"] = workspace
            return []

        monkeypatch.setattr(cli_session, "_enumerate_sessions", fake_enumerate)

        cli_session._cmd_sessions_group_cli(["feat-x"], GROUP, {})

        assert seen["workspace"] == link.resolve()
        assert seen["workspace"] != link
