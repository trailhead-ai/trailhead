The `## Gauntlet` section the review wrote onto the spec, as the throwaway record holds it:

````
## Gauntlet

- Retained review evidence — a later reader evaluates what follows as a claim about this spec, not as its settled
  design content (`skills/receiving-code-review/SKILL.md`).
- Adversarial spec review (gauntlet, 2026-11-03): 8 passes — maturity: production (basis: stamp); facts 3/8 confirmed
  (5 facts about the society unverifiable from the vault or code, none refuted); 5 design-changing findings folded in
  (a steward-measured level anchors every estimate; steward sign-in and per-plot PINs; bounds on every entered figure;
  an all-tanks page, lowest first; AC2, AC3 and AC5 split, with "same day", days remaining and "fullest tank" pinned).
  Criticals dispositioned: C1 `resolved` (from proposal), C2 `accepted-as-risk` (operator override — "the spec only
  asks that the steward hears about a low tank in time to order a top-up, so the society will have the steward ring
  round when no email arrives rather than build retries into the log"), C3 `resolved` (from proposal), C4 `resolved`
  (from proposal), C5 `resolved` (from proposal), C6 `resolved` (from proposal) — 5 from proposal, 1 operator
  override. Important 7, Minor 4, detail below.

### Would build the wrong thing, needs your decision

- **The level has no starting point and never meets reality (C1)** — fixed in the spec. Every tank's level is now
  counted from a reading the steward takes off the tank, so rain and unlogged draws stop pushing the figure further
  from the truth. Worst case before the fix: the level fell only with logged draws, "A plot holder records a draw by
  giving a plot number, the tank and the litres taken, and sees the tank's new estimated level straight away" (AC1),
  and rose only with top-ups, "The steward records a mains top-up with the tank, the litres added and the delivery
  date, and the tank's estimated level rises by that amount" (AC4). Nothing recorded rain, capacity or a starting
  level. After a wet week a full tank could read 80 litres, so "A draw that would take a tank below 100 litres is
  refused" (AC2, original wording) blocked every draw and the warning fired falsely. In a dry July with unlogged
  draws the figure read high and the tank ran dry unwarned, which is the Problem's own failure.
  Fix: the steward enters a measured reading and every level counts from the latest one; this added "The steward
  records a measured level for a tank, giving the litres read off the tank and the date of the reading, and from then
  on the tank's estimated level is that reading plus the top-ups and minus the draws recorded after it" (AC7) and
  "Until a tank has its first measured level it shows "not yet measured" in place of a level, draws from it are
  recorded but never refused, and it is never named in a low-tank email" (AC8), plus a Constraints line on rainfall.
  It rewrote "The tank history lists every draw, top-up and correction for a tank, newest first, with the plot
  number, the litres and the date" (AC6) to include measured levels, the recorded date, who recorded each entry, and
  a top-up's delivery date. That rewrite also removed a contradiction: the tank history (AC6) had asked for a plot
  number on top-ups, which have none.
  Raised by: right problem, buildability, failure cases, ease of use, left undecided, self-consistency (security as
  worth fixing) — six independent passes. The right-problem pass prescribed a rewrite, but its prescription was this
  same edit, so it was proposed and accepted as fixed in the spec.
- **The low-tank email can fail silently (C2)** — accepted as a risk: "the spec only asks that the steward hears about
  a low tank in time to order a top-up, so the society will have the steward ring round when no email arrives rather
  than build retries into the log". Worst case: the original email criterion read "When a tank's estimated level
  falls below 300 litres, the water steward receives one email naming the tank and its level. The email is not sent
  again for that tank until its level has risen above 300 litres and then fallen below it once more. If two tanks
  fall below 300 litres on the same day, the steward receives a single email naming both, ordered from the lowest
  level to the highest, and each tank's line states how many days of average draw remain" (AC3). A failed send (mail
  server down, wrong address) uses up the one warning, nothing retries, and nobody sees the failure; the first sign is
  a dry tank. Fix proposed and withdrawn by the override: a tank counts as warned only after a successful send, with
  hourly retries and a visible email status per low tank. The spec therefore carries no retry or delivery-status
  criterion and no dependency line for the outbound email service.
  Raised by: failure cases, ease of use, left undecided (security as worth fixing); rated serious at production
  maturity as a failure nobody would see.
- **Nobody's access is defined (C3)** — fixed in the spec (security). Steward actions now need a steward sign-in
  checked by the server, and draws need the plot's own PIN. Worst case before the fix: anyone with the link could post
  a 5,000-litre top-up, correct someone else's draw, or log draws against any plot, silently corrupting the figures the
  steward orders water from and blaming the wrong plot in the tank history (AC6).
  Fix: added "Recording a top-up, a correction or a measured level, or setting a tank's capacity, requires signing in
  as the water steward, and the server refuses each of them otherwise" (AC10) and "Recording a draw requires the
  plot's PIN, which the steward issues to that plot's holder; a draw with a wrong PIN is refused and not recorded"
  (AC11), a Constraints line locking a plot's draws for the rest of the hour after five wrong PINs, and a Dependencies
  line naming steward sign-in and per-plot PINs. Caveat: the per-plot PIN was the adjudicator's choice over the
  no-login alternative (self-reported plot numbers), which is cheaper to roll out but leaves attribution to whatever
  people type; the lockout lets a guesser block a plot's draws for an hour.
  Raised by: security, buildability, left undecided, self-consistency.
