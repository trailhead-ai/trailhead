# A drawing named as the reference is published to the vault as a page of only the chosen drawings

A pad is scratch work: it lives in a camp workspace and dies with it, or sits in one person's state
directory. When the user settles on a drawing as the design to build and says so, the session
publishes a **reference page** to the vault: one page holding only the drawings named, drawn by the
same pen, that every teammate opens from their own Outpost and that outlives the pad.

The reference page is read-only. It has no picking, no notes, no reply, no saved answers and no
round switcher. It is a record of what was chosen, not a place to choose.

## Naming a reference

The session writes one JSON file, the way it writes a round, and runs the pad script with it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/scratch-pad/scratch_pad.py \
  --pad <pad-slug> --reference <path-to-reference.json> \
  --vault-path <vault path> [--vault <vault name>]
```

```json
{
  "site": "settings-page-header",
  "title": "Settings page: the header we are building",
  "drawings": [
    {"round": 1, "question": 1, "option": "B"},
    {"round": 2, "question": 1, "option": "A"}
  ]
}
```

- `site` is the reference page's slug in the vault's `sites/`, matching `^[a-z0-9][a-z0-9._-]*$`,
  at most 64 characters. A name already taken in that vault is refused before anything is built;
  the session retries with a new name. The script never replaces a published reference.
- `title` is required and non-empty: the page's heading, in the session's own words.
- `drawings` holds at least one entry. `round` is the round's number (as in `round-<n>/` and the
  reply's `round`), `question` is the question's 1-based place in that round, and `option` is the
  option's letter. Each must name something that exists in the pad. The same drawing named twice
  is refused. Drawings appear on the page in the order listed.
- An unknown key anywhere in this shape is refused, as in a round.
- The vault is the one the publish-site skill resolves (`lore vault resolve --kind blob --json`,
  or a vault the user names), passed through as `--vault-path` and, when it has a real name,
  `--vault`. The pad script runs no `lore` command itself; the publisher does the sync.

`--reference` is its own mode: it takes no `--round` and no `--add`, and it never changes the pad.

**Confirm before anything is shared.** Before running the script, the session says in one line
which drawings (by question and option name), under what title, are going to which vault for the
team, and waits for the user's yes. A remark like "I like the stacked one" is an answer to a
question, not a reference; only a yes to that line runs the script. The session only names drawings
the user has seen drawn on the pad.

## Where the drawings come from

The pad is found where it lives, exactly as `--add` finds it: inside a camp workspace (a member
folder included) at `<workspace>/sites/<pad>`, otherwise under Outpost's state directory. It is read
under the pad's lock, so a reference never reads a pad halfway through an add, and it goes through
the same read-back an add uses: a pad the read-back refuses cannot be referenced, for the same
reasons. Each named round's data is decoded from its `round.js` string literal, never evaluated.
Rounds built by an older kit (no round id line, no `rounds.js`) read the same way.

Only the named options travel onto the page: for each, the round's title, the question's prompt,
and the option's letter, name, cost and its `layout` or `flow`. Nothing else from the pad (the other
options, intros, `settled` lists, any saved answers) is copied.

## Before anything is team-visible

A pad is seen by one person; a reference is synced to the team. Before building, every string that
will be on the page (the title, and for each drawing its round title, prompt, name, cost, and every
string inside its layout or its flow text) is run through a credential-pattern check. A match
refuses the whole reference, naming the drawing and the field that matched, never the matched text,
and nothing is published. Text is still shown only as text on the page: the check is about what is
shared, not about markup.

## The page

```
┌──────────────────────────────────────────────────────────────────────┐
│ Settings page: the header we are building                            │
│ Chosen on a scratch pad, 2026-10-22. Each drawing is a sketch.       │
│                                                                      │
│ ┌ Header layout ── Settings page, round 1 ─────────────────────────┐ │
│ │ B  Stacked header                                                │ │
│ │ Simplest; the controls sit below the logo.                       │ │
│ │   ╭──────────────────────╮                                       │ │
│ │   │ Logo                 │                                       │ │
│ │   │ (Sign in)            │                                       │ │
│ │   ╰──────────────────────╯                                       │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌ Sign-in steps ── Settings page, round 2 ─────────────────────────┐ │
│ │ A  Retry in place                                                │ │
│ │ Fewest screens; a wrong password loops back to the form.         │ │
│ │   (Open page) → [Email and password] → <Right?> → [Settings]     │ │
│ │                        ↖──────── no ───────┘                     │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

