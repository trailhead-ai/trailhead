# Spec: a rainwater tank log for the Marsh End allotment society

**Problem.** The Marsh End allotment society shares four rainwater tanks between forty plots. Plot holders fill
their cans without writing down how much they took, so the tanks run dry in July without warning, the water steward
cannot see which tank is lowest, and nobody can say afterwards where a tank's water went.

**Objectives.** Put the tank log online so that a plot holder can record a draw and see the tank's level, so that the
steward hears about a low tank in time to order a mains top-up, and so that every change to a tank's figures can be
checked afterwards.

## Acceptance Criteria

### Recording water

- **AC1.** A plot holder records a draw by giving a plot number, the tank and the litres taken, and sees the
  tank's new estimated level straight away.
- **AC2.** A draw that would take a tank below 100 litres is refused. The refusal shows the tank's current level and
  points the plot holder to the fullest other tank.
- **AC3.** When a tank's estimated level falls below 300 litres, the water steward receives one email naming the
  tank and its level. The email is not sent again for that tank until its level has risen above 300 litres and then
  fallen below it once more. If two tanks fall below 300 litres on the same day, the steward receives a single email
  naming both, ordered from the lowest level to the highest, and each tank's line states how many days of average
  draw remain.

### Top-ups and corrections

- **AC4.** The steward records a mains top-up with the tank, the litres added and the delivery date, and the tank's
  estimated level rises by that amount.
- **AC5.** The steward can correct a draw that was entered wrongly within 7 days of it being recorded, and the
  correction keeps the original figure visible beside the new one.
- **AC6.** The tank history lists every draw, top-up and correction for a tank, newest first, with the plot number,
  the litres and the date.

## Maturity

- tank-log: production

## Non-Goals

- Metering the mains supply that fills the tanks.
- Charging plot holders for the water they take.
