# Tests never read git history: no git log, git show or lookup by commit in the checked-out repository, and a past version a test needs is committed as a fixture

Tests never read the checked-out repository's git history. A test must not run `git log` or `git show`, or look anything up by commit, in the repository the code is checked out in; a past version of a file that a test needs is committed as a fixture beside the test. A test that builds its own throwaway repository under its temporary directory is unaffected.

## Context

A test that reads history depends on something the repository does not guarantee to keep. Rebased and squashed commits get new IDs, so a lookup by commit stops resolving after a rewrite. Continuous integration usually checks out at depth 1, so the older commit a test asks for is not there at all, and the test fails on the pipeline while passing on a developer's full clone.

Worked example, entirely synthetic. A migration helper `upgrade_config(old_text)` must turn the old layout of a settings file into the new layout. The test needs a sample of the old layout.

Fragile: the test asks history for the old file.

    def test_upgrade_config_reads_old_layout():
        old = run("git show <some earlier commit>:settings.cfg")
        assert "mode = fast" in upgrade_config(old)

After a squash that commit no longer exists, and on a depth-1 checkout it was never fetched.

Stable: the old layout is committed as a fixture file, `fixtures/settings_old_layout.cfg`, and the test reads it.

    def test_upgrade_config_converts_old_layout():
        old = (FIXTURES / "settings_old_layout.cfg").read_text()
        assert "mode = fast" in upgrade_config(old)
        assert "mode = slow" in upgrade_config(old.replace("fast", "slow"))

The fixture is ordinary content that any checkout holds, and varying the input shows the output depends on it.

## Decision

No test runs `git log` or `git show`, or looks anything up by commit, tag or revision, in the checked-out repository. A test that builds its own throwaway repository under its temporary directory, and reads the history it made there, is unaffected: that history is created by the test and cannot be rewritten or missing. When a test needs a past version of a file, that version is committed as a fixture and the test reads the fixture.

The reasoning is the pair above: rebased and squashed commits get new IDs, so history citations go stale, and CI checks out at depth 1, so history is absent where tests run.

Examples, fixtures and test names in core content use words, never hex-looking strings, and core content cites no pull request, commit or incident from any vault.

## Consequences

- Tests behave the same on a full clone, a shallow checkout and after a history rewrite.
- A fixture costs a small file in the repository, and has to be updated by hand if the past format it captures is ever corrected.
- Where a past version matters to the reader, the fixture's name says what it is, in words, rather than pointing at a commit.
- Related: [[adr/a-test-needs-an-input-it-can-vary]] explains why the fixture test above also varies its input, and [[adr/after-the-fact-tests-are-repair-only]] describes saving a baseline copy of a file to restore after a mutation, which is also a file on disk and never a history lookup.
- Part of the area profile [[area/testing]].
