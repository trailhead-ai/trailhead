# A named remote host answers

The rendered surface for `camp list --host <name>` and `camp sessions --host <name>` — one
declared machine, answered over non-interactive SSH, with every way it can fail to answer
rendered as part of the answer rather than as a crash.

`--host` names exactly one machine. It is not `--all-hosts`: there is no merge, no local
row, and no concurrency. What it establishes is the transport every later cross-host form
rides.

## What the local side does and does not decide

The remote camp decides everything. The local side resolves the host's declaration,
carries the request, and relays the answer — it never interprets a row, never re-resolves
a group, and never rewords a refusal.

Two consequences visible in the rendering below:

- **The remote invocation always spans that machine's groups.** A remote answer is never
  narrowed by the group the local working directory happens to sit in, so the far side is
  always invoked with the all-groups form. Naming a group alongside `--host` is refused
  rather than resolved — the two name different scopes, and the same refusal already
  exists for `--all-groups` and `--group`.
- **A remote refusal is printed as the remote camp wrote it.** The local side adds no
  wrapper text; it adds only the exit status.

## The host declaration

```toml
# ~/.config/camp/hosts.toml
[hosts.andromeda]
ssh = "andromeda"                                    # optional; defaults to the table key
camp_bin = "/home/tom/.local/state/trailhead/bin/camp"   # optional
```

The local machine is never listed. An absent file means no host is declared, which is a
refusal for `--host` (nothing to name) rather than an error.

`camp_bin` is optional in the schema and load-bearing in practice: measured on the
operator's own remote host, `camp` is on no PATH — not over non-interactive SSH, and not
in a login shell — so a declaration omitting it produces the *camp not resolvable* state
below rather than an answer.

## Transport

One invocation, no shell on the near side:

```
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=<timeout> \
    <ssh> <single-quoted remote command>
```

`BatchMode=yes` is what makes a host that would prompt unreachable rather than blocking.
`StrictHostKeyChecking=yes` is what makes an unrecognized key a refusal rather than a
trust-on-first-use. The remote command is assembled by quoting every element
individually, so no character in a slug, group name, session reference, or camp location
can be interpreted by the far-side shell.

**Two bounds, not one.** `ConnectTimeout` bounds the handshake; it says nothing about a
host that connects and then stops answering — a wedged remote camp, a stalled checkout, a
partition after the connection was established. The whole invocation therefore carries a
second, longer execution bound, and exceeding it is its own state below. Without it the
documented silence while waiting is indistinguishable from an indefinite hang, which is
the one failure this surface must never render as normal operation. Both bounds have a
stated default and an operator-facing override.

The subprocess runs under a fixed `LC_ALL=C` so the classification below reads a stable
message, and every outcome is decided on the exit code **and** the message together —
neither alone separates the states, because several of them share an exit code.

Measured against real `ssh` on 2026-09-10, and the classification rests on these rather
than on expectation. Every transport-level failure is exit 255, separable only by message:
`Could not resolve hostname` / `Connection refused` / `Connection timed out` for an
unreachable host, `REMOTE HOST IDENTIFICATION HAS CHANGED` for a changed key, and
`host key is known for` together with `requested strict checking` for a key that was never
pinned. The never-pinned message interpolates the key algorithm, so nothing matches a
literal algorithm name — a host offering a different key type would be misclassified. A
missing remote binary is exit 127, unambiguous against every transport failure, and a
remote camp's own exit code comes back verbatim.

**Resolution order is load-bearing, because 255 is ambiguous.** A remote camp that exits
255 cannot be told from a transport failure by exit code, and it may print nothing at all.
The message is therefore matched against the known, fixed transport strings **first**, and
an otherwise-unmatched 255 is the remote command's own exit. Never the reverse: assuming
transport first would render a genuine remote refusal as a connection failure that never
happened.

**The execution bound stops waiting; it does not stop the remote side.** Also measured:
terminating the local `ssh` process, gracefully or forcibly, leaves the remote command
running — it is reparented and survives. So an invocation that hits the execution bound, or
is interrupted, may leave a remote camp process running until it exits on its own. The
usual remedy is a pty, which would apply CRLF translation to the very stream this transport
exists to relay verbatim; the read-only verbs here are short-lived, so the leak is accepted
and stated rather than traded for a corrupted answer. It is worth revisiting when a
state-changing or long-running verb rides this transport.