- One column, one card per drawing, full width, so every drawing gets the page's width and a wide
  flow scales down only as far as it does on a pad.
- Each card is headed by the question's prompt and the round's title, then the option's letter and
  name, its cost, then the drawing. There is no "my pick" tag: on a reference every drawing is the
  choice.
- The line under the title says the page was chosen on a scratch pad, gives the date it was
  published (written by the script at build time), and says each drawing is a sketch, so a teammate
  can tell how current it is and does not read it as a finished design.
- Black ink on paper, one accent colour, Architects Daughter lettering, light and dark following
  the page's own switch: the pad's look, from the pad's own stylesheet.
- The page loads only files beside it, under the same `script-src 'self'` policy as a pad, with no
  inline script. It opens from Outpost at `/<vault>/<site>/` and from disk alike.

The page is built from the **current** kit: its own copy of the pen, the flow reader, the
stylesheet, the font and the font's licence, plus the reference page's own files and its data. A
round of the pad keeps the kit it was drawn with; the reference is a new page drawn now, and once
published its kit copy never changes, so it opens the same on any later Outpost version. Each
drawing is traced with the same seed it has on the pad (its question and option place), so the
reference draws the same strokes on every load at a given width.

Publishing goes through the publish-site script with `--vault-path` (and `--vault` when given),
never `--overwrite`. The pad's lock is released once the drawings are read, before publishing, so a
slow sync never holds up an add to the pad. The reference is built in an owner-only temporary folder
outside the vault, removed on every path. Unlike a workspace publish, the script does not kill the
publisher on a timer: the publisher's last step is `lore sync`, and stopping a sync partway through a
commit can leave the vault's git mid-operation for every later writer. Because it can wait (on the
vault's lock, or on a commit-signing prompt), the script first prints one line saying it is
publishing to the vault and that the sync can wait on a lock or a signing prompt, then lets the
publisher's output (the sync's own lines, then the link) through as it is written rather than
capturing it. The link appears only when the publisher printed one. The skill tells the session to
allow the command a long wait.

## Taking a reference back

A published reference is never replaced by the script, so taking one back is the publish-site
skill's own removal: delete `<vault>/sites/<site>/`, then sync that vault. The vault's git history
keeps what was published; removal stops the page being served and shipped, it does not erase it.
After every publish the session tells the user, in one line, how to take the page back. A wrong
drawing is fixed by taking the page back and publishing a new reference; text the credential check
missed is taken back the same way, and a real credential is rotated, not merely removed.

Who can open the page is decided by who shares the vault. The page adds no access check of its own,
and holds nothing a teammate with the vault cannot already read.

## State — one chosen drawing

The page shows its title, the fixed line, and one card: prompt and round title, letter and name,
cost, drawing. Nothing else from the pad appears in the page or its files.

## State — many chosen drawings

One card per named drawing, in the order the reference lists them, each naming its own question and
round, so drawings from different rounds read apart. Two options of one question may both be named;
each gets its own card.

## State — no drawing named as the reference

A reference with an empty or missing `drawings` list is refused (`drawings: name at least one
drawing`) before the pad is read. No page is built and nothing is published. The session says in one
line that no reference was published and asks in words which drawing is the reference.

## State — a chosen drawing that cannot be drawn

The pad script never reads inside a layout or a flow, so a drawing the pen cannot draw is still
published. Its card keeps its heading, name and cost, and shows the pen's own "Can't draw this
layout" or "Can't draw this flow" message, naming the broken line, in place of the drawing, exactly
as the pad shows it. The other cards draw normally.

## State — the reference cannot be built or synced

Every refusal names what is wrong on stderr, exits non-zero, and leaves the vault and the pad
untouched: a badly shaped reference; a pad that does not exist or that the read-back refuses; a
round, question or letter the pad does not have; a credential-pattern match; a site name already
taken; the publisher refusing or failing to start (its lines relayed as `scratch-pad: publish:`).
When the publisher published but its sync failed, the publisher's own lines say the page is
published on this machine but not synced, and there is no link. The page is still in the local
vault and will reach the team on the vault's next sync, by any session, unless it is taken back. The
session tells the user the page is not yet shared, hands back no link, and gives both ways forward:
sync the vault to share it, or take it back (delete the site folder, then sync). A retry of the same
name is refused as taken until the folder is gone.
