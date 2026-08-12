"""Tests for trailhead/pathint.py — shim dir + brew-style shellenv.

trailhead does not edit the shell rc; it builds a shim dir and `shellenv`
prints the export lines the user adds to their profile.
"""

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from trailhead.pathint import (
    PathIntegrationError,
    ShimDenylistError,
    create_shims,
    detect_shell,
    repo_root,
    resolve_shim_dir,
    shellenv_lines,
)

_FISH_BIN = shutil.which("fish")
_HAS_FISH = _FISH_BIN is not None


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TRAILHEAD_STATE_DIR": str(tmp_path)}


# ---------------------------------------------------------------------------
# resolve_shim_dir
# ---------------------------------------------------------------------------


class TestResolveShimDir:
    def test_under_state_dir(self, tmp_path):
        assert resolve_shim_dir(env=_env(tmp_path)) == tmp_path / "bin"

    def test_pure_does_not_create(self, tmp_path):
        resolve_shim_dir(env=_env(tmp_path))
        assert not (tmp_path / "bin").exists()


# ---------------------------------------------------------------------------
# create_shims
# ---------------------------------------------------------------------------


class TestCreateShims:
    def test_creates_one_shim_per_tool(self, tmp_path):
        tools = {
            "camp": Path("/repo/tools/camp/bin/camp"),
            "lore": Path("/repo/tools/lore/bin/lore"),
        }
        res = create_shims(tools, "/repo", env=_env(tmp_path))
        assert (res.shim_dir / "camp").is_file()
        assert (res.shim_dir / "lore").is_file()
        assert set(res.shims) == {"camp", "lore"}

    def test_shim_is_executable(self, tmp_path):
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/repo", env=_env(tmp_path))
        mode = (res.shim_dir / "camp").stat().st_mode
        assert mode & 0o100  # owner-executable

    def test_shim_hardcodes_trailhead_root_and_exec(self, tmp_path):
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/the/root", env=_env(tmp_path))
        content = (res.shim_dir / "camp").read_text()
        assert 'export TRAILHEAD_ROOT="/the/root"' in content
        assert 'exec "/repo/bin/camp" "$@"' in content

    def test_denylisted_name_rejected(self, tmp_path):
        with pytest.raises(ShimDenylistError):
            create_shims({"python3": Path("/x")}, "/repo", env=_env(tmp_path))

    def test_only_selected_tools_shimmed(self, tmp_path):
        # The shim dir's contents encode the selection (--no-lore omits lore).
        res = create_shims({"camp": Path("/repo/bin/camp")}, "/repo", env=_env(tmp_path))
        assert (res.shim_dir / "camp").exists()
        assert not (res.shim_dir / "lore").exists()

    def test_rejects_unsafe_trailhead_root(self, tmp_path):
        with pytest.raises(PathIntegrationError):
            create_shims(
                {"camp": Path("/repo/bin/camp")}, '/repo"; evil', env=_env(tmp_path)
            )

    def test_rejects_unsafe_bin_path(self, tmp_path):
        with pytest.raises(PathIntegrationError):
            create_shims(
                {"camp": Path('/repo/bin/camp"; evil')}, "/repo", env=_env(tmp_path)
            )

    def test_rejects_unsafe_shim_name(self, tmp_path):
        with pytest.raises(PathIntegrationError):
            create_shims(
                {'camp"; evil': Path("/repo/bin/camp")}, "/repo", env=_env(tmp_path)
            )


# ---------------------------------------------------------------------------
# detect_shell
# ---------------------------------------------------------------------------


class TestDetectShell:
    @pytest.mark.parametrize(
        "shell,expected",
        [
            ("/usr/bin/fish", "fish"),
            ("/bin/zsh", "zsh"),
            ("/bin/bash", "bash"),
            ("/usr/local/bin/weird", "bash"),
            ("", "bash"),
        ],
    )
    def test_detects_from_shell_env(self, shell, expected):
        assert detect_shell({"SHELL": shell}) == expected


