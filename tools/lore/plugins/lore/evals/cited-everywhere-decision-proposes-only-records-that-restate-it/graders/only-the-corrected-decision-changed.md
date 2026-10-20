---
type: regex
pattern: 'vault/(?!decision/set-the-shelf-hold-for-donated-produce\.(?:md|json)$)'
flags: m
match: not_contains
target: files
---
