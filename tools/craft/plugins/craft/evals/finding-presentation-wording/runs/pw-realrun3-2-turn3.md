The spec review is done, and the tank-log spec is now **`ready`**.

**Changes in the spec** (one write; the stored text matches the draft exactly):
- **Fixed in the spec:** the starting level (C1), who may do what (C3), checks on entered numbers (C4), the all-tanks page (C5) and splitting the bundled criteria (C6). The spec now has 16 criteria.
- **Gaps in numbering:** there is no AC13 or AC14. They belonged to the email-retry fix you declined, and the other numbers keep their ids.
- **Your accepted risk:** the silent-email finding (C2) is recorded with your reason quoted word for word. The spec has no retry or delivery-status criterion, and no dependency line for the email service.
- **Review record:** a `## Gauntlet` section is appended to the spec. It holds the provenance stamp (5 fixes from my proposal, 1 override by you), the full detail on all six findings, the 7 worth-fixing and 4 rough-edge items, and the fact-check results.

**Choices of mine that went in with your "go":**
- Plot holders use a per-plot PIN (AC11) rather than having no login.
- Low-tank emails go out once a day at 19:00 (AC3).
- Dates and times are Europe/London.

**Still for you to confirm:** five facts about the society that nothing here could check: four tanks, forty plots, draws not being recorded today, the July dry-outs, and the water steward role.

To choose the first slice, run this in a fresh session:

```
/craft:slice spec/a-rainwater-tank-log-for-the-marsh-end-allotment-society
```
