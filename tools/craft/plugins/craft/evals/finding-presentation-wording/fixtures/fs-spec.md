# Spec: a kiln booking sheet for the pottery studio

**Problem.** The Marlow Street pottery studio keeps its kiln bookings on a paper sheet pinned beside
the large kiln. Two kilns serve about 90 members. Two members regularly turn up with work for the same
firing, the technician cannot see ahead which days a kiln is free, and nobody can tell afterwards who
was told what.

**Objectives.** Put the booking sheet online so that a member can book a firing and see where it
stands, so that a booking and its reminder reach the right member, and so that every change to a
kiln's calendar can be checked afterwards.

## Acceptance Criteria

- **AC1.** A member books a firing by giving a name, an email address, the kiln (large or small) and
  a day, and receives a confirmation email stating the booking.
- **AC2.** A kiln holds one firing per day. A second booking for the same kiln and day is refused and
  the next free day is offered instead.
- **AC3.** A member can cancel a booking up to 24 hours before its day. The day is freed and offered
  to the first member on that kiln's standby list.
- **AC4.** The studio technician can lock a kiln for repair over a range of days by recording a
  reason, and the lock shows on the kiln's calendar.
- **AC5.** The evening before a firing, the member receives a reminder listing what must be on the
  shelves by 8 am.
- **AC6.** The calendar lists every booking, cancellation, lock and standby offer, newest first,
  with the date.

## Maturity

- pottery-booking: production

## Non-Goals

- Charging for firings online.
- Tracking glaze recipes.
