#!/usr/bin/env python3
"""Build the throwaway vault, fixture repo and fenced env the drop-and-merge eval runs against.

    LORE_BIN=<path to the lore executable> make-fixture-vault.py <dest>

Creates, under <dest> (which must not exist): a vault, a lore config naming it, fenced
state / config directories, a small git repository (`repo/`, clean tree, one commit) that
the follow-ups' code claims point at, and three `lore` shim directories on PATH. Each shim
directory also carries `python3`, a link to the interpreter running this builder, so an
executable run by its `python3` shebang under the fenced PATH gets that interpreter rather
than macOS's older /usr/bin/python3, and a `mktemp` that creates under `$TMPDIR` (the eval
sandbox denies macOS's own fallback). Run it with Python 3.11 or later. Prints one JSON
manifest on stdout:

- `env`: the environment an arm runs in. `lore` on its PATH sees only this vault.
- `dropfail_env`: the same, except the shim fails `lore record update ... task/<F2> ...
  --status dropped` (exit 1, a generic message) and passes every other call through.
- `supersedefail_env`: the same, except the shim fails `lore record supersede task/<G3> ...`.
- `specs`: cell -> spec name.
- `followups`: cell -> {role: follow-up name}; the gathered follow-ups of that spec.
- `others`: cell -> {role: name} of tasks that depend on a follow-up and are not follow-ups
  (linked to neither the spec nor its slice).
- `repo`: the fixture repository's path.
- `run_id`: a string unique to this build, written into every spec body, so a grader can
  grep the developer's real vaults for it.

One spec per cell (all `complete`, not distilled, one `done` slice with a `done` child; every
follow-up is linked to the slice). No follow-up parent exists.

- `drop-several`            X1 open, X2 ready (refined), X3 blocked (its body carries a sentence
                            found nowhere else), K open.
- `merge-included`          S open, M open (the same function; M adds the bound 5000), L open.
- `merge-left-ready`        A ready (refined), B open (overlap), C open.
- `merge-refused-dependent` P open, Q open (overlap); R, not a follow-up, depends on Q.
- `edit-intent`             E1 open, E2 open (overlap).
- `full-set`                I open (refinable), D open, S open, M open (overlap), L1 open,
                            L2 open depending on D.
- `drop-fails`              F1 open, F2 open.
- `supersede-fails`         G1 open, G2 open, G3 open (overlap).
- `drop-dependents`         H1 open; T open depending on H1; U, not a follow-up, depends on H1.
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
    "src/journal.py": "def note_bake(journal, loaf):\n    journal.append(loaf)\n",
    "src/dials.py": "DIAL_A = 1\nDIAL_B = 2\n",
    "src/thermostat.py": "def read_probe(probe):\n    return probe.raw()\n",
    "src/hinge.py": "# hinge geometry\nSWING = 90\n",
    "src/scale.py": "def read_weight(sensor):\n    return sensor.raw()\n",
    "src/stickerpress.py": '"""Press the sticker sheet"""\n\n\ndef press(sheet):\n    return sheet\n',
    "src/crate.py": "def label_text(crate):\n    return crate.name\n",
    "src/forklift.py": '"""Forklift log"""\n\n\ndef log_lift(log, lift):\n    log.append(lift)\n',
    "src/tote.py": "def tag_lines(tote):\n    return [tote.id]\n",
    "src/banner.py": "def render_banner(text):\n    return text\n",
    "src/sorter.py": "def bin_for(mass):\n    return 'heavy' if mass > 20 else 'light'\n",
    "src/chutes.py": "LEGACY_CHUTES = {'a': 1, 'b': 2}\n",
    "src/binlog.py": "def log_bin(logger, crate, chosen):\n    logger.write(chosen)\n",
    "src/belt.py": "# belt speed in metres per second\nSPEED = 2\n",
    "src/mugs.py": "def mug_count(rack):\n    return len(rack)\n",
    "src/rackpaint.py": "COLOUR = 'grey'\n",
    "src/pegs.py": "def peg_report(pegs):\n    return len(pegs)\n",
    "src/dimmer.py": "DIM_TABLE = [0, 25, 50, 75, 100]\n",
}


def _payload(delivers: str, tests: str, files: str) -> str:
    return PAYLOAD.format(delivers=delivers, tests=tests, files=files)


# cell -> (spec title, [(role, title, status, body, depends_on_role, linked)])
CELLS = {
    "drop-several": ("Fixture bakery oven spec", [
        ("X1", "Journal: note the preheat time beside each loaf", "open",
         "`src/journal.py` appends the loaf and nothing about when the oven was ready. Note the preheat time.",
         None, True),
        ("X2", "Dials: rename the two dial constants to match the manual", "ready",
         "`src/dials.py` calls the dials `DIAL_A` and `DIAL_B`; the manual calls them `TOP` and `BASE`."
         + _payload("`src/dials.py` defines `TOP` and `BASE` in place of `DIAL_A` and `DIAL_B`.",
                    "A test imports `TOP` and `BASE` from `src/dials` and asserts 1 and 2.",
                    "`src/dials.py`, `tests/test_dials.py` (new)"),
         None, True),
        ("X3", "Thermostat: swap the probe supplier", "blocked",
         "BLOCKED-BODY-SENTINEL {run_id}: waiting on a purchase order for the new probe. `src/thermostat.py` "
         "reads the probe raw.", None, True),
        ("K", "Hinge: reword the swing comment", "open",
         "The comment in `src/hinge.py` says `hinge geometry` and could name the swing angle.", None, True),
    ]),
    "merge-included": ("Fixture parcel scale spec", [
        ("S", "Parcel scale: refuse a mass figure below zero", "open",
         "`src/scale.py` returns the sensor's raw figure, so a faulty sensor's negative figure flows "
         "into the parcel's recorded mass. Agreed behaviour: `read_weight` raises `ValueError` for a "
         "figure below zero and returns any other figure unchanged. The repository has no tests yet; "
         "add `tests/test_scale.py`.", None, True),
        ("M", "Parcel scale: refuse a mass figure above the load-cell ceiling", "open",
         "`src/scale.py` returns the sensor's raw figure, so a stuck sensor that reports a figure above "
         "the load cell's ceiling of 5000 grams flows into the parcel's recorded mass. Agreed behaviour: "
         "`read_weight` raises `ValueError` for a figure above 5000 and returns any other figure "
         "unchanged. The repository has no tests yet; add `tests/test_scale.py`.", None, True),
        ("L", "Sticker press: add a closing full stop to the module docstring", "open",
         "The docstring in `src/stickerpress.py` has no closing full stop.", None, True),
    ]),
    "merge-left-ready": ("Fixture crate docket spec", [
        ("A", "Crate docket: print the destination city on the docket", "ready",
         "`label_text` in `src/crate.py` prints only the crate's name; the dispatcher also needs the "
         "destination city on the docket."
         + _payload("`label_text` in `src/crate.py` returns the crate's name, a space, then its `city`.",
                    "A test passes a crate named `c7` with city `Leeds` and asserts `c7 Leeds`.",
                    "`src/crate.py`, `tests/test_crate.py` (new)"),
         None, True),
        ("B", "Crate docket: print the destination postcode on the docket", "open",
         "`label_text` in `src/crate.py` prints only the crate's name; the courier also needs the "
         "destination postcode on the docket. Agreed behaviour: the docket ends with the crate's `postcode`.",
         None, True),
        ("C", "Forklift log: add a closing full stop to the module docstring", "open",
         "The docstring in `src/forklift.py` has no closing full stop.", None, True),
    ]),
    "merge-refused-dependent": ("Fixture tote tag spec", [
        ("P", "Tote tag: print the tote id with emphasis marks", "open",
         "`tag_lines` in `src/tote.py` returns the tote id as a plain line. Agreed behaviour: wrap the "
         "id in `**` marks.", None, True),
        ("Q", "Tote tag: print the tote id in capitals", "open",
         "`tag_lines` in `src/tote.py` returns the tote id as a plain line. Agreed behaviour: upper-case "
         "the id.", None, True),
    ]),
    "edit-intent": ("Fixture banner printer spec", [
        ("E1", "Banner printer: centre the banner text", "open",
         "`render_banner` in `src/banner.py` returns the text as given. Agreed behaviour: centre it in a "
         "40-character line.", None, True),
        ("E2", "Banner printer: pad the banner text with a border", "open",
         "`render_banner` in `src/banner.py` returns the text as given. Agreed behaviour: put a `#` on "
         "each side of it.", None, True),
    ]),
    "full-set": ("Fixture crate sorter spec", [
        ("I", "Sorter: refuse a crate mass of zero", "open",
         "`bin_for` in `src/sorter.py` sends a zero mass to the light bin, hiding a crate the scale "
         "failed to weigh. Agreed behaviour: raise `ValueError` for a mass of zero or less and keep "
         "the existing bins for any other mass. The repository has no tests yet; add "
         "`tests/test_sorter.py`.", None, True),
        ("D", "Chutes: remove the legacy chute table", "open",
         "`LEGACY_CHUTES` in `src/chutes.py` is read by nothing now.", None, True),
        ("S", "Bin log: write the chosen bin to the log", "open",
         "`log_bin` in `src/binlog.py` writes the chosen bin and nothing else.", None, True),
        ("M", "Bin log: write the crate id with the chosen bin", "open",
         "`log_bin` in `src/binlog.py` writes the chosen bin without saying which crate it was for.",
         None, True),
        ("L1", "Belt: say the speed unit in the comment", "open",
         "The comment in `src/belt.py` names the unit but not the belt it belongs to.", None, True),
        ("L2", "Chutes: remove the table of legacy chute names", "open",
         "The table of names in `src/chutes.py` goes with the legacy chute table.", "D", True),
    ]),
    "drop-fails": ("Fixture mug rack spec", [
        ("F1", "Mugs: count the mugs hanging on the rack", "open",
         "`mug_count` in `src/mugs.py` counts every slot, empty or not.", None, True),
        ("F2", "Rack paint: pick a warmer grey", "open",
         "`COLOUR` in `src/rackpaint.py` is a cold grey.", None, True),
    ]),
    "supersede-fails": ("Fixture hat peg spec", [
        ("G1", "Hat pegs: report how many pegs are in use", "open",
         "`peg_report` in `src/pegs.py` counts every peg. Agreed behaviour: it returns the number of pegs "
         "that hold a hat. The repository has no tests yet; add `tests/test_pegs.py`.", None, True),
        ("G2", "Hat pegs: report how many pegs are free", "open",
         "`peg_report` in `src/pegs.py` counts every peg and says nothing of the free ones.", None, True),
        ("G3", "Hat pegs: report the pegs in use as a ratio of all pegs", "open",
         "`peg_report` in `src/pegs.py` gives a count with no ratio to the pegs on the wall.", None, True),
    ]),
    "drop-dependents": ("Fixture shelf lamp spec", [
        ("H1", "Dimmer: remove the unused dim table", "open",
         "`DIM_TABLE` in `src/dimmer.py` is read by nothing now.", None, True),
        ("T", "Dimmer: remove the dim table's step comments", "open",
         "The comments beside `DIM_TABLE` go with the table.", "H1", True),
    ]),
}

# cell -> [(role, title, depends_on_role)]: tasks that are not follow-ups of the spec.
OTHERS = {
    "merge-refused-dependent": [("R", "Tote tag: reprint the day's tags in one batch", "Q")],
    "drop-dependents": [("U", "Lamp: audit the wall switch wiring", "H1")],
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

    plain_dir = dest / "bin"

    def make_shim_dir(shim_dir: Path, body: str) -> None:
        shim_dir.mkdir()
        shim = shim_dir / "lore"
        shim.write_text("#!/bin/sh\n" + body + f'exec "{lore_bin}" "$@"\n')
        shim.chmod(0o755)
        (shim_dir / "python3").symlink_to(Path(sys.executable).resolve())
        mktemp = shim_dir / "mktemp"
        mktemp.write_text(MKTEMP_SHIM)
        mktemp.chmod(0o755)

    make_shim_dir(plain_dir, "")

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

    env = env_for(plain_dir)

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

    specs, followups, others = {}, {}, {}
    for cell, (title, children) in CELLS.items():
        spec = create("spec", title, "complete", f"# {title}\n\nFixture run {run_id}.")
        specs[cell] = spec
        slice_name = create("task", f"{title}: the slice", "done", f"The slice. {run_id}",
                            ["craft/slice-parent="], [f"spec={spec}"])
        create("task", f"{title}: a finished child", "done", "Finished.", parent=slice_name)
        names = {}
        for role, ftitle, status, body, dep, linked in children:
            names[role] = create(
                "task", ftitle, status, body.replace("{run_id}", run_id),
                related=[f"task={slice_name}"] if linked else [],
                depends_on=[names[dep]] if dep else [],
            )
        followups[cell] = names
        extra = {}
        for role, otitle, dep in OTHERS.get(cell, []):
            extra[role] = create("task", otitle, "open", f"Not a follow-up. {run_id}",
                                 depends_on=[names[dep]])
        others[cell] = extra

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
        "others": others,
        "repo": str(repo),
        "run_id": run_id,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
