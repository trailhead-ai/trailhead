"""`camp transfer-probe` — what this host answers about itself.

Dispatched before group resolution (`cli/dispatch.py`'s `main`, alongside
`groups`), because the whole point of this verb is to answer "is this group
even configured here" rather than fail before it gets the chance — a group
that does not resolve from cwd or `--group` here is a valid, distinct answer
(`group_configured: false`), never a dispatch-time error.

`--group` and `--slug` are both required flags rather than resolved from cwd:
this verb runs on the receiving end of an ssh invocation, whose non-interactive
login default cwd the sending side cannot choose (see
`camp.host.transport.run_camp`'s module docstring).
"""

from __future__ import annotations

import json
import sys


def _cmd_transfer_probe_cli(args: list[str]) -> None:
    from ..group.config import GroupConfigError, load_all_groups
    from ..host.config import HostConfigError, self_host_name
    from ..spine import _consume_flag_value
    from ..transfer.probe import build_probe_answer
    from .common import _groups_dir

    group_name = _consume_flag_value(args, "--group")
    if not group_name:
        print("camp transfer-probe: --group is required", file=sys.stderr)
        sys.exit(1)

    slug = _consume_flag_value(args, "--slug")
    if not slug:
        print("camp transfer-probe: --slug is required", file=sys.stderr)
        sys.exit(1)

    try:
        self_name = self_host_name()
    except HostConfigError as e:
        print(f"camp transfer-probe: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        groups = load_all_groups(_groups_dir())
    except GroupConfigError as e:
        print(f"camp transfer-probe: {e}", file=sys.stderr)
        sys.exit(1)

    answer = build_probe_answer(
        group_name=group_name,
        slug=slug,
        groups=groups,
        self_name=self_name,
    )
    print(json.dumps(answer))