# ---------------------------------------------------------------------------
# shellenv_lines
# ---------------------------------------------------------------------------


class TestShellenv:
    def test_posix_exports_root_and_path(self, tmp_path):
        out = shellenv_lines(shell="zsh", env=_env(tmp_path), trailhead_root="/repo")
        assert 'export TRAILHEAD_ROOT="/repo"' in out
        assert f'export PATH="{tmp_path / "bin"}:$PATH"' in out

    def test_bash_same_as_zsh(self, tmp_path):
        z = shellenv_lines(shell="zsh", env=_env(tmp_path), trailhead_root="/repo")
        b = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        assert z == b

    def test_fish_uses_set_gx_and_fish_add_path(self, tmp_path):
        out = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        assert 'set -gx TRAILHEAD_ROOT "/repo"' in out
        assert f'fish_add_path "{tmp_path / "bin"}"' in out

    def test_shell_detected_from_env_when_unspecified(self, tmp_path):
        env = {**_env(tmp_path), "SHELL": "/usr/bin/fish"}
        out = shellenv_lines(env=env, trailhead_root="/repo")
        assert "fish_add_path" in out

    def test_default_root_is_repo(self, tmp_path):
        # No trailhead_root → the repo containing pathint.py.
        out = shellenv_lines(shell="zsh", env=_env(tmp_path))
        assert "TRAILHEAD_ROOT=" in out
        assert "trailhead" in out

    def test_default_root_matches_shared_helper(self, tmp_path):
        out = shellenv_lines(shell="zsh", env=_env(tmp_path))
        assert f'export TRAILHEAD_ROOT="{repo_root()}"' in out

    def test_shared_helper_uses_resolve(self):
        assert repo_root() == repo_root().resolve()


# ---------------------------------------------------------------------------
# trailhead() shell function
# ---------------------------------------------------------------------------


def _fake_repo_with_bin(tmp_path: Path, *, executable: bool = True, directory: bool = False) -> Path:
    root = tmp_path / "fakerepo"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    trailhead_bin = bin_dir / "trailhead"
    if directory:
        trailhead_bin.mkdir()
    else:
        trailhead_bin.write_text("#!/usr/bin/env python3\n")
        mode = 0o755 if executable else 0o644
        trailhead_bin.chmod(mode)
    return root


class TestTrailheadFunction:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_emitted_when_bin_trailhead_is_executable(self, tmp_path, shell):
        root = _fake_repo_with_bin(tmp_path)
        out = shellenv_lines(shell=shell, env=_env(tmp_path), trailhead_root=str(root))
        target = str(root / "bin" / "trailhead")
        if shell == "fish":
            assert "function trailhead" in out
            assert f'"{target}" $argv' in out
        else:
            assert "trailhead() {" in out
            assert f'"{target}" "$@"' in out

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_not_emitted_when_bin_trailhead_missing(self, tmp_path, shell):
        root = tmp_path / "norepo"
        root.mkdir()
        out = shellenv_lines(shell=shell, env=_env(tmp_path), trailhead_root=str(root))
        assert "trailhead()" not in out
        assert "function trailhead" not in out
        # Remaining output stays eval-valid (root/PATH lines still present).
        assert "TRAILHEAD_ROOT" in out

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_not_emitted_when_bin_trailhead_not_executable(self, tmp_path, shell):
        root = _fake_repo_with_bin(tmp_path, executable=False)
        out = shellenv_lines(shell=shell, env=_env(tmp_path), trailhead_root=str(root))
        assert "trailhead()" not in out
        assert "function trailhead" not in out

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_not_emitted_when_bin_trailhead_is_a_directory(self, tmp_path, shell):
        root = _fake_repo_with_bin(tmp_path, directory=True)
        out = shellenv_lines(shell=shell, env=_env(tmp_path), trailhead_root=str(root))
        assert "trailhead()" not in out
        assert "function trailhead" not in out

    def test_invokes_the_real_binary(self, tmp_path):
        root = _fake_repo_with_bin(tmp_path)
        marker = tmp_path / "ran.txt"
        (root / "bin" / "trailhead").write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s" "$*" > {marker}\n'
        )
        (root / "bin" / "trailhead").chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root=str(root))
        script = f'{wrapper}\ntrailhead doctor --json\n'
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert marker.read_text() == "doctor --json"


