---
type: regex
pattern: '^>|\b(amended|revised|corrected|correction|erratum)\b'
flags: mi
match: not_contains
target:
  source: file
  path: vault/decision/keep-a-wipe-down-step-on-every-tool-return.md
---
