# Testing: how tests are written, what they may assert, and how they stay fast

This profile is the long-form guide to testing. It indexes the six decision records of the core vault and extends them with worked examples; the rules themselves live in the records, and each section below points to the one that holds its rule. Every example is synthetic.

The six records:

- [[adr/tests-are-written-red-first]]: the failing test comes first.
- [[adr/a-test-executes-its-subject]]: a test runs what it tests.
- [[adr/a-test-needs-an-input-it-can-vary]]: the answer must depend on the input.
- [[adr/presence-and-absence-are-not-behaviour]]: existence is not an observation of behaviour.
- [[adr/after-the-fact-tests-are-repair-only]]: late tests are repair, and are mutation-checked.
- [[adr/tests-never-read-git-history]]: history is not test input.

## Legitimate and banned assertions

Rule: [[adr/presence-and-absence-are-not-behaviour]], built on [[adr/a-test-executes-its-subject]].

The line is not how an assertion looks. It is whether the test ran the code that produced the thing it asserts about. Take a function `export_invoice(order, out_dir)` that writes `invoice.csv`.

Banned, because nothing ran and the assertion describes the checkout:

    def test_invoice_exporter_is_defined():
        assert callable(getattr(billing, "export_invoice", None))

    def test_invoice_header_is_documented():
        assert "total,tax" in read("docs/invoice.md")

Legitimate, because the test executed the subject and the file is its output:

    def test_export_invoice_writes_one_row_per_line_item(tmp_path):
        export_invoice(order_with(items=["tea", "cup"]), tmp_path)
        rows = (tmp_path / "invoice.csv").read_text().splitlines()
        assert rows[0] == "item,qty,price"
        assert len(rows) == 3

Both banned tests pass on an exporter that returns garbage. The legitimate one fails on it, and fails differently if the exporter drops a line item. Contents beat existence: asserting that `invoice.csv` exists after the call would be allowed but weak, because an empty file satisfies it.

## Restating a decision and testing a behaviour

Rule: [[adr/a-test-needs-an-input-it-can-vary]].

Take one subject, `retry_delay(attempt)`, which returns the seconds to wait before retry number `attempt`: doubling from 1, capped at 30.

Restating the decision:

    BASE_DELAY = 1
    MAX_DELAY = 30

    def test_delay_constants():
        assert BASE_DELAY == 1
        assert MAX_DELAY == 30

It can only go red when someone changes their mind, and then it asks them to make the same edit twice.

Testing the behaviour, with inputs on either side of what decides the answer:

    def test_retry_delay_doubles_then_caps():
        assert retry_delay(1) == 1
        assert retry_delay(3) == 4
        assert retry_delay(5) == 16
        assert retry_delay(6) == 30
        assert retry_delay(50) == 30

Here the honest answer to "what would have to break for this to go red" is that the mapping from attempt to delay stops working. A snapshot of the full table of delays, or a golden file holding it, falls on the restating side unless the test also changes what it feeds the function.

One exception is allowed: the seam smoke. Wiring has no input to vary, so one test per seam, marked as such, is enough.

## The tests worth reaching for, in order

Rules: [[adr/a-test-executes-its-subject]] for the first, [[adr/a-test-needs-an-input-it-can-vary]] for the second, [[adr/tests-are-written-red-first]] for the fourth. Reach for these five before anything else. The running subject is a small `Cart` with `add(item, price)`, `total()` and `checkout(payment)`.

1. **The contract, end to end.** Call the public entry point with real inputs and assert the returned value, the written file or the exit code.

        def test_cart_total_sums_what_was_added():
            cart = Cart(); cart.add("tea", 3); cart.add("cup", 5)
            assert cart.total() == 8

2. **The boundaries that decide something.** Empty, one, many, and either side of a threshold. Each is its own test and fails for exactly one reason.

        def test_empty_cart_totals_zero():
            assert Cart().total() == 0

        def test_free_shipping_starts_at_fifty():
            assert Cart.with_items(49).shipping() == 5
            assert Cart.with_items(50).shipping() == 0

3. **The failure paths.** Assert the observable consequence: the raised type, the message a person reads, the state left behind.

        def test_checkout_with_a_declined_card_keeps_the_cart():
            cart = Cart.with_items(20)
            with pytest.raises(PaymentDeclined, match="card was declined"):
                cart.checkout(declined_card())
            assert cart.total() == 20