# ---------------------------------------------------------------------------
# Injection hardening
# ---------------------------------------------------------------------------


class TestInjectionHardening:
    @pytest.mark.parametrize("bad_char", ['"', "`", "$", "\n", "\\"])
    def test_rejects_unsafe_root(self, tmp_path, bad_char):
        with pytest.raises(PathIntegrationError):
            shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root=f"/repo{bad_char}evil")

    @pytest.mark.parametrize("bad_char", ['"', "`", "$", "\n", "\\"])
    def test_rejects_unsafe_shim_dir(self, tmp_path, bad_char):
        env = {"TRAILHEAD_STATE_DIR": str(tmp_path) + bad_char + "evil"}
        with pytest.raises(PathIntegrationError):
            shellenv_lines(shell="bash", env=env, trailhead_root="/repo")

    @pytest.mark.parametrize("bad_char", ['"', "`", "$", "\n", "\\"])
    def test_rejects_unsafe_root_in_trailhead_function_path(self, tmp_path, bad_char):
        # The bin/trailhead check itself passes (real file at a safe path),
        # but the root used to build the function body is unsafe.
        safe_root = _fake_repo_with_bin(tmp_path)
        unsafe_root = str(safe_root) + bad_char + "evil"
        with pytest.raises(PathIntegrationError):
            shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root=unsafe_root)


# ---------------------------------------------------------------------------
# camp() cd-wrapper
# ---------------------------------------------------------------------------


class TestCampWrapperPosix:
    """The bash/zsh `camp()` wrapper appended to shellenv output."""

    @pytest.fixture(params=["bash", "zsh"])
    def out(self, request, tmp_path):
        return shellenv_lines(shell=request.param, env=_env(tmp_path), trailhead_root="/repo")

    def test_defines_a_camp_function(self, out):
        assert "camp() {" in out

    def test_uses_command_camp_to_avoid_path_recursion(self, out):
        # Both the capture (new) and the passthrough branch must call `command camp`.
        assert 'command camp "$@"' in out

    def test_cds_only_on_the_intercepted_verbs(self, out):
        # new enters a workspace; remove/rm (both spellings — aliasing happens
        # inside the CLI, the wrapper sees the raw token) exit one; resume is a
        # separate two-line-contract branch below.
        assert 'case "$1" in' in out
        assert "new|remove|rm)" in out
        # One cd per intercepted branch (new|remove|rm, resume) — none in passthrough.
        assert out.count("cd -- ") == 2

    def test_cd_is_quote_safe(self, out):
        assert 'cd -- "$p"' in out

    def test_exports_marker_only_around_new(self, out):
        assert "CAMP_SHELL_INTEGRATION=1 command camp" in out

    def test_resume_is_intercepted_and_execs_line_two(self, out):
        assert "resume)" in out
        assert "eval " in out

    def test_exports_marker_around_resume(self, out):
        # The marker export must cover the resume invocation too, not just new.
        lines = out.splitlines()
        resume_idx = next(i for i, line in enumerate(lines) if "resume)" in line)
        block = "\n".join(lines[resume_idx : resume_idx + 6])
        assert "CAMP_SHELL_INTEGRATION=1 command camp" in block

    def test_does_not_spawn_a_subshell_function_body(self, out):
        # A `camp() ( … )` body would run the cd in a subshell and never reach the
        # parent shell. The function must be brace-bodied (current shell).
        assert "camp() (" not in out

    def test_keeps_the_existing_path_lines(self, out):
        assert 'export TRAILHEAD_ROOT="/repo"' in out
        assert "export PATH=" in out


