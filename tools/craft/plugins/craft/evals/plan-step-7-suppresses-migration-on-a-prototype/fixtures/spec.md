# Spec — replace the widget's category field with a tags list

**Problem.** Every widget currently carries one free-text `category` field. Users want to
apply several tags to a widget instead of being limited to one category.

**Objectives.** Add a `tags: list[str]` field to every widget and stop reading `category`
for new writes.

## Acceptance Criteria

- **AC1.** New widgets are created with a `tags` list instead of a `category` value.
- **AC2.** The widget list view groups by tag instead of by category.
- **AC3.** This is a disposable-state prototype with no deployed consumers: existing
  widgets may keep an empty `tags` list after this ships, and a user may re-tag them by
  hand later. No automated preservation of prior `category` values is required.

## Maturity

- trailhead: prototype

