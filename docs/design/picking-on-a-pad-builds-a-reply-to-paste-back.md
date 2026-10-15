# Picking on a pad builds a reply to paste back

The answering surface of a scratch pad. Each option card gains "This one" and "Not this" buttons;
each question gains a note box and a "None of these" button; the question list gains a last entry,
"Your reply", where the pad shows one reply built from every answer and a Copy button. The user
pastes that reply back into the session.

What this surface establishes is **the reply**: one JSON block carrying, for every question, the
pick, the options rejected, whether none fit, and the note. It is the part of the shared page frame
the spec-review page builds on, so its shape is the interface this design fixes.

Answers live only in the open page. Nothing is saved and nothing is sent anywhere; reloading the
page starts again from nothing picked.

## The answer to one question

| Part | Set by | Rule |
|---|---|---|
| pick | "This one" on an option | At most one per question. Pressing "This one" on another option moves the pick there; pressing it on the picked option clears the pick. Picking an option clears "None of these" and that option's "Not this". |
| not this | "Not this" on an option | Any number per question; a toggle per option. Marking the picked option clears the pick. |
| none of these | "None of these" under the options | A toggle per question. Turning it on clears the pick and keeps every "Not this". |
| note | the note box under the options | Free text, kept as typed, line breaks included. |

A question is **answered** once it has a pick or "None of these". A note or "Not this" marks alone
do not answer it.

Every button that toggles carries `aria-pressed`, so its state is read out, not only shown.

## The reply

```json
{
  "pad": "Settings page",
  "answers": [
    {
      "question": "Header layout",
      "pick": "B",
      "not": ["C"],
      "none": false,
      "note": "keep the avatar on the right"
    },
    {
      "question": "Nav placement",
      "pick": null,
      "not": ["A"],
      "none": true,
      "note": ""
    },
    {
      "question": "Footer",
      "pick": null,
      "not": [],
      "none": false,
      "note": ""
    }
  ]
}
```

- `pad` is the round's `title`. `answers` holds one entry per question, in the round's order.
- `question` is the question's `prompt`. `pick` is the picked option's letter, or `null`. `not`
  lists rejected options' letters in letter order. `none` is `true` only when "None of these" is
  on. `note` is the note as typed, or `""`.
- The reply is the JSON pretty-printed with two-space indents inside a fenced `json` block, so it
  reads as a block when pasted into a terminal session. Building it through the browser's own JSON
  serialiser means a prompt, title or note holding quotes, backslashes, line breaks or a closing
  fence still comes out as one valid JSON string.
- A question with nothing chosen still has its entry, with `pick: null` and `none: false`, so the
  session can tell "not answered" from "none of these".

## The page

```
┌──────────────────────────────────────────────────────────────────────┐
│ Settings page                                                        │
│ Where should the account controls live?                              │
├────────────────┬─────────────────────────────────────────────────────┤
│ ● Header layout│  Header layout                                      │
│ ○ Nav placement│                                                     │
│ ○ Footer       │  ┌ A  Split header     [my pick] ┐ ┌ B  Stacked ──┐ │
│   Your reply   │  │ [This one] [Not this]         │ │ [This one]✓  │ │
│                │  │ One more component; …         │ │ Simplest; …  │ │
│                │  │  ╭───────────────────────╮    │ │  ╭────────╮  │ │
│                │  │  │ Logo       (Sign in)  │    │ │  │  …     │  │ │
│                │  │  ╰───────────────────────╯    │ │  ╰────────╯  │ │
│                │  └───────────────────────────────┘ └──────────────┘ │
│                │                                                     │
│                │  Note  ┌─────────────────────────────────────────┐  │
│                │        └─────────────────────────────────────────┘  │
│                │  [None of these]                                    │
└────────────────┴─────────────────────────────────────────────────────┘
```

Each card's buttons sit under its letter, name and "my pick" tag and above its cost and drawing, in the order the spec's UI direction gives.
The picked card and the rejected cards are marked by a word on the card as well as by its border,
so the state never rests on colour or line weight alone. Each question entry carries a dot, filled
when the question is answered and hollow when not, with hidden text saying which.

The reply step replaces the question panel when its entry is chosen:

```
│   Your reply   │  Your reply                                         │
│                │                                                     │
│                │  Header layout — B, Stacked header                  │
│                │  Nav placement — none of these                      │
│                │  Footer — no answer yet                             │
│                │                                                     │
│                │  1 question has no answer yet. You can still copy.  │
│                │  ┌─────────────────────────────────────────────┐    │
│                │  │ ```json                                     │    │
│                │  │ { "pad": "Settings page", …                 │    │
│                │  └─────────────────────────────────────────────┘    │
│                │  [Copy reply]   Copied.                             │
```

The summary lists each question's answer by letter and name. The reply sits in a read-only text
box, so it can be selected by hand too. Leaving the reply step and changing an answer rebuilds the
reply; any "Copied" line is cleared, because it no longer describes what is on the clipboard.

Copy tries the browser's clipboard. A page opened from disk is a secure context, so the clipboard
is present, but a browser may still refuse the write — a missing user gesture, a denied
permission, or no clipboard at all. When it does, the page selects the reply in its box and says
how to copy it by hand. There is no third path through the deprecated copy command.

## State — Nothing picked yet

The pad as it opens. Every card shows "This one" and "Not this" unpressed, the note box is empty,
"None of these" is unpressed, and every question's dot is hollow. The reply step lists every
question as "no answer yet", and its reply has `pick: null`, `not: []`, `none: false` and
`note: ""` for each.

## State — One option picked

Pressing "This one" on an option presses it, marks the card as picked, fills the question's dot,
and makes that option's letter the question's `pick` in the reply. The session's own "my pick"
tag is unchanged — it marks the session's suggestion, not the user's answer.

## State — Pick changed to another option

Pressing "This one" on a different option moves the pick: the first card is unpressed and
unmarked, the second is pressed and marked, and the reply's `pick` becomes the second letter.
Pressing "This one" on the picked option clears the pick, and the question's dot goes hollow
again.

## State — Option marked not this

Pressing "Not this" presses it and marks the card as rejected; its letter joins the question's
`not` list in letter order. Pressing it again removes the mark. Marking the picked option clears
the pick. Marking every option is not the same as "None of these": the reply lists every letter
in `not` and keeps `none: false`.

## State — None of these chosen

Pressing "None of these" presses it, clears any pick, keeps every "Not this" mark, fills the
question's dot, and sets `none: true`. Pressing it again sets `none: false` and the dot goes
hollow unless a pick has been made since.

## State — Note added

Text typed in a question's note box becomes that question's `note`, exactly as typed, including
line breaks, quotes and anything that looks like markup. A note alone does not answer the
question.

## State — Reply with some questions unanswered

When any question has neither a pick nor "None of these", the reply step says how many have no
answer yet, above the reply, and those questions read "no answer yet" in the summary. Copy still
works; the reply carries those questions with `pick: null` and `none: false`.

## State — Reply copied

When the clipboard accepts the reply, the step shows "Copied." beside the button. The line is
cleared as soon as any answer changes.

## State — Clipboard unavailable, reply selected for copying

When the clipboard refuses the write or is missing, the reply text in its box is selected and the
step says: "Your browser blocked copying. The reply is selected — press Ctrl+C (⌘C on a Mac)."
Nothing is lost; the text box holds the whole reply.

## State — Same pad open in two tabs

Each tab holds its own answers. Nothing is saved or shared between them, so a pick made in one tab
never shows in the other, and each tab's reply carries only that tab's answers. A tab opened later
starts from nothing picked.
