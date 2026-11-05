This is the spec review (gauntlet) for the Marsh End tank log. All eight passes ran, the criterion gate certified the spec, and it was reviewed at production maturity.

**What the passes found.** Almost every pass hit the same gap: the "estimated level" behind the draw display (AC1), the refusal (AC2) and the warning email (AC3). The spec says nothing about where a tank's level starts, and these are rainwater tanks with no record of rain coming in. So after the first wet week the figure is wrong, and nothing ever brings it back to a real reading. Around that gap there were five smaller ones:
- The warning email can fail silently and is never re-sent.
- The spec doesn't say who may record a top-up or a correction, or record a draw against a plot.
- A draw of −500 litres would raise the level.
- No criterion lets the steward see which tank is lowest, even though the Problem names exactly that.
- AC2, AC3 and AC5 each bundle promises that could ship separately.

**Whether it holds.** The level gap is real and is the strongest result: six passes that couldn't see each other's work reached it on their own. The right-problem pass went furthest, saying a gauge would solve two of the three problems more cheaply and asking for a rewrite. But its own prescription was "add a measured reading and capacity", which is an edit to this spec, not a new framing, so I've drafted that edit instead. The fact check confirmed the three claims it could check: the codebase is greenfield, no other vault record depends on or conflicts with this spec, and the project is marked production. The other five (four tanks, forty plots, how people fill cans now, July dry-outs, the steward role) are facts about the society that only you can confirm. That's expected, and it says nothing about the design. The left-undecided pass gave the most usable detail: exact definitions for "same day", "days remaining" and "fullest tank", which I've folded into the splits. I downgraded the buildability pass's "no backup of the history store" to worth-fixing, because it names no concrete failure.

**What to do.** Make a steward-entered measured reading the anchor for every level. Rain and unlogged draws then get corrected at each reading, which also gives the right-problem pass its measured figure to compare against. On top of that, add an all-tanks page, make the warning email confirm and retry, state who may do what, check the numbers people enter, and split the three bundled criteria. That takes the spec from 6 criteria to 18, adds Constraints and Dependencies sections, and adds no new objective. The full drafted text is ready to apply as written.

**Recommended outcome: advance to `ready` this round.** All six serious findings can be fixed by edits to the spec, so none needs a rewrite.

| finding | proposed outcome | proposed edit |
|---|---|---|
| *The level has no starting point and never meets reality (rain)* (C1) | fixed in the spec | *add steward-measured level (AC7), the not-yet-measured state (AC8), and a Constraints line saying rain is corrected only at the next reading; add measured levels and who recorded each entry to the history (AC6)* |
| *The low-tank email can fail silently* (C2) | fixed in the spec | *a tank counts as alerted only after a successful send, with hourly retries (AC13); the all-tanks page shows each low tank's email status (AC14); a Dependencies line for outbound email* |
| *Nobody's access is defined* (C3) — security | fixed in the spec | *full text below* |
| *Entered numbers are unchecked* (C4) — security | fixed in the spec | *full text below* |
| *The steward can't see which tank is lowest* (C5) | fixed in the spec | *all four tanks on one page, lowest first (AC9)* |
| *Three criteria bundle separable promises* (C6) | fixed in the spec | *split AC2 (adding AC15), AC3 (adding AC16 and AC17) and AC5 (adding AC18), pinning a 19:00 daily send, the days-remaining formula and the "no other tank can supply this draw" case* |

**Nobody's access is defined (C3), in full.** Anyone with the URL could record top-ups, corrections, or draws against any plot, which corrupts the figures the steward orders water from. Proposed text:
> **AC10.** Recording a top-up, a correction or a measured level, or setting a tank's capacity, requires signing in as the water steward, and the server refuses each of them otherwise.
> **AC11.** Recording a draw requires the plot's PIN, which the steward issues to that plot's holder; a draw with a wrong PIN is refused and not recorded.
> Constraint: Five wrong PINs for a plot within one hour refuse that plot's draws for the rest of that hour.
> Dependency: Steward sign-in and per-plot PINs (AC10, AC11).

*The per-plot PIN is my choice, and it's the one to look at hardest. The alternative is no login for plot holders, with self-reported plot numbers, which is cheaper to roll out but weakens the "where did the water go" objective.*

**Entered numbers are unchecked (C4), in full.** A draw of −500 litres raises the level and passes the 100-litre check. Proposed text:
> **AC12.** A draw, top-up or measured level is refused, and nothing is recorded, unless its litres are a whole number from 1 up to the tank's capacity (from 0 for a measured level), its tank is one of the society's four tanks and, for a draw, its plot is one of the forty plots; the refusal names the value that was wrong.
> Constraint: Each tank's capacity in litres is set by the steward and bounds every litres figure for that tank (AC12).

Reply go, or dispute C2: <why>, accept C3 as a risk: <why>, answer C1: <what you know>.

**Worth fixing, your call (7):** two people drawing from the same tank at once can both get past the 100-litre check; top-ups can't be corrected, and no draw can be fixed after 7 days; nothing says who can see each plot's usage or how long it's kept; AC2 refuses the log entry but not the tap, which rewards not logging near the bottom; "in time to order a top-up" has no lead time; the history store has no stated backup (downgraded); there's no UI direction for slow-signal double taps or empty history.

**Rough edges, nothing to decide (4):** "Put the tank log online" is a mechanism, not an outcome; "steward" and "water steward" name the same role; AC6 was borderline-compound; accessibility (status shown by colour alone).
