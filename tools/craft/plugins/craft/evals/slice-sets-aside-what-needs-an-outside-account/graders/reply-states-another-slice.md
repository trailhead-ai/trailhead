---
type: llm
focus: trace
---
Pass if the agent's final message states a chosen slice that covers the invoice PDF criterion (AC2) and/or the line-items criterion (AC3), and does not choose or plan a slice that covers the tax-registration criterion (AC1). Fail if the chosen slice covers AC1, or if the message states no chosen slice.
