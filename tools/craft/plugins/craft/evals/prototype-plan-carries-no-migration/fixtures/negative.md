# Fixture — negative

A planning session paused at step 7 ("Define Tasks"), immediately before the feature is
broken into tasks. The spec under review and the renderer's real output for this fixture's
own `## Maturity` stamp follow. Read this file in full, then perform step 7 on it.

This fixture is byte-identical to `positive.md` except for **AC3**, which here requires
preserving existing state instead of waiving it — the carve-out the renderer's own block
names as the one condition that reopens a suppressed migration task.

## Spec under review

# Spec — replace the widget's category field with a tags list

**Problem.** Every widget currently carries one free-text `category` field. Users want to
apply several tags to a widget instead of being limited to one category.

**Objectives.** Add a `tags: list[str]` field to every widget and stop reading `category`
for new writes.

## Acceptance Criteria

- **AC1.** New widgets are created with a `tags` list instead of a `category` value.
- **AC2.** The widget list view groups by tag instead of by category.
- **AC3.** Every existing widget's current `category` value must survive this change as
  its initial tag — no widget may end up with an empty `tags` list after this ships.

## Maturity

- trailhead: prototype

## Migration bar (rendered)

migration-bar: (level: prototype) (basis: stamp) (target-repo: trailhead) — suppressed: migration and backfill
Migration and backfill tasks are suppressed for this plan.
Reopens only if the plan's acceptance criteria require preserving existing state — decompose the migration/backfill work normally in that case despite this suppression.
