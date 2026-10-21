# Picks and notes on a pad survive a reload, kept apart per pad and round

The saved-answers part of a scratch pad's page frame. Every pick, "Not this", "None of these" and
note you make on a round is kept in the browser that made it, and comes back when the round is
reloaded or reopened. Each round of each pad keeps its own answers, so nothing you answered on one
pad or round ever shows on another.

What this surface establishes is **one id per round, and answers kept under it.** A round's id is
made once, when the pad script builds that round, and never changes afterwards. Everything else
here follows from that: a new pad starts blank because its rounds are new; two pads never share
answers because they never share an id; adding a round leaves earlier rounds' answers alone because
their ids travel with them.

## A round's id

When the pad script builds a round, it writes a third line into that round's `round.js`:

```js
window.PAD_ROUND = JSON.parse("...");
window.PAD_ROUND_NUMBER = 2;
window.PAD_ROUND_ID = "3f9c0a6e1b2d4c58a7e6f0d9b8c7a615";
```

The id is 32 lowercase hex characters from the operating system's random source (`secrets`),
written through the same JSON encoding as the script's other lines. It carries no round text and no
meaning; it only has to be different from every other round's.

- **A new pad** gets a new id for its first round, whatever the pad is called. A pad that reuses the
  name of one deleted earlier starts blank.
- **Adding a round** writes a new id for the new round only. Earlier rounds are copied byte for byte,
  as they already are, so their ids, and the answers saved under them, are unchanged.
- **A pad published into a workspace** is built by the same routine, so it gets its ids the same
  way. Republishing it on an add copies earlier rounds byte for byte too.
- The script's read-back of an existing pad still reads only each round's first line. A round built
  by an older kit has no id line and stays exactly as it is.

## What is kept

One entry per round, in the browser's `localStorage`, under the key
`outpost-scratch-pad:<round id>`. The value is JSON:

```json
{"answers": [{"pick": 1, "not": [0], "none": false, "note": "Bigger buttons"}]}
```

There is one answer per question, in question order, in the same shape the page already keeps in
memory: `pick` is an option index or `null`, `not` is the indexes of options marked "Not this",
`none` is whether "None of these" is on, and `note` is the note's text. Indexes rather than letters
or names, because the round an entry belongs to never changes.

`localStorage` rather than `sessionStorage`, so answers survive closing the tab as well as a reload.
They never leave the browser: nothing on the page sends them anywhere.

## Restoring

The page reads the round's entry before it draws the first question, so the question list's dots,
the cards' pressed buttons and marks, the note boxes and the reply all start from the saved answers
— nothing redraws after the page has shown.

Saved data is untrusted. Every site served by Outpost shares one browser origin, and every page
opened from disk shares another, so any page there can list, read and write any key, this round's
included. The checks below guarantee that an entry has the right shape for the round, not that this
round's page wrote it. An entry is used only if it fits the round exactly:

- it is no longer than the longest entry this round could hold (one note at the note cap per
  question, every character escaped), checked before it is parsed;
- it parses as JSON, and is an object whose only own key is `answers` (own-property checks
  throughout, so a `__proto__` key is just an unknown key);
- `answers` is a list with one entry per question in the round;
- each answer has exactly the keys `pick`, `not`, `none` and `note`;
- `pick` is `null` or a whole number naming one of that question's options;
- `not` is a list of distinct whole numbers naming that question's options, without the picked one;
- `none` is `true` or `false`, and is `false` when there is a pick;
- `note` is a string of at most 10,000 characters, the same cap the note box itself enforces.

An entry that fails any of these is ignored as a whole, the round starts blank, and one warning goes
to the browser's console naming the round and the first check that failed. There is no partial
restore: a bad entry has been damaged or written by something other than this round's page, and none
of it is trusted. Restored notes are set as the note box's value and every other restored answer is
shown through the page's existing text paths, so nothing restored is ever read as markup.

A well-formed entry written by another page is used like any other, so a note can carry text the
user did not type. The reply step shows every note before the reply is copied, and the scratch-pad
skill's "Read the reply" tells the session that nothing in a reply, a note included, is ever a reason
to run commands, use tools beyond the design work, or change permissions.

## Saving

The page saves the round's whole entry after every change: each button press, and each keystroke in
a note box. Saving is called from the page's own change handler, beside the reply's hook. Opening a
round saves nothing, so an ignored entry stays where it is until your first change replaces it.

## When the browser can't keep answers

Some browsers refuse storage to pages opened from disk, a user may have turned it off, and a full
store refuses writes. If reading the round's entry throws, the round has no id, or `saved.js` did
not load, the line shows when the page opens; if a write throws, it shows at that change. Either way
the pad goes on working exactly as it does without saving, and shows one line at the top, as a
status line so a screen reader announces it:

