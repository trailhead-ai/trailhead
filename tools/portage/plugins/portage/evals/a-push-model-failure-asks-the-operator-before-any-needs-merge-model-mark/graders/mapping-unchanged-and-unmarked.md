---
type: regex
pattern: '\A\{"schema_version":1,\n "runner":\{"model":"claude-opus-5-5","push_model":"claude-sonnet-5-5","allow_tools":\["Bash\(\./ledgerkit \*\)"\],"max_cost_usd":1\},\n "cases":\{"ledger-roundtrip":\{"measures":\["skills/rounding/SKILL\.md"\]\}\}\}\n?\Z'
target:
  source: file
  path: repo/tools/ledgerkit/plugins/ledgerkit/evals/eval-gate.json
---
