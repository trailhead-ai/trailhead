"""Tests for the follow-ups write gate.

The gate reads the proposed spec body on stdin and, from `--prior`, the JSON
document `lore record show spec/<n> --json` printed when the session read the
spec. Exit codes: 0 certified (`base: <revision>` on stdout), 1 violation,
2 could not certify. Every refusal prints `reason:` and `reason-code:`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).parent.parent / "plugins" / "craft" / "scripts" / "followups_gate.py"
REV = "a" * 64


def entry(id_="F1", status="open", title="Short title", what="something is wrong",
          where="`a/b.py`", found="during review", from_=None):
    text = (
        f"- **{id_}.** {title}\n"
        f"  - status: {status}\n"
        f"  - what: {what}\n"
        f"  - where: {where}\n"
        f"  - found: {found}\n"
    )
    if from_ is not None:
        text += f"  - from: {from_}\n"
    return text


def section(*entries: str) -> str:
    return "## Follow-ups\n\n" + "".join(entries)


BASE = "# Spec\n\nprose\n\n## Slices\n\n- task/s1 covers AC1\n"


def with_section(*entries: str, base: str = BASE) -> str:
    return base.rstrip("\n") + "\n\n" + section(*entries)


def prior_json(body: str, revision: str = REV, layer: str = "personal") -> str:
    return json.dumps(
        {"kind": "spec", "name": "n", "body": body, "revision": revision, "layer": layer}
    )


def run(proposed, prior=None, *, prior_raw=None, stdin_bytes=None, tmp_path):
    prior_path = tmp_path / "prior.json"
    if prior_raw is not None:
        prior_path.write_bytes(prior_raw)
    elif prior is not None:
        prior_path.write_text(prior, encoding="utf-8")
    data = stdin_bytes if stdin_bytes is not None else proposed.encode("utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), "--prior", str(prior_path)],
        input=data,
        capture_output=True,
        timeout=30,
    )


def code_of(res) -> str:
    for line in res.stderr.decode().splitlines() + res.stdout.decode().splitlines():
        if "reason-code:" in line:
            return line.split("reason-code:", 1)[1].strip()
    return ""


def reason_of(res) -> str:
    for line in res.stderr.decode().splitlines():
        if "reason:" in line and "reason-code:" not in line:
            return line.split("reason:", 1)[1].strip()
    return ""


def gate(prior_body, proposed, tmp_path, **kw):
    return run(proposed, prior_json(prior_body, **kw), tmp_path=tmp_path)


def accepted(res):
    assert res.returncode == 0, res.stderr.decode()
    assert res.stdout.decode() == f"base: {REV}\n"


# ---- accepts -------------------------------------------------------------


def test_first_write_creates_section_with_one_open_entry(tmp_path):
    accepted(gate(BASE, with_section(entry()), tmp_path))


def test_first_write_accepted_when_prior_lacks_trailing_newline(tmp_path):
    accepted(gate(BASE.rstrip("\n"), with_section(entry(), base=BASE.rstrip("\n")), tmp_path))


def test_appending_a_new_id_is_accepted(tmp_path):
    prior = with_section(entry("F1"))
    accepted(gate(prior, with_section(entry("F1"), entry("F2")), tmp_path))


def test_open_to_folded_to_done(tmp_path):
    o = with_section(entry(status="open"))
    f = with_section(entry(status="folded into task/x"))
    d = with_section(entry(status="done by task/x"))
    accepted(gate(o, f, tmp_path))
    accepted(gate(f, d, tmp_path))


def test_folded_back_to_open(tmp_path):
    accepted(
        gate(with_section(entry(status="folded into task/x")), with_section(entry()), tmp_path)
    )


def test_open_to_dropped_with_reason(tmp_path):
    accepted(gate(with_section(entry()), with_section(entry(status="dropped: moot")), tmp_path))


def test_editing_title_what_and_where_is_accepted(tmp_path):
    prior = with_section(entry())
    new = with_section(entry(title="New title", what="other", where="`c.py`, `d/e.py`"))
    accepted(gate(prior, new, tmp_path))


def test_unchanged_resubmission_is_accepted(tmp_path):
    p = with_section(entry())
    accepted(gate(p, p, tmp_path))


def test_section_in_the_middle_can_grow(tmp_path):
    prior = "# S\n\n## Follow-ups\n\n" + entry("F1") + "\n## Slices\n\n- x\n"
    new = "# S\n\n## Follow-ups\n\n" + entry("F1") + entry("F2") + "\n## Slices\n\n- x\n"
    accepted(gate(prior, new, tmp_path))


# ---- base: -----------------------------------------------------------------


@pytest.mark.parametrize("rev", ["f" * 64, "0123abcd" * 8, "rev-1"])
def test_base_is_the_prior_documents_revision(tmp_path, rev):
    res = gate(BASE, with_section(entry()), tmp_path, revision=rev)
    assert res.returncode == 0, res.stderr.decode()
    assert res.stdout.decode() == f"base: {rev}\n"


def test_base_is_not_a_hash_of_the_body_text_for_a_crlf_prior(tmp_path):
    import hashlib

    body = BASE.replace("\n", "\r\n")
    folded = BASE
    res = gate(folded, with_section(entry()), tmp_path, revision="b" * 64)
    assert res.stdout.decode() == "base: " + "b" * 64 + "\n"
    assert hashlib.sha256(folded.encode()).hexdigest() != "b" * 64
    assert hashlib.sha256(body.encode()).hexdigest() != "b" * 64


# ---- refusals (exit 1) ------------------------------------------------------


def refused(res, code, *needles):
    assert res.returncode == 1, (res.returncode, res.stderr.decode())
    assert res.stdout.decode() == ""
    assert code_of(res) == code, res.stderr.decode()
    reason = reason_of(res)
    assert reason, res.stderr.decode()
    for n in needles:
        assert n in reason, reason


def test_removed_entry(tmp_path):
    prior = with_section(entry("F1"), entry("F2"))
    refused(gate(prior, with_section(entry("F1")), tmp_path), "removed-entry", "F2")


def test_removing_the_whole_section_is_a_removed_entry(tmp_path):
    refused(gate(with_section(entry("F1")), BASE, tmp_path), "removed-entry", "F1")


def test_whole_section_replacement_with_fresh_entries_is_a_removed_entry(tmp_path):
    prior = with_section(entry("F1"), entry("F2"))
    refused(gate(prior, with_section(entry("F7"), entry("F8")), tmp_path), "removed-entry", "F1")


def test_duplicate_entry_id(tmp_path):
    refused(
        gate(BASE, with_section(entry("F1"), entry("F1", title="again")), tmp_path),
        "duplicate-entry-id",
        "F1",
    )


@pytest.mark.parametrize("status", ["dropped:", "dropped: ", "dropped"])
def test_drop_without_reason(tmp_path, status):
    refused(
        gate(with_section(entry()), with_section(entry(status=status)), tmp_path),
        "drop-without-reason",
        "F1",
    )


def test_drop_without_status_line_reason_for_new_entry(tmp_path):
    refused(
        gate(BASE, with_section(entry(status="dropped:")), tmp_path),
        "drop-without-reason",
        "F1",
    )


@pytest.mark.parametrize(
    "settled,new",
    [
        ("done by task/x", "open"),
        ("done by task/x", "folded into task/y"),
        ("done by task/x", "done by task/z"),
        ("done by task/x", "dropped: other"),
        ("dropped: moot", "open"),
        ("dropped: moot", "folded into task/y"),
        ("dropped: moot", "done by task/z"),
        ("dropped: moot", "dropped: other"),
    ],
)
def test_settled_entry_changed(tmp_path, settled, new):
    refused(
        gate(with_section(entry(status=settled)), with_section(entry(status=new)), tmp_path),
        "settled-entry-changed",
        "F1",
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"found": "somewhere else"},
        {"from_": "task/y"},
    ],
)
def test_entry_origin_changed(tmp_path, changed):
    prior = with_section(entry(from_="task/x"))
    kwargs = {"from_": "task/x", **changed}
    refused(gate(prior, with_section(entry(**kwargs)), tmp_path), "entry-origin-changed", "F1")


def test_from_added_to_an_existing_entry_is_an_origin_change(tmp_path):
    refused(
        gate(with_section(entry()), with_section(entry(from_="task/x")), tmp_path),
        "entry-origin-changed",
        "F1",
    )


def test_from_dropped_from_an_existing_entry_is_an_origin_change(tmp_path):
    refused(
        gate(with_section(entry(from_="task/x")), with_section(entry()), tmp_path),
        "entry-origin-changed",
        "F1",
    )


@pytest.mark.parametrize(
    "bad_entry",
    [
        "- **X1.** bad id\n  - status: open\n  - what: w\n  - where: `a`\n  - found: f\n",
        "- **F1** no dot\n  - status: open\n  - what: w\n  - where: `a`\n  - found: f\n",
        "- **F1.** t\n  - status: open\n  - where: `a`\n  - found: f\n",
        "- **F1.** t\n  - status: maybe\n  - what: w\n  - where: `a`\n  - found: f\n",
        "- **F1.** t\n  - status: open\n  - what: w\n  - where: a.py\n  - found: f\n",
        "- **F1.** t\n  - status: open\n  - what: w\n  - where: `a`\n  - found: f\n  - bogus: x\n",
        entry() + "stray prose\n",
    ],
)
def test_malformed_entry(tmp_path, bad_entry):
    res = gate(BASE, with_section(bad_entry), tmp_path)
    assert res.returncode == 1
    assert code_of(res) == "malformed-entry"
    assert reason_of(res)


def test_malformed_entry_names_the_entry_id(tmp_path):
    bad = "- **F4.** t\n  - status: maybe\n  - what: w\n  - where: `a`\n  - found: f\n"
    refused(gate(BASE, with_section(bad), tmp_path), "malformed-entry", "F4", "status")


@pytest.mark.parametrize(
    "target",
    ["task/", "task/a b", "task/a/b", "tasks/x", "x", "task/é", "task/x;y"],
)
@pytest.mark.parametrize("where_status", ["folded into {}", "done by {}", "from"])
def test_malformed_task_id(tmp_path, target, where_status):
    if where_status == "from":
        e = entry(from_=target)
    else:
        e = entry(status=where_status.format(target))
    refused(gate(BASE, with_section(e), tmp_path), "malformed-task-id", "F1")


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "-rf", "a/../b", "..", ".", "a//b", "./a", "a/", "a b", "a;b", "a/./b", "é.py"],
)
def test_path_outside_repository(tmp_path, path):
    refused(
        gate(BASE, with_section(entry(where=f"`{path}`")), tmp_path),
        "path-outside-repository",
        "F1",
    )


def test_path_outside_repository_second_path(tmp_path):
    refused(
        gate(BASE, with_section(entry(where="`ok.py`, `/abs`")), tmp_path),
        "path-outside-repository",
        "F1",
    )


def test_change_outside_followups_in_authored_text(tmp_path):
    new = with_section(entry(), base=BASE.replace("prose", "edited prose"))
    refused(gate(BASE, new, tmp_path), "change-outside-followups", "Follow-ups")


def test_change_outside_followups_in_slices(tmp_path):
    new = with_section(entry(), base=BASE + "- task/s2 covers AC2\n")
    refused(gate(BASE, new, tmp_path), "change-outside-followups", "Follow-ups")


def test_change_outside_followups_when_section_already_exists(tmp_path):
    prior = with_section(entry())
    new = with_section(entry(), base=BASE.replace("AC1", "AC9"))
    refused(gate(prior, new, tmp_path), "change-outside-followups", "Follow-ups")


def test_section_not_appended_at_end_is_refused(tmp_path):
    new = "# Spec\n\n" + section(entry()) + "\n# Spec2\n\nprose\n\n## Slices\n\n- task/s1 covers AC1\n"
    refused(gate(BASE, new, tmp_path), "change-outside-followups")


def test_duplicate_followups_heading_in_proposed(tmp_path):
    new = with_section(entry("F1")) + "\n## Slices\n\n## Follow-ups\n\n" + entry("F2")
    refused(gate(BASE, new, tmp_path), "duplicate-followups-heading", "Follow-ups")


# ---- exit 2 -----------------------------------------------------------------


def unreadable(res, code):
    assert res.returncode == 2, (res.returncode, res.stderr.decode())
    assert res.stdout.decode() == ""
    assert code_of(res) == code, res.stderr.decode()
    assert reason_of(res)


def test_empty_stdin(tmp_path):
    unreadable(run("", prior_json(BASE), tmp_path=tmp_path), "empty-stdin")


def test_stdin_over_the_size_cap(tmp_path):
    big = with_section(entry(what="x" * (300 * 1024)))
    unreadable(run(big, prior_json(BASE), tmp_path=tmp_path), "stdin-too-large")


def test_non_utf8_stdin(tmp_path):
    unreadable(
        run("", prior_json(BASE), stdin_bytes=b"\xff\xfe## Follow-ups\n", tmp_path=tmp_path),
        "non-utf8-stdin",
    )


def test_unreadable_prior_path(tmp_path):
    res = subprocess.run(
        [sys.executable, str(GATE), "--prior", str(tmp_path / "missing.json")],
        input=b"x",
        capture_output=True,
        timeout=30,
    )
    unreadable(res, "unreadable-prior")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b'"s"',
        b'{"body": "x", "revision": "r"}',
        b'{"body": "x", "layer": "personal"}',
        b'{"revision": "r", "layer": "personal"}',
        b'{"body": 1, "revision": "r", "layer": "personal"}',
        b'{"body": "x", "revision": 1, "layer": "personal"}',
        b'{"body": "x", "revision": "r", "layer": 1}',
        b'{"body": "x", "revision": "", "layer": "personal"}',
        b'{"body": "x", "revision": "a\\nb", "layer": "personal"}',
        b"\xff\xfe",
    ],
)
def test_malformed_prior(tmp_path, raw):
    unreadable(run(with_section(entry()), prior_raw=raw, tmp_path=tmp_path), "malformed-prior")


@pytest.mark.parametrize("layer", ["shared", "other", ""])
def test_non_personal_layer_prior(tmp_path, layer):
    res = gate(BASE, with_section(entry()), tmp_path, layer=layer)
    unreadable(res, "shared-layer-prior")


@pytest.mark.parametrize(
    "bad_prior_section",
    [
        "- **F1.** t\n  - status: maybe\n  - what: w\n  - where: `a`\n  - found: f\n",
        entry() + "stray prose\n",
    ],
)
def test_prior_whose_own_section_does_not_parse(tmp_path, bad_prior_section):
    res = gate(with_section(bad_prior_section), with_section(entry()), tmp_path)
    unreadable(res, "malformed-prior-section")


def test_prior_with_duplicate_followups_heading(tmp_path):
    prior = with_section(entry()) + "\n## Slices\n\n## Follow-ups\n\n" + entry("F2")
    unreadable(gate(prior, with_section(entry()), tmp_path), "malformed-prior-section")


def test_proposed_body_ending_inside_open_fence(tmp_path):
    unreadable(
        gate(BASE, with_section(entry()) + "\n```\nopen\n", tmp_path),
        "unterminated-masked-region",
    )


# ---- scrubbing -------------------------------------------------------------


def test_reason_scrubs_credential_shaped_entry_text(tmp_path):
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    bad = f"- **F4.** t\n  - status: {secret}\n  - what: w\n  - where: `a`\n  - found: f\n"
    res = gate(BASE, with_section(bad), tmp_path)
    assert res.returncode == 1
    assert secret not in res.stderr.decode()
    assert "[redacted]" in reason_of(res)
