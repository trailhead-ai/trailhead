"""Ephemeral U2 probe script (assumption-prover): runs the real production
enumeration path (_parsable_groups -> _addressable_harnesses) in a
standalone process and prints the resulting account sequence as JSON, so a
caller can diff the sequence across separate subprocess invocations.

Not a permanent test file -- see the U2 assumption-prover report for
cleanup instructions. Deleted alongside test_u2_ordering_probe.py.
"""
import json
import os
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import camp.cli.session as cli_session
import camp.launch.profile as profile


class _FixtureHarness:
    name = "fixtureharness"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        if account is None:
            return {}
        return {"FAKE_STORE_DIR": account}


def main() -> None:
    # The real import path production uses: _addressable_harnesses does a
    # deferred `from ..launch.profile import harness_store_for,
    # StoreBindingError` at call time, and harness_store_for calls
    # `harness_for`, a plain module-level function -- patching the attribute
    # on the already-imported profile module is what a real subprocess with
    # no trailhead harness installed would need too, and is not a mock of
    # the enumeration logic itself.
    profile.harness_for = lambda group: _FixtureHarness()
    groups = cli_session._parsable_groups()
    stores = cli_session._addressable_harnesses(groups, env={})
    sequence = [s.account for s in stores]
    print(json.dumps(sequence))


if __name__ == "__main__":
    main()
