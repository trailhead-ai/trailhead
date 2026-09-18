"""``camp window-dispatch`` — the hidden verb camp's own window-creation-key
binding fires against.

Reached only from the `run-shell` string
`camp.launch.binding.install_window_key_binding` installs, never typed by an
operator. Dispatched groupless, before group resolution — like
``session-bootstrap``/``inject``/``transfer-probe`` — because it resolves
its own (group, slug) from the tmux session that fired it, not from cwd (a
`run-shell` process's cwd is tmux's own working directory, not the
workspace's).

Three settled design decisions this module builds on, all owned by the
task that added it:

1. **Parameters arrive as tmux OPTIONS, never as interpolated shell text.**
   `create_workspace_session` writes `@camp_group`/`@camp_slug` as
   session-local options at creation time; this verb reads them back with
   `Tmux.show_option`, addressed by the tmux-minted `#{session_id}` the
   binding's `run-shell` string carries — the ONLY value that string
   interpolates. Group and slug never touch a shell: `run-shell` hands its
   string to `/bin/sh -c`, and `validate_workspace_slug`'s own docstring
   states a stored slug carrying shell metacharacters is still a valid,
   resolvable workspace — interpolating either would be a command-injection
   vector on a resource shared with every session on the machine.

2. **The slug read back is a STORED value, not a freshly-captured one** —
   re-gated through `validate_workspace_slug` before it reaches
   `workspace_dir` (a path join), exactly as every other reader of a stored
   slug must (see that function's own docstring on why a hand-edited or
   corrupted record cannot be trusted the way a slug this process just
   validated can).

3. **A refusal surfaces via `display-message`, issued explicitly by this
   verb.** `run-shell` here has no attached tty of its own — an operator
   who pressed the key would see nothing from a bare exception. Every
   refusal branch below (missing marks, an invalid stored slug, an unknown
   group, or `compose_window` itself refusing) forwards to
   `Tmux.display_message` against the firing session, carrying the
   refusal's own words verbatim — no redaction logic added on top, since
   `WindowAtCredentialStore` already withholds its path by construction.
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from ..group.manifest import workspace_dir as _real_workspace_dir
from ..group.resolve import GroupConfinementError, GroupResolutionError, validate_workspace_slug
from ..group.resolve import resolve_group_override
from ..launch.window_compose import WindowComposeError, WindowRefused, compose_window as _real_compose_window

#: The window name every camp-dispatched window is created under. Not yet
#: operator-configurable — the verb this task adds is the first and only
#: production caller of `compose_window`.
_WINDOW_NAME = "claude"

#: The refusal shown when the session that fired this verb carries no (or
#: only a partial) camp mark. Reachable only if a marked session is
#: destroyed and a differently-marked (or unmarked) one is recreated under
#: the same name between the binding firing and this verb reading the
#: option back — see the task's own open question about `#{session_id}`
#: stability across such a recreate, resolved against real tmux in
#: `test_window_binding_end_to_end.py`.
_NOT_A_WORKSPACE_SESSION = (
    "camp: cannot open window — this session is not a marked camp workspace session"
)


def dispatch_window(
    session_id: str,
    *,
    tmux,
    all_configs: Sequence[dict],
    env: Mapping[str, str] | None = None,
    workspace_dir_fn: Callable[..., object] = _real_workspace_dir,
    compose: Callable[..., object] = _real_compose_window,
) -> None:
    """Compose a window in the workspace session *session_id* fired from.

    Reads `@camp_group`/`@camp_slug` back off *session_id* (never a session
    NAME — see the module docstring's decision 1), resolves the group
    config from *all_configs* (the same `load_all_groups` result
    `cli/group.py`'s own `_cmd_group_cli` resolves against, via
    `resolve_group_override` — decision 2 of that task's design reusing
    this exact idiom), and calls *compose* — `compose_window` in
    production, injected here so this function's own branching is testable
    without a real groups directory or a real tmux subprocess.

    Every refusal path calls `tmux.display_message(session_id, ...)` and
    returns — never raises — since this runs detached from any run-shell
    dispatch with no tty of its own to report a traceback to.
    """

    def _refuse(message: str) -> None:
        tmux.display_message(session_id, message)

    group_name = tmux.show_option(session_id, "@camp_group")
    slug = tmux.show_option(session_id, "@camp_slug")
    if not group_name or not slug:
        _refuse(_NOT_A_WORKSPACE_SESSION)
        return

    try:
        validate_workspace_slug(slug)
    except GroupConfinementError as exc:
        _refuse(f"camp: {exc}")
        return

    try:
        group = resolve_group_override(group_name, list(all_configs))
    except GroupResolutionError as exc:
        _refuse(f"camp: {exc}")
        return

    ws_dir = workspace_dir_fn(group_name, slug, env=env)

    try:
        compose(
            group,
            slug,
            ws_dir,
            cwd=ws_dir,
            window_name=_WINDOW_NAME,
            tmux=tmux,
            env=env,
        )
    except (WindowRefused, WindowComposeError) as exc:
        _refuse(str(exc))


def _cmd_window_dispatch_cli(args: list[str]) -> None:
    """``camp window-dispatch --session-id <id>`` — the hidden verb's real
    wiring: parse the one flag, build a real `Tmux`, load every configured
    group, and call `dispatch_window`.

    Never raises past this function: a malformed invocation (this route is
    camp calling itself from a `run-shell` string, never an operator typo)
    or a failure to even load the groups directory both degrade to a
    `display-message` refusal where a session id was given, and a silent
    no-op where argument parsing itself failed and there is no session to
    address at all.
    """
    from ..group.config import GroupConfigError, load_all_groups
    from ..launch.tmux import Tmux
    from .common import _groups_dir
    from .parser import CampParser

    parser = CampParser(verb="window-dispatch")
    parser.add_argument("--session-id")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return

    session_id = parsed.session_id
    if not session_id:
        return

    tmux = Tmux()
    try:
        all_configs = load_all_groups(_groups_dir())
    except GroupConfigError as exc:
        tmux.display_message(session_id, f"camp: {exc}")
        return

    dispatch_window(session_id, tmux=tmux, all_configs=all_configs)
