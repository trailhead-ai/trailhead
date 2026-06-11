"""Agent↔script fidelity tests (M-3).

Guards against invented flags or invented subcommands in agent markdown docs.

Strategy:
  1. Parse each `python3 .../script.py ...` invocation line from the agent markdown.
  2. For CLI scripts: import the module, call its argument-parser factory, and
     check that every flag the agent passes actually exists in the parser.
  3. For subcommand scripts: verify the subcommand name and expected flags exist.

Hermetic: no real gh/git calls, no ~/.claude vault dependency, stdlib only.
"""
from __future__ import annotations

import argparse
import importlib
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "forge" / "scripts"
AGENTS_DIR = REPO_ROOT / "plugins" / "forge" / "agents"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

PR_UPDATER_MD = AGENTS_DIR / "pr-updater.md"
WATCH_PR_MD = AGENTS_DIR / "watch-pr.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _script_invocations(text: str) -> list[str]:
    """Extract lines that invoke `python3 .../script.py` from agent markdown.

    Returns each such line, stripped.
    """
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if re.search(r"python3\s+.*\.py", stripped):
            lines.append(stripped)
    return lines


def _flags_on_line(line: str) -> list[str]:
    """Return all --flag tokens that appear on a shell invocation line."""
    # Match --flag (optionally --flag-with-dashes); stop at positional / shell operators
    return re.findall(r"(--[a-zA-Z][a-zA-Z0-9-]+)", line)


def _script_name_from_line(line: str) -> str | None:
    """Return the script basename from a python3 invocation line."""
    m = re.search(r"python3\s+\S+/(\w[\w_-]+\.py)", line)
    if not m:
        m = re.search(r"python3\s+(\w[\w_-]+\.py)", line)
    return m.group(1) if m else None


def _load_module(script_name: str):
    """Import a script module from SCRIPTS_DIR by its basename."""
    module_name = script_name.replace(".py", "").replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        module_name, SCRIPTS_DIR / script_name
    )
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        return None
    return mod


def _parser_option_strings(parser: argparse.ArgumentParser) -> set[str]:
    """Return the set of all --flag strings a parser accepts (including subparsers)."""
    strings: set[str] = set()
    for action in parser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                strings.add(opt)
    # Also dig into subparsers
    for action in parser._subparsers._group_actions if parser._subparsers else []:
        if hasattr(action, "choices") and action.choices:
            for sub in action.choices.values():
                strings.update(_parser_option_strings(sub))
    return strings


def _subcommands(parser: argparse.ArgumentParser) -> set[str]:
    """Return all subcommand names registered with the parser."""
    names: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            for name in action.choices:
                names.add(name)
    return names


