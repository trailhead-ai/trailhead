---
type: regex
flags: m
pattern: '--vault(?=[ \t]*$|[ \t]+--|[ \t]+(null|None|nil|undefined|""|'''')([ \t]|$)|[ \t]+<|=)'
match: not_contains
target:
  source: file
  path: lore-calls.log
---
