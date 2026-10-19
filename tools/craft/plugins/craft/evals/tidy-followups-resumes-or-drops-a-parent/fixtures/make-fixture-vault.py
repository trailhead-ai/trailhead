#!/usr/bin/env python3
"""Build the throwaway vault, fixture repo and fenced env the resume-or-drop eval runs against.

    LORE_BIN=<path to the lore executable> make-fixture-vault.py <dest>

Creates, under <dest> (which must not exist): a vault, a lore config naming it, fenced
state / config directories, a small git repository (`repo/`, clean tree, one commit) that
the follow-ups' code claims point at, and a `lore` shim directory on PATH. The shim
directory also carries `python3`, a link to the interpreter running this builder, so an
executable run by its `python3` shebang under the fenced PATH gets that interpreter rather
than macOS's older /usr/bin/python3, and a `mktemp` that creates under `$TMPDIR` (the eval
sandbox denies macOS's own fallback). Run it with Python 3.11 or later. Prints one JSON
manifest on stdout:

- `env`: the environment an arm runs in. `lore` on its PATH sees only this vault.
- `specs`: cell -> spec name.
- `parents`: cell -> the follow-up parent's name.
- `followups`: cell -> {role: follow-up name}; roles `A` and `B`.
- `repo`: the fixture repository's path.
- `run_id`: a string unique to this build, written into every spec body, so a grader can
  grep the developer's real vaults for it.

One spec per cell (all `complete`, one `done` slice with a `done` child; every follow-up is
linked to the slice). Each spec carries one follow-up parent: a task labelled
`craft/followup-parent`, related to the spec, whose body lists its follow-ups as
`### task/<name>` headings each followed by a `**Selection reason:**` line.

- `resume`           parent `open`; A held and `ready`; B `ready`, listed, not held.
- `resume-left-out`  parent `open`; A held and `ready`; B listed, not held, back at `open`.
- `refuse-build`     parent `ready`; A and B held and `ready`.
- `refuse-drop`      parent `in-progress`; A held and `done`; B held and `ready`.
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
    "src/ledger.py": (
        "def post_entry(book, entry):\n"
        "    book.append(entry)\n"
        "    return len(book)\n"
    ),
    "src/receipts.py": (
        "def print_receipt(printer, order):\n"
        "    printer.write(repr(order))\n"
    ),
    "src/stickers.py": (
        "def sticker_width(text):\n"
        "    return len(text) * 8\n"
    ),
    "src/tally.py": (
        "def shelf_total(counts):\n"
        "    return sum(counts)\n"
    ),
    "src/pallets.py": (
        "def pallet_load(items):\n"
        "    return sum(i.load for i in items)\n"
    ),
    "src/polls.py": (
        "def poll_average(votes):\n"
        "    return sum(votes) / len(votes)\n"
    ),
    "src/ballots.py": (
        "def ballot_id(n):\n"
        "    return 'b' + str(n)\n"
    ),
}

# cell -> (spec title, parent status, [(role, title, status, held, body)])
CELLS = {
    "resume": ("Fixture ledger posting spec", "open", [
        ("A", "Ledger posting: refuse an entry with a negative amount", "ready", True,
         "`src/ledger.py` appends any entry to the book, so a negative amount silently lowers a "
         "balance. Refuse it." + PAYLOAD.format(
             delivers="`post_entry` in `src/ledger.py` raises `ValueError` for an entry whose "
                      "`amount` is below zero and appends nothing.",
             tests="A test posts an entry with amount -1 and asserts `ValueError` and an unchanged book.",
             files="`src/ledger.py`, `tests/test_ledger.py` (new)")),
        ("B", "Ledger posting: print the order total on the receipt footer", "ready", False,
         "`src/receipts.py` writes the order as one raw line with no total. Add the total as a "
         "footer line." + PAYLOAD.format(
             delivers="`print_receipt` in `src/receipts.py` writes a final line `Total: <n>` where "
                      "`<n>` is the order's `total`.",
             tests="A test prints a receipt for an order with total 12 and asserts the last line is `Total: 12`.",
             files="`src/receipts.py`, `tests/test_receipts.py` (new)")),
    ]),
    "resume-left-out": ("Fixture shelf sticker spec", "open", [
        ("A", "Shelf sticker: cap the sticker width at the printer limit", "ready", True,
         "`src/stickers.py` computes a width that can exceed what the printer feeds. Cap it at 400."
         + PAYLOAD.format(
             delivers="`sticker_width` in `src/stickers.py` returns at most 400.",
             tests="A test asserts `sticker_width` of a 100-character text is 400 and of a 3-character text is 24.",
             files="`src/stickers.py`, `tests/test_stickers.py` (new)")),
        ("B", "Shelf sticker: choose the barcode symbology", "open", False,
         "`src/stickers.py` has no barcode. Operator decision needed before this can be built: the "
         "warehouse has not decided between Code 128 (compact, every scanner reads it) and QR "
         "(carries a link, needs the newer scanners the budget has not covered). Both would work. "
         "The scanner budget is the operator's call and nothing in the code or the vault settles it."),
    ]),
    "refuse-build": ("Fixture shelf count spec", "ready", [
        ("A", "Shelf count: ignore negative counts in the shelf total", "ready", True,
         "`src/tally.py` sums every count, so one mistyped negative count lowers the total. Skip them."
         + PAYLOAD.format(
             delivers="`shelf_total` in `src/tally.py` ignores counts below zero.",
             tests="A test asserts `shelf_total([3, -2, 4])` is 7.",
             files="`src/tally.py`, `tests/test_tally.py` (new)")),
        ("B", "Shelf count: report an empty pallet load as zero", "ready", True,
         "`src/pallets.py` sums item loads; an empty pallet must carry zero, as a plain number."
         + PAYLOAD.format(
             delivers="`pallet_load([])` in `src/pallets.py` returns `0`.",
             tests="A test asserts `pallet_load([])` equals 0.",
             files="`src/pallets.py`, `tests/test_pallets.py` (new)")),
    ]),
    "refuse-drop": ("Fixture customer poll spec", "in-progress", [
        ("A", "Customer poll: return the average of an empty poll as zero", "done", True,
         "`src/polls.py` divides by the number of votes and fails on an empty poll. Done: it returns 0."),
        ("B", "Customer poll: zero-pad the ballot number", "ready", True,
         "`src/ballots.py` builds ids such as `b7`, which sort wrongly next to `b10`. Zero-pad to "
         "four digits." + PAYLOAD.format(
             delivers="`ballot_id` in `src/ballots.py` returns `b0007` for 7.",
             tests="A test asserts `ballot_id(7)` is `b0007` and `ballot_id(1234)` is `b1234`.",
             files="`src/ballots.py`, `tests/test_ballots.py` (new)")),
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

    shim_dir = dest / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{lore_bin}" "$@"\n')
    shim.chmod(0o755)
    (shim_dir / "python3").symlink_to(Path(sys.executable).resolve())
    mktemp = shim_dir / "mktemp"
    mktemp.write_text(MKTEMP_SHIM)
    mktemp.chmod(0o755)

    env = {
        "PATH": f"{shim_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(dest / "state"),
        "XDG_STATE_HOME": str(dest / "state"),
        "XDG_CONFIG_HOME": str(cfg),
        "LORE_GROUPS_DIR": str(dest / "groups"),
        "LORE_PUBLISH_DISABLE": "1",
        "LORE_EMAIL": "fixture@example.com",
    }

    def create(kind, title, status, body, labels=(), related=(), parent=None) -> str:
        argv = ["lore", "record", "create", "--kind", kind, "--title", title,
                "--status", status, "--vault", "default"]
        for label in labels:
            argv += ["--label", label]
        for edge in related:
            argv += ["--related", edge]
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

    specs, parents, followups = {}, {}, {}
    for cell, (title, parent_status, children) in CELLS.items():
        spec = create("spec", title, "complete", f"# {title}\n\nFixture run {run_id}.")
        specs[cell] = spec
        slice_name = create("task", f"{title}: the slice", "done", f"The slice. {run_id}",
                            ["craft/slice-parent="], [f"spec={spec}"])
        create("task", f"{title}: a finished child", "done", "Finished.", parent=slice_name)
        names = {role: create("task", ftitle, status if not held else "open", body,
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

    print(json.dumps({
        "env": env,
        "specs": specs,
        "parents": parents,
        "followups": followups,
        "repo": str(repo),
        "run_id": run_id,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
