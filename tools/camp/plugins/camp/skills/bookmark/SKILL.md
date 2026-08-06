---
name: bookmark
description: Capture, list, and re-enter camp session bookmarks — a named pointer from a camp workspace back to the harness session you were running in it. Use for /camp:bookmark, "bookmark this session", "save this session so I can come back", "what sessions do I have bookmarked", "resume the <ref> session", "drop that bookmark".
---

# /camp:bookmark — name this session so you can come back to it

A **bookmark** is a durable, short ref pointing at one harness session started
from one camp workspace. It is what makes "pick this back up next week" a single
command instead of an archaeology exercise.

Everything here is the `camp` CLI. Run the command, relay what it prints — do not
read or write the bookmark store yourself, and do not re-derive a transcript path
or a resume command: camp and the harness seam own both.

## Capture the current session

Run from **inside the workspace** whose session you want to keep:

```bash
camp bookmark [--ref <ref>] [--note <text>]
```

- The default ref is the workspace slug. Refs are lowercase, short, and free of
  shell metacharacters (`^[a-z0-9][a-z0-9._-]{0,63}$`).
- One bookmark per workspace: re-running under the SAME ref updates that record in
  place (new session id, new transcript, note replaced) rather than accumulating a
  second one. Re-running under a DIFFERENT ref is refused, naming the existing ref
  — moving a bookmark means `camp bookmark rm <old-ref>` first, because dropping a
  bookmark is always explicit.
- A ref already held by a **different** workspace is refused, not stolen — pick
  another ref, or remove the old one.
- Three preconditions each fail on their own terms: cwd must be inside a camp
  workspace, the harness must have exported a session id, and the transcript must
  actually resolve. Relay the refusal as printed; it names which one to fix.

## List what is bookmarked

```bash
camp bookmark ls
```

Global — every bookmark on the machine, most-recently-updated first, whatever
directory you run it from, including outside every camp group (`ls`, `rm`, and
`resume` address a ref, and a ref is looked up without knowing its group; only bare
capture is cwd-scoped). Columns are ref / group-workspace / age / note, plus at
most one marker per row:

- `[workspace gone]` / `[transcript gone]` — the bookmark now points at nothing;
  the fix is `camp bookmark rm <ref>`.
- `[expires ~Nd]` — still healthy, but the harness's own cleanup is closing in on
  the transcript. Resume it, re-capture it, or raise the harness's retention
  setting (`trailhead doctor` names the setting).

## Re-enter a bookmarked session

```bash
camp resume <ref>
```

Needs the camp shell integration active (`eval "$(trailhead shellenv)"`); without
it camp refuses rather than printing lines nothing will act on. The wrapper does
the two things camp will not: change directory into the workspace and exec the
harness. **A user runs this in their own shell** — it replaces the shell's
foreground process, so do not run it on their behalf inside a session; tell them
the command.

## Drop a bookmark

```bash
camp bookmark rm <ref>
```

Removes exactly that ref. An unknown ref is refused by name rather than silently
succeeding. Removing a bookmark never touches the workspace or the transcript.

## Guardrails

- Tearing down a workspace that still holds a bookmark is guarded — relay the
  refusal and let the user decide, rather than forcing past it.
- Bookmarks outlive the harness's transcript retention window. When `ls` or
  `trailhead doctor` says a session is approaching expiry, say so plainly: after
  cleanup the bookmark is a pointer to nothing and only `rm` is left.
