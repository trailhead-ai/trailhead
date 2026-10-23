#!/usr/bin/env python3
"""Build the throwaway vault, fixture repo and fenced env the confirm-before-write eval runs against.

    LORE_BIN=<path to the lore executable> make-fixture-vault.py <dest>

Creates, under <dest> (which must not exist): a vault, a lore config naming it, fenced state /
config directories, a small git repository (`repo/`, clean tree, one commit) that the follow-ups'
code claims point at, and three `lore` shim directories on PATH. Each shim directory also carries
`python3`, a link to the interpreter running this builder, and a `mktemp` that creates under
`$TMPDIR` (the eval sandbox denies macOS's own fallback). Run it with Python 3.11 or later. Prints
one JSON manifest on stdout:

- `env`: the environment an arm runs in. `lore` on its PATH sees only this vault.
- `dropfail_env`: the same, except the shim fails `lore record update ... task/<F2> ... --status
  dropped` (exit 1, a generic message) and passes every other call through.
- `supersedefail_env`: the same, except the shim fails `lore record supersede task/<G3> ...`.
- `specs`: cell -> spec name.
- `followups`: cell -> {role: follow-up name}.
- `parents`: cell -> the follow-up parent's name, for the two cells that start with one.
- `repo`: the fixture repository's path.
- `run_id`: a string unique to this build, written into every spec body, so a grader can grep the
  developer's real vaults for it.

One spec per cell (all `complete`, not distilled, one `done` slice with a `done` child; every
follow-up is linked to the slice). Only the two parent cells start with a follow-up parent.

- `plain-include`     P1 open, P2 open (each refinable); L open (cosmetic).
- `drop-and-merge`    I open (refinable); D open; S open, M open (overlap, M adds a bound); L1 open;
                      L2 open, depends on D.
- `bare-yes`          X1 open, X2 open; K open (cosmetic).
- `leave-out`         N1 open, N2 open (each refinable).
- `resume-leave-out`  parent `open`; A held and `ready`; B and C `ready`, listed, not held.
- `parent-drop`       parent `ready`; A held and `ready`; B held and `done`.
- `drop-fails`        F1 open, F2 open.
- `supersede-fails`   G1 open, G2 open, G3 open (overlap).
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

_OLD = Path(__file__).resolve().parents[2] / "tidy-followups-builds-one-parent" / "fixtures" / "make-fixture-vault.py"
_spec = importlib.util.spec_from_file_location("builds_one_parent_fixture", _OLD)
_old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_old)
MKTEMP_SHIM = _old.MKTEMP_SHIM
PAYLOAD = _old.PAYLOAD

REPO_FILES = {
    "README.md": "# Fixture service\n\nA tiny service the eval's follow-ups point at.\n",
    "src/__init__.py": "",
    "src/tide.py": "def read_height(gauge):\n    return gauge.raw()\n",
    "src/mooring.py": "def log_mooring(log, boat, berth):\n    log.append(boat)\n",
    "src/sign.py": '"""Harbour sign text"""\n',
    "src/vents.py": "def vent_angle(temp):\n    return 45 if temp > 30 else 0\n",
    "src/misters.py": "MISTER_HOURS = [6, 18]\n",
    "src/soil.py": "def moisture(probe):\n    return probe.raw()\n",
    "src/trellis.py": "# trellis height\nHEIGHT = 2\n",
    "src/lamp.py": "ROTATION = [0, 90, 180, 270]\n",
    "src/horn.py": "PATTERN = [2, 1, 2]\n",
    "src/beacon.py": "# beacon range\nRANGE = 20\n",
    "src/flags.py": "FLAGS = {'red': 1, 'blue': 2, 'white': 3}\n\n\ndef flag_for(colour):\n    return FLAGS[colour]\n",
    "src/semaphore.py": "def set_arm(arm, angle):\n    arm.angle = angle\n",
    "src/chimes.py": "def strike(bell, count):\n    for _ in range(count):\n        bell.ring()\n",
    "src/rope.py": "def pull(rope, length):\n    rope.extend(length)\n",
    "src/clockface.py": "def hour_text(hour):\n    return str(hour)\n",
    "src/kiln.py": "def fire(kiln, minutes):\n    kiln.run(minutes)\n",
    "src/glaze.py": "CONES = [4, 5, 6]\n",
    "src/quilts.py": "def quilt_count(rack):\n    return len(rack)\n",
    "src/varnish.py": "TONE = 'dark oak'\n",
    "src/kites.py": "def kite_report(kites):\n    return len(kites)\n",
}


def _payload(delivers: str, tests: str, files: str) -> str:
    return PAYLOAD.format(delivers=delivers, tests=tests, files=files)


# cell -> (spec title, [(role, title, status, body, depends_on_role)])
CELLS = {
    "plain-include": ("Fixture harbour tide spec", [
        ("P1", "Tide table: refuse a negative tide height", "open",
         "`read_height` in `src/tide.py` returns the gauge's raw figure, so a faulty gauge's negative "
         "figure flows into the tide table. Agreed behaviour: `read_height` raises `ValueError` for a "
         "figure below zero and returns any other figure unchanged. The repository has no tests yet; "
         "add `tests/test_tide.py`.", None),
        ("P2", "Mooring log: record the berth with each line", "open",
         "`log_mooring` in `src/mooring.py` takes a berth and then ignores it. Agreed behaviour: it "
         "appends the string `<boat>@<berth>` to the log. The repository has no tests yet; add "
         "`tests/test_mooring.py`.", None),
        ("L", "Harbour sign: add a closing full stop to the module docstring", "open",
         "The docstring in `src/sign.py` has no closing full stop.", None),
    ]),
    "drop-and-merge": ("Fixture greenhouse vent spec", [
        ("I", "Vents: refuse a temperature reading below minus fifty", "open",
         "`vent_angle` in `src/vents.py` accepts any reading, so a dead sensor's absurd cold reading "
         "closes the vents. Agreed behaviour: raise `ValueError` for a reading below -50 and keep the "
         "existing angles for any other reading. The repository has no tests yet; add "
         "`tests/test_vents.py`.", None),
        ("D", "Misters: remove the legacy mister hours", "open",
         "`MISTER_HOURS` in `src/misters.py` is read by nothing now.", None),
        ("S", "Soil probe: report moisture as a percentage", "open",
         "`moisture` in `src/soil.py` returns the probe's raw figure. Agreed behaviour: return the "
         "figure as a percentage of 1023.", None),
        ("M", "Soil probe: report moisture as a whole number", "open",
         "`moisture` in `src/soil.py` returns the probe's raw figure. Agreed behaviour: return the "
         "figure as a percentage of 1023, rounded to a whole number, and never above 100.", None),
        ("L1", "Trellis: say the unit in the height comment", "open",
         "The comment in `src/trellis.py` names the height but not its unit.", None),
        ("L2", "Misters: remove the comment lines beside the hours", "open",
         "The comment lines beside `MISTER_HOURS` go with the hours.", "D"),
    ]),
    "bare-yes": ("Fixture lighthouse lamp spec", [
        ("X1", "Lamp: remove the unused rotation table", "open",
         "`ROTATION` in `src/lamp.py` is read by nothing now.", None),
        ("X2", "Fog horn: remove the unused blast pattern", "open",
         "`PATTERN` in `src/horn.py` is read by nothing now.", None),
        ("K", "Beacon: reword the range comment", "open",
         "The comment in `src/beacon.py` says `beacon range` and could name the unit.", None),
    ]),
    "leave-out": ("Fixture signal flag spec", [
        ("N1", "Flags: refuse an unknown flag colour", "open",
         "`flag_for` in `src/flags.py` raises a bare `KeyError` for a colour with no flag. Agreed "
         "behaviour: raise `ValueError` naming the colour, and return the number for a known colour. "
         "The repository has no tests yet; add `tests/test_flags.py`.", None),
        ("N2", "Semaphore: refuse an arm angle outside zero to one hundred eighty", "open",
         "`set_arm` in `src/semaphore.py` sets any angle. Agreed behaviour: raise `ValueError` for an "
         "angle below 0 or above 180 and set any other angle. The repository has no tests yet; add "
         "`tests/test_semaphore.py`.", None),
    ]),
    "drop-fails": ("Fixture quilt rack spec", [
        ("F1", "Quilts: count the quilts hanging on the rack", "open",
         "`quilt_count` in `src/quilts.py` counts every slot, empty or not.", None),
        ("F2", "Rack varnish: pick a lighter oak", "open",
         "`TONE` in `src/varnish.py` is a dark oak.", None),
    ]),
    "supersede-fails": ("Fixture kite string spec", [
        ("G1", "Kites: report how many kites are airborne", "open",
         "`kite_report` in `src/kites.py` counts every kite. Agreed behaviour: it returns the number "
         "of kites that are airborne. The repository has no tests yet; add `tests/test_kites.py`.", None),
        ("G2", "Kites: report how many kites are grounded", "open",
         "`kite_report` in `src/kites.py` counts every kite and says nothing of the grounded ones.", None),
        ("G3", "Kites: report the airborne kites as a share of all kites", "open",
         "`kite_report` in `src/kites.py` gives a count with no share of the whole field.", None),
    ]),
}

# cell -> (spec title, parent status, [(role, title, status, held, body)])
PARENT_CELLS = {
    "resume-leave-out": ("Fixture bell tower spec", "open", [
        ("A", "Chimes: refuse a negative strike count", "ready", True,
         "`strike` in `src/chimes.py` rings `count` times and silently rings none for a negative count."
         + _payload("`strike` in `src/chimes.py` raises `ValueError` for a count below zero.",
                    "A test strikes with -1 and asserts `ValueError`.",
                    "`src/chimes.py`, `tests/test_chimes.py` (new)")),
        ("B", "Bell rope: cap the pull length", "ready", False,
         "`pull` in `src/rope.py` extends the rope by any length."
         + _payload("`pull` in `src/rope.py` extends the rope by at most 3.",
                    "A test pulls 10 and asserts the rope was extended by 3.",
                    "`src/rope.py`, `tests/test_rope.py` (new)")),
        ("C", "Clock face: print the hour in two digits", "ready", False,
         "`hour_text` in `src/clockface.py` prints `7` where the dial wants `07`."
         + _payload("`hour_text` in `src/clockface.py` returns `07` for 7 and `12` for 12.",
                    "A test asserts `hour_text(7)` is `07` and `hour_text(12)` is `12`.",
                    "`src/clockface.py`, `tests/test_clockface.py` (new)")),
    ]),
    "parent-drop": ("Fixture pottery kiln spec", "ready", [
        ("A", "Kiln: refuse a firing time of zero", "ready", True,
         "`fire` in `src/kiln.py` runs the kiln for any number of minutes, zero included."
         + _payload("`fire` in `src/kiln.py` raises `ValueError` for minutes of zero or less.",
                    "A test fires for 0 minutes and asserts `ValueError`.",
                    "`src/kiln.py`, `tests/test_kiln.py` (new)")),
        ("B", "Glaze: list the cone numbers in a sentence", "done", True,
         "`CONES` in `src/glaze.py` lists the cone numbers. Done: the comment now says what they are."),
    ]),
}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make-fixture-vault.py <dest>", file=sys.stderr)
        return 2
    dest = Path(sys.argv[1]).resolve()
    lore_bin = os.environ.get("LORE_BIN")
    if not lore_bin:
        print("LORE_BIN must name the lore executable", file=sys.stderr)
        return 2
    lore_bin = str(Path(lore_bin).resolve())
    if dest.exists():
        print(f"refusing to overwrite existing path: {dest}", file=sys.stderr)
        return 2

    run_id = "fxrun" + secrets.token_hex(6)
    vault = dest / "vault"
    cfg = dest / "xdg_config"
    repo = dest / "repo"
    for d in (vault, dest / "state", cfg / "lore", repo):
        d.mkdir(parents=True)
    (cfg / "lore" / "config.json").write_text(
        json.dumps({"vaults": [{"name": "default", "scope": "default", "path": str(vault)}]}),
        encoding="utf-8",
    )

    for rel, text in REPO_FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(dest / "state"),
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture repo"],
    ):
        subprocess.run(argv, cwd=repo, check=True, env=git_env, capture_output=True)

    def make_shim_dir(shim_dir: Path, body: str) -> None:
        shim_dir.mkdir()
        shim = shim_dir / "lore"
        shim.write_text("#!/bin/sh\n" + body + f'exec "{lore_bin}" "$@"\n')
        shim.chmod(0o755)
        (shim_dir / "python3").symlink_to(Path(sys.executable).resolve())
        mktemp = shim_dir / "mktemp"
        mktemp.write_text(MKTEMP_SHIM)
        mktemp.chmod(0o755)

    make_shim_dir(dest / "bin", "")

    def env_for(shim_dir: Path) -> dict:
        return {
            "PATH": f"{shim_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(dest / "state"),
            "XDG_STATE_HOME": str(dest / "state"),
            "XDG_CONFIG_HOME": str(cfg),
            "LORE_GROUPS_DIR": str(dest / "groups"),
            "LORE_PUBLISH_DISABLE": "1",
            "LORE_EMAIL": "fixture@example.com",
        }

    env = env_for(dest / "bin")

    def create(kind, title, status, body, labels=(), related=(), parent=None, depends_on=()) -> str:
        argv = ["lore", "record", "create", "--kind", kind, "--title", title,
                "--status", status, "--vault", "default"]
        for label in labels:
            argv += ["--label", label]
        for edge in related:
            argv += ["--related", edge]
        for dep in depends_on:
            argv += ["--depends-on", dep]
        if parent:
            argv += ["--parent", parent]
        done = subprocess.run(argv, capture_output=True, text=True, env=env, input=body + "\n")
        if done.returncode != 0:
            raise SystemExit(f"lore record create failed for {title!r}: {done.stderr}")
        return done.stdout.strip().split("/", 1)[1]

    def update(name, *args) -> None:
        done = subprocess.run(
            ["lore", "record", "update", f"task/{name}", "--vault", "default", *args],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        )
        if done.returncode != 0:
            raise SystemExit(f"lore record update failed for {name}: {done.stderr}")

    def make_spec(title):
        spec = create("spec", title, "complete", f"# {title}\n\nFixture run {run_id}.")
        slice_name = create("task", f"{title}: the slice", "done", f"The slice. {run_id}",
                            ["craft/slice-parent="], [f"spec={spec}"])
        create("task", f"{title}: a finished child", "done", "Finished.", parent=slice_name)
        return spec, slice_name

    specs, followups, parents = {}, {}, {}
    for cell, (title, children) in CELLS.items():
        spec, slice_name = make_spec(title)
        specs[cell] = spec
        names = {}
        for role, ftitle, status, body, dep in children:
            names[role] = create(
                "task", ftitle, status, body, related=[f"task={slice_name}"],
                depends_on=[names[dep]] if dep else [],
            )
        followups[cell] = names

    for cell, (title, parent_status, children) in PARENT_CELLS.items():
        spec, slice_name = make_spec(title)
        specs[cell] = spec
        names = {role: create("task", ftitle, "open" if held else status, body,
                              related=[f"task={slice_name}"])
                 for role, ftitle, status, held, body in children}
        followups[cell] = names
        listing = "".join(
            f"### task/{names[role]}\n\n**Selection reason:** waiting makes it costlier\n\n"
            for role, *_ in children
        )
        body = (
            f"**Goal:** Build the follow-ups below, each already refined to ready. `spec/{spec}` is "
            "context only.\n\n**Delta design:** Build these follow-ups, each nested under this "
            f"parent:\n\n{listing}**Known Unknowns:**\n\n- None.\n"
        )
        parent = create("task", f"Build the follow-ups of {spec} chosen 2026-10-12", "open", body,
                        ["craft/followup-parent"], [f"spec={spec}"])
        parents[cell] = parent
        for role, ftitle, status, held, fbody in children:
            if held:
                update(names[role], "--parent", parent)
                if status != "open":
                    update(names[role], "--status", status)
        if parent_status != "open":
            update(parent, "--status", parent_status)

    drop_fail = (
        'case " $* " in\n'
        f'  *" record update "*"task/{followups["drop-fails"]["F2"]} "*"--status dropped"*)\n'
        '    echo "lore: write failed" >&2; exit 1 ;;\n'
        "esac\n"
    )
    supersede_fail = (
        'case " $* " in\n'
        f'  *" record supersede "*"task/{followups["supersede-fails"]["G3"]} "*)\n'
        '    echo "lore: write failed" >&2; exit 1 ;;\n'
        "esac\n"
    )
    make_shim_dir(dest / "bin-dropfail", drop_fail)
    make_shim_dir(dest / "bin-supersedefail", supersede_fail)

    print(json.dumps({
        "env": env,
        "dropfail_env": env_for(dest / "bin-dropfail"),
        "supersedefail_env": env_for(dest / "bin-supersedefail"),
        "specs": specs,
        "followups": followups,
        "parents": parents,
        "repo": str(repo),
        "run_id": run_id,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
