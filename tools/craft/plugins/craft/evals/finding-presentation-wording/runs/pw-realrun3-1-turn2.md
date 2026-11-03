Here is the full detail on all eight findings that need your decision. Criteria are numbered two ways: "old" is the spec as it stands, and "new" is my proposed rewrite. Old AC1 becomes new AC5 and AC6, AC2 becomes AC9 and AC10, AC3 becomes AC11–AC13, AC4 becomes AC16, AC5 becomes AC17 and AC18, and AC6 becomes AC19 and AC20.

---

**The level has no starting point and drifts from the real water (C1)**: fixed in the spec
*Raised by: right problem, buildability, failure cases, ease of use, left undecided, self-consistency*

The question is what a tank's "estimated level" is measured from. I recommend basing it on the steward's dip readings, because otherwise every refusal and alert in the spec acts on a made-up number. The spec only ever changes a level through draws (old AC1) and top-ups: "The steward records a mains top-up with the tank, the litres added and the delivery date, and the tank's estimated level rises by that amount" (old AC4). It never says what the level is on the first day, and nothing records rain, even though these are rainwater tanks.

Worst case: on the first day a tank has no level, so either every draw is refused or the system invents a figure. Later, a wet week fills a tank while the log still shows it nearly empty. Plot holders are turned away from full water and the steward is told to order mains water nobody needs. In a dry July the opposite happens. Unlogged draws keep the logged figure high while the real tank empties, and it runs dry with no warning, which is the exact problem the spec is meant to fix.

Fix: the steward would dip each tank and enter the reading, and the log would count forward from the latest reading. This adds new AC1–AC3 under a new "Tank levels" section, adds dip readings to the history (new AC19), and adds a Non-Goal.
> **AC1.** A tank's estimated level is its most recent dip reading, plus the top-ups and minus the draws recorded since that reading, each at its corrected figure where it has been corrected.
> **AC2.** The steward records a dip reading with the tank and the litres measured in it, and the tank's estimated level becomes that figure.
> **AC3.** Until a tank has its first dip reading, a draw from it is refused and the plot holder is told the tank is waiting for its first reading.
> Non-Goal: Estimating rain, evaporation or leaks; the steward's dip readings correct the estimated level for them.

Caveat: the right-problem pass argued this needs a rewrite, not an edit. I disagree, because the change it said would satisfy it (an observed reading plus a starting level) is exactly this edit. The fix leaves open how often the steward dips. The tank screen (new AC4) shows the date of each tank's last reading, so a stale figure is visible.

---

**The low-tank alert can fail silently, and the steward can't see which tank is lowest (C2)**: fixed in the spec
*Raised by: failure cases, ease of use, buildability, security (who held it below Critical only because of its own limit), self-consistency*

The question is whether the steward's only warning should be one email that might never arrive. I recommend adding a screen showing all four tanks and making a failed alert visible. The Problem section says "the water steward cannot see which tank is lowest", but no criterion gives the steward that view. The only warning is the email in old AC3. Restated whole, old AC3 sends the steward one email when a tank falls below 300 litres. It doesn't send again for that tank until the level has risen above 300 and fallen back. Two tanks falling below 300 on the same day get one combined email, lowest first, with the days of average draw left on each line. At production maturity, a failure nobody would notice needs your decision.

Worst case: in July the email bounces, or goes to spam, or the steward's address is out of date. Nothing in the log shows it. The steward assumes no news is good news, and the first anyone hears of it is a plot holder at an empty tank.

Fix: anyone could open one screen and see all four tanks, lowest first, with low ones marked, plus a note on any low tank whose alert hasn't reached the steward. This adds new AC4 and AC14 and widens the Objectives ("so that the steward can see which tank is lowest and hears about a low tank…").
> **AC4.** Anyone using the log can see all four tanks on one screen, lowest estimated level first, each with the date of its last dip reading, and with any tank below 300 litres marked as low.
> **AC14.** When an alert email cannot be delivered it is retried, and until it is delivered the tank screen (AC4) shows beside each low tank that the steward has not yet been told.

---

**Anyone can do the steward's job (C3)**: fixed in the spec
*Raised by: security; also buildability and left undecided*