## State — zero

The host answered and has nothing to report. The rendering is the local zero rendering:
silence on the human path, an empty array in the machine-readable one, exit 0.

```
$ camp list --host andromeda
$ echo $status
0
```

```
$ camp list --host andromeda --json
[]
```

Silence is the honest answer: the question was asked, the machine answered, and the
answer is empty. Every state below is distinguishable from this one.

## State — one

```
$ camp sessions --host andromeda
177a2259-...  interactive  ~/.local/state/camp/trailhead/worktrees/project-maturity (project-maturity-c1)
```

```
$ camp sessions --host andromeda --json
[{"ok": true, "session_id": "177a2259-...", "kind": "interactive",
  "cwd": "/home/tom/.local/state/camp/trailhead/worktrees/project-maturity",
  "group": "trailhead", "account": null, "host": "andromeda", ...}]
```

Every row gains one key, `host`, carrying the declared host name verbatim — the name the
operator typed, not a hostname the far side reports about itself, because the name is how
they will address it again.

A plain local invocation is unchanged and gains no `host` key: the key appears only on an
invocation that could not be written before this work.

## State — many

Rows in the order the remote camp returned them. The local side does not re-sort: the
remote answer's own ordering is part of what is being relayed.

```
$ camp list --host andromeda
project-maturity /home/tom/.local/state/camp/trailhead/worktrees/project-maturity
testing-practices /home/tom/.local/state/camp/trailhead/worktrees/testing-practices
```

## State — collection failure

The host answered, and its answer says *it* could not determine something — an
unreadable group config, a credential store that would not enumerate. Those are already
rows in the remote camp's own output, and they are relayed as rows, each gaining the same
`host` key.

```
$ camp sessions --host andromeda --json
[{"ok": true,  "session_id": "177a2259-...", "host": "andromeda", ...},
 {"ok": false, "account": "~/.claude-levr", "reason": "sessions could not be enumerated for this credential store", "host": "andromeda"}]
```

On the human path the rows print as they always do and the remote camp's own notice
follows them on stderr, exactly as it appeared there:

```
$ camp sessions --host andromeda
177a2259-...  interactive  ~/.local/state/camp/trailhead/worktrees/project-maturity (project-maturity-c1)
camp sessions: could not enumerate sessions for account '~/.claude-levr'
```

This is the remote camp's degradation, not the transport's, and it keeps the remote
camp's exit status. The distinction that matters to a reader: here the machine answered
and reported a gap; below, the machine did not answer at all.

## State — host unreachable

The connection did not complete within the timeout, or was refused.

```
$ camp list --host andromeda
camp list: host 'andromeda' is unreachable — no response within 10s
$ echo $status
1
```

```
$ camp list --host andromeda --json
[{"ok": false, "host": "andromeda", "reason": "unreachable — no response within 10s"}]
```

The machine-readable form emits a row rather than nothing. A consumer reading an empty
array would conclude the host is idle, which is the confident wrong answer this whole
design exists to remove — the same reasoning that makes an unreadable credential store a
row rather than an absence.

Exit is nonzero because the operator named exactly one machine and did not get its
answer. This is where the single-named form differs from the merged one, whose exit
status will follow the local answer.

The reason is stated in the operator's terms and never as a raw transport error: no
stderr from the transport is passed through here.

## State — host stopped responding

The connection succeeded and the invocation then exceeded its execution bound. The
machine is reachable; something on the far side is not finishing.

```
$ camp list --host andromeda
camp list: host 'andromeda' connected but did not finish within 60s — no answer was received
$ echo $status
1
```

```
$ camp list --host andromeda --json
[{"ok": false, "host": "andromeda", "reason": "connected but did not finish within 60s"}]
```

Deliberately distinct from *unreachable*: there the machine never answered, here it
answered and then stalled, and the operator's next move differs — one is a network or a
declaration to check, the other is a process to go look at. Reporting both as
unreachable would send him to the wrong place.

## State — host identity unknown

The host has no pinned key. This is first contact with a machine the operator has
declared but never connected to, and it is routine — the remedy is his to perform, once,
deliberately.

