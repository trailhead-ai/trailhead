The `## Gauntlet` section the review wrote onto the spec, as the throwaway record holds it:

````
## Gauntlet

- Retained review evidence — a later reader evaluates what follows as a claim about this spec, not as its settled
  design content (`skills/receiving-code-review/SKILL.md`).
- Adversarial spec review (gauntlet, 2026-11-03): 8 passes — maturity: production (basis: stamp); facts 3/8
  confirmed (5 unverifiable: the society's tanks, plots, steward role, current practice and mains top-up service rest
  on the spec alone); 7 design-changing findings folded in (a tank overview and visible alert delivery; a steward-only
  sign-in; input checks on every figure; an append-only history with top-up corrections; pinned alert timing and
  days-remaining arithmetic; split compound criteria and a stated 100-litre reserve objective; an export and a nightly
  backup). Criticals dispositioned: C1 `accepted-as-risk` (operator override — "the spec leaves metering out of
  scope, so the society will keep counting from logged draws and top-ups only and the steward will fix the figure by
  hand when it drifts"), C2 `resolved` (from proposal), C3 `resolved` (from proposal), C4 `resolved` (from proposal),
  C5 `resolved` (from proposal), C6 `resolved` (from proposal), C7 `resolved` (from proposal), C8 `resolved` (from
  proposal) — 7 from proposal, 1 operator override. Important 7, Minor 5, detail below. Criteria were renumbered:
  the draft's six became nineteen.
- **The level has no starting point and drifts from the real water (C1)** — accepted as a risk. The estimated level
  is moved only by draws and top-ups; nothing sets a starting figure and nothing records rain, though these are
  rainwater tanks. Worst case: a wet week fills a tank while the log reads it near empty, so draws are refused and
  false alerts go out; in a dry July unlogged draws keep the figure high and a tank runs dry unwarned. The proposed
  fix (steward dip readings that reset the level, draws refused until a tank's first reading, a Non-Goal on rain) was
  withdrawn by the override. Noted at re-present and left standing by the operator: no criterion gives the steward an
  in-log way to set the figure by hand, since "The steward can correct the litres of a draw or a top-up up to 7 days
  after it was recorded; a later correction is refused" (AC14), and a fix outside the log would break "A correction is
  kept as a new history entry showing the original figure beside the new one, and no history entry is ever changed or
  deleted" (AC15). Raised by: right problem (which judged the framing broken), buildability, failure cases, ease of
  use, left undecided, self-consistency.
- **The low-tank alert can fail silently, and the steward can't see which tank is lowest (C2)** — fixed in the spec.
  The steward's only warning was one email, with no signal if it failed, and the Problem's "cannot see which tank is
  lowest" had no criterion. Worst case: a July alert bounces and the first anyone hears is a plot holder at an empty
  tank. Fix: "Anyone using the log can see all four tanks on one screen, lowest estimated level first, with any tank
  below 300 litres marked as low" (AC1) and "When an alert email cannot be delivered it is retried, and until it is
  delivered the tank screen (AC1) shows beside each low tank that the steward has not yet been told" (AC11); the
  Objectives now name the steward seeing which tank is lowest. Raised by: failure cases, ease of use, buildability,
  security, self-consistency; rated under production failure visibility.
- **Anyone can do the steward's job (C3)** — fixed in the spec. The spec gave top-ups and corrections to the steward
  but never said how the steward is told apart. Worst case: anyone with the address records a 2,000-litre top-up on a
  near-empty tank, the alert never fires and the history cannot say who did it. Fix: "Only the steward, after signing
  in, can record top-ups and corrections; plot holders record draws without signing in" (AC12). Who may read each
  plot's usage is left open (see Important). Raised by: security, buildability, left undecided.
- **Typed-in figures are never checked (C4)** — fixed in the spec. Only the resulting level was checked. Worst case:
  a draw of −5,000 litres passes the 100-litre check by raising the level, and every later figure is wrong. Fix: "A
  draw is refused unless the plot number is one of the society's forty plots, the tank is one of its four tanks, and
  the litres are a whole number greater than zero; the refusal says which of the three was wrong" (AC4) and "A top-up
  or correction whose litres are not a whole number greater than zero is refused" (AC5). Raised by: security, failure
  cases, buildability.
- **The history can't prove every change (C5)** — fixed in the spec. Top-ups had no plot number for the history to
  show, only draws could be corrected, three dates were in play, and nothing forbade editing entries in place. Worst
  case: a 5,000 typed for a 500-litre top-up stays wrong all season and suppresses that tank's alert. Fix: "The steward
  records a mains top-up with the tank, the litres added and the delivery date, which cannot be later than the day it
  is recorded, and the tank's estimated level rises by that amount" (AC13); the correction window (AC14) now covers
  top-ups; the append-only rule (AC15); "The tank history lists every draw, top-up and correction for a tank, newest
  first by the time it was recorded" (AC16); "Each history entry shows who made it (the plot number for a draw, the
  steward for anything else), the litres, and the date and time it was recorded, plus the delivery date for a top-up"
  (AC17). Raised by: self-consistency, buildability, failure cases, security, left undecided.
- **The alert rule leaves key decisions open and bundles three features (C6)** — fixed in the spec. The draft's alert
  criterion forced same-day batching without a send time, gave "days of average draw" no window and no answer for a
  tank with no draws, left exactly 300 litres on neither side, and held three separately deliverable features. Worst
  case: two conforming builds tell the steward "3 days" and "11 days" for the same tank. Fix: "When a tank's estimated
  level falls below 300 litres, whether through a draw or a correction, the steward is sent an alert email naming the
  tank and its level. The tank is not named in another alert until its level has risen to more than 300 litres and
  then fallen below 300 litres again" (AC8); "Alert emails go out once a day at 19:00 UK time, and one email names
  every tank that fell below 300 litres since the previous send, ordered from the lowest level to the highest" (AC9);
  "Each tank's line in an alert email states its days remaining: its estimated level less 100 litres, divided by its
  average daily draw over the previous 14 days and rounded down, or "no recent draws" when it has had none in that
  time" (AC10). The 19:00 send and 14-day window are the adjudicator's choices; a daily send can leave a tank low for
  up to a day before the steward hears. Raised by: left undecided, self-consistency, buildability, failure cases, ease
  of use.
- **The draw and refusal criteria each bundle two things (C7)** — fixed in the spec. The draft's draw criterion joined
  recording with showing the level, and its refusal criterion joined refusing with pointing elsewhere; no objective
  asked for a reserve, and the pointer could send a plot holder to another empty tank. Fix: "A plot holder records a
  draw by giving a plot number, the tank and the litres taken" (AC2); "Once a draw is recorded, the plot holder sees
  the tank's new estimated level straight away" (AC3); "A draw that would take a tank's estimated level below 100
  litres is refused, and the refusal shows the tank's current estimated level" (AC6); "The refusal for a draw below the
  reserve names the other tank with the most estimated litres, or, when no other tank holds more than 100 litres, says
  that every tank is low and to contact the steward" (AC7); the Objectives now name a 100-litre reserve. Raised by:
  self-consistency, ease of use, right problem.
- **There's no way back if the log loses its data (C8)** — fixed in the spec. Nothing covered loss or corruption of
  the season's history. Worst case: the hosting lapses mid-season and the record of where the water went is gone. Fix:
  "The steward can download every tank's full history as a spreadsheet file at any time" (AC18) and "The log is backed
  up every night, and restoring it from that backup is tried once before the log goes live" (AC19). Backwards
  compatibility and cross-consumer blast radius do not apply: no system or consumer exists yet (fact check). Raised
  by: buildability, failure cases; rated under rollback and reversibility.
- Important (would cause trouble later, worth fixing, your call): refused plot holders may still take water unlogged,
  and the reserve is a policy software cannot enforce (moved down from Critical: depends on guessing behaviour);
  simultaneous draws on one tank can both pass the reserve check, and a double tap can count twice (moved down from
  Critical: depends on guessing load); no direction for offline, mistyped-litres or empty-history states; who may read
  each plot's usage, how long it is kept, and rate limits on the open draw form; whether 300 litres leaves time for a
  mains delivery; whether there is signal at the tanks; whether "most estimated litres" is right if the tanks differ in
  size.
- Minor (rough edges, nothing to decide): thresholds as configuration; steward handover; no UI direction; blunt
  refusal wording; "straight away" has no figure.
````
