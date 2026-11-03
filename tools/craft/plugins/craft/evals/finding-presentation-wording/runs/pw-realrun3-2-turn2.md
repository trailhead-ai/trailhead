# What the spec review found that needs your decision

All six are proposed as **fixed in the spec**, so none needs a rewrite. Each one shows the drafted text that "go" would apply, word for word.

---

### The level has no starting point and never meets reality (C1)

**Proposed: fixed in the spec.** Decide whether every tank's level should be anchored to a reading the steward takes off the tank. I recommend it, because otherwise the number that drives refusals and warnings drifts further from the truth with every rain and every unlogged can.

**Worst case:** The spec says the level falls with each logged draw, "A plot holder records a draw by giving a plot number, the tank and the litres taken, and sees the tank's new estimated level straight away" (AC1). It rises only with a mains top-up, "The steward records a mains top-up with the tank, the litres added and the delivery date, and the tank's estimated level rises by that amount" (AC4). But these are rainwater tanks, and nothing in the spec records rain, a capacity or a starting level. After a wet week a full tank can read 80 litres, so "A draw that would take a tank below 100 litres is refused" (AC2) blocks every draw from a full tank, and the warning email fires falsely. In a dry July with unlogged draws the opposite happens: the figure reads high, the tank runs dry and no warning goes out. That is the exact failure the Problem section describes.

**Fix:** the steward enters a measured reading, and every level is counted from the latest one. Rain, leaks and unlogged draws get corrected at each reading. This adds two criteria and a Constraints line, and rewrites the tank history (AC6), "The tank history lists every draw, top-up and correction for a tank, newest first, with the plot number, the litres and the date."
> **AC7.** The steward records a measured level for a tank, giving the litres read off the tank and the date of the reading, and from then on the tank's estimated level is that reading plus the top-ups and minus the draws recorded after it.
> **AC8.** Until a tank has its first measured level it shows "not yet measured" in place of a level, draws from it are recorded but never refused, and it is never named in a low-tank email.
> **AC6 (rewritten).** The tank history lists every draw, top-up, measured level and correction for a tank, newest first by the time it was recorded, showing the litres, the date recorded, who recorded it (the plot number for a draw, the steward otherwise) and, for a top-up, its delivery date.
> Constraint: Rainfall, overflow, leaks and draws nobody logged are not estimated; they reach a tank's estimated level only through its next measured level (AC7).

The AC6 rewrite also fixes a contradiction the self-consistency pass found: the old text asked for a plot number on top-ups, which don't have one.

**Raised by:** right problem, buildability, failure cases, ease of use, left undecided, self-consistency; security raised it as worth fixing. These six passes reached it independently, which is the strongest agreement in this run. The right-problem pass asked for a rewrite, but its prescription was this same edit, so I've proposed it as a fix in the spec.

---

### The low-tank email can fail silently (C2)

**Proposed: fixed in the spec.** Decide whether a warning should only count once it has actually been sent. I recommend it, because the warning is the spec's whole answer to tanks running dry, and as written a single failed send uses it up.

**Worst case:** The spec says "the water steward receives one email naming the tank and its level. The email is not sent again for that tank until its level has risen above 300 litres and then fallen below it once more" (AC3). If the mail server is down, or the steward's address is wrong, the system has already marked the tank as warned. It never tries again, nobody sees that it failed, and the first sign is a dry tank in July.

**Fix:** a tank counts as warned only after a successful send. A failed send retries hourly, and the steward can see each low tank's email status. This adds two criteria and a Dependencies line.
> **AC13.** A tank counts as named in a low-tank email only once that email has been sent successfully, and a failed send is retried every hour until it succeeds.
> **AC14.** The all-tanks page shows, beside each tank below 300 litres, when its low-tank email was sent, or the reason it has not been sent yet.
> Dependency: An outbound email service that reports whether each message was accepted (AC3, AC13).

**Raised by:** failure cases, ease of use and left undecided; security raised it as worth fixing. At production maturity, a failure nobody would notice counts as serious.

---

### Nobody's access is defined (C3) — security

**Proposed: fixed in the spec.** Decide who may do what. I recommend a steward sign-in for steward actions and a per-plot PIN for draws, because as written anyone with the link can rewrite the figures the steward orders water from.

**Worst case:** The spec names a plot holder and a steward but no sign-in and no role check. "Put the tank log online" (Objectives) with nothing else means anyone with the URL can post a 5,000-litre top-up, correct someone else's draw, or log draws against any plot number. The level is corrupted silently, and the history (AC6) blames the wrong plot. That defeats the objective that "every change to a tank's figures can be checked afterwards".

**Fix:** steward actions need a steward sign-in, enforced on the server. Draws need the plot's own PIN, and repeated wrong guesses are locked out. This adds two criteria, a Constraints line and a Dependencies line:
> **AC10.** Recording a top-up, a correction or a measured level, or setting a tank's capacity, requires signing in as the water steward, and the server refuses each of them otherwise.
> **AC11.** Recording a draw requires the plot's PIN, which the steward issues to that plot's holder; a draw with a wrong PIN is refused and not recorded.
> Constraint: Five wrong PINs for a plot within one hour refuse that plot's draws for the rest of that hour (AC11).
> Dependency: Steward sign-in and per-plot PINs (AC10, AC11).

**Caveat, and the one choice here that is mine rather than the passes':** the per-plot PIN. The alternative the left-undecided pass offered is no login for plot holders, with self-reported plot numbers. That's cheaper to roll out across forty plots, but then the history only records what people typed. The lockout also means someone who guesses wrong five times can block a plot's draws for an hour. That's a nuisance, not a security hole. If you'd rather take the no-login route, "answer C3:" with that choice and I'll redraft.

