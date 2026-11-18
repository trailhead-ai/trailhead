# A test needs an input it can vary: restating a constant, snapshot, golden file or inventory guards a decision, not a behaviour

A test needs an input it can vary. The unit's answer must depend on what it is given, and the test must be one of the points that changes that answer. A test that restates a constant, an inventory, a snapshot, a golden file or a sentence only repeats a decision already written in the code.

## Context

Running the subject is the floor, not the bar. A test earns its place by showing that the answer depends on the input: feed the unit one thing and get one answer, feed it another and get a different one. That dependency is what a later edit can break without anyone noticing.

Where a unit has only one possible output there is no behaviour to pin. The test restates a decision. Such a test can only go red when a person changes their mind, and then it is not catching a regression, it is asking them to make the same edit twice.

Worked example, entirely synthetic. A module declares `DEFAULT_PAGE_SIZE = 25` and a function `paginate(items, size=DEFAULT_PAGE_SIZE)` that splits a list into pages.

A test that restates a decision:

    def test_default_page_size():
        assert DEFAULT_PAGE_SIZE == 25

It is true by construction. It fails only when someone edits the constant, and then it demands the same edit in the test. The same holds for a golden file that holds a function's whole output, a test that lists the shipped inventory of plugins, or one that checks a closed vocabulary equals itself.

A test that guards the behaviour varies the input across the branch that decides:

    def test_paginate_splits_by_the_size_asked_for():
        assert len(paginate([1, 2, 3, 4, 5], size=2)) == 3
        assert len(paginate([1, 2, 3, 4, 5], size=5)) == 1
        assert len(paginate([], size=2)) == 0

Each call changes the answer. A version that ignores `size`, or always returns one page, fails at least one of them.

## Decision

The subject of a test must have an input that can change its answer, and the test must be one of the points that changes it. Vary the value on either side of a threshold, the key the caller asked for against the order that comes back, an inventory built from two entries and from none.

Restating a constant, an inventory, a snapshot, a golden file or a sentence guards a decision, not a behaviour, and does not count as coverage. To tell the two apart, ask what would have to break for the test to go red. If the honest answer is that someone edits the same constant or rewords the same sentence, it guards a decision. If it is that the logic mapping inputs to outputs stops working, it guards a behaviour.

The seam-smoke exception: wiring has no input to vary and is still worth exactly one test, because nothing else shows that the pieces are connected. The installer really writes the tree, the documented command really parses, the plugin really loads. Write one such test per wiring seam, never one per constant the seam touches, and mark its `def` line with `# inert-gate: allow <reason>` so the exemptions stay countable and reviewable instead of accumulating unseen.

## Consequences

- Tests need more than one input, so they take a little longer to design than a single restated value.
- A later edit that breaks the mapping from inputs to outputs turns a test red, instead of passing silently.
- Changing a default, a vocabulary or an inventory no longer forces a matching edit in a test that only copied it.
- Seam smokes stay allowed, but each one is a deliberate, marked exception, so the count of exemptions is a number a reviewer can read.
- This builds on [[adr/a-test-executes-its-subject]]: running the subject is necessary, and varying its input is what makes the run mean something. Tests that only inspect existence are covered by [[adr/presence-and-absence-are-not-behaviour]].
- Part of the area profile [[area/testing]].
