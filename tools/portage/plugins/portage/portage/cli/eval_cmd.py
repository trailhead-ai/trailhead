"""``portage eval`` — run named eval cases of a plugin and read their verdicts.

``start --plugin <repo-relative plugin root> --case <name> [--case ...] [--backend local]
[--repo <path>]`` loads ``<plugin root>/evals/eval-gate.json`` from the working tree, creates a
request (``portage.eval_command.request``), starts a detached worker and prints ``request: <id>``
and ``wait: <paste-ready command>`` without waiting. Exit 0 started, 3 not run (the not-run block
is printed), 2 refused (a stderr line names why: an undeclared case, a plugin root that leaves the
repo, a mapping that does not load).

``wait <id> [--timeout-seconds N]`` (default 110, 1..540) polls the request and prints a summary
line, then each case's verdict and each run's score, and for a not-run case its reason and next
step. Exit 0 every case passed, 1 a case failed, 3 none failed and one not run, 4 still running
(the command to wait again is printed), 2 refused: an identifier that is not ``local-`` plus 12
lowercase letters or digits (never read as a path), an unknown request, or a bound outside 1..540.

With no ``--backend``, a call whose environment carries ``SANDBOX_RUNTIME`` (the harness sets it in
sandboxed calls only) is not run ``could-not-leave-sandbox``: nothing starts, exit 3, and the block is
followed by the absolute pinned-shim command to run outside the sandbox. ``--backend local`` runs
regardless, but a worker started that way from inside the sandbox is killed when the call returns and
``wait`` reports it ``could-not-leave-sandbox``; with no marker the local backend is the default.

The entry points the shim dispatches to are ``cmd_start`` and ``cmd_wait``. The gate's pass cache
and state directories are never read or written.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

from ..eval_command import local, request
from ..eval_gate.mapping import CASE_NAME, MappingError, load_mapping, valid_corpus_path
from ..eval_gate.select import MAPPING_NAME


def add_eval_subparser(sub) -> None:
    p = sub.add_parser(
        "eval",
        help="Run named eval cases of a plugin and read their verdicts.",
        description="Run named eval cases of a plugin and read their verdicts.",
    )
    group = p.add_subparsers(dest="eval_command", required=True)

    s = group.add_parser("start", help="Start a request for named cases and return its identifier.",
                         description="Start a request for named cases and return its identifier.")
    s.add_argument("--plugin", required=True, help="Repo-relative plugin root")
    s.add_argument("--case", action="append", required=True, dest="cases", metavar="NAME",
                   help="A case the plugin's eval-gate.json declares (repeatable)")
    s.add_argument("--backend", choices=["local"], default=None, help="Where to run the cases")
    s.add_argument("--repo", default=None, help="Repository (default: the working directory's git top level)")
    s.set_defaults(func=cmd_start)

    w = group.add_parser("wait", help="Wait for a request and print its verdicts.",
                         description="Wait for a request and print its verdicts.")
    w.add_argument("request_id", metavar="id")
    w.add_argument("--timeout-seconds", type=int, default=local.DEFAULT_WAIT_SECONDS,
                   help=f"Bound on the wait (1..{local.MAX_WAIT_SECONDS}, default {local.DEFAULT_WAIT_SECONDS})")
    w.set_defaults(func=cmd_wait)


def _repo(arg: str | None) -> Path:
    if arg is not None:
        return Path(arg).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    except OSError:
        top = None
    if top is None or top.returncode != 0:
        raise request.Refused("not inside a git work tree; pass --repo")
    return Path(top.stdout.strip()).resolve()


def _plan(args: argparse.Namespace) -> tuple[Path, str, list[dict]]:
    repo = _repo(args.repo)
    root = args.plugin
    if not valid_corpus_path(root) or PurePosixPath(root).is_absolute():
        raise request.Refused(f"plugin root {root!r} must be a repo-relative path without '..'")
    plugin_dir = repo / root
    if not plugin_dir.resolve().is_relative_to(repo):
        raise request.Refused(f"plugin root {root!r} leaves the repo")
    mapping_file = plugin_dir / "evals" / MAPPING_NAME
    try:
        mapping = load_mapping(mapping_file.read_text(), str(mapping_file), plugin_root=plugin_dir,
                               evals_dir=plugin_dir / "evals")
    except (OSError, UnicodeDecodeError) as e:
        raise request.Refused(f"cannot read {mapping_file}: {e}") from e
    except MappingError as e:
        raise request.Refused(f"mapping invalid: {e}") from e
    names = list(dict.fromkeys(args.cases))
    for name in names:
        if not CASE_NAME.match(name) or name not in mapping.cases:
            raise request.Refused(f"case {name!r} is not declared in {mapping_file}")
    cases = [{"case": n, "runs": mapping.cases[n].runs, "pass_at": mapping.cases[n].pass_at,
              "runner": mapping.runner} for n in names]
    return repo, root, cases


def _leave_sandbox(args: argparse.Namespace) -> int:
    request.print_not_run_block(request.COULD_NOT_LEAVE_SANDBOX)
    shim = request.shim_from_env()
    if shim:
        print("cause: this call ran inside the sandbox even though it went through the pinned shim; "
              "the sandbox exclusion for it is probably not installed on this host "
              "(run `chezmoi apply` and start a new session)")
    else:
        shim = request.default_shim()
        if not (shim and os.path.isfile(shim) and os.access(shim, os.X_OK)):
            print("the pinned portage-eval shim is not installed on this host, so there is no command to run "
                  "outside the sandbox; run `trailhead update` with `claude` on PATH, then `chezmoi apply` "
                  "and start a new session")
            return 3
    words = [shim, "start", "--plugin", args.plugin]
    for case in args.cases:
        words += ["--case", case]
    if args.repo is not None:
        words += ["--repo", os.path.abspath(args.repo)]
    print(f"run outside the sandbox: {' '.join(shlex.quote(w) for w in words)}")
    return 3


def cmd_start(args: argparse.Namespace) -> int:
    if args.backend is None and "SANDBOX_RUNTIME" in os.environ:
        return _leave_sandbox(args)
    try:
        repo, root, cases = _plan(args)
    except request.Refused as e:
        print(f"eval start: {e}", file=sys.stderr)
        return 2
    try:
        request_id = local.start_request(repo=str(repo), plugin_root=root, cases=cases)
    except local.StartFailed as e:
        print(f"eval start: could not start a request: {e}", file=sys.stderr)
        request.print_not_run_block(request.RUNNER_BROKEN)
        return 3
    print(f"request: {request_id}")
    print(f"wait: {request.wait_command(request_id)}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    if not 1 <= args.timeout_seconds <= local.MAX_WAIT_SECONDS:
        print(f"eval wait: --timeout-seconds must be between 1 and {local.MAX_WAIT_SECONDS}",
              file=sys.stderr)
        return 2
    try:
        return local.wait(args.request_id, args.timeout_seconds)
    except request.Refused as e:
        print(f"eval wait: {e}", file=sys.stderr)
        return 2
