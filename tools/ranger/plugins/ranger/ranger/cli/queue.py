"""``ranger queue`` — CLI surface for sweep queue derivation.

Exists as its own verb, separate from the (not-yet-wired) `ranger sweep`
orchestration verbs, purely for testability: `ranger queue derive --vault
<name> --json` lets a caller (or a test) drive
`ranger.sweep.queue.derive_queue` end-to-end through the CLI without needing
the rest of the sweep orchestration built first. Stays thin — parse argv,
call the domain function, print the agreed output — so the classification
logic lives in and is tested against `ranger.sweep.queue` alone.
"""

from __future__ import annotations

import json
import sys


def add_queue_subparser(sub) -> None:
    p_queue = sub.add_parser(
        "queue",
        help="Derive and classify a vault's sweep queue",
    )
    p_queue_sub = p_queue.add_subparsers(dest="queue_action", required=True)

    p_derive = p_queue_sub.add_parser(
        "derive",
        help="Derive + classify the sweep queue for a vault",
    )
    p_derive.add_argument(
        "--vault", required=True, metavar="NAME",
        help="Vault name to derive the queue for (resolved by `lore task list`)",
    )
    p_derive.add_argument(
        "--json", action="store_true",
        help="Emit the queue as a JSON array",
    )
    p_derive.set_defaults(func=cmd_queue)


def _print_queue(entries: list[dict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(entries))
        return
    for e in entries:
        near_miss = " answer_near_miss" if e["answer_near_miss"] else ""
        print(f"{e['name']} status={e['status']} bucket={e['bucket']}{near_miss}")


def _cmd_queue_derive(args) -> int:
    from ..sweep import queue as queue_mod

    try:
        entries = queue_mod.derive_queue(args.vault)
    except queue_mod.QueueDeriveError as exc:
        print(f"ranger: {exc}", file=sys.stderr)
        return 1

    _print_queue(entries, as_json=args.json)
    return 0


def cmd_queue(args) -> int:
    """Dispatch ``ranger queue <action>`` — currently only ``derive``."""
    action = getattr(args, "queue_action", None)
    if action == "derive":
        return _cmd_queue_derive(args)
    print(
        f"ranger queue: unknown action {action!r}. Use 'ranger queue derive'.",
        file=sys.stderr,
    )
    return 1
