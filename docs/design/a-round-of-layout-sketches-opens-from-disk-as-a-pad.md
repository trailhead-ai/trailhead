# A round of layout sketches opens from disk as a pad

The rendered surface for a scratch pad: a page a session builds when a design question is visual,
so the options can be compared as drawings rather than read as descriptions. A session writes one
round of questions as a JSON file; the pad script checks its shape, builds the pad into a folder
under the user's state directory, and prints a `file://` link. The page opens from that folder
with nothing running and no network.

```
scratch_pad.py --pad settings-header --round round.json
# → file:///home/<user>/.local/state/outpost/pads/settings-header/index.html
```

What this surface establishes is that **a pad is plain files.** The kit — page, pen, stylesheet,
font and its licence — is copied into the round's own folder beside the round's data, so the pad
needs no server, no install and nothing in the project it was drawn for.

## A round's questions

One JSON object per round. Every string in it is untrusted text: the session may have copied it
from files, pages or tool output, so the page only ever shows it as text.

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
            {"image": "Hero"},
            {"rule": true},
            {"pill": "New"}
          ]}
        }
      ]
    }
  ]
}
```

- `title` — required, non-empty. `intro` — optional.
- `questions` — required, at least one. Each has a non-empty `prompt` and at least one option, at
  most 26 (options are lettered A–Z in order).
- Each option has a non-empty `name`, a non-empty `cost`, and a `layout`. Exactly one option per
  question carries `"pick": true` — the session's own pick.
- An unknown key anywhere in this outer shape is refused, so a misspelt `costs` is caught rather
  than silently dropped.

The script checks this outer shape only. What is inside `layout` is read by the pen, when the page
draws it.

## Layout blocks

A layout is a tree of blocks. Each block is a JSON object with exactly one key, naming its kind.

| Block | Value | Drawn as |
|---|---|---|
| `box` | list of blocks | a bordered panel stacking its blocks top to bottom |
| `row` | list of blocks | its blocks side by side, no border |
| `column` | list of blocks | its blocks top to bottom, no border |
| `text` | string | a line of lettering |
| `button` | string | lettering in a rounded box |
| `pill` | string | lettering in a capsule |
| `field` | string | a label over an empty input box |
| `image` | string | a box crossed corner to corner, its caption in the middle |
| `rule` | `true` | a single line across its container |

The browser lays the blocks out as ordinary page elements. The pen then measures each element's
box and traces it on an SVG layer above: every stroke is drawn twice, with a slight wobble, in
black ink on paper. The wobble comes from a random source seeded per option, so a drawing does not
shift between loads.

## The page

```
┌──────────────────────────────────────────────────────────────────────┐
│ Settings page                                                        │
│ Where should the account controls live?                              │
├────────────────┬─────────────────────────────────────────────────────┤
│ ● Header layout│  Header layout                                      │
│ ○ Nav placement│                                                     │
│                │  ┌ A  Split header      [my pick] ┐ ┌ B  Stacked ─┐ │
│                │  │ One more component; …          │ │ Simplest; … │ │
│                │  │  ╭─────────────────────────╮   │ │  ╭────────╮ │ │
│                │  │  │ Logo        (Sign in)   │   │ │  │  …     │ │ │
│                │  │  ╰─────────────────────────╯   │ │  ╰────────╯ │ │
│                │  └────────────────────────────────┘ └─────────────┘ │
└────────────────┴─────────────────────────────────────────────────────┘
```

A short title and introduction across the top. The list of questions on the left; one question
at a time in the middle, chosen by clicking its entry. Each option is a card: its letter, its
name, a "my pick" tag on the session's choice, one or two lines on what it costs, then the
drawing. All lettering is Architects Daughter, loaded from the font file inside the round's
folder.

## On disk

```
<outpost state dir>/pads/<pad>/
  index.html            sends the browser to the latest round
  round-1/
    index.html  pad.css  pad.js  pen.js
    ArchitectsDaughter-Regular.ttf  OFL.txt
    round.js            the round's questions, as data
```

The pad slug matches `^[a-z0-9][a-z0-9._-]*$`. The pads live under Outpost's state directory as
trailhead resolves it on every OS — `$OUTPOST_STATE_DIR`, else `$XDG_STATE_HOME/outpost`, else
`~/.local/state/outpost` — never inside the current project. The pad is built in a temporary folder beside its final place and
renamed in at the end, so a failure leaves no partial pad. A pad whose folder already exists is
refused.

The round's data ships as its own script file assigning a JSON value. A page opened from disk
cannot fetch a JSON file, and keeping the data out of the HTML means round text never passes
through the HTML parser.

## State — One question

The question list holds a single entry, already selected; the middle shows that question's
options. The list stays, so the page has the same frame whatever the round holds.

## State — Many questions

The question list holds one entry per question, in the round's order, with the first selected on
load. Clicking an entry shows that question's options in the middle and marks the entry as
current; every other question's options are hidden. Each question's drawings are traced when the
question is first shown, so every drawing is measured at its real size.

## State — A drawing that cannot be drawn

A layout the pen cannot read — a block with no key or more than one, a kind it does not know, a
value of the wrong type — shows a plain message in the drawing's place instead of an empty box:

```
┌ C  Sidebar ──────────────────────────────┐
│ Lets the menu grow; one more breakpoint. │
│ ┌──────────────────────────────────────┐ │
│ │ Can't draw this layout:              │ │
│ │ box › 2 › {"slider": "Volume"}       │ │
│ │ "slider" is not a block kind.        │ │
│ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

The message names the block that broke by its position in the tree and quotes it, as text. The
option's letter, name, pick tag and cost still show, and every other drawing on the page is drawn
as usual.

## State — Round refused before writing

A round whose outer shape is wrong never becomes a page. The script prints one line naming what
is wrong and where, exits non-zero, and writes nothing:

```
scratch-pad: questions[0].options[1]: no "cost" — every option states what it costs
scratch-pad: questions[2]: no option has "pick": true — mark the session's own pick
scratch-pad: questions: empty — a round asks at least one question
scratch-pad: pad "settings-header" already exists
```

A round with no questions, an option with no cost, and a question with no pick (or more than one)
all land here, as do unreadable JSON, an unknown key, and a bad pad slug.
