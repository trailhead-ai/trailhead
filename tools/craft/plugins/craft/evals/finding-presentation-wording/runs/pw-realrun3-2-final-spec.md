# Spec: a rainwater tank log for the Marsh End allotment society

**Problem.** The Marsh End allotment society shares four rainwater tanks between forty plots. Plot holders fill
their cans without writing down how much they took, so the tanks run dry in July without warning, the water steward
cannot see which tank is lowest, and nobody can say afterwards where a tank's water went.

**Objectives.** Put the tank log online so that a plot holder can record a draw and see the tank's level, so that the
steward hears about a low tank in time to order a mains top-up, and so that every change to a tank's figures can be
checked afterwards.

## Acceptance Criteria

### Tank levels

- **AC7.** The steward records a measured level for a tank, giving the litres read off the tank and the date of the
  reading, and from then on the tank's estimated level is that reading plus the top-ups and minus the draws recorded
  after it. *Verified by: manual-check.*
- **AC8.** Until a tank has its first measured level it shows "not yet measured" in place of a level, draws from it
  are recorded but never refused, and it is never named in a low-tank email. *Verified by: manual-check.*
- **AC9.** The steward and plot holders can see all four tanks' estimated levels on one page, lowest first, each with
  the date of its last measured level. *Verified by: manual-check.*

### Recording water

- **AC1.** A plot holder records a draw by giving a plot number, the tank and the litres taken, and sees the
  tank's new estimated level straight away. *Verified by: manual-check.*
- **AC2.** A draw that would take a tank below 100 litres is refused, and the refusal shows the tank's current
  estimated level. *Verified by: manual-check.*
- **AC15.** A refused draw points the plot holder to the other tank with the most litres above 100; if no other tank
  could supply the requested litres and stay at or above 100 litres, the refusal says so instead of naming one.
  *Verified by: manual-check.*
- **AC12.** A draw, top-up or measured level is refused, and nothing is recorded, unless its litres are a whole number
  from 1 up to the tank's capacity (from 0 for a measured level), its tank is one of the society's four tanks and, for
  a draw, its plot is one of the forty plots; the refusal names the value that was wrong. *Verified by: manual-check.*

### Low-tank warning

- **AC3.** When a recorded entry takes a tank's estimated level from 300 litres or more to below 300 litres, the tank
  is named, with its level, in the next low-tank email to the water steward. Low-tank emails go out at 19:00 on each
  day with at least one such tank. A tank is not named again until its level has risen to 300 litres or more and then
  fallen below 300 litres once more. *Verified by: manual-check.*
- **AC16.** A low-tank email naming more than one tank lists them from the lowest level to the highest.
  *Verified by: manual-check.*
- **AC17.** Each tank's line in a low-tank email states its days remaining: its estimated level minus 100 litres,
  divided by the mean litres drawn from it per calendar day over the last 14 days (days with no draws counting as
  zero), rounded down; when that mean is zero the line says "no recent draws". *Verified by: manual-check.*

### Top-ups and corrections

- **AC4.** The steward records a mains top-up with the tank, the litres added and the delivery date, and the tank's
  estimated level rises by that amount. *Verified by: manual-check.*
- **AC5.** The steward can correct the litres of a draw within 7 days of the draw being recorded, and the estimated
  level uses the corrected figure. *Verified by: manual-check.*
- **AC18.** A corrected draw shows its original litres beside the corrected litres wherever it is listed.
  *Verified by: manual-check.*
- **AC6.** The tank history lists every draw, top-up, measured level and correction for a tank, newest first by the
  time it was recorded, showing the litres, the date recorded, who recorded it (the plot number for a draw, the steward
  otherwise) and, for a top-up, its delivery date. *Verified by: manual-check.*

### Access

- **AC10.** Recording a top-up, a correction or a measured level, or setting a tank's capacity, requires signing in
  as the water steward, and the server refuses each of them otherwise. *Verified by: manual-check.*
- **AC11.** Recording a draw requires the plot's PIN, which the steward issues to that plot's holder; a draw with a
  wrong PIN is refused and not recorded. *Verified by: manual-check.*

## Constraints

- Rainfall, overflow, leaks and draws nobody logged are not estimated; they reach a tank's estimated level only
  through its next measured level (AC7).
- Each tank's capacity in litres is set by the steward and bounds every litres figure for that tank (AC12).
- Five wrong PINs for a plot within one hour refuse that plot's draws for the rest of that hour (AC11).
- All dates, days and times are Europe/London.

## Dependencies

- Steward sign-in and per-plot PINs (AC10, AC11).

## Maturity

- tank-log: production

## Non-Goals

- Metering the mains supply that fills the tanks.
- Charging plot holders for the water they take.