- **Entered numbers are unchecked (C4)** — fixed in the spec (security). Worst case before the fix: a draw of -500
  litres raised the tank's level and passed the 100-litre check; an oversized or non-numeric figure could corrupt the
  level or keep the warning from firing; an unknown tank or plot had no defined outcome.
  Fix: added "A draw, top-up or measured level is refused, and nothing is recorded, unless its litres are a whole
  number from 1 up to the tank's capacity (from 0 for a measured level), its tank is one of the society's four tanks
  and, for a draw, its plot is one of the forty plots; the refusal names the value that was wrong" (AC12), and a
  Constraints line making each tank's steward-set capacity the bound on every figure for that tank.
  Raised by: security (failure cases and ease of use as worth fixing).
- **The steward can't see which tank is lowest (C5)** — fixed in the spec. Worst case before the fix: the Problem says
  "the water steward cannot see which tank is lowest", yet a level appeared only after a draw (AC1) or a refusal
  (AC2), so every criterion could pass with the steward still unable to compare tanks.
  Fix: added "The steward and plot holders can see all four tanks' estimated levels on one page, lowest first, each
  with the date of its last measured level" (AC9).
  Raised by: self-consistency, ease of use.
- **Three criteria bundle separable promises (C6)** — fixed in the spec. Worst case before the fix: the email criterion
  (AC3) bundled the single-tank warning, the combined same-day email and the days-remaining line; "same day" had no
  definition, so one conformant build reported a tank that dipped at 10:00 and was topped up at 15:00 and another
  never did, and "days of average draw" had no window or empty-history answer. The refusal (AC2) bundled "points the
  plot holder to the fullest other tank", a dead end when every tank is near 100 litres. "The steward can correct a
  draw that was entered wrongly within 7 days of it being recorded, and the correction keeps the original figure
  visible beside the new one" (AC5) bundled the correction with its display.
  Fix: the refusal (AC2) now reads "A draw that would take a tank below 100 litres is refused, and the refusal shows
  the tank's current estimated level", with "A refused draw points the plot holder to the other tank with the most
  litres above 100; if no other tank could supply the requested litres and stay at or above 100 litres, the refusal
  says so instead of naming one" (AC15). The email criterion (AC3) now names each tank crossing below 300 litres in
  a daily 19:00 email, with "A low-tank email naming more than one tank lists them from the lowest level to the
  highest" (AC16) and "Each tank's line in a low-tank email states its days remaining: its estimated level minus 100
  litres, divided by the mean litres drawn from it per calendar day over the last 14 days (days with no draws counting
  as zero), rounded down; when that mean is zero the line says "no recent draws"" (AC17). The correction criterion
  (AC5) now reads "The steward can correct the litres of a draw within 7 days of the draw being recorded, and the
  estimated level uses the corrected figure", with "A corrected draw shows its original litres beside the corrected
  litres wherever it is listed" (AC18). A Constraints line sets all dates and times to Europe/London (an assumption
  from the society's setting). Caveat: the single daily send can delay a morning crossing by up to eleven hours.
  Raised by: self-consistency (bundling), left undecided (open definitions), ease of use (refusal dead end).

### Would cause trouble later, worth fixing, your call

- Two simultaneous draws on one tank can both pass the 100-litre check (buildability, failure cases).
- Top-ups have no correction path, and no draw can be fixed after 7 days (failure cases, ease of use, right problem).
- Nothing states who can see each plot's usage in the history, how long it is kept, or how a leaver's data is handled
  (security, left undecided).
- The refusal (AC2) blocks the log entry but not the tap, so near the bottom it rewards not logging (right problem).
- "In time to order a mains top-up" has no stated supplier lead time behind the 300-litre threshold (self-consistency,
  right problem).
- The history store, the spec's audit record, has no stated backup; downgraded from serious because no concrete loss
  scenario was named (buildability, failure cases).
- No UI direction for a slow-signal double tap, an empty history, or a pending or failed save (ease of use).

### Rough edges, nothing to decide

- "Put the tank log online" states a mechanism rather than an outcome (right problem).
- "Steward" and "water steward" name the same role (self-consistency).
- The tank history (AC6) was borderline compound and was left whole (self-consistency).
- Tank level and low status must not rely on colour alone (ease of use).

### Fact check

F1 four tanks, F2 forty plots, F3 draws unrecorded today, F4 July dry-outs, F5 the water steward role: unverifiable
from the vault or code (society records needed). F6 greenfield codebase, F7 no sibling record depended on or in
conflict, F8 production maturity matching CLAUDE.md: confirmed.
````