The question is who is allowed to record top-ups and corrections. I recommend that only a signed-in steward can, and that plot holders keep their quick no-login draw. The spec gives these actions to the steward: top-ups in old AC4, and "The steward can correct a draw that was entered wrongly within 7 days of it being recorded, and the correction keeps the original figure visible beside the new one" (old AC5). It never says how the log tells the steward from anyone else who finds the web address.

Worst case: a plot holder, or a stranger who finds the URL, records a 2,000-litre "top-up" on a nearly empty tank. The level jumps, the low-tank email is never sent, and the history can't say who did it. Someone could also "correct" their own draws down to nothing.

Fix: `Only the steward, after signing in, can record top-ups, dip readings and corrections; plot holders record draws without signing in.` This is new AC15. Who may *read* each plot's usage is left as a "worth fixing" item and not decided here.

---

**Typed-in figures are never checked (C4)**: fixed in the spec
*Raised by: security; also failure cases and buildability*

The question is what the log accepts as a plot, a tank and an amount of litres. I recommend accepting only real plots, real tanks and whole positive litres, because the refusal rule can currently be beaten with a minus sign. Old AC2 reads "A draw that would take a tank below 100 litres is refused. The refusal shows the tank's current level and points the plot holder to the fullest other tank". It checks only where the level ends up, not whether the number makes sense.

Worst case: someone enters a draw of −5,000 litres, or plot 99, or a mistyped tank name. The −5,000 passes the 100-litre check, because it *raises* the level. Every later level, alert and refusal for that tank is then wrong.

Fix:
> `A draw is refused unless the plot number is one of the society's forty plots, the tank is one of its four tanks, and the litres are a whole number greater than zero; the refusal says which of the three was wrong.` (new AC7)
> `A top-up or correction whose litres are not a whole number greater than zero, or a dip reading whose litres are not a whole number of zero or more, is refused.` (new AC8)

A dip reading may be zero, because an empty tank is a real reading.

---

**The history can't prove every change (C5)**: fixed in the spec
*Raised by: self-consistency, buildability, failure cases, security, left undecided*

The question is how the history stores changes, so that "every change to a tank's figures can be checked afterwards" holds. I recommend that nothing in it is ever edited or deleted, and that every entry says who made it and when. As written, "The tank history lists every draw, top-up and correction for a tank, newest first, with the plot number, the litres and the date" (old AC6). A top-up has no plot number (old AC4), so the two criteria contradict each other. Only draws can be corrected (old AC5), so a mistyped top-up is wrong for good. Three dates are in play: the delivery date, the recorded date and "the date". Nothing stops an implementation from overwriting an entry in place.

Worst case: the steward types 5,000 for a 500-litre top-up. Nothing can correct it, so the tank reads 4,500 litres too high for the rest of the season, and its alert never fires. Separately, a build that edits draws in place satisfies the spec as written, and an earlier figure is then gone with no trace.

Fix: corrections would become new entries alongside the original, would cover top-ups too, and every entry would show who and when. This rewrites old AC4–AC6 as new AC16–AC20:
> **AC16.** The steward records a mains top-up with the tank, the litres added and the delivery date, which cannot be later than the day it is recorded, and the tank's estimated level rises by that amount.
> **AC17.** The steward can correct the litres of a draw or a top-up up to 7 days after it was recorded; a later correction is refused, and the refusal points the steward to recording a dip reading instead.
> **AC18.** A correction is kept as a new history entry showing the original figure beside the new one, and no history entry is ever changed or deleted.
> **AC19.** The tank history lists every draw, top-up, dip reading and correction for a tank, newest first by the time it was recorded.
> **AC20.** Each history entry shows who made it (the plot number for a draw, the steward for anything else), the litres, and the date and time it was recorded, plus the delivery date for a top-up.

---

**The alert rule leaves key decisions open and bundles three features (C6)**: fixed in the spec
*Raised by: left undecided, self-consistency; also buildability, failure cases, ease of use*

