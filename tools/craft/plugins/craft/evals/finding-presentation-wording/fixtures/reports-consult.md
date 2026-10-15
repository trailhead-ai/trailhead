# Reviewer reports: direct plot swaps

Four members of the council each answered the question in `consult-question.md` in parallel and
returned the report below. The reports are in the form the council's prompt template fixes. Resolved
maturity: `maturity: production (basis: requested)`. No stand-downs.

### Builder

## Findings
- [Critical] With direct swaps and no single record of who holds which plot, two tenants can each
  agree a swap for the same plot with different partners; both swaps go through and one tenant turns
  up to find a stranger on their plot. Suggested: keep one table of plot holders and apply a swap
  only if both tenants still hold the plots named when the second one confirms.
- [Important] A swap form needs both tenants to confirm, and nothing says what happens to a half
  confirmed swap. Suggested: let it expire after seven days.
- [Minor] The form could reuse the waiting list's email templates.

## Confidence
high — the double swap follows from the two-confirmation design.

### Reliability

## Findings
- [Critical] Two swaps naming the same plot, confirmed within minutes of each other, are both applied
  because each is checked only against what the tenant saw when they started: one tenant loses the
  plot they thought they were getting. Suggested: check at the moment of the second confirmation.
- [Important] No record is kept of past swaps, so a dispute a year later cannot be settled.
  Suggested: write every swap to the same history the waiting list uses.
- [Minor] Seven days is arbitrary; pick a number and note why.

## Confidence
high — the failure follows from the form as described.

### Security

## Findings
- [Important] Direct swaps let a tenant arrange payment for a good plot and call it a swap, passing
  over everyone on the waiting list. Suggested: require the warden's approval of any swap that moves
  a tenant to a different site.
- [Minor] The confirmation email for a swap should name the plots, so a tenant can tell it is real.

## Confidence
medium — depends on whether the society cares about plot sales.

### Advocate

## Findings
- [Critical] Tenants might feel the swap process is unfair to people on the waiting list. Suggested:
  explain the process on the society's site.
- [Important] The warden handles six swaps a year by hand and it works; moving it onto a form adds a
  task for tenants who are mostly not online. Suggested: keep the warden route and add the form as
  a second way, not a replacement.
- [Minor] Call the form "swap my plot", not "reallocate".

## Confidence
medium — no tenant has been asked.
