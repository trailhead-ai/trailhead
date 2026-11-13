---
type: regex
pattern: '(^|\n)merge [^\n]*--eval-state[ =]\S*/repo=\S*gate-state'
target:
  source: file
  path: portage-invocations.log
---