The question is how the alert email actually behaves. I recommend pinning the send time, the "days remaining" sum and the exact-300 case. Old AC3 (restated in full under C2 above) has two problems. First, it forces same-day batching without saying when the batch goes out. Its "days of average draw" has no time window and divides by zero on a tank nobody has drawn from, and a tank at exactly 300 litres is neither above nor below the threshold. Second, it packs three separately deliverable things into one criterion: the alert with its re-arm rule, the combined email, and the days-remaining line. A criterion that bundles things like that has to be split before the spec advances.

Worst case: two builds both pass old AC3. One emails at midnight and estimates days remaining from the whole season. The other emails on a rolling 24 hours and uses only the last two weeks. For the same tank, the steward is told "3 days" by one and "11 days" by the other, and orders water on that figure.

Fix: the steward would get one email each evening, with days remaining worked out the same way every time. This splits old AC3 into new AC11–AC13:
> **AC11.** When a tank's estimated level falls below 300 litres, whether through a draw, a correction or a dip reading, the steward is sent an alert email naming the tank and its level. The tank is not named in another alert until its level has risen to more than 300 litres and then fallen below 300 litres again.
> **AC12.** Alert emails go out once a day at 19:00 UK time, and one email names every tank that fell below 300 litres since the previous send, ordered from the lowest level to the highest.
> **AC13.** Each tank's line in an alert email states its days remaining: its estimated level less 100 litres, divided by its average daily draw over the previous 14 days and rounded down, or "no recent draws" when it has had none in that time.

Caveat: 19:00 and 14 days are my choices, not the spec's. A once-a-day send means a tank can sit below 300 litres for up to a day before the steward hears. If you'd rather it went out at once, say so and I'll redraft AC12.

---

**The draw and refusal criteria each bundle two things (C7)**: fixed in the spec
*Raised by: self-consistency; also ease of use and right problem on the refusal*

The question is whether to split two criteria that each hold two separately deliverable pieces. I recommend splitting them, and also writing the 100-litre reserve into the Objectives. One criterion is "A plot holder records a draw by giving a plot number, the tank and the litres taken, and sees the tank's new estimated level straight away" (old AC1). The other is the refusal rule (old AC2, quoted under C4). Separately, no objective asks for a reserve at all, so the refusal is a rule with no stated purpose.

Worst case: a slice delivers recording draws, and the level display becomes ambiguous scope, "done" under one reading and not under the other. And if every tank is low, the refusal's "fullest other tank" points the plot holder at another tank that is also below the reserve, a dead end.

Fix: each piece would be its own criterion, and an all-tanks-low refusal would tell the plot holder to contact the steward. This replaces old AC1 and AC2 with new AC5, AC6, AC9 and AC10, and adds "so that no tank is drawn below a 100-litre reserve" to the Objectives.
> **AC5.** A plot holder records a draw by giving a plot number, the tank and the litres taken.
> **AC6.** Once a draw is recorded, the plot holder sees the tank's new estimated level straight away.
> **AC9.** A draw that would take a tank's estimated level below 100 litres is refused, and the refusal shows the tank's current estimated level.
> **AC10.** The refusal for a draw below the reserve names the other tank with the most estimated litres, or, when no other tank holds more than 100 litres, says that every tank is low and to contact the steward.

---

**There's no way back if the log loses its data (C8)**: fixed in the spec
*Raised by: buildability, failure cases*

The question is what happens if the log's data is lost or corrupted. I recommend a download the steward can take at any time, plus a nightly backup that has actually been restored once. At production maturity, the ability to undo or recover needs your decision. The spec says nothing about it. Moving existing data and breaking existing users don't apply: the fact check confirmed there is no existing system or data. The starting levels are covered by the first dip readings in C1.

Worst case: in mid-season the hosting account lapses or the database is corrupted. The whole season's history goes with it, so "where a tank's water went" can't be answered, and there's no paper log to fall back on.

Fix: the steward could download everything as a spreadsheet, and a lost log could be brought back from the previous night. This adds a new section, "Keeping the log safe", with new AC21 and AC22:
> **AC21.** The steward can download every tank's full history as a spreadsheet file at any time.
> **AC22.** The log is backed up every night, and restoring it from that backup is tried once before the log goes live.

---

Reply go, or dispute C2: <why>, accept C2 as a risk: <why>, answer C2: <what you know>.
