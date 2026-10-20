# A flow written as Mermaid text is drawn on a pad as a hand-drawn diagram

The flow surface of a scratch pad. An option on a pad can be a flow (steps and the arrows between
them) instead of a screen layout. The session writes the flow in a small part of Mermaid's flowchart
syntax, and the pen draws it with the same wobbly double stroke it uses for layouts.

```json
{
  "name": "Ask, then draw",
  "cost": "One more step before the pad opens.",
  "pick": true,
  "flow": "flowchart LR\n  q[A question comes up] --> v{Is it visual?}\n  v -->|no| t[Ask in words]\n  v -->|yes| d[Draw a pad]:::accent\n  d --> t"
}
```

What this surface establishes is **one pen for every drawing.** A flow and a layout differ only in
where the pen gets its shapes. A layout's shapes come from the boxes the browser laid out; a flow's
shapes come from its own layered layout. Every shape is traced by the same stroke routine, with the
same seeded wobble.

## A round's questions

An option carries exactly one of two drawing keys:

- `layout`: a tree of blocks, unchanged.
- `flow`: a string of flow text (below).

The pad script checks only the outer shape: an option with both keys, or with neither, is refused
("give each option either a layout or a flow"), and a `flow` that is not a string is refused ("must
be a string"). What is inside the flow text is never read by the script. The pen reads it when the
page draws it, and anything it cannot draw shows the "can't draw" message in that option's place.
So a flow with a mistake still builds a pad (exit 0), exactly as a bad layout block does today.

Nothing else in the round changes. The reply, the rounds list and the read-back of an existing pad
are untouched. A round with flows can be added to a pad whose earlier rounds were drawn by a kit
that knows nothing of flows: each round keeps its own kit, so the earlier rounds draw exactly as
they did.

## The flow text

A flow is read line by line. Leading and trailing spaces and one trailing `;` on a line are ignored,
and blank lines are skipped.

**The first line that is not blank and not a styling line is the header:** `flowchart LR`,
`flowchart TD` or `flowchart TB` (`graph` is accepted in place of `flowchart`; `TB` means the same as
`TD`). `LR` draws left to right and `TD` top down. Styling lines and `%%{ … }%%` directives (see
below) may come before it. Any other first line is refused, and the message names that line.

**A step** is written as an id, optionally followed by its shape and label:

| Written | Drawn as |
|---|---|
| `a` | a plain box labelled with its id |
| `a[Label]` | a plain box |
| `a(Label)` | a box with rounded corners |
| `a([Label])` | a pill (fully rounded ends) |
| `a{Label}` | a decision (a diamond) |

An id is letters, digits and `_`. A label may be wrapped in double quotes (`a["Label (with
brackets)"]`), which lets it hold the bracket characters. A step's first labelled appearance sets its
shape and label; a later bare mention refers to it. A step written with a different shape or label a
second time is refused, naming the line, because the two are contradictory.

`:::accent` after a step marks it as what the question is about (see "Drawing it"). Any other
`:::<name>` is a styling class and is ignored.

**An arrow** joins two steps:

| Written | Drawn as |
|---|---|
| `a --> b` | a plain arrow |
| `a -.-> b` | a dashed arrow |
| `a -->\|label\| b` or `a -- label --> b` | a plain arrow with a label (a space before the first bar, `a --> \|label\| b`, is accepted) |
| `a -.->\|label\| b` or `a -. label .-> b` | a dashed arrow with a label |
| `a --> b --> c` | a chain: one arrow per link, in order |

A line holding a single step, with no arrow, declares that step.

One newline at the very end of the text ends the last line; it is not a further line, so it does not
count toward the line limit.

**Lines that only style a diagram are ignored:** lines starting with `classDef`, `class`, `style` or
`linkStyle`, and `%%{ … }%%` configuration directives.

**Every other line makes the flow one that cannot be drawn,** named by its line number and text.
That includes plain `%%` comments, `RL` and `BT` directions, `subgraph`/`end`, `&` between steps,
thick (`==>`), open (`---`), circle-ended (`--o`) and cross-ended (`--x`) links, two-way arrows,
other node shapes (`((circle))`, `{{hexagon}}`, `[(database)]`, `>flag]`, `[/slanted/]`), `click`
lines, and any arrow whose text the patterns above do not match whole. A line is either understood
completely or refused; nothing on a line is ever dropped silently.

A label is shown exactly as written. `<br>`, HTML tags and Mermaid's `#quot;` entity codes are
shown as those characters. The pen wraps long labels itself.

## Laying a flow out

The layout is a small layered layout, run in the page:

1. **Loops.** Arrows are taken in the order they were written. An arrow whose target can already
   reach its source through the arrows kept so far would close a loop: it becomes a **loop back**
   and is set aside. An arrow from a step to itself is a loop back too. Every other arrow is a
   forward arrow, and the forward arrows have no loop.
2. **Layers.** Each step's layer is the length of the longest chain of forward arrows leading into
   it; a step nothing leads into is in layer 0. Steps that nothing joins sit in layer 0.
3. **Waypoints.** A forward arrow that spans more than one layer gets a waypoint in every layer it
   crosses, so it travels in its own lane instead of through the steps in between.
4. **Order within a layer.** Layer 0 keeps the order steps first appeared in. Each later layer is
   sorted by the average position of what leads into each step (or waypoint), ties keeping first
   appearance; one sweep down, then one sweep up, then one down again.
5. **Sizes and places.** Each label is set in the pad's font and measured by the browser, wrapped
   to at most about ten words' width, and its shape is sized around it. Layers are placed one after
   another along the flow's direction, each centred across it.

Every step is a pure function of the flow text and the measured label sizes. Nothing depends on the
page width.

## Drawing it

- **The pen.** Every outline, arrow line and arrowhead is drawn by the pen's one stroke routine:
  each stroke twice, each pass with its own small seeded wobble. A flow uses the same seed as a
  layout in the same place on the pad (one per question and option), and draws its strokes in a
  fixed order (steps in order of first appearance, then forward arrows in the order written, then
  loop backs in the order written), so the same flow draws the same strokes every time.
- **Steps.** A plain box, a rounded box, a pill and a diamond, traced around the measured label. The
  label sits centred inside, as SVG text set through `textContent`.
- **Arrows.** A forward arrow leaves its source on the side facing the next layer (right for `LR`,
  bottom for `TD`), runs through its waypoints, and enters its target on the opposite side, ending
  in a two-stroke arrowhead. A dashed arrow is drawn in dashes. A label sits on the arrow's first leg
  across the gap beside its source column, on a halo in the colour of the card the drawing sits on so
  the line does not run through the letters. Each gap is widened to fit the labels of the arrows
  leaving the column before it, with room either side for an arrowhead, so a halo never reaches a step
  or a head. When two labels in one gap would overlap, one slides along its own arrow's leg to the
  nearest place where it does not, and the gap widens further if the leg has no such place. The
  drawing grows to hold every label and halo, on either side of the flow, so none is clipped, and the
  loop lanes begin beyond the furthest label.
- **Loop backs.** Each loop back gets its own lane outside the flow: below it for `LR`, to the right
  of it for `TD`. The first loop back written takes the lane nearest the flow, the next the one
  beyond it. A loop back leaves its source from the side facing the lane, runs along the lane, and
  enters its target from that same side. It never crosses a step.
  Loop backs that end on the same step's lane-side edge are spread along it, each end at its own
  point (written order, evenly spaced, inside the edge), so no two arrowheads or legs coincide; a
  step with a single loop back keeps its end at the middle of the edge. A loop back's label sits at
  the middle of its run along the lane, not at the middle of its first leg: that leg is a short drop
  beside its step, and a halo there would cover the step's outline.
- **The accent mark.** A step marked `:::accent` is outlined in the page's accent colour and filled
  with diagonal hatching that stays inside its outline. Only the step is marked; its arrows stay in
  ink.
- **Size.** A flow draws into its own SVG, sized to the flow's natural size, with a viewBox of the
  same size. When the card is narrower, the drawing scales down to fit its width, keeping its
  proportions; it never scales up, and never below 0.6 of its natural size: a flow that would need
  more shrinking than that keeps 0.6 and the card scrolls sideways, so its labels stay readable.
  Scaling changes nothing inside the drawing, so its strokes are the same at every page width.
- **Where it lives.** The parser and the layout are their own kit file, `flow.js`, loaded before
  the pen; the pen traces what the layout returns. Every table keyed by a step id is a `Map`, so an
  id such as `__proto__` or `constructor` is an ordinary step.

## When a flow can't be drawn

A flow the pen cannot draw shows a message in that option's place, instead of an empty box:

```
Can't draw this flow:
line 4: a --x b
"--x" is not an arrow this pen draws.
line 7: %% the retry path
A "%%" comment is not part of a flow this pen draws.
Ask the session to redraw this option.
```

- The first line is always `Can't draw this flow:`.
- Then every line that broke, up to five, in order: its number in the flow text (counting from 1,
  blank lines included) and its text, cut to 200 characters with `…`, followed by what is wrong in
  plain words. More than five end with `and <n> more`. A problem with no single line (an empty flow,
  a limit) names the flow as a whole instead, and is the only problem listed.
- The last line is always `Ask the session to redraw this option.`, so a user who has never seen
  the flow text knows what to do with the message.
- Every line is set as text. A line of flow text holding a script tag shows as those characters.

Only that option's drawing shows the message; the other options on the question draw normally.

**Limits.** A flow holds at most 400 lines, no line longer than 1000 characters, 60 steps and 120
arrows. Lines and characters per line are checked before any line is read; steps and arrows are
counted by reading the flow, which those first two limits already bound, and are checked before
layout. Over any limit, the flow cannot be drawn: only the limit message shows, naming the number
found, never a broken line that turns up later. Long arrows add at most 2000 waypoints
in all, and a label word wider than a step is broken across lines. The limits keep a flow written in
error, or one copied from somewhere hostile, from hanging the page: the largest flow they allow
draws in under a second.

## Light and dark

The pad follows the operating system's light or dark setting (`prefers-color-scheme`), since a pad
has no switch of its own. In dark mode the paper is dark and the ink light, for the page and for
every drawing: the pen draws in the page's ink colour, never a fixed black, so layouts follow too.
The accent colour has a light and a dark variant, each readable on its own paper. Cards, buttons,
the note box and the font notice take the dark palette too.

## State — left-to-right flow drawn

A flow headed `flowchart LR` draws its layers from left to right. Each step is in its own shape,
traced in two wobbly passes around its label, and each arrow runs from the right side of its source
to the left side of its target, its arrowhead pointing at the target. A flow wider than its card
scales down to fit the card's width.

## State — top-down flow drawn

A flow headed `flowchart TD` or `flowchart TB` draws its layers from top to bottom. Arrows leave a
step's bottom edge and enter the next step's top edge, the arrowheads pointing down. `graph TD` draws
the same as `flowchart TD`.

## State — flow with decisions and labelled, dashed and chained arrows

A decision is a diamond around its label. A labelled arrow carries its label on its first leg
across the gap beside its source, on a halo in the colour of the card the drawing sits on, kept inside the
drawing and clear of the other labels and of the loop lanes. A dashed arrow is drawn in dashes, both passes. A chain
`a --> b --> c` draws two arrows, `a` to `b` and `b` to `c`. An arrow that skips a layer runs
through its own lane and crosses no step.

## State — flow with a loop back routed around the flow

An arrow that would close a loop (`r --> b` after `b --> … --> r`) is drawn as a loop back: out of
its source towards the lane, along a lane outside the flow (below it for `LR`, to its right for
`TD`), and into its target from the same side, arrowhead pointing at the target. Two loop backs take
two lanes, the first written nearest the flow. A step's arrow to itself loops out and back into the
same step. No loop back crosses a step. Loop backs sharing a step's edge end at separate points along it.

## State — flow carrying styling lines, drawn with them ignored

A flow with `classDef`, `class`, `style` or `linkStyle` lines, or a `%%{init: …}%%` directive,
draws exactly as the same flow without them: the same steps, the same arrows, the same strokes. A
`:::<name>` class other than `accent` on a step is ignored the same way.

## State — step carrying an accent mark

A step written with `:::accent` is outlined in the accent colour and hatched with diagonal lines
that stay inside its outline: inside the box, inside the pill's rounded ends, inside the diamond.
Every other step, and every arrow, stays in ink. Several steps may carry the mark.

## State — empty flow text shows the cannot-be-drawn message

A `flow` that is the empty string, only blank lines, a header alone, or a header with only styling
lines shows the message:

```
Can't draw this flow:
the whole flow
The flow has no steps.
Ask the session to redraw this option.
```

The pad is still built and the option's name, cost and buttons are there; only its drawing is the
message.

## State — unsupported line shows the message naming that line

A flow holding any line outside the supported part shows the message naming each such line (up to
five) by its number and text, with what is wrong: `subgraph` is not drawn, `RL` is not a direction this pen
draws, a `%%` comment is not part of a flow it draws, and so on. A first line that is neither a header nor a
styling line names that line, by its own number. A flow over a limit names the limit. The other options on the question draw normally.

## State — label written as markup shows as plain characters

A step label, an arrow label or a refused line holding `<script>…</script>` or
`<img src=x onerror=…>` shows as those characters, in the drawing or in the message. No element is
created from it and nothing in it runs.

## State — flow and layout options side by side in one question

A question may mix options: one drawn from a layout, the next from a flow. Both are traced by the
same pen. Their strokes have the same weight and the same wobble, each shape drawn twice, so neither
looks more finished than the other. Each option has its own seed, so no two drawings share their
wobble.

## State — same pad redrawn at the same width gives identical strokes

Opening the same pad twice at the same page width draws every stroke of every drawing, layout and
flow alike, with the same path data. A flow's strokes are the same at every width, since only its
scale changes. A layout's strokes follow its boxes, which move with the width, so they repeat at the
same width.

## State — page in dark mode

With the operating system set to dark, the pad opens on dark paper with light ink: the page text,
the cards, every layout and flow drawing, and the accent mark in its dark variant. Hatching and
dashed arrows stay readable. Switching the system setting while the pad is open redraws in the other
palette without a reload.
