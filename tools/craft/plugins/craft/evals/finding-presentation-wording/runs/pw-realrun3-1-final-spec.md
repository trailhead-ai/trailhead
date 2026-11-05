# Spec: a rainwater tank log for the Marsh End allotment society

**Problem.** The Marsh End allotment society shares four rainwater tanks between forty plots. Plot holders fill
their cans without writing down how much they took, so the tanks run dry in July without warning, the water steward
cannot see which tank is lowest, and nobody can say afterwards where a tank's water went.

**Objectives.** Put the tank log online so that a plot holder can record a draw and see the tank's level, so that the
steward can see which tank is lowest and hears about a low tank in time to order a mains top-up, so that no tank is
drawn below a 100-litre reserve, and so that every change to a tank's figures can be checked afterwards.

## Acceptance Criteria

### Tank overview

- **AC1.** Anyone using the log can see all four tanks on one screen, lowest estimated level first, with any tank
  below 300 litres marked as low. *Verified by: manual-check.*

### Recording water

- **AC2.** A plot holder records a draw by giving a plot number, the tank and the litres taken. *Verified by:
  manual-check.*
- **AC3.** Once a draw is recorded, the plot holder sees the tank's new estimated level straight away. *Verified by:
  manual-check.*
- **AC4.** A draw is refused unless the plot number is one of the society's forty plots, the tank is one of its four
  tanks, and the litres are a whole number greater than zero; the refusal says which of the three was wrong.
  *Verified by: manual-check.*
- **AC5.** A top-up or correction whose litres are not a whole number greater than zero is refused. *Verified by: manual-check.*
- **AC6.** A draw that would take a tank's estimated level below 100 litres is refused, and the refusal shows the
  tank's current estimated level. *Verified by: manual-check.*
- **AC7.** The refusal for a draw below the reserve names the other tank with the most estimated litres, or, when no
  other tank holds more than 100 litres, says that every tank is low and to contact the steward. *Verified by:
  manual-check.*

### Low-tank alerts

- **AC8.** When a tank's estimated level falls below 300 litres, whether through a draw or a correction, the
  steward is sent an alert email naming the tank and its level. The tank is not named in another alert
  until its level has risen to more than 300 litres and then fallen below 300 litres again. *Verified by:
  manual-check.*
- **AC9.** Alert emails go out once a day at 19:00 UK time, and one email names every tank that fell below 300
  litres since the previous send, ordered from the lowest level to the highest. *Verified by: manual-check.*
- **AC10.** Each tank's line in an alert email states its days remaining: its estimated level less 100 litres,
  divided by its average daily draw over the previous 14 days and rounded down, or "no recent draws" when it has had
  none in that time. *Verified by: manual-check.*
- **AC11.** When an alert email cannot be delivered it is retried, and until it is delivered the tank screen (AC1)
  shows beside each low tank that the steward has not yet been told. *Verified by: manual-check.*

### Top-ups, corrections and history

- **AC12.** Only the steward, after signing in, can record top-ups and corrections; plot holders record
  draws without signing in. *Verified by: manual-check.*
- **AC13.** The steward records a mains top-up with the tank, the litres added and the delivery date, which cannot be
  later than the day it is recorded, and the tank's estimated level rises by that amount. *Verified by:
  manual-check.*
- **AC14.** The steward can correct the litres of a draw or a top-up up to 7 days after it was recorded; a later
  correction is refused. *Verified by:
  manual-check.*
- **AC15.** A correction is kept as a new history entry showing the original figure beside the new one, and no
  history entry is ever changed or deleted. *Verified by: manual-check.*
- **AC16.** The tank history lists every draw, top-up and correction for a tank, newest first by the
  time it was recorded. *Verified by: manual-check.*
- **AC17.** Each history entry shows who made it (the plot number for a draw, the steward for anything else), the
  litres, and the date and time it was recorded, plus the delivery date for a top-up. *Verified by: manual-check.*

### Keeping the log safe

- **AC18.** The steward can download every tank's full history as a spreadsheet file at any time. *Verified by:
  manual-check.*
- **AC19.** The log is backed up every night, and restoring it from that backup is tried once before the log goes
  live. *Verified by: manual-check.*

## Maturity

- tank-log: production

## Non-Goals

- Metering the mains supply that fills the tanks.
- Charging plot holders for the water they take.