**Raised by:** security, buildability, left undecided, self-consistency.

---

### Entered numbers are unchecked (C4) — security

**Proposed: fixed in the spec.** Decide whether the spec should state what counts as a valid entry. I recommend it, because the spec as written accepts entries that raise the level or break the warning check.

**Worst case:** A plot holder records a draw by giving "a plot number, the tank and the litres taken" (AC1), and nothing limits those values. A draw of −500 litres raises the tank's level and passes the 100-litre check. A huge or non-numeric figure can corrupt the stored level or stop the warning (AC3) from firing. An unknown tank or plot has no defined outcome.

**Fix:** entries outside sensible bounds are refused, nothing is recorded, and the refusal says which value was wrong. This adds one criterion and a Constraints line:
> **AC12.** A draw, top-up or measured level is refused, and nothing is recorded, unless its litres are a whole number from 1 up to the tank's capacity (from 0 for a measured level), its tank is one of the society's four tanks and, for a draw, its plot is one of the forty plots; the refusal names the value that was wrong.
> Constraint: Each tank's capacity in litres is set by the steward and bounds every litres figure for that tank (AC12).

Whole litres is a deliberate simplification, since cans are filled to whole litres.

**Raised by:** security; failure cases and ease of use raised it as worth fixing.

---

### The steward can't see which tank is lowest (C5)

**Proposed: fixed in the spec.** Decide whether to add a criterion for the problem the spec opens with. I recommend it, because no criterion delivers it today.

**Worst case:** The Problem says "the water steward cannot see which tank is lowest". The only place a level appears is after recording a draw (AC1) or being refused (AC2). Every criterion could pass and the steward would still have to log a fake draw on each tank to compare them.

**Fix:** one page shows all four tanks, lowest first. This adds one criterion:
> **AC9.** The steward and plot holders can see all four tanks' estimated levels on one page, lowest first, each with the date of its last measured level.

That page is also where the email status from the low-tank email fix (C2) appears.

**Raised by:** self-consistency and ease of use.

---

### Three criteria bundle separable promises (C6)

**Proposed: fixed in the spec.** Decide whether to split three criteria that each pack in promises that could ship separately. I recommend the split. The review's rules treat a bundled criterion as serious, and the split is also where I've pinned three definitions the spec left open.

**Worst case:** The warning email (AC3) bundles the single-tank warning, the combined "two tanks on the same day" email, and the "days of average draw remain" line. Because "same day" was never defined, two builds that both pass disagree on when the email is sent. One sends at day's end, so a tank that dipped at 10:00 and was topped up at 15:00 is never reported. The other reports it. "Days of average draw" had no window and no answer for a tank with no draws, so a manual tester can't say which figure is right. The refusal (AC2) also bundles pointing "the plot holder to the fullest other tank", which is a dead end when every tank is near 100 litres. The correction criterion (AC5), "The steward can correct a draw that was entered wrongly within 7 days of it being recorded, and the correction keeps the original figure visible beside the new one", bundles the correction with how it is displayed.

**Fix:** each bundled criterion keeps its core promise, and the rest moves to new criteria, with "same day", "days remaining" and "fullest tank" pinned. This rewrites the refusal (AC2), the warning email (AC3) and the correction criterion (AC5), adds four criteria, and adds a Constraints line:
> **AC2.** A draw that would take a tank below 100 litres is refused, and the refusal shows the tank's current estimated level.
> **AC15.** A refused draw points the plot holder to the other tank with the most litres above 100; if no other tank could supply the requested litres and stay at or above 100 litres, the refusal says so instead of naming one.
> **AC3.** When a recorded entry takes a tank's estimated level from 300 litres or more to below 300 litres, the tank is named, with its level, in the next low-tank email to the water steward. Low-tank emails go out at 19:00 on each day with at least one such tank. A tank is not named again until its level has risen to 300 litres or more and then fallen below 300 litres once more.
> **AC16.** A low-tank email naming more than one tank lists them from the lowest level to the highest.
> **AC17.** Each tank's line in a low-tank email states its days remaining: its estimated level minus 100 litres, divided by the mean litres drawn from it per calendar day over the last 14 days (days with no draws counting as zero), rounded down; when that mean is zero the line says "no recent draws".
> **AC5.** The steward can correct the litres of a draw within 7 days of the draw being recorded, and the estimated level uses the corrected figure.
> **AC18.** A corrected draw shows its original litres beside the corrected litres wherever it is listed.
> Constraint: All dates, days and times are Europe/London.

**Caveats:** A single 19:00 daily send means a tank that crosses 300 litres at 08:00 isn't reported for eleven hours. That's the cost of combining tanks into one email, which the original wording required. Days remaining counts down to the 100-litre floor, not to empty, because that's when draws stop. Europe/London is my assumption from "allotment society" and litres; if it's wrong, answer this finding (C6) with the right zone.

**Raised by:** self-consistency, which found the bundling; left undecided, which found the three open definitions; ease of use, which found the refusal dead end.

---

The edited spec has all 18 criteria, and I ran it through the criterion gate: it certifies cleanly. One thing I didn't print above: the draft also re-orders the criteria under new headings (Tank levels, Recording water, Low-tank warning, Top-ups and corrections, Access), so the numbers no longer run in order.

Reply go, or dispute C2: <why>, accept C3 as a risk: <why>, answer C1: <what you know>.
