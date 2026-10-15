---
type: regex
pattern: '[Rr]evised|[Aa]mended|[Cc]orrection|[Cc]orrected|[Ee]rratum|[Uu]pdated:'
match: not_contains
target:
  source: file
  path: vault/spec/export-the-activity-feed.md
---