4. **The regression.** A bug just fixed is pinned by a test that fails against the pre-fix code. Write it red first, and show the failure; nothing else proves the fix was the fix.

        def test_removing_an_item_updates_the_total():
            cart = Cart(); cart.add("tea", 3); cart.remove("tea")
            assert cart.total() == 0

5. **The integration seam.** Two components wired together, run together, with the real adapter where it is cheap enough.

        def test_checkout_writes_the_order_the_store_can_read(tmp_path):
            store = FileStore(tmp_path)
            Cart.with_items(20).checkout(good_card(), store=store)
            assert store.load_orders()[0].total == 20

## Testing a prose artifact

Rule: [[adr/a-test-executes-its-subject]], with the input-variation rule of [[adr/a-test-needs-an-input-it-can-vary]].

A skill, a rule file or an agent prompt is source, not behaviour. Asserting that a sentence appears in it tests that someone typed the sentence. There are two honest ways to test one.

Through its consumer. Suppose a loader reads a skill file and returns the capabilities it declares:

    def test_loader_reads_the_declared_tools(tmp_path):
        write(tmp_path / "skill.md", front_matter(tools=["read", "search"]))
        assert load_skill(tmp_path / "skill.md").tools == {"read", "search"}
        write(tmp_path / "skill.md", front_matter(tools=[]))
        assert load_skill(tmp_path / "skill.md").tools == set()

The loader ran, and a different file produced a different answer. The same applies to an installer: run it, then assert what it wrote, by reading the contents back.

With a behavioural eval. Give an agent a fixture scenario and judge the outcome it produces: a reviewer prompt, shown a diff with one planted defect and one without, must flag the first and pass the second. This is expensive, so keep it for the instructions that actually decide something.

## Keeping a suite fast without weakening it

Rules: [[adr/tests-are-written-red-first]] for scoped runs and the single full run, [[adr/tests-never-read-git-history]] for fixtures, [[adr/a-test-needs-an-input-it-can-vary]] for parametrising. A slow suite gets run less, and a suite that is run less catches less. Each habit has a cheaper form that proves the same thing.

- **Run the real code in-process where the process boundary is not the subject.** A command-line wrapper whose logic lives in a function can be tested by calling the function with its environment fenced; keep one test per seam that spawns the real process.
- **Build shared setup once, copy it per test.** A prebuilt data directory is made once per worker and copied. Whatever differs from the template, such as a dirty tree, is a parameter the test passes.
- **Use the real disk under a temporary directory.** Make the failure real: a read-only directory, a file where a directory was expected.
- **Wait on an observable, never on a duration.** Wait for the file, the sentinel line or the process exit. Where elapsed time is the subject, inject the clock.
- **Inject a collaborator when the test varies its answer.** Patching a function by module path names an import layout and breaks when code moves.
- **Add a case to the file that already tests the subject.** Cases of the same shape with different values are one parametrised test, and it still needs its mutation check.
- **Re-home a slow test, never delete it.** Move real-process tests to an opt-in tier that runs as its own required job, and say in the closing line that the tier was left out.
- **Budget the run.** A guard that fails when run time passes a budget derived from measured history keeps the cost from creeping back.

## Mutation checks and fixtures

Rules: [[adr/after-the-fact-tests-are-repair-only]] and [[adr/tests-never-read-git-history]].

A mutation check proves a test binds its behaviour. Name the kind of break deliberately: reverting the fix, moving the guarded line, planting a decoy where the guarded string occurs twice, nudging a limit by one. Deleting the guarded line proves only that a pin exists, so use it last. Save a copy first, restore from the copy, and require an empty diff.

When a test needs an earlier shape of a file, commit that shape as a fixture named in words, such as `settings_old_layout`, and read it like any other file. A fixture is a snapshot of an input, which is fine: the test still varies what it does with it.

Back to the records: [[adr/tests-are-written-red-first]], [[adr/a-test-executes-its-subject]], [[adr/a-test-needs-an-input-it-can-vary]], [[adr/presence-and-absence-are-not-behaviour]], [[adr/after-the-fact-tests-are-repair-only]], [[adr/tests-never-read-git-history]].