class TestCampWrapperFish:
    """The fish `camp()` wrapper (separate body — fish syntax differs)."""

    @pytest.fixture
    def out(self, tmp_path):
        return shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")

    def test_defines_a_camp_function(self, out):
        assert "function camp" in out
        assert "\nend\n" in out or out.rstrip().endswith("end")

    def test_uses_command_camp_to_avoid_path_recursion(self, out):
        assert "command camp $argv" in out

    def test_cds_only_on_the_intercepted_verbs(self, out):
        assert 'switch "$argv[1]"' in out
        assert "case new remove rm" in out
        # One cd per intercepted branch (new/remove/rm, resume) — none in passthrough.
        assert out.count("cd -- ") == 2

    def test_cd_is_quote_safe(self, out):
        # fish cmd-sub splits on newlines, not spaces, so a one-line path is a
        # single-element list → `cd -- $p` is quote-safe.
        assert "cd -- $p" in out

    def test_exports_marker_with_function_scoped_set(self, out):
        # Must NOT use `env VAR=val command camp` (env execs a binary named
        # `command`); function-scoped `set -lx` is the validated form.
        assert "set -lx CAMP_SHELL_INTEGRATION 1" in out
        assert "env CAMP_SHELL_INTEGRATION" not in out

    def test_resume_is_intercepted_and_runs_line_two_via_sh(self, out):
        # NEVER fish-eval line 2 — hand it to sh -c so POSIX-shlex quoting stays
        # authoritative and fish-active syntax (`(...)`, `$var`) is never
        # reinterpreted.
        assert "case resume" in out
        assert "sh -c " in out

    def test_exports_marker_around_resume(self, out):
        lines = out.splitlines()
        resume_idx = next(i for i, line in enumerate(lines) if "case resume" in line)
        block = "\n".join(lines[resume_idx : resume_idx + 6])
        assert "set -lx CAMP_SHELL_INTEGRATION 1" in block

    def test_keeps_the_existing_path_lines(self, out):
        assert 'set -gx TRAILHEAD_ROOT "/repo"' in out
        assert "fish_add_path" in out


class TestCampWrapperBehavior:
    """Exercise the emitted bash wrapper end-to-end."""

    def test_space_path_cds_correctly(self, tmp_path):
        target = tmp_path / "work space"  # a directory whose path contains a space

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "new" ]; then\n'
            '  mkdir -p "$TARGET"\n'
            '  printf "%s\\n" "$TARGET"\n'
            "fi\n"
        )
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp new feat\n"
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"TARGET": str(target), "PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        # Last line of stdout is the cwd after the wrapper cd'd us in.
        assert proc.stdout.strip().splitlines()[-1] == str(target)

    def test_rm_cds_back_to_the_printed_repo_root(self, tmp_path):
        # `camp rm` from inside a workspace prints the group's first-member
        # repo_root on stdout; the wrapper must land the shell there.
        home = tmp_path / "repo home"  # spaces: cd must stay quote-safe
        home.mkdir()

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "rm" ] || [ "$1" = "remove" ]; then\n'
            '  printf "%s\\n" "$HOME_REPO"\n'
            "fi\n"
        )
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp rm\n"
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"HOME_REPO": str(home), "PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == str(home)

    def test_rm_with_empty_stdout_stays_put(self, tmp_path):
        # remove run OUTSIDE the workspace (or any no-path case) prints nothing
        # on stdout — the wrapper must not cd anywhere.
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_camp.chmod(0o755)

        start = tmp_path / "start"
        start.mkdir()
        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp rm --name other\n"
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(start),
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == str(start)

    def test_rm_failure_propagates_exit_and_stays_put(self, tmp_path):
        # A failed removal exits nonzero with empty stdout: the wrapper must
        # surface that exit code and leave the shell where it was.
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text("#!/usr/bin/env bash\nexit 3\n")
        fake_camp.chmod(0o755)

        start = tmp_path / "start"
        start.mkdir()
        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp rm\n"
            'printf "rc=%s\\n" "$?"\n'
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(start),
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.strip().splitlines()
        assert "rc=3" in lines
        assert lines[-1] == str(start)

    def test_other_verbs_pass_through_without_cd(self, tmp_path):
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(
            "#!/usr/bin/env bash\n"
            'printf "ran: %s\\n" "$*"\n'
        )
        fake_camp.chmod(0o755)

        start = tmp_path / "start"
        start.mkdir()
        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp list\n"
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(start),
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "ran: list" in proc.stdout
        # No cd happened — still in the starting dir.
        assert proc.stdout.strip().splitlines()[-1] == str(start)


