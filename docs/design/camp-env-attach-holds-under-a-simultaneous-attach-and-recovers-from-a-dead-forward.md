# camp env attach holds under a simultaneous attach and recovers from a dead forward

The rendered surface for two attaches run at the same moment, and for an attachment whose forward
has died. The verbs and every message they already print are unchanged; what changes is that the
one-attachment-per-machine rule now holds when two attaches race, and that a dead forward is shown
to leave nothing behind.

```
camp env attach TARGET    # in two terminals at once: one attaches, the other is refused
camp env status           # names the one attachment
camp env attach TARGET    # after the forward died: succeeds with no detach first
```

## What already holds, and is not re-litigated here

Target parsing, resolution, the port probe, the forward and the relay, how an attachment is found
again from the process table with nothing recorded on disk, and every state the remote and local
attach design docs describe (`camp-env-attach-status-and-detach-for-a-remote-environment.md`,
`camp-env-attach-status-and-detach-for-an-environment-on-this-machine.md`) are as those documents
say. Every way out of an attach after its forward or relay started already ends only the one
carrying its own attach id, never another attach's.

## How two attaches are kept to one

Attach still refuses at once, before reading any machine, when something is already attached. That
check alone cannot stop two attaches that both pass it before either has started anything. So the
part of attach that decides and starts — check that nothing is attached, probe the standard ports,
start the forward or relay, and find it again in the process table — runs while attach holds an
exclusive lock that every attach by this user on this machine takes.

The lock is an advisory `flock` on one file, `env-attach.lock` in camp's state directory. It holds
nothing: the file is never written, never read and never removed, and no attachment is recorded in
it. It is only the thing two attaches meet on. The operating system releases it when the attach that
holds it exits, however it exits, so a crashed or killed attach never leaves it held. The forward and
the relay do not inherit it, so it is released when attach returns, not when the attachment ends.

Reading machines (a bare name's reads can take seconds) happens before the lock is taken, so two
attaches still read at the same time. An attach that finds the lock held waits for it, then checks
again from the start of the locked part. By then the attach that held it has either attached, and
the waiting attach is refused naming that attachment, or failed and ended what it started, and the
waiting attach goes ahead.

The lock file is opened without following a symlink and is created readable and writable by its
owner only. Every camp install and checkout carrying this change, run by the same user from any
directory, uses the same state directory, so the lock covers attaches started by different checkouts
too.

What the lock does not cover:

- **A camp from before this change.** It never takes the lock, so an attach it runs can still race a
  newer attach exactly as two attaches race today. Update every checkout to close it.
- **A camp run with a different state directory** (`CAMP_STATE_DIR` or `XDG_STATE_HOME` set
  differently, as a test does) meets on a different file.
- **Another user on the same machine.** The lock, like the process-table reading of attachments, is
  per user. Two users attaching at once on a shared machine are not kept to one. That is the spec's
  shared-machine residual, unchanged.

Rolling back leaves an empty `env-attach.lock` behind. Nothing reads it, and it can be deleted or
left.

## State — one of two simultaneous attaches wins

```
$ camp env attach perseus/levr/main
attached perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

On stdout, exit 0, in exactly the shape of an attach run alone. Whichever attach takes the lock
first is the one that attaches, whatever order they were started in.

## State — simultaneous attach loser refused naming the winner

While it waits for the lock, the loser says so once, on stderr, so it does not look hung:

```
camp env: another attach is starting; waiting for it to finish
```

An attach that finds the lock free prints nothing extra.

```
$ camp env attach local/levr/main
camp env: perseus/levr/main is attached; run camp env detach before attaching another (a forward whose link has died ends on its own within about 45s)
```

On stderr, exit 1. It is the already-attached refusal, word for word, naming the attachment that
won, with the dead-link note only when that attachment is a forward. Both attaches may target the
same environment or different ones; the refusal is the same.

If the waiting attach is still waiting after the longest an attach can take to start (the connect
timeout plus thirty seconds), it gives up without starting anything:

```
camp env: another attach is still starting; wait for it to finish, then try again
```

On stderr, exit 1. An interrupt while waiting prints `camp env: interrupted` on stderr and exits 130,
with nothing started.

## State — simultaneous attach loser leaves no forward or relay

The refused attach never started a forward or a relay, because it was refused before that point.
`camp env status` afterwards prints the winner's line and nothing else, and `ps` shows no process
carrying the loser's attach id.

## State — simultaneous attach loser leaves no held port

The refused attach never bound a port. Every standard port the loser's environment uses is either
free or held by the winning attachment.

## State — remote attach races a local attach

```
terminal 1 $ camp env attach perseus/levr/main
attached perseus/levr/main on ports 5173 8080 8443; its health was not checked

terminal 2 $ camp env attach local/levr/main
camp env: perseus/levr/main is attached; run camp env detach before attaching another (a forward whose link has died ends on its own within about 45s)
```

A forward and a relay take the same lock, so a remote attach and a local one exclude each other
exactly as two remote or two local attaches do. When the relay wins, the forward's attach is refused
with `camp env: local/levr/main is attached; run camp env detach before attaching another`, with no
note.

## State — single attach unchanged

```
$ camp env attach perseus/levr/main
attached perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

An attach with nothing to race takes the lock at once. Its output, exit status and every refusal are
unchanged. If the lock file cannot be opened (camp's state directory is unwritable), attach is
refused before starting anything:

```
camp env: cannot take the attach lock at <state directory>/env-attach.lock (<why>); nothing was started
```

The path and the reason pass through the same control-character removal as every other message.

## State — status after a race names one attachment

```
$ camp env status
attached: perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

On stdout, exit 0: one `attached:` line, the winner's. `camp env ls` opens with the same single line.

## State — forward died after sleep or network change reads as nothing attached

```
$ camp env status
nothing attached
```

On stdout, exit 0. A forward has died once its ssh process has exited: killed, or gone on its own
because the keep-alive found its link dead after the machine slept or changed network (within about
45 seconds of the link going quiet). Nothing about the attachment is recorded anywhere else, so once
the process is gone there is nothing left to name. Until ssh notices, the forward is still running
and status still names it; the already-attached refusal's note says how long that takes.

## State — fresh attach after a dead forward succeeds without cleanup

```
$ camp env attach perseus/levr/main
attached perseus/levr/main on ports 5173 8080 8443; its health was not checked
```

On stdout, exit 0, with no `camp env detach` first. The dead forward's ports were released when its
process exited, and a port whose only trace is a closed connection waiting out its timeout is free to
the probe and to ssh alike.
