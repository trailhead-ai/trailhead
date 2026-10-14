---
name: scratch-pad
description: Open a design question as a pad of hand-drawn layout sketches in the browser, so options can be compared as drawings instead of read as descriptions. Use when a design question's options are visual layouts (where a control lives, how a page is arranged, which of several screens to build), or for "sketch these options", "draw the layouts", "show me the options", "open a scratch pad".
---

# Scratch pad

A pad is a page of layout sketches for one round of design questions. You
write the round as a JSON file; the bundled script (`scratch_pad.py`,
stdlib-only) checks it, builds the pad as plain files under Outpost's state
directory, and prints a `file://` link. The page opens from disk with nothing
running and no network.

## When to use it

Use it when the question being decided is about how something *looks or is
arranged* and the options differ visually: header layouts, where a control
lives, one screen versus two, list versus grid. Do not use it for questions
whose options differ in behaviour, naming, data or trade-offs that a drawing
cannot show; ask those in words.

## Write the round

One JSON file, written anywhere you can read back (a temp file is fine):

```json
{
  "title": "Settings page",
  "intro": "Where should the account controls live?",
  "questions": [
    {
      "prompt": "Header layout",
      "options": [
        {
          "name": "Split header",
          "cost": "One more component; the avatar menu moves right.",
          "pick": true,
          "layout": {"box": [
            {"row": [{"text": "Logo"}, {"button": "Sign in"}]},
            {"field": "Email"},
            {"rule": true}
          ]}
        },
        {
          "name": "Stacked header",
          "cost": "Simplest; the controls sit below the logo.",
          "pick": false,
          "layout": {"column": [{"text": "Logo"}, {"button": "Sign in"}]}
        }
      ]
    }
  ]
}
```

- `title` is required; `intro` is optional.
- `questions` holds at least one question. Each has a `prompt` and 1 to 26
  `options` (lettered A to Z in order).
- Each option has a `name`, a `cost` (a line or two on what choosing it
  costs), a `layout`, and `pick`. Exactly one option per question has
  `"pick": true`: your own recommendation.
- An unknown key in this outer shape is refused, so a misspelt key is caught
  rather than dropped.

A `layout` is a tree of blocks. Each block is an object with exactly one key
naming its kind:

| Block | Value |
|---|---|
| `box` | list of blocks, in a bordered panel, top to bottom |
| `row` | list of blocks, side by side |
| `column` | list of blocks, top to bottom |
| `text` | a string |
| `button` | a string |
| `pill` | a string |
| `field` | a string label over an empty input |
| `image` | a string caption in a crossed box |
| `rule` | `true` |

Keep drawings schematic: a few blocks per option, enough to show how the
options differ. Every string is shown as text, never as markup. The full
contract, including how a layout the pen cannot read is shown, is in
`docs/design/a-round-of-layout-sketches-opens-from-disk-as-a-pad.md`.

## Build the pad

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/scratch-pad/scratch_pad.py \
  --pad <slug> --round <path-to-round.json>
```

- `<slug>` names the pad and matches `^[a-z0-9][a-z0-9._-]*$`. A pad that
  already exists is refused, so pick a new slug for a new pad.
- On success, line 1 of stdout is the `file://` link and line 2 is
  `pad folder: <path>`.
- On refusal the script exits non-zero and prints its reasons on stderr; no
  pad was made.

## Report it

- **Success:** give the user the printed `file://` link.
- **Refusal:** say in one plain line that no pad was made, followed by the
  script's reasons. Fix the round and rerun only if the reasons make the fix
  obvious; otherwise fall back to asking in words.

Always ask the question in words too, and tell the user to reply in words. The
pad has no answering yet: the "my pick" tag on an option is only your
suggestion, and clicking in the page records nothing.