# ---------------------------------------------------------------------------
# camp() cd-wrapper — resume (two-line machine contract)
# ---------------------------------------------------------------------------


def _fake_camp_resume_bash(target: Path, argv_line: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "resume" ]; then\n'
        f'  printf "%s\\n" "{target}"\n'
        f'  printf "%s\\n" {shlex.quote(argv_line)}\n'
        "fi\n"
    )


def _fake_camp_resume_recording_marker(target: Path, marker_file: Path) -> str:
    """A fake camp whose line-2 command records CAMP_SHELL_INTEGRATION as the
    RESUMED process sees it — the value a nested `camp resume` would inherit."""
    argv_line = (
        f'sh -c {shlex.quote(f"printf %s [${{CAMP_SHELL_INTEGRATION}}] > {shlex.quote(str(marker_file))}")}'
    )
    return _fake_camp_resume_bash(target, argv_line)


class TestCampWrapperResumeBehaviorBash:
    """Exercise the emitted bash resume branch end-to-end."""

    def test_the_marker_does_not_leak_into_the_resumed_process(self, tmp_path):
        """The marker exists to tell camp that a wrapper is listening for THIS
        invocation. If it survived into the resumed harness, a nested
        `camp resume` inside that session would pass the integration guard with
        no wrapper present and print its inert machine lines as if they worked."""
        target = tmp_path / "ws"
        target.mkdir()
        seen = tmp_path / "seen.txt"

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(_fake_camp_resume_recording_marker(target, seen))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nexport PATH="{fakebin}:$PATH"\ncamp resume alpha\n'
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert seen.read_text() == "[]"

    def test_resume_cds_and_invokes_the_argv_line(self, tmp_path):
        target = tmp_path / "work space"
        target.mkdir()

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        marker = tmp_path / "ran"
        fake_camp.write_text(_fake_camp_resume_bash(target, f"touch {shlex.quote(str(marker))}"))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = f"{wrapper}\nexport PATH=\"{fakebin}:$PATH\"\ncamp resume alpha\npwd\n"
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == str(target)
        assert marker.exists()

    def test_resume_nonzero_exit_propagates_without_cd_or_exec(self, tmp_path):
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text("#!/usr/bin/env bash\nexit 4\n")
        fake_camp.chmod(0o755)

        start = tmp_path / "start"
        start.mkdir()
        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'export PATH="{fakebin}:$PATH"\n'
            "camp resume alpha\n"
            'printf "rc=%s\\n" "$?"\n'
            "pwd\n"
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(start),
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.strip().splitlines()
        assert "rc=4" in lines
        assert lines[-1] == str(start)

    def test_resume_exports_marker(self, tmp_path):
        # camp's stdout is captured by the wrapper's $(...), so the marker check
        # writes to a side file instead of stdout.
        marker_file = tmp_path / "marker.txt"
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s" "$CAMP_SHELL_INTEGRATION" > {shlex.quote(str(marker_file))}\n'
            "exit 1\n"
        )
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nexport PATH="{fakebin}:$PATH"\ncamp resume alpha\n'
        subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert marker_file.read_text() == "1"

    def test_metacharacter_argv_line_executes_as_literal_arguments(self, tmp_path):
        # eval on line 2 is safe because line 2 is POSIX-shlex-quoted: metachars
        # inside a quoted token stay literal through eval's single parse pass.
        target = tmp_path / "ws"
        target.mkdir()
        marker = tmp_path / "out.txt"

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        payload = shlex.join(
            ["printf", "%s", "literal: $(whoami) `id` $HOME ${PATH}"]
        ) + f" > {shlex.quote(str(marker))}"
        fake_camp.write_text(_fake_camp_resume_bash(target, payload))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="bash", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nexport PATH="{fakebin}:$PATH"\ncamp resume alpha\n'
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert marker.read_text() == "literal: $(whoami) `id` $HOME ${PATH}"