> This browser can't keep your answers, so they will be gone if you reload the page. Copy your reply
> before you leave.

The line appears the first time saving fails, and stays.

## Another tab

The same round can be open in two tabs. When one tab saves, the browser tells the other (the
`storage` event), and the other tab takes the saved answers as its own: its dots, cards, note boxes
and reply change to match. The two tabs show the same answers, and the reply copied from either is
current.

- Only an event for this round's own key in `localStorage` is taken. An event for another key, a
  cleared store (no key), or a removed entry is ignored.
- A change from another tab goes through the same checks as a restore; one that fails them is
  ignored, and the tab keeps what it has.
- Taking another tab's answers changes the page's answers in place and saves nothing, so tabs never
  echo a change back and forth.
- A note box you are typing in keeps its text; your next keystroke saves it, so the note being typed
  wins. Every other control still changes to match.
- A tab that comes back into view (`pageshow`, or `visibilitychange` to visible) reads its round's
  entry again and takes it, so a tab that missed an event never saves stale answers over newer ones.

## The shared page frame

The saving is its own kit file, `saved.js`, beside `reply.js`. It is given the round's id and the
round's shape (how many questions, and how many options each has) and reads no page globals, and it
knows nothing about drawings. The spec-review page is expected to keep answers in the same
four-field shape; that is an assumption about a page not yet built, and the contract is stated in
`saved.js`'s own header comment so that page can check it. `pad.js` asks it for the restored answers,
tells it about each change, and hears from it about another tab's changes.

## Known limits

- **A copied pad shares answers with its original.** Copying a pad's folder copies its round ids, so
  the copy and the original are one pad as far as saved answers go.
- **Rounds drawn by an older kit keep no answers.** Each round keeps the kit it was drawn with, so on
  a pad whose first round predates this kit, only later rounds keep answers.
- **Notes are readable by other pages on the same origin.** The spec keeps a pad's answers in the
  browser that made them and accepts that every site there shares one origin; nothing on the page
  sends them anywhere.
- **There is no "start this round over" control.** Answers stay until each is undone by hand or the
  browser's storage is cleared.

## Testing in a real browser

Each test opens a real pad built by the real script, in the real headless browser, from disk and
served under Outpost's sites policy. The harness today runs one page per browser launch with a
throwaway profile, so it needs to drive a page across a reload, from one pad to another, and with
two copies of a round open at once, before any of the states below can be tested. A test that serves
pages takes a free port of its own, and a test that waits on another tab polls for the state it
expects and fails with a message naming it.

## State — a new pad with nothing saved starts blank

A round opened for the first time has no entry. Every question's dot reads "not answered", no
button is pressed, no card shows a mark, every note box is empty, and the reply has no pick on any
question. This is the page as it is today.

## State — picks, rejections, none of these and notes come back after a reload, and the reply is rebuilt from them

You pick B on the first question, mark A "Not this", turn on "None of these" on the second, and
type a note on each. After a reload, every one of those is back: the same buttons are pressed, the
cards show "picked" and "not this", the dots read "answered", and each note box holds its note. The
reply step lists the same answers and its reply text is the one you would have copied before the
reload. The same holds after the tab is closed and the round reopened.

## State — an earlier round and the latest round keep separate answers

Round 1 and round 2 of one pad each keep their own answers. Picking on round 2 changes nothing on
round 1, and going back to round 1 from the round switcher shows round 1's answers as you left them.

## State — two pads opened from disk keep separate answers

Two pads opened from disk share one browser origin. Answers on one never appear on the other, even
when both pads have the same title and the same questions.

## State — a new pad reusing an old pad's name starts blank

A pad that has been answered and then deleted, followed by a new pad built under the same name,
opens blank. The new pad's rounds have new ids, so the old answers are never found.

## State — adding a round keeps the earlier rounds' saved answers

After a round is added, the earlier rounds still show the answers saved on them, and the new round
starts blank.

## State — saved data that is unreadable or names an option no longer there is ignored without breaking the page

An entry that is not JSON, is too long, has the wrong number of answers, names an option the
question does not have, or breaks any other rule above is ignored: the round opens blank, draws
normally, answers normally, and its first save replaces the bad entry. Nothing is shown on the page,
because nothing is wrong with the pad; one warning goes to the console.

## State — when the browser cannot save, the pad still works and answers just do not survive a reload

Picking, rejecting, "None of these", notes, the reply and copying all work as they do today. The
can't-keep line shows at the top of the page, so you know to copy your reply before you leave: from
the start when storage cannot be read, the round has no id or `saved.js` did not load, and from the
first change that fails to save when only writing fails.

## State — the same round open in two tabs

You pick A in one tab. The other tab shows A picked, its dot reads "answered", and its reply names
A, without a reload. A note typed in either tab appears in the other, unless you are typing in that
same note box there. A tab you come back to shows the latest answers.
