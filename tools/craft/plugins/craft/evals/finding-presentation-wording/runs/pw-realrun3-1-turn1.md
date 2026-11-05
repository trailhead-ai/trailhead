I've finished the spec review (gauntlet) of *A rainwater tank log for the Marsh End allotment society*. Maturity is production, read from the spec's own stamp. The criterion gate passed on the original draft and on my proposed rewrite.

**What the passes found.** The spec works out each tank's level only from logged draws and top-ups. It has no starting figure, no way to check against the actual tank, and ignores rain, even though these are rainwater tanks. So the level would start undefined and then drift. The 100-litre refusal and the 300-litre alert would both act on a number nobody had measured. Two more problems sit around that. First, the steward's only warning is one email: nothing shows if it fails, and the Problem's "cannot see which tank is lowest" has no criterion. Second, the history can't prove "every change can be checked". Top-ups have no plot number for the history to show, a wrong top-up can never be corrected, nothing stops entries being edited in place, and three different dates are in play. Security found that anyone can act as the steward, and that a negative draw raises the level. The alert criterion leaves the send time, the days-remaining sum and the exact-300 case undecided. Several criteria also bundle things that could be delivered separately.

**Whether it holds.** The level-model finding came independently from six of the eight passes, and it is right. The right-problem pass called the framing broken and asked for a rewrite. On substance I agree with it, but its own "what would change my mind" is a dip reading plus a starting level, and an edit to this spec delivers both. So I'm proposing a fix in the spec, not a rewrite. The silent-alert finding came from four passes, and at production maturity a failure nobody can see is a decision for you. The fact check confirmed only that the repo and vault are empty; it could check none of the claims about the society itself. Two Criticals were moved down to "worth fixing, your call" because they depend on guessing how people will behave or how busy the tanks get. One says refused plot holders will take the water anyway without logging it. The other says two draws at the same instant will clash. Backwards compatibility and blast radius don't apply here, because nothing exists yet that could break.

**What to do.** Base the level on steward dip readings and refuse draws until a tank has its first one. Add one screen showing all four tanks, plus visible alert delivery. Make the history append-only, with corrections for top-ups too. Pin the alert rules. Name the steward sign-in and input checks. Add an export and a nightly backup. The spec's six criteria become 22, renumbered, all drafted and certified.

**Recommended outcome: advance to `ready` this round**, with every Critical fixed in the spec.

| finding | proposed outcome | proposed edit |
|---|---|---|
| *Level has no starting point and drifts from the real water* (C1) | fixed in the spec | *dip readings set the level (AC1–AC3); add a Non-Goal that rain is not estimated* |
| *Alert can fail silently; steward can't see the lowest tank* (C2) | fixed in the spec | *one screen with all tanks, lowest first (AC4); retry failed alerts and show them (AC14); widen the Objectives* |
| *History can't prove every change* (C5) | fixed in the spec | *append-only corrections of draws and top-ups; who and when on every entry (AC16–AC20)* |
| *Alert rule undecided and bundled* (C6) | fixed in the spec | *split into three: re-arm only above 300 (AC11), daily 19:00 UK send (AC12), 14-day days-remaining sum (AC13)* |
| *Draw and refusal criteria bundle two deliverables each* (C7) | fixed in the spec | *split draw from level shown (AC5, AC6) and refusal from pointer (AC9, AC10); add a "100-litre reserve" objective* |
| *No way back if the log loses its data* (C8) | fixed in the spec | *spreadsheet export (AC21); nightly backup, restore tried before go-live (AC22)* |

**Anyone can do the steward's job (C3)**, from security, and also raised by buildability and left-undecided. Fixed in the spec. The spec separates the steward from plot holders but never says how. Anyone with the URL could log a fake top-up, which lifts the level and stops the low-tank email. Edit, a new criterion:
> **AC15.** Only the steward, after signing in, can record top-ups, dip readings and corrections; plot holders record draws without signing in.

**Typed-in figures are never checked (C4)**, from security, and also raised by failure cases. Fixed in the spec. A draw of −5000 litres passes the 100-litre check and raises the level. Edit, two new criteria:
> **AC7.** A draw is refused unless the plot number is one of the society's forty plots, the tank is one of its four tanks, and the litres are a whole number greater than zero; the refusal says which of the three was wrong.
> **AC8.** A top-up or correction whose litres are not a whole number greater than zero, or a dip reading whose litres are not a whole number of zero or more, is refused.

Reply go, or dispute C2: <why>, accept C2 as a risk: <why>, answer C2: <what you know>.

**Worth fixing, your call (7):**
- Refused plot holders may still take the water without logging it, which is the refusal-rule premise (moved down from Critical).
- Two draws at the same instant can clash, and a double tap can count twice (moved down from Critical).
- What a plot holder sees when they're offline, when they mistype the litres, or when the history is empty.
- Who may read each plot's usage, how long it is kept, and rate limits.
- Whether 300 litres leaves enough time for a mains delivery.
- Whether there is phone signal at the tanks.
- Whether the "fullest" tank means most litres or most full by percentage, if the tanks differ in size.

**Rough edges, nothing to decide (5):** the thresholds should be configurable; there's no steward handover; there's no UI direction; the refusal wording is a bit blunt; "straight away" has no number.