def _build_parser(mod) -> argparse.ArgumentParser | None:
    """Try to get an ArgumentParser from a module."""
    # Prefer a factory function
    for attr in ("_build_parser", "build_parser", "get_parser"):
        fn = getattr(mod, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass

    # Fall back: call main([]) and catch the parse error to extract the parser
    # by monkey-patching argparse.ArgumentParser.parse_args
    captured: list[argparse.ArgumentParser] = []

    original_parse = argparse.ArgumentParser.parse_args

    def _capturing_parse(self, args=None, namespace=None):
        captured.append(self)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = _capturing_parse  # type: ignore[method-assign]
    try:
        main = getattr(mod, "main", None)
        if callable(main):
            main([])
    except (SystemExit, Exception):
        pass
    finally:
        argparse.ArgumentParser.parse_args = original_parse  # type: ignore[method-assign]

    return captured[0] if captured else None


# ---------------------------------------------------------------------------
# C-1 / M-3: release_prs_sidecar.py must have a real CLI with write + read subcommands
# ---------------------------------------------------------------------------

class TestSidecarCLIExists:
    """C-1: release_prs_sidecar.py must expose a real CLI with write + read subcommands."""

    def test_sidecar_has_main_block(self) -> None:
        """release_prs_sidecar.py must have a __main__ entry point."""
        text = (SCRIPTS_DIR / "release_prs_sidecar.py").read_text(encoding="utf-8")
        assert 'if __name__ == "__main__"' in text, (
            "release_prs_sidecar.py must have a __main__ block so agents can invoke it as a CLI"
        )

    def test_sidecar_has_argparse(self) -> None:
        """release_prs_sidecar.py must use argparse (not be a pure library)."""
        text = (SCRIPTS_DIR / "release_prs_sidecar.py").read_text(encoding="utf-8")
        assert "argparse" in text, (
            "release_prs_sidecar.py must use argparse — it's invoked as a CLI by agents"
        )

    def test_sidecar_has_write_subcommand(self) -> None:
        """release_prs_sidecar.py CLI must register a 'write' subcommand."""
        mod = _load_module("release_prs_sidecar.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None, "Could not extract ArgumentParser from release_prs_sidecar.py"
        subcmds = _subcommands(parser)
        assert "write" in subcmds, (
            f"release_prs_sidecar.py CLI must have a 'write' subcommand; found: {subcmds}"
        )

    def test_sidecar_has_read_subcommand(self) -> None:
        """release_prs_sidecar.py CLI must register a 'read' subcommand."""
        mod = _load_module("release_prs_sidecar.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None, "Could not extract ArgumentParser from release_prs_sidecar.py"
        subcmds = _subcommands(parser)
        assert "read" in subcmds, (
            f"release_prs_sidecar.py CLI must have a 'read' subcommand; found: {subcmds}"
        )

    def test_sidecar_write_subcommand_has_sidecar_flag(self) -> None:
        """release_prs_sidecar.py write subcommand must accept --sidecar flag."""
        mod = _load_module("release_prs_sidecar.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None
        all_flags = _parser_option_strings(parser)
        assert "--sidecar" in all_flags, (
            f"release_prs_sidecar.py write subcommand must accept --sidecar; found: {all_flags}"
        )

    def test_sidecar_write_subcommand_has_pr_flag(self) -> None:
        """release_prs_sidecar.py write subcommand must accept --pr (repeatable)."""
        mod = _load_module("release_prs_sidecar.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None
        all_flags = _parser_option_strings(parser)
        assert "--pr" in all_flags, (
            f"release_prs_sidecar.py write subcommand must accept --pr; found: {all_flags}"
        )

    def test_sidecar_read_subcommand_has_sidecar_flag(self) -> None:
        """release_prs_sidecar.py read subcommand must accept --sidecar flag."""
        mod = _load_module("release_prs_sidecar.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None
        all_flags = _parser_option_strings(parser)
        assert "--sidecar" in all_flags, (
            f"release_prs_sidecar.py read subcommand must accept --sidecar; found: {all_flags}"
        )

    def test_sidecar_library_functions_still_present(self) -> None:
        """The existing write() and read() library functions must still exist after adding CLI."""
        import release_prs_sidecar as sidecar  # noqa: F401
        assert callable(getattr(sidecar, "write", None)), (
            "release_prs_sidecar.write() library function must still exist"
        )
        assert callable(getattr(sidecar, "read", None)), (
            "release_prs_sidecar.read() library function must still exist"
        )


# ---------------------------------------------------------------------------
# C-1: CLI round-trip via main() (write → read under tmp_path)
# ---------------------------------------------------------------------------

class TestSidecarCLIRoundTrip:
    """C-1: CLI write→read round-trip, malformed-input nonzero exit."""

    def test_cli_write_then_read_roundtrips_single_pr(self, tmp_path: Path) -> None:
        """CLI write followed by CLI read reproduces the PR entry."""
        import release_prs_sidecar as sidecar
        sidecar_path = str(tmp_path / "prs.json")
        rc = sidecar.main([
            "write",
            "--sidecar", sidecar_path,
            "--pr", "alpha:42:https://gh.com/42:feat",
        ])
        assert rc == 0, f"sidecar CLI write returned nonzero: {rc}"

        import json
        data = json.loads((tmp_path / "prs.json").read_text())
        assert data["prs"][0]["repo"] == "alpha"
        assert data["prs"][0]["pr_number"] == "42"
        assert data["prs"][0]["url"] == "https://gh.com/42"
        assert data["prs"][0]["branch"] == "feat"
        assert data["external_tracker"] is None

    def test_cli_write_then_read_roundtrips_multiple_prs(self, tmp_path: Path) -> None:
        """CLI write with multiple --pr flags round-trips all entries."""
        import release_prs_sidecar as sidecar
        sidecar_path = str(tmp_path / "prs.json")
        rc = sidecar.main([
            "write",
            "--sidecar", sidecar_path,
            "--pr", "alpha:42:https://gh.com/42:feat",
            "--pr", "beta:7:https://gh.com/7:feat",
        ])
        assert rc == 0

        import json
        data = json.loads((tmp_path / "prs.json").read_text())
        assert len(data["prs"]) == 2
        repos = {e["repo"] for e in data["prs"]}
        assert repos == {"alpha", "beta"}

    def test_cli_read_prints_json(self, tmp_path: Path, capsys) -> None:
        """CLI read subcommand prints the sidecar JSON to stdout."""
        import release_prs_sidecar as sidecar
        import json

        # First write via library
        sidecar_path = tmp_path / "prs.json"
        sidecar.write(sidecar_path, [{"repo": "r", "pr_number": "1", "url": "u", "branch": "b"}])

        rc = sidecar.main(["read", "--sidecar", str(sidecar_path)])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["prs"][0]["repo"] == "r"

    def test_cli_read_nonexistent_returns_nonzero(self, tmp_path: Path) -> None:
        """CLI read on a nonexistent sidecar exits nonzero."""
        import release_prs_sidecar as sidecar
        rc = sidecar.main(["read", "--sidecar", str(tmp_path / "missing.json")])
        assert rc != 0, "CLI read on a missing sidecar must return nonzero exit code"

    def test_cli_write_malformed_pr_format_returns_nonzero(self, tmp_path: Path) -> None:
        """CLI write with a malformed --pr value exits nonzero."""
        import release_prs_sidecar as sidecar
        rc = sidecar.main([
            "write",
            "--sidecar", str(tmp_path / "prs.json"),
            "--pr", "malformed-no-colons",
        ])
        assert rc != 0, "CLI write with malformed --pr must return nonzero exit code"

    def test_cli_write_produces_mode_0600_file(self, tmp_path: Path) -> None:
        """CLI write must preserve the 0o600 mode posture."""
        import os, stat
        import release_prs_sidecar as sidecar
        sidecar_path = tmp_path / "prs.json"
        sidecar.main([
            "write",
            "--sidecar", str(sidecar_path),
            "--pr", "x:1:https://gh.com/1:main",
        ])
        mode = stat.S_IMODE(os.stat(sidecar_path).st_mode)
        assert mode == 0o600, f"CLI write must produce 0o600 file, got 0o{mode:o}"


# ---------------------------------------------------------------------------
# I-1 / M-3: wait_for_actionable.py must NOT define --scripts-dir
# ---------------------------------------------------------------------------

class TestWaitForActionableNoScriptsDir:
    """I-1: wait_for_actionable.py must not define --scripts-dir (it never existed)."""

    def test_wait_for_actionable_has_no_scripts_dir_flag(self) -> None:
        """wait_for_actionable.py argparse must NOT include --scripts-dir."""
        mod = _load_module("wait_for_actionable.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None
        flags = _parser_option_strings(parser)
        assert "--scripts-dir" not in flags, (
            "wait_for_actionable.py must NOT define --scripts-dir — this flag never existed "
            "and agent invocations that pass it will crash with argparse exit 2"
        )


# ---------------------------------------------------------------------------
# I-1: watch-pr.md must NOT pass --scripts-dir to wait_for_actionable.py
# ---------------------------------------------------------------------------

class TestWatchPrNoScriptsDirInvocation:
    """I-1: watch-pr.md must not pass --scripts-dir to wait_for_actionable.py."""

    def test_watch_pr_wait_invocation_has_no_scripts_dir(self) -> None:
        """watch-pr.md wait_for_actionable.py invocation must not pass --scripts-dir."""
        if not WATCH_PR_MD.exists():
            pytest.skip("watch-pr.md not yet present")
        text = WATCH_PR_MD.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines() if "wait_for_actionable" in ln]
        for line in lines:
            assert "--scripts-dir" not in line, (
                f"watch-pr.md passes --scripts-dir to wait_for_actionable.py — "
                f"that flag does not exist; drop it:\n  {line}"
            )


# ---------------------------------------------------------------------------
# I-2 / M-3: watch-pr.md must pass --toml to merge_prs.py
# ---------------------------------------------------------------------------

class TestWatchPrMergePrsTOML:
    """I-2: watch-pr.md must pass --toml to merge_prs.py to unlock multi-PR merge."""

    def _merge_prs_invocation_blocks(self, text: str) -> list[str]:
        """Return each contiguous bash block that contains a merge_prs invocation.

        A block is all lines between ``` fences that reference merge_prs.py.
        """
        blocks: list[str] = []
        in_fence = False
        current: list[str] = []
        for line in text.splitlines():
            if line.strip().startswith("```"):
                if in_fence:
                    block = "\n".join(current)
                    if "merge_prs" in block:
                        blocks.append(block)
                    current = []
                    in_fence = False
                else:
                    in_fence = True
                    current = []
            elif in_fence:
                current.append(line)
        return blocks

    def test_watch_pr_merge_invocation_has_toml_flag(self) -> None:
        """watch-pr.md merge_prs.py invocation must include --toml."""
        if not WATCH_PR_MD.exists():
            pytest.skip("watch-pr.md not yet present")
        text = WATCH_PR_MD.read_text(encoding="utf-8")
        blocks = self._merge_prs_invocation_blocks(text)
        assert blocks, "watch-pr.md must contain a bash block with a merge_prs.py invocation"
        for block in blocks:
            assert "--toml" in block, (
                f"watch-pr.md merge_prs.py invocation must pass --toml <group_toml_path> — "
                f"without it _load_merge_order(None) returns None and multi-PR merge is blocked "
                f"regardless of config:\n{block}"
            )


# ---------------------------------------------------------------------------
# I-3 / M-3: watch-pr.md must thread --review-bot-login into wait_for_actionable.py
# ---------------------------------------------------------------------------

class TestWatchPrReviewBotLogin:
    """I-3: watch-pr.md must pass --review-bot-login to wait_for_actionable.py when configured."""

    def test_watch_pr_documents_review_bot_login_flag(self) -> None:
        """watch-pr.md must mention --review-bot-login for the wait_for_actionable call."""
        if not WATCH_PR_MD.exists():
            pytest.skip("watch-pr.md not yet present")
        text = WATCH_PR_MD.read_text(encoding="utf-8")
        assert "--review-bot-login" in text, (
            "watch-pr.md must document passing --review-bot-login to wait_for_actionable.py — "
            "without it the review action never fires even when a bot is configured (I-3)"
        )

    def test_wait_for_actionable_has_review_bot_login_flag(self) -> None:
        """wait_for_actionable.py must define --review-bot-login (confirms I-3 is wirable)."""
        mod = _load_module("wait_for_actionable.py")
        assert mod is not None
        parser = _build_parser(mod)
        assert parser is not None
        flags = _parser_option_strings(parser)
        assert "--review-bot-login" in flags, (
            "wait_for_actionable.py must define --review-bot-login so the agent can pass it"
        )


# ---------------------------------------------------------------------------
# M-1: watch-pr.md must pass 3-part repo:pr:member_name to merge_prs.py
# ---------------------------------------------------------------------------

class TestWatchPrMergePrsPairFormat:
    """M-1: watch-pr.md merge_prs.py invocations must use the 3-part pair format."""

    def test_watch_pr_mentions_member_name_in_merge_pairs(self) -> None:
        """watch-pr.md must document that merge_prs.py pairs include member_name."""
        if not WATCH_PR_MD.exists():
            pytest.skip("watch-pr.md not yet present")
        text = WATCH_PR_MD.read_text(encoding="utf-8")
        # The agent must communicate the 3-part format <repo_path>:<pr_number>:<member_name>
        assert "member_name" in text or ":member_name" in text or "<member_name>" in text, (
            "watch-pr.md must document the 3-part pair format "
            "<repo_path>:<pr_number>:<member_name> for merge_prs.py — "
            "the 2-part form causes member_name to be derived from the worktree basename "
            "which may not match manifest members[].name, silently breaking merge_order (M-1)"
        )


# ---------------------------------------------------------------------------
# M-3 (catch-all): agent invocation flags vs script parsers
# ---------------------------------------------------------------------------

class TestAgentScriptFlagFidelity:
    """M-3 catch-all: flags that agents document must actually exist in the scripts."""

    _KNOWN_SKIP = {
        # SCRIPTS_DIR placeholder — not a real flag
        "<SCRIPTS_DIR>",
    }

    def _check_agent_invocations(self, agent_path: Path) -> list[str]:
        """Return a list of violation strings (empty == pass)."""
        if not agent_path.exists():
            return []

        text = agent_path.read_text(encoding="utf-8")
        violations: list[str] = []

        for line in _script_invocations(text):
            script_name = _script_name_from_line(line)
            if not script_name:
                continue
            script_path = SCRIPTS_DIR / script_name
            if not script_path.exists():
                continue

            mod = _load_module(script_name)
            if mod is None:
                continue
            parser = _build_parser(mod)
            if parser is None:
                continue
            known_flags = _parser_option_strings(parser)

            for flag in _flags_on_line(line):
                if flag in self._KNOWN_SKIP:
                    continue
                if flag not in known_flags:
                    violations.append(
                        f"{agent_path.name}: line invokes '{script_name}' with '{flag}' "
                        f"which is NOT in the script's argparse\n  line: {line}"
                    )
        return violations

    def test_pr_updater_invocations_match_script_parsers(self) -> None:
        """All flags in pr-updater.md script invocations must exist in the scripts."""
        violations = self._check_agent_invocations(PR_UPDATER_MD)
        assert not violations, "\n".join(violations)

    def test_watch_pr_invocations_match_script_parsers(self) -> None:
        """All flags in watch-pr.md script invocations must exist in the scripts."""
        violations = self._check_agent_invocations(WATCH_PR_MD)
        assert not violations, "\n".join(violations)
