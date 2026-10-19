# A later round of questions is added to the same pad

The rounds surface of a scratch pad. A session that has a follow-up question adds it to the pad the
user already has open, as a new round, instead of starting a fresh pad. The pad opens on its latest
round; a round switcher at the top keeps every earlier round one click away; and the new round
shows, folded under its introduction, what earlier rounds settled.

```
scratch_pad.py --pad settings-header --round round-2.json --add
# → the same link the pad was first given, now opening round 2
```

What this surface establishes is **a pad that grows by whole rounds.** Each round is its own folder
holding its own copy of the kit as it was when that round was drawn, so an earlier round never
changes under you. Adding a round rebuilds the whole pad off to the side and puts it in place in one
step, so the pad is only ever the whole old pad or the whole new one.

## Adding a round

`--add` adds the round to the pad named by `--pad`, which must already exist where the session is:
the workspace's site inside a camp workspace, or the folder under Outpost's state directory outside
one. Without `--add`, a pad name already taken is refused exactly as before, so a new pad never lands
on top of an existing one by accident; with `--add`, a pad that does not exist is refused, naming
the pad and saying to drop `--add` to start a new one.

The round is checked first, before the pad is read, so a badly shaped round refuses with the pad
untouched. Then:

1. Take the pad's lock (see "Two adds at once").
2. Read the pad back from where it lives — its only copy — and check it is a pad this script can
   add to (see "Reading a pad back").
3. Build the whole new pad in a fresh temp folder: every earlier round folder copied byte for byte,
   a new `round-<n+1>/` holding a copy of the current kit and the new round's data, a new list of
   rounds, and a top-level `index.html` that sends the browser to the new round.
4. Put it in place. Outside a workspace the script renames the old pad aside, renames the new one
   into its place, and removes the old one; if the second rename fails, the old pad is renamed back.
   Inside a workspace it publishes the temp folder through the publish-site script with
   `--overwrite`, which makes the same swap under the workspace's own lock.
5. Release the lock and remove the temp folder, on every path.

On success the output is the same as for a new pad: the link (the pad's top-level page, which now
opens the new round), any caveat the publisher printed, then `pad folder: <path>`.

## A round's questions

Unchanged from the first round's format, with one new optional key on the round:

```json
{
  "title": "Settings page, round 2",
  "intro": "With the split header settled, where does search go?",
  "settled": [
    "Header layout: B, Stacked header",
    "Nav sits on the left, collapsed on narrow screens"
  ],
  "questions": [ … ]
}
```

- `settled` — optional. A list of non-empty strings, one line each, written by the session: what
  earlier rounds decided, in the session's own words. The script checks only that it is a list of
  non-empty strings. An empty list is the same as leaving it out.
- `settled` is allowed on any round, the first included, since a session may already have settled
  things in words before the first pad.
- Every string in it is untrusted text, like the rest of the round, and is shown only as text.

## On disk

```
<pad>/
  index.html            sends the browser to the latest round
  rounds.js             the pad's list of rounds, as data
  round-1/              the kit as it was when round 1 was drawn, plus its round.js
  round-2/              the kit as it was when round 2 was drawn, plus its round.js
```

- `rounds.js` is `window.PAD_ROUNDS = JSON.parse(<s>);` where `<s>` is a JSON list, one entry per
  round in order, each `{"title": <that round's title>}`, ASCII-escaped and encoded once more as a
  string literal — the same encoding `round.js` already uses, for the same reasons.
- Each `round-<k>/round.js` keeps the round's data as before and gains one line,
  `window.PAD_ROUND_NUMBER = <k>;`, so the page knows which round it is without parsing its own
  address.
- A page opened from disk cannot fetch a file, so the list of rounds ships as a script, loaded by
  each round page from the folder above it (`../rounds.js`).
- The new-pad path writes `rounds.js` too, with one entry, so every pad this kit builds has the same
  shape.

## Reading a pad back

The pad where it lives is its only copy. Before adding to it, the script checks it:

- The pad folder is a real folder, not a symlink, and nothing inside it is a symlink or anything but
  a regular file or folder.
- Its round folders are `round-1` up to `round-<n>` with no gaps, each holding an `index.html` and a
  `round.js` whose first line starts with the prefix this script writes.
- Each round's title is read back from its `round.js`, to build the new list of rounds. Nothing else
  in an earlier round is read or changed.
- `rounds.js` is not read: it is rebuilt from the round folders on every add. A pad built before
  `rounds.js` existed — one round, no list — is added to like any other.

A pad that fails any check is refused with every problem named and nothing changed: "pad
"settings-header" can't take another round: …".

## Two adds at once

