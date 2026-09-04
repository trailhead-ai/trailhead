# Fixture: unidentified bullet whose credential straddles the snippet's 48-char truncation boundary

Synthetic fixture spec body for `criterion_gate.py` tests. Every existing
credential fixture places its secret at the *start* of the bullet, inside
the `_snippet` 48-char window, so the scrub-before-truncate regression
(scrubbing a token cut in half by truncation never matches) goes
undetected unless the secret is positioned to straddle the cut.

## Acceptance Criteria

- **AC1.** A clean criterion. *Verified by: automated assertion.*
- filler words before the secret token Rw5Xn3Qp8Bt1Cv6Km2Zy9Ld4Hg7Jf0Ns5Ar3Ei8T trailing words after token here

## Non-Goals

n/a — fixture only.
