# Security posture — credential scrub and untrusted-value validation

Two rules every craft surface applies, stated once here so no citing surface restates them.

## Credential-pattern scrub

Any finding or note text entering a record body or a report — session candidates, `blocked`
bodies, task-body notes, raw command stderr quoted into either — runs through this list first.
A vault is git-backed and has its own push path, so a credential transcribed into a record body
ships as surely as one committed to code. Report bodies are **summarized, never captured
verbatim** — quote only `file:line` references for anything caught. Run the text through this
credential-pattern scrub regex list and drop/redact any match rather than capturing it:

- **Key-like tokens** — `(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+`
  — the trailing character class is load-bearing: it lets the keyword carry qualifier text before
  the separator, which is what catches `SECRET_KEY=`, `AWS_SECRET_ACCESS_KEY=`, and `API_KEY_ID=`. A
  keyword anchored straight to `[=:]` walks past every compound name.
- **Vendor fixed-prefix tokens** —
  `(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+|AIza[0-9A-Za-z_-]{35})\b`
  — issuer-shaped credentials identifiable on their own, with no `key=` preamble to trip the pattern
  above
- **Bearer / api-key shapes** — `(?i)bearer\s+[A-Za-z0-9._\-]+`,
  `(?i)api[_-]?key['"]?\s*[:=]\s*['"]?[A-Za-z0-9._\-]{16,}`
- **High-entropy literals** — `\b[A-Za-z0-9+/]{32,}={0,2}\b` (base64/hex-shaped secrets),
  `\b[A-Fa-f0-9]{40,}\b`
- **PEM private-key blocks** — `-----BEGIN [A-Z ]*PRIVATE KEY-----` (the high-entropy pattern
  catches the body but not this header, so pin it separately)

Prefer over-matching to under-matching: this list is a tripwire, and a false hit costs one manual
look. **Known blind spot:** a binary file's diff renders as `Binary files … differ` rather than
content, so a credential inside a binary artifact is invisible to every pattern above — a change
that adds binary files needs a manual look regardless of what the patterns return.

## Untrusted-value validation

Any value sourced from vault-writable input — a label value, a task-body field, an operator- or
vault-supplied string — that will be substituted into a command or a path is untrusted input.
Validate it against the safe-value shape `^[A-Za-z0-9._/-]+$` before any substitution; a value that
fails validation is never substituted, quoted, or escaped in. That shape alone still admits `..`
and a leading `/`, so a value that resolves to a path additionally needs the traversal checks the
consuming site states — this document fixes the shape, not every site's path-resolution rule.