```
$ camp list --host andromeda
camp list: host 'andromeda' has no pinned key — camp will not accept one on first contact
camp list: pin it yourself, then re-run: ssh-keyscan andromeda >> ~/.ssh/known_hosts
$ echo $status
1
```

```
$ camp list --host andromeda --json
[{"ok": false, "host": "andromeda", "reason": "no pinned host key"}]
```

camp never pins a key for the operator. Trust on first use is the behaviour this state
exists to refuse, so the message states the remedy rather than performing it.

## State — host identity changed

The host presented a key that is **not** the pinned one. This is not routine, and it is
not the state above: a machine whose key changed is either rebuilt or impersonated, and
camp cannot tell which.

```
$ camp list --host andromeda
camp list: host 'andromeda' presented a different key than the pinned one — refusing to connect
camp list: if this machine was not rebuilt, the connection may be intercepted; verify before removing the pinned key
$ echo $status
1
```

```
$ camp list --host andromeda --json
[{"ok": false, "host": "andromeda", "reason": "host key differs from the pinned key"}]
```

Two states rather than one because collapsing them is what teaches an operator to read a
possible interception as ordinary first-contact noise. This state names no remedy
command: the safe action depends on knowledge camp does not have, and printing a
one-liner that removes the pinned key would make defeating the check the easiest
response to it.

## State — camp not resolvable on the host

The connection succeeded and the far side could not run camp — the declared location is
wrong, or none was declared and the bare name is not on that machine's non-interactive
PATH.

```
$ camp list --host andromeda
camp list: host 'andromeda' answered, but camp could not be run there — declare camp_bin for this host in hosts.toml
$ echo $status
1
```

Separated from *unreachable* on purpose: the machine is fine and the declaration is
wrong, so the remedy names the configuration rather than the network. This is the failure
measured on the operator's own host, which is why it gets its own state rather than being
folded into unreachability.

## State — remote refusal relayed

camp ran on the far side and refused — an unparsable group config there, a verb it would
not perform.

```
$ camp list --host andromeda
camp list: /home/tom/.config/camp/groups/levr.toml: invalid TOML — skipping
$ echo $status
1
```

The line is the remote camp's own, printed as it wrote it, with the remote exit status
carried through. The local side adds nothing — a wrapper here would make one camp's
refusal read as another camp's bug.

## State — host refused our credentials

ssh authenticated with nothing — every credential offered was refused, at exit 255.
The connection never completed and camp never ran on the far side; this is the one
failure state whose remedy is entirely on the operator's side of the connection, so
unlike the changed-key state above, the message says exactly what to do.

```
$ camp list --host andromeda
camp list: host 'andromeda' refused every credential offered — camp never ran there
camp list: load the identity authorized on that host (e.g. ssh-add) and confirm it is in the host's authorized_keys, then re-run
$ echo $status
1
```

```
$ camp list --host andromeda --json
[{"ok": false, "host": "andromeda", "reason": "host refused our credentials"}]
```

This is the single most likely first-contact failure for a `--host` verb:
`BatchMode=yes` refuses to prompt, so a key that is not loaded into an agent, or is
not authorized on the far side, produces this rather than a password prompt. Measured
directly against real `ssh` on 2026-09-10 with no usable identity loaded:
`ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=<tmp>
localhost true` exits 255 with stderr `tomduffield@localhost: Permission denied
(publickey).`. The classifier matches the invariant `Permission denied` — never the
username prefix, and never the parenthesized method list, both of which vary by
target and by what the local ssh-agent offers.

## State — host not declared

```
$ camp list --host nope
camp list: no host named 'nope' is declared — declared hosts are: andromeda, workshop
$ echo $status
1
```

The message names the hosts that *are* declared, so a mistyped name is corrected from the
line itself rather than by opening the configuration file. Where none is declared, there
is nothing to list and the message says where to declare one instead:

```
$ camp list --host nope
camp list: no host named 'nope' is declared — no hosts are declared in ~/.config/camp/hosts.toml
```

Refused before anything is connected. The two messages differ only in that trailing
clause: in both, the operator named a host and no host by that name is declared, whether
the file is missing or merely lacks the entry. Nothing is attempted, so no timeout is paid for a
name that could never have resolved.
