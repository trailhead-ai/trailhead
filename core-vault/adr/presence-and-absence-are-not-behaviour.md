# Presence and absence are not behaviour: asserting a file exists, hasattr or a removal does not test anything

Presence and absence are not behaviour. A test that asserts a file exists, that `hasattr` is true, or that something was removed has observed the shape of the source, not what the code does.

## Context

Whether a thing exists in the checkout says nothing about whether it works. A function that exists and returns the wrong answer passes every presence check, on every commit, forever. The mirror is as empty: a test that asserts something is gone passes vacuously on a fresh checkout where the thing never existed, and it pins a one-time migration as a permanent invariant that some future change has to argue its way past.

Worked example, entirely synthetic. A report generator `build_report(rows, out_dir)` writes `summary.txt` with one line per row. The old `legacy_summary.txt` was dropped because the generator now produces a single file.

Banned, because nothing ran or because the thing asserted is an absence:

    def test_summary_template_exists():
        assert (repo / "templates" / "summary.tpl").exists()

    def test_generator_has_build_report():
        assert hasattr(reports, "build_report")

    def test_legacy_file_removed():
        assert not (repo / "legacy_summary.txt").exists()

Legitimate, because the test ran the step that produced the file and then checked what it holds:

    def test_build_report_writes_one_line_per_row(tmp_path):
        build_report([("a", 1), ("b", 2)], tmp_path)
        lines = (tmp_path / "summary.txt").read_text().splitlines()
        assert lines == ["a: 1", "b: 2"]

Existence alone would have passed with an empty file. The contents show the step did the right thing, and varying the rows shows the output depends on them.

## Decision

Existence is a valid observation only when the test ran the step that produced it. Run the installer, then assert the file it wrote exists. Do not assert that a committed file exists, that a symbol is defined, or that something is callable, because none of those ran anything.

Contents beat existence. Where a generator, installer or build step writes files, assert what is in them, or better, what the real loader makes of them. A bare existence check is the weakest acceptable form, reached for only when the artifact is opaque.

Never assert a removal. Do not test that a file does not exist, a symbol is undefined, a config key is absent or an endpoint answers 404 because it was deleted. Test the behaviour the removal was for. If a file was dropped because the generator now writes one summary, pin that the generator writes the one summary with the right contents.

## Consequences

- Removing something no longer leaves behind a test that has to be argued with, and a fresh checkout cannot pass a test vacuously.
- Tests that check generated output are slightly longer, because they read the contents back.
- A refactor that renames or moves a symbol does not break tests, as long as the behaviour is intact.
- When no consumer can be named that would break without the thing, the thing has no behaviour to test, and the better question is whether it should exist at all.
- Related: [[adr/a-test-executes-its-subject]] states the general rule this one applies to existence, and [[adr/a-test-needs-an-input-it-can-vary]] covers tests whose subject has one possible answer.
- Part of the area profile [[area/testing]].
