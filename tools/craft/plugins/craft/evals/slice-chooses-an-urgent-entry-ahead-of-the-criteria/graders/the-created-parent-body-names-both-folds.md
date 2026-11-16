---
type: regex
pattern: '\*\*Folds:\*\*(?=[^\n]*spec/route-planner#F1\b)(?=[^\n]*spec/route-planner#F2\b)'
target:
  source: file
  path: created-bodies.log
---