@pytest.mark.skipif(not _HAS_FISH, reason="fish not installed")
class TestCampWrapperResumeBehaviorFish:
    """Exercise the emitted fish resume branch end-to-end."""

    def test_the_marker_does_not_leak_into_the_resumed_process(self, tmp_path):
        """Mirrors the bash guarantee: the marker covers the camp invocation only,
        never the harness the wrapper goes on to run."""
        target = tmp_path / "ws"
        target.mkdir()
        seen = tmp_path / "seen.txt"

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text(_fake_camp_resume_recording_marker(target, seen))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nset -gx PATH "{fakebin}" $PATH\ncamp resume alpha\n'
        proc = subprocess.run(
            [_FISH_BIN, "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert seen.read_text() == "[]"

    def test_resume_cds_and_invokes_the_argv_line(self, tmp_path):
        target = tmp_path / "work space"
        target.mkdir()

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        marker = tmp_path / "ran"
        fake_camp.write_text(_fake_camp_resume_bash(target, f"touch {shlex.quote(str(marker))}"))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nset -gx PATH "{fakebin}" $PATH\ncamp resume alpha\npwd\n'
        proc = subprocess.run(
            [_FISH_BIN, "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1] == str(target)
        assert marker.exists()

    def test_resume_nonzero_exit_propagates_without_cd_or_exec(self, tmp_path):
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        fake_camp.write_text("#!/usr/bin/env bash\nexit 4\n")
        fake_camp.chmod(0o755)

        start = tmp_path / "start"
        start.mkdir()
        wrapper = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        script = (
            f"{wrapper}\n"
            f'set -gx PATH "{fakebin}" $PATH\n'
            "camp resume alpha\n"
            'printf "rc=%s\\n" $status\n'
            "pwd\n"
        )
        proc = subprocess.run(
            [_FISH_BIN, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(start),
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.strip().splitlines()
        assert "rc=4" in lines
        assert lines[-1] == str(start)

    def test_metacharacter_argv_line_executes_as_literal_arguments(self, tmp_path):
        # fish MUST hand line 2 to `sh -c`, never eval it natively — fish-active
        # syntax like `(...)` and `$var` inside the quoted token would otherwise
        # be reinterpreted before sh ever sees it.
        target = tmp_path / "ws"
        target.mkdir()
        marker = tmp_path / "out.txt"

        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_camp = fakebin / "camp"
        payload = shlex.join(
            ["printf", "%s", "literal: $(whoami) `id` $HOME (echo hi)"]
        ) + f" > {shlex.quote(str(marker))}"
        fake_camp.write_text(_fake_camp_resume_bash(target, payload))
        fake_camp.chmod(0o755)

        wrapper = shellenv_lines(shell="fish", env=_env(tmp_path), trailhead_root="/repo")
        script = f'{wrapper}\nset -gx PATH "{fakebin}" $PATH\ncamp resume alpha\n'
        proc = subprocess.run(
            [_FISH_BIN, "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert marker.read_text() == "literal: $(whoami) `id` $HOME (echo hi)"
