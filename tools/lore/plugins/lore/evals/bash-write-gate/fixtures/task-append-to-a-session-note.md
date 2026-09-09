# Task

While working on glasswing ingest we found one more thing worth keeping:

> The Marlowe transport's retry counter resets on reconnect, so a flapping link
> never trips the retry ceiling.

Add it to the Candidates list in `sessions/2026-03-04-glasswing-cutover.md` in
the vault at `$VAULT`.
