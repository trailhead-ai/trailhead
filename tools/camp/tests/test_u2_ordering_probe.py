"""EPHEMERAL assumption probe for U2 (task/the-health-check-reports-whether-
each-declared-account-is-authenticated). Delete this file and the sibling
`_u2_probe_fixture/` directory once the unknown is resolved -- see the
assumption-prover report for exact cleanup paths.

U2 asks: does the addressable-account pool
(`camp.cli.session._addressable_harnesses`, fed by `_parsable_groups`) come
out in a stable order across SEPARATE PROCESSES, and is that stability by
construction (dict insertion order fed by a sorted glob) or by accident (a
`set`/hash-order dependency that `PYTHONHASHSEED` would expose)?

This test runs the real enumeration entry path -- not a source-reading
check -- in child processes with different `PYTHONHASHSEED` values, against
on-disk group TOML files created in a NON-alphabetical order, and compares
the resulting account sequences.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_RUN_ENUM = _HERE / "_u2_probe_fixture" / "run_enum.py"


def _write_group(groups_dir: Path, name: str, account: str) -> None:
    (groups_dir / f"{name}.toml").write_text(
        textwrap.dedent(
            f"""
            [group]
            name = "{name}"

            [[members]]
            name = "m1"
            repo_root = "/tmp/repo-{name}"

            [launch]
            account = "{account}"
            """
        ).strip()
        + "\n"
    )


def _run_enum(config_dir: Path, *, hashseed: str, home: Path) -> list[str | None]:
    env = {
        **os.environ,
        "CAMP_CONFIG_DIR": str(config_dir),
        "PYTHONHASHSEED": hashseed,
        "HOME": str(home),
    }
    result = subprocess.run(
        [sys.executable, str(_RUN_ENUM)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (hashseed={hashseed}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture
def group_configs(tmp_path: Path) -> Path:
    config_dir = tmp_path / "camp_config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True)
    # Created in a deliberately NON-alphabetical order, so a pass keyed on
    # filesystem creation/inode order (instead of the sorted glob
    # `_parsable_groups` actually uses) would show up as a mismatch below.
    for name in ("zeta", "mike", "alpha", "delta", "kilo"):
        _write_group(groups_dir, name, account=f"/acct/{name}")
    return config_dir


def test_the_account_sequence_is_identical_across_separate_processes_and_hashseeds(
    group_configs: Path, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    seq_a = _run_enum(group_configs, hashseed="1", home=home)
    seq_b = _run_enum(group_configs, hashseed="999999", home=home)
    seq_c = _run_enum(group_configs, hashseed="random", home=home)

    assert seq_a == seq_b == seq_c, (
        "the addressable-account pool's order differed across separate "
        "processes / PYTHONHASHSEED values -- this is exactly the "
        "hash-order defect U2 is checking for"
    )


def test_the_stable_order_matches_the_sorted_glob_not_declaration_or_creation_order(
    group_configs: Path, tmp_path: Path
) -> None:
    """Pins WHICH construction the stability rides on: `_parsable_groups`
    globs and `sorted()`s the group TOML filenames -- so the resulting
    account order is alphabetical-by-group-name, never the order the fixture
    created the files in, and never a `None` (accident) explanation.
    """
    home = tmp_path / "home"
    home.mkdir()

    sequence = _run_enum(group_configs, hashseed="1", home=home)

    named = [a for a in sequence if a is not None]
    assert named == sorted(named), (
        "expected the sorted-glob-driven order (alphabetical by group name); "
        f"got {named!r} -- ordering is not resolution-derived and Task 2 "
        "must impose its own key rather than inherit this one"
    )
    creation_order = ["/acct/zeta", "/acct/mike", "/acct/alpha", "/acct/delta", "/acct/kilo"]
    assert named != creation_order, (
        "the sequence matched file-creation order rather than sorted-glob "
        "order -- the stability claim would rest on filesystem enumeration "
        "order, which is a different (and much weaker) guarantee"
    )