An add holds a lock for its one pad from before it reads the pad until after the new pad is in
place. The lock is an exclusive `flock` on a file under Outpost's state directory,
`<outpost state>/pad-locks/<sha256 of the pad's resolved path>.lock`, created owner-only, never
inside the pad or the workspace's `sites/` tree. A second add to the same pad waits for the first,
then reads the pad as the first left it and becomes the next round after it. Both land, in the order
they took the lock; neither is lost and the pad is never a mix of the two.

A wait has a limit. An add that cannot take the lock within the publish timeout is refused —
"another session is adding a round to pad "settings-header"; try again" — and changes nothing.

## The page

```
┌──────────────────────────────────────────────────────────────────────┐
│ Rounds:  Round 1 · Round 2 · [Round 3]                               │
│ Settings page, round 3                                               │
│ With search placed, which footer?                                    │
│ ▸ Settled in earlier rounds (3)                                      │
├────────────────┬─────────────────────────────────────────────────────┤
│ ○ Footer       │  Footer                                             │
│   Your reply   │  ┌ A  Links row     [my pick] ┐ ┌ B  Two columns ─┐ │
│                │  │ …                          │ │ …               │ │
```

- **The round switcher** sits at the top, above the title: a list of links labelled "Round 1",
  "Round 2", …, each with its round's title as its accessible name ("Round 2: Settings page, round
  2"). The round being viewed is marked current with `aria-current="page"` and shown in brackets.
  Each link goes to `../round-<k>/index.html`, so it works from disk and from Outpost alike, and
  opens in a new tab: answers live only in the open page, so leaving it would lose them.
- **The settled list** sits under the introduction as a `<details>` element, folded, its summary
  "Settled in earlier rounds (<count>)". Unfolded, it lists each settled line as a list item, as
  text.
- **The reply** gains a `round` field after `pad`, the round number the reply answers:

  ```json
  {"pad": "Settings page, round 2", "round": 2, "answers": [ … ]}
  ```

  With earlier rounds one click away, a reply copied from round 1 after round 2 exists says which
  round it answers.
- The list of rounds is read from `../rounds.js`. A round page that finds no list, or one it cannot
  read, draws its round as before with no switcher; the round itself never depends on the list.

## State — first round only, no switcher shown

A pad with one round shows no round switcher at all — the page looks exactly as a one-round pad
looked before rounds existed, title first. The reply carries `"round": 1`.

## State — several rounds, viewing the latest

The pad's link opens the latest round. The switcher lists every round, the latest marked current;
every earlier round is one click away. A round's link opens that round in a new tab, so the tab
holding the latest round, and every answer on it, stays as it was.

## State — viewing an earlier round drawn with an older kit

An earlier round is drawn by the kit in its own folder, so it looks and behaves exactly as it did
when it was drawn, even if the kit has changed since. A round drawn with a kit that has a switcher
lists every round, the later ones included, because it reads the pad's current list — under its
switcher a line says "This is round 1 of 3." with a link to the latest. A round drawn before the
switcher existed shows no switcher; its page is unchanged, and the way back to the latest is the
browser's Back button or the pad's own link. Its reply, built by its own kit, carries no `round`
field.

## State — settled list with items

The settled list sits folded under the introduction, its summary counting the lines. Unfolded, each
line is a list item. A line written as a script tag shows as those characters.

## State — no settled list

A round with no `settled` key, or an empty list, shows nothing in its place — no heading, no empty
fold.

## State — round added

The script prints the pad's link — the same link the pad was first given — and any caveat the
publisher printed, then `pad folder: <path>`. Opening the link shows the new round, its switcher
listing every round. Every earlier round folder is byte-for-byte what it was before the add.

## State — new round refused for bad shape, pad unchanged

A round whose shape is wrong is refused before the pad is read, with every problem named in one
pass, exactly as a first round is. A pad that cannot be added to — it does not exist, it is not a
pad this script recognises, or the lock could not be taken in time — is refused the same way. In
every refusal the pad is left exactly as it was, earlier rounds included, and no temp folder is
left behind:

```
scratch-pad: settled[1]: empty — each settled line says what was decided
scratch-pad: pad "settings-header" does not exist — drop --add to start a new pad
scratch-pad: pad "settings-header" can't take another round: round-2 has no round.js
```

A failure while the new pad is being put in place leaves the old pad in place: outside a workspace
the old pad is renamed back; inside one the publisher's own swap is whole-or-nothing.

## State — two sessions adding a round to the same pad at once

The two adds take the pad's lock one after the other. The first lands as round `n+1`; the second
waits, reads the pad with that round in it, and lands as round `n+2`. Each session is handed the
pad's link, and the pad holds both rounds, whole, in the order they landed. The second session's
`settled` list was written before it saw the first's round, so it may not mention it; that is the
session's text, shown as written.
