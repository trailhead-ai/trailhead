"""The task wrapper: an argv prefix the caller supplies for every per-member step.

`CAMP_TASK_WRAPPER` (TASK_WRAPPER_ENV) carries a JSON array of strings. When a
workspace is created camp records the raw value in the workspace manifest as
`task_wrapper` (group.manifest.TASK_WRAPPER_KEY); every later run of that workspace's steps (provision, reconcile
reruns, activate, retry cleanups) is wrapped by the RECORDED value, whatever
the running shell's environment says. A step runs as
`<wrapper argv, placeholders substituted> + <step argv>`. Camp's own
repository operations (fetch, `git worktree add`) are not wrapped.

Fail closed: a wrapper that cannot be used never lets the step run bare.
`wrapped_argv` raises WrapperError, whose message names the wrapper, for:

- a value that is not a JSON array of non-empty strings;
- an unknown `{placeholder}` (only TASK_WRAPPER_PLACEHOLDERS are known);
- an unusable wrapper. camp has no notion of a "policy file", so the rule is
  generic: the executable (first element) must be an existing file when
  absolute, or resolve on PATH when not, and every OTHER element that is a
  literal absolute path (starts with "/" and carries no placeholder) must still
  exist. An element built from a placeholder is skipped because it names the
  member's worktree, which camp itself manages; relative elements are never
  treated as paths.

A workspace created with no wrapper records nothing and runs bare. If the
environment then carries CAMP_TASK_WRAPPER anyway (a stray variable in an
operator shell), the step fails closed rather than silently running unwrapped
under a caller who expected wrapping — see `resolve_wrapper`.

An empty or whitespace-only variable counts as unset.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TASK_WRAPPER_ENV = "CAMP_TASK_WRAPPER"
TASK_WRAPPER_PLACEHOLDERS = ("{worktree}", "{member}")

#: Capabilities `camp capabilities --json` reports.
CAPABILITIES = ("task-wrapper",)

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")

_WRAPPER_LABEL = f"task wrapper ({TASK_WRAPPER_ENV})"


class WrapperError(Exception):
    """The wrapper cannot be applied; the message names it and says why."""


@dataclass(frozen=True)
class TaskWrapper:
    """What a run of steps must do about wrapping.

    raw: the recorded wrapper text (or the malformed value as recorded), or
        None when the workspace has none.
    stray: True when the workspace recorded no wrapper but the environment
        carries one — every step then fails closed.
    """

    raw: Any = None
    stray: bool = False


def env_wrapper(env: Mapping[str, str] | None) -> str | None:
    """The wrapper value in *env* (os.environ when None), None if unset/blank."""
    value = (os.environ if env is None else env).get(TASK_WRAPPER_ENV)
    if value is None or not value.strip():
        return None
    return value


def resolve_wrapper(recorded: Any, env: Mapping[str, str] | None) -> TaskWrapper | None:
    """The wrapper a run must apply: the recorded one, else fail-closed on a stray var.

    The environment never overrides or supplements a recorded wrapper.
    """
    if recorded is not None:
        return TaskWrapper(raw=recorded)
    if env_wrapper(env) is not None:
        return TaskWrapper(stray=True)
    return None


def _parse(raw: Any) -> list[str]:
    if not isinstance(raw, str):
        raise WrapperError(
            f"{_WRAPPER_LABEL} must be a JSON array of non-empty strings, "
            f"got {type(raw).__name__}"
        )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WrapperError(f"{_WRAPPER_LABEL} is not valid JSON: {exc}") from exc
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(t, str) and t for t in value)
    ):
        raise WrapperError(
            f"{_WRAPPER_LABEL} must be a JSON array of non-empty strings"
        )
    return value


def wrapped_argv(
    raw: Any, step: list[str], *, worktree: Any, member: str
) -> list[str]:
    """`<wrapper, substituted for this member> + step`, or raise WrapperError."""
    tokens = _parse(raw)
    substitutions = {"{worktree}": str(worktree), "{member}": str(member)}

    for token in tokens:
        for placeholder in _PLACEHOLDER.findall(token):
            if placeholder not in TASK_WRAPPER_PLACEHOLDERS:
                raise WrapperError(
                    f"{_WRAPPER_LABEL} has unknown placeholder {placeholder} "
                    f"(known: {', '.join(TASK_WRAPPER_PLACEHOLDERS)})"
                )

    executable = tokens[0]
    if os.path.isabs(executable):
        exe_ok = os.path.isfile(executable) and os.access(executable, os.X_OK)
    else:
        exe_ok = shutil.which(executable) is not None
    if not exe_ok:
        raise WrapperError(f"{_WRAPPER_LABEL} executable {executable} is not found")

    for token in tokens[1:]:
        if token.startswith("/") and not _PLACEHOLDER.search(token):
            if not os.path.exists(token):
                raise WrapperError(
                    f"{_WRAPPER_LABEL} policy file {token} is missing"
                )

    substituted = []
    for token in tokens:
        for placeholder, value in substitutions.items():
            token = token.replace(placeholder, value)
        substituted.append(token)
    return substituted + list(step)


def stray_message() -> str:
    return (
        f"{_WRAPPER_LABEL} is set in the environment but this workspace was "
        f"created without a task wrapper — refusing to run its steps"
    )
