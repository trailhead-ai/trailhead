"""Contract pins for the execute drain's two prose surfaces.

`ranger drain`'s CLI verbs are tested by behavior; the loop that drives them is
prose, and prose has no type system. These pins hold the handful of contracts a
well-meaning edit would otherwise dissolve — each one a rule whose violation is
silent, unattended, and expensive:

  - **The loop session is the only status writer.** A drained task's `done`,
    its `blocked` park, and its crash-preserved `in-progress` are all written by
    the coordinator. A dispatched executor that writes status gives one status
    two writers, and the loop's own bookkeeping is then a lie.
  - **The portage tail is dispatched by the loop session, never the executor.**
    A monitor dispatched from inside another subagent loses its notification
    channel, and the drain's completion signal degrades with it.
  - **The monitor outcome file is the contract, not the notification.** Even
    dispatched from the top-level session, the drain never waits on a reply:
    it polls the outcome file, and a missing or empty one reads as crashed.
  - **The resume ritual is unconditional on a `craft/branch` label.** Camp
    provisioning never fetches the task branch, so a remote-only branch yields a
    fresh local branch off base — skipping the fetch/reset/rebase silently
    discards the previous run's pushed work.
  - **The sync gate reads per-member actions.** `camp sync --json` reports
    `ok_with_warnings` only when `errors > 0`, so `skip-dirty`, `skip-off-main`,
    and `absent` all hide under a top-level `"ok"`.
  - **No component of the drain ever applies the approval signal.** The merge
    gate is only a gate if the thing it gates cannot open it.

Every pinned span is asserted as a contiguous substring **within one physical
line**, through the same `_pin` helper the refine sweep's contract uses — per
[[lesson/phrase-pinned-prose-contracts-break-on-line-wraps]], a pin that
straddles a markdown wrap fails while the prose is perfectly correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest

from test_sweep_contract import _FISH_SET_RE, _code_block_lines, _frontmatter, _pin

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_TOOL_ROOT = _REPO_ROOT / "tools" / "ranger"
_PLUGIN_DIR = _TOOL_ROOT / "plugins" / "ranger"
MANIFEST = _TOOL_ROOT / "capabilities.toml"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from ranger.drain import loop as drain_loop  # noqa: E402  (needs the sys.path above)

SKILL = _PLUGIN_DIR / "skills" / "execute" / "SKILL.md"
AGENT = _PLUGIN_DIR / "agents" / "execute.md"
RITUALS = _PLUGIN_DIR / "skills" / "execute" / "operator-rituals.md"
REPORT_PY = _PLUGIN_DIR / "ranger" / "drain" / "report.py"

#: The drain outcome grammar's four tokens, as the agent document must spell
#: them. `ranger.drain.report.parse_drain_outcome` accepts exactly these, so a
#: fifth spelling in the agent doc buckets a finished build as `failed`.
RETURN_TOKENS = [
    "`PUSHED <branch> <sha> <diffstat>`",
    "`BLOCKED <reason>`",
    "`FAILED <reason>`",
    "`SKIPPED <reason>`",
]

DRAIN_VERBS = [
    "ranger drain start",
    "ranger drain derive",
    "ranger drain record",
    "ranger drain finish",
]

#: The verbs that own state or a classification the loop must never re-derive
#: in prose: the durable in-flight cap (which prose cannot hold across a
#: restart), the two buckets no outcome file produces, and the two
#: classifications of another tool's JSON.
DRAIN_SUBSTRATE_VERBS = [
    "ranger drain sync-gate",
    "ranger drain inflight mark",
    "ranger drain inflight count",
    "ranger drain inflight resolve",
    "ranger drain inflight expire",
    "ranger drain teardown-check",
    "ranger drain crashed",
    "ranger drain dropped",
]


# --- both documents ship ------------------------------------------------------


def test_drain_skill_ships():
    assert SKILL.exists(), f"Expected the /ranger:execute coordinator loop at {SKILL}"


def test_drain_agent_ships():
    assert AGENT.exists(), f"Expected the per-task executor at {AGENT}"


def test_drain_skill_frontmatter_names_the_skill():
    front = _frontmatter(SKILL)
    assert "name: execute" in front


def test_drain_agent_frontmatter_names_the_agent():
    front = _frontmatter(AGENT)
    assert "name: execute" in front


# --- skill: the bounded pool --------------------------------------------------


def test_skill_pins_pool_cap_and_its_flag():
    _pin(
        SKILL,
        "up to 2 executor agents in flight at once",
        "The pool cap is the whole of the drain's concurrency control; a coordinator that "
        "reads no cap dispatches the entire queue at once.",
    )
    _pin(
        SKILL,
        "ranger drain start --concurrency N",
        "The cap is configurable, and the flag is the only way an operator changes it.",
    )


def test_skill_pins_the_per_slot_state_quadruple():
    _pin(
        SKILL,
        "its task id, its workspace slug, its outcome file path, its dispatch deadline, and the queue bucket",
        "With several tasks interleaving, none of these five is recoverable from a return; "
        "each has to be carried from the derivation that produced the task — the slug twice "
        "over, for §5's `craft/branch` re-assert and for §7's teardown.",
    )


def test_skill_pins_record_then_rederive_then_refill_order():
    _pin(
        SKILL,
        "record its outcome, re-derive, then dispatch the next task into that slot",
        "A derivation taken before the outcome lands still classifies the finished task as "
        "buildable, and the loop redispatches it.",
    )


def test_skill_pins_the_attempted_set():
    _pin(
        SKILL,
        "attempted-this-drain set",
        "Filtering, not derivation, is what ends the loop: a SKIPPED task's record is "
        "byte-identical after the attempt.",
    )
    _pin(
        SKILL,
        "never re-dispatch a task already in the attempted-this-drain set",
        "Re-dispatching an attempted task rebuilds work that is already committed.",
    )


def test_skill_pins_the_drain_verbs():
    for verb in DRAIN_VERBS:
        _pin(SKILL, verb, "The loop is driven entirely through the drain CLI's verbs.")


def test_skill_calls_the_substrate_verbs_rather_than_re_deriving_them():
    for verb in DRAIN_SUBSTRATE_VERBS:
        _pin(
            SKILL,
            verb,
            "The cap is durable state on disk and the sync/teardown gates are "
            "classifications of another tool's JSON — a coordinator that tracked either in "
            "its own transcript loses the cap to a restart and drifts from the JSON "
            "silently.",
        )


def test_skill_opens_the_cap_slot_through_record_not_a_retyped_command():
    # The coordinator must never re-interpolate the agent's own branch/sha/
    # diffstat into a second command string: `record --mark-inflight` opens the
    # slot in the process that already parsed those values from the file.
    _pin(
        SKILL,
        "--mark-inflight",
        "Without it the loop reads branch/sha/diffstat back out of `record`'s JSON and "
        "retypes them into `drain inflight mark` — agent-authored free text reassembled "
        "into a shell command, which is exactly what the ground rules forbid.",
    )
    _pin(
        SKILL,
        "**You never retype the branch, the sha, or the diffstat.**",
        "The prose has to forbid the substitution explicitly; a flag that merely exists "
        "does not stop a coordinator from doing it the old way.",
    )


# --- skill: the camp sync gate ------------------------------------------------


def test_skill_pins_the_sync_gate_per_member_actions():
    _pin(
        SKILL,
        "per-member `action`",
        "The top-level status is not the signal; each member's own action is.",
    )
    for action in ("`skip-dirty`", "`skip-off-main`", "`absent`"):
        _pin(
            SKILL,
            action,
            "A member left un-synced by any of these three is a stale base the drain would "
            "build the task on top of.",
        )


def test_skill_pins_the_ok_with_warnings_trap():
    _pin(
        SKILL,
        "`ok_with_warnings` only when `errors > 0`",
        "Reading the top-level status as the gate is the exact trap: every skip surfaces "
        "under a top-level `ok`.",
    )


# --- skill: the resume ritual -------------------------------------------------


def test_skill_pins_the_unconditional_resume_ritual():
    _pin(
        SKILL,
        "whenever the task carries a `craft/branch` label",
        "Camp provisioning never fetches the task branch, so a remote-only branch yields a "
        "fresh local branch off base; a conditional resume silently discards pushed work.",
    )
    for step in (
        "git fetch origin",
        "reset the workspace branch to the remote branch",
        "rebase onto `origin/main`",
    ):
        _pin(SKILL, step, "The three resume steps are the whole of the recovery.")


def test_skill_pins_resume_conflict_goes_to_failed():
    _pin(
        SKILL,
        "conflict is a `FAILED` bucket, never a fresh start",
        "A fresh start on conflict throws away the previous run's committed work.",
    )


def test_skill_pins_the_stale_worktree_registration_edge():
    _pin(
        SKILL,
        "stale worktree registration",
        "An abnormally torn-down workspace (an `rm -rf` rather than `camp remove`) leaves a "
        "registration that fails `git worktree add`, and the failure reads as unexplained.",
    )


# --- skill: status-edge ownership ---------------------------------------------


def test_skill_pins_the_loop_session_as_sole_status_writer():
    _pin(
        SKILL,
        "The loop session writes every task status edge",
        "Status ownership split between the loop and a dispatched agent gives one status "
        "two writers.",
    )


def test_skill_pins_done_immediately_after_push():
    _pin(
        SKILL,
        "`done` immediately after the push succeeds",
        "`done` means committed and pushed; deferring it past the push loses the edge if "
        "the coordinator dies.",
    )


def test_skill_pins_the_blocked_park():
    _pin(
        SKILL,
        "## Refine — unresolved",
        "The literal heading is the answered-blocked predicate a later refine sweep greps "
        "for; a reworded heading parks the task where nothing finds it.",
    )
    _pin(
        SKILL,
        "re-assert `craft/branch`",
        "Without the label the next drain cannot find the branch the parked work is on.",
    )


def test_skill_pins_the_run_claim_written_at_dispatch():
    _pin(
        SKILL,
        "--status in-progress --label craft/branch=worktree-<slug>",
        "Nothing else writes the run claim: the executor agent is forbidden from writing "
        "any status, so without this write at dispatch a crashed run leaves the task "
        "`ready` (not `in-progress`), the resume ritual finds no `craft/branch` label, and "
        "the drain queue's workspace-ownership check reads the task as a collision.",
    )
    _pin(
        SKILL,
        "before you dispatch",
        "A claim written after the dispatch is a claim a crash between the two loses.",
    )


def test_skill_pins_the_crash_edge():
    _pin(
        SKILL,
        "stays `in-progress` — the claim §4.4 already wrote",
        "The crash state is true only because the loop wrote the claim at dispatch; a "
        "crashed run writes nothing itself, so the dispatch-time write is what leaves the "
        "record and the workspace as recovery handles.",
    )
    _pin(
        SKILL,
        "its workspace is preserved",
        "A crashed run's workspace holds uncommitted work; removing it destroys the only "
        "recovery handle.",
    )


# --- skill: the portage tail --------------------------------------------------


def test_skill_pins_the_loop_session_dispatching_the_portage_tail():
    _pin(
        SKILL,
        "the loop session dispatches portage's `updater` and then `monitor` — never the executor agent",
        "A monitor dispatched from inside another subagent loses its notification channel.",
    )


def test_skill_pins_outcome_file_independence_from_the_notification_channel():
    _pin(
        SKILL,
        "the monitor outcome file is the contract, never the notification",
        "The drain must survive a lost notification; only the file is polled.",
    )
    _pin(
        SKILL,
        "a missing or empty monitor outcome file reads as crashed",
        "An unwritten file is the crash signal; treating it as 'still running' wedges the "
        "cap forever.",
    )


def test_skill_pins_the_inflight_cap_pause():
    _pin(
        SKILL,
        "pause dispatch while the in-flight count is at the cap",
        "Without the pause an unattended drain opens unbounded unmerged PRs.",
    )
    _pin(
        SKILL,
        "ranger drain start --inflight-cap N",
        "The cap is configurable, and the flag is the only way an operator changes it.",
    )
    _pin(SKILL, "default 3", "The default in-flight cap is 3 unmerged pushed tasks.")


def test_skill_pins_the_monitor_deadline_reclaim():
    _pin(
        SKILL,
        "ranger drain start --monitor-deadline",
        "A hung monitor with no deadline wedges a cap slot forever with no signal.",
    )
    _pin(SKILL, "default 2h", "The default monitor-slot deadline is 2 hours.")
    _pin(
        SKILL,
        "`monitor-timeout` bucket",
        "A reclaimed slot must be distinguishable from a merged one in the report.",
    )
    _pin(
        SKILL,
        "a reclaimed slot never removes the task's workspace",
        "An expired deadline means the loop lost track of the PR, not that the work is "
        "disposable.",
    )


def test_skill_pins_that_nothing_in_the_drain_applies_the_approval_signal():
    _pin(
        SKILL,
        "never applies the approval signal itself",
        "A gate the automation can open is not a gate.",
    )


# --- skill: teardown, reporting, exit -----------------------------------------


def test_skill_pins_teardown_at_monitor_terminal():
    _pin(
        SKILL,
        "tear the workspace down with `camp remove`",
        "An ephemeral workspace left standing per task exhausts the operator's disk and "
        "their attention.",
    )
    _pin(
        SKILL,
        "portage absent (degraded), tear down at push instead",
        "With no monitor there is no monitor-terminal to wait for.",
    )


def test_skill_pins_monitor_timeout_workspaces_as_listed_but_never_removable():
    # §7 preserves the workspace and §8 hands `finish` the still-standing
    # list; without this the two read as licensing a `camp remove` on the one
    # workspace class whose whole point is that the loop lost track of its PR.
    _pin(
        SKILL,
        "never carries a `camp remove`",
        "A monitor-timeout workspace is preserved because the loop lost track of the PR; a "
        "remove command next to it destroys the only handle back to that work.",
    )


def test_skill_pins_one_summary_line_per_task():
    _pin(
        SKILL,
        "one summary line per task",
        "A long unattended drain with no per-task line is indistinguishable from a stalled "
        "one.",
    )


def test_skill_pins_the_exit_condition():
    _pin(
        SKILL,
        "Exit when the filtered buildable set is empty, no slot is still in flight, and no monitor is outstanding",
        "An empty derivation with monitors outstanding is a drained queue, not a finished "
        "drain — their outcomes are still owed to the report.",
    )


def test_skill_never_reads_a_task_body():
    _pin(
        SKILL,
        "You never read a task record",
        "Reading record bodies in the coordinator defeats the containment the dispatch "
        "exists to provide.",
    )


# --- skill: shell hygiene -----------------------------------------------------


def test_skill_snippets_are_posix_sh():
    offenders = [
        (n, line) for n, line in _code_block_lines(SKILL) if _FISH_SET_RE.match(line)
    ]
    assert not offenders, (
        "SKILL.md carries fish-style `set NAME value` assignments; snippets must run "
        f"unchanged under POSIX sh: {offenders}"
    )


def test_agent_snippets_are_posix_sh():
    offenders = [
        (n, line) for n, line in _code_block_lines(AGENT) if _FISH_SET_RE.match(line)
    ]
    assert not offenders, (
        f"agents/execute.md carries fish-style `set NAME value` assignments: {offenders}"
    )


# --- agent: the outcome-file contract -----------------------------------------


def test_agent_pins_the_return_vocabulary():
    for token in RETURN_TOKENS:
        _pin(
            AGENT,
            token,
            "`ranger.drain.report.parse_drain_outcome` accepts exactly these four tokens; a "
            "fifth spelling buckets a finished build as `failed`.",
        )


def test_agent_pins_the_outcome_file_as_the_only_result_channel():
    _pin(
        AGENT,
        "Your reply is never read as the result of your run",
        "A reply is a channel no contract can enforce.",
    )
    _pin(
        AGENT,
        "printf '%s\\n'",
        "The one-line write is the whole result; anything else is unparseable.",
    )


def test_agent_pins_mandatory_vault():
    _pin(
        AGENT,
        "--vault <elected-vault>",
        "`lore` locates a record by a cwd-blind first-match scan across configured vaults; "
        "an unvaulted write lands in someone else's vault, silently.",
    )


def test_agent_pins_its_four_prohibitions():
    _pin(
        AGENT,
        "You never write a task status",
        "The loop session is the sole status writer.",
    )
    _pin(
        AGENT,
        "You never merge",
        "The merge is the monitor's, behind the human-approval gate.",
    )
    _pin(
        AGENT,
        "You never invoke a skill",
        "No trailhead subagent has the Skill tool; prose telling it to invoke one describes "
        "a capability it does not have.",
    )
    _pin(
        AGENT,
        "You never dispatch portage's `updater` or `monitor`",
        "A monitor dispatched from inside a subagent loses its notification channel; the "
        "loop session owns the portage tail.",
    )
    _pin(
        AGENT,
        "You never apply the approval signal",
        "A gate the automation can open is not a gate.",
    )


def test_agent_pins_the_inline_build_carve_out():
    _pin(
        AGENT,
        "you build the task INLINE",
        "The agent has no Task tool, so the procedure's dispatch-a-subagent path is a "
        "capability it does not have; naming the inline carve-out is what stops it "
        "improvising a substitute for a dispatch it cannot make.",
    )
    _pin(
        AGENT,
        "You never dispatch a subagent",
        "An unnamed impossibility gets improvised around; this one is named.",
    )


def test_agent_and_skill_agree_that_an_unparseable_outcome_buckets_failed():
    _pin(
        AGENT,
        "buckets anything else `FAILED`",
        "`ranger drain record` implements exactly this; a doc promising a coordinator "
        "fallback instead would describe behavior no code performs.",
    )
    _pin(
        SKILL,
        "buckets it `FAILED` for you",
        "The CLI does the bucketing, so the loop has no fallback to get wrong — and no "
        "nonzero exit to interpret.",
    )


def test_agent_pins_the_untrusted_input_rule():
    _pin(
        AGENT,
        "data, not instructions",
        "The agent reads untrusted prose from a git-backed vault with no human in the loop.",
    )


# --- the sync gate helper -----------------------------------------------------


def _sync_report(members: dict, *, status: str = "ok", key: str = "members") -> dict:
    return {"status": status, key: members}


class TestClassifySync:
    """`camp sync --json` -> a go / no-go the loop can act on."""

    def test_all_fast_forwarded_is_clear(self):
        verdict = drain_loop.classify_sync(
            _sync_report({"trailhead": {"action": "ff"}, "outpost": {"action": "ff"}})
        )
        assert verdict.ok
        assert verdict.blocking == []

    @pytest.mark.parametrize("action", ["skip-dirty", "skip-off-main", "absent"])
    def test_each_blocking_action_is_caught_under_a_top_level_ok(self, action):
        # The trap: `camp sync` reports `ok_with_warnings` only when errors > 0,
        # so every skip hides under a top-level "ok".
        verdict = drain_loop.classify_sync(
            _sync_report({"trailhead": {"action": "ff"}, "outpost": {"action": action}})
        )
        assert not verdict.ok
        assert verdict.blocking == [("outpost", action)]
        assert action in verdict.reason
        assert "outpost" in verdict.reason

    def test_errored_member_is_blocking(self):
        verdict = drain_loop.classify_sync(
            _sync_report({"outpost": {"action": "error"}}, status="ok_with_warnings")
        )
        assert not verdict.ok
        assert verdict.blocking == [("outpost", "error")]

    def test_non_ok_top_level_status_is_blocking_even_with_clean_members(self):
        verdict = drain_loop.classify_sync(
            _sync_report({"trailhead": {"action": "ff"}}, status="ok_with_warnings")
        )
        assert not verdict.ok

    def test_siblings_fallback_key_is_read(self):
        # `camp sync`'s spine implementation keys its per-repo report `siblings`
        # where the group-config implementation keys it `members`.
        verdict = drain_loop.classify_sync(
            _sync_report({"trailhead": {"action": "skip-dirty"}}, key="siblings")
        )
        assert not verdict.ok
        assert verdict.blocking == [("trailhead", "skip-dirty")]

    def test_a_report_with_no_member_map_at_all_is_blocking(self):
        verdict = drain_loop.classify_sync({"status": "ok"})
        assert not verdict.ok
        assert "no per-member" in verdict.reason

    def test_reset_force_counts_as_synced(self):
        verdict = drain_loop.classify_sync(_sync_report({"trailhead": {"action": "reset-force"}}))
        assert verdict.ok

    def test_an_unknown_action_is_blocking_rather_than_assumed_clean(self):
        verdict = drain_loop.classify_sync(_sync_report({"trailhead": {"action": "rejected"}}))
        assert not verdict.ok
        assert verdict.blocking == [("trailhead", "rejected")]


# --- teardown eligibility -----------------------------------------------------


class TestTeardownEligibility:
    """Which monitor outcomes license `camp remove` on the ephemeral workspace."""

    def test_merged_tears_down(self):
        decision = drain_loop.teardown_decision("MERGED")
        assert decision.teardown
        assert "merged" in decision.reason

    @pytest.mark.parametrize(
        "line",
        [
            "READY approved label absent — awaiting human approval",
            "BLOCKED CI red after three fix attempts",
            "STOPPED operator interrupt",
        ],
    )
    def test_terminal_states_still_needing_a_human_preserve_the_workspace(self, line):
        decision = drain_loop.teardown_decision(line)
        assert not decision.teardown

    @pytest.mark.parametrize("line", [None, "", "   \n"])
    def test_a_missing_or_empty_outcome_file_preserves_the_workspace(self, line):
        decision = drain_loop.teardown_decision(line)
        assert not decision.teardown
        assert decision.crashed

    def test_an_unparseable_line_preserves_the_workspace_and_is_not_a_crash(self):
        decision = drain_loop.teardown_decision("all good, merged it!")
        assert not decision.teardown
        assert not decision.crashed

    def test_degraded_mode_tears_down_at_push_with_no_monitor_outcome(self):
        decision = drain_loop.teardown_decision(None, degraded=True)
        assert decision.teardown
        assert not decision.crashed

    def test_an_expired_slot_never_tears_down(self):
        assert not drain_loop.teardown_decision("MERGED", expired=True).teardown


# --- operator re-entry rituals -------------------------------------------------


def test_rituals_doc_ships():
    assert RITUALS.exists(), f"Expected the operator re-entry rituals doc at {RITUALS}"


def test_skill_references_the_rituals_doc():
    _pin(
        SKILL,
        "operator-rituals.md",
        "The loop skill must point an operator at the rituals doc, not just at this file.",
    )


def test_report_py_references_the_rituals_doc():
    _pin(
        REPORT_PY,
        "skills/execute/operator-rituals.md",
        "The report module must point back at the rituals doc a stranded state sends the "
        "operator to.",
    )


def test_rituals_doc_names_the_failed_ritual():
    _pin(
        RITUALS,
        "craft/push=failed",
        "The failed ritual's guard label must be named exactly as craft's execute ritual "
        "writes it.",
    )
    _pin(
        RITUALS,
        "lore record update task/<name> --vault <vault> --unset-label craft/push",
        "The exact clearing command is the whole of the failed ritual's recovery, and "
        "without `--vault` it clears the guard in whichever vault lore's config happens to "
        "list first.",
    )


def test_rituals_doc_names_the_blocked_ritual():
    _pin(
        RITUALS,
        "## Refine — unresolved",
        "The blocked ritual must name the literal section heading the answer is added to.",
    )
    _pin(
        RITUALS,
        "**Answer:**",
        "The answered-blocked edge is the exact-case `**Answer:**` line refine's queue "
        "classifier reads.",
    )


def test_rituals_doc_names_the_crashed_ritual():
    _pin(
        RITUALS,
        "`in-progress` and its workspace preserved",
        "The crashed ritual must name the exact state a dead coordinator leaves the task in.",
    )
    _pin(
        RITUALS,
        "the loop wrote at dispatch",
        "The task reads `in-progress` because the loop claimed it before dispatching, not "
        "because anything wrote a status on the way down — an operator who expects a "
        "crash-time write looks for a record that was never made.",
    )
    _pin(
        RITUALS,
        "lore record update task/<name> --vault <vault> --status ready "
        "--label craft/branch=worktree-<slug>",
        "The exact recovery command re-asserting `craft/branch` is the crashed ritual's "
        "clear-to-ready path, and without `--vault` it lands in whichever vault lore's "
        "config happens to list first.",
    )


def test_every_rituals_recovery_command_names_its_vault():
    for line in RITUALS.read_text().splitlines():
        if "lore record update" in line:
            assert "--vault" in line, (
                "every `lore record update` in the rituals doc must name `--vault`; an "
                f"unvaulted write lands in someone else's vault, silently: {line!r}"
            )


def test_rituals_doc_routes_a_monitor_blocked_to_the_failed_bucket():
    _pin(
        RITUALS,
        "monitor's own `BLOCKED` line is a red PR, not a parked question",
        "The `blocked` bucket and its answer-line ritual exist for an executor's parked "
        "operator question; a monitor's BLOCKED parks no question anywhere, so it reports "
        "`failed` and the answer ritual would have nothing to answer.",
    )
    _pin(
        RITUALS,
        "`camp remove <slug>`",
        "The crashed ritual's abandon path is the exact teardown command.",
    )


def test_rituals_doc_names_the_stale_lock_ritual_and_crash_signal():
    _pin(
        RITUALS,
        "state_dir(\"ranger\")/locks/<vault>.lock",
        "The stale lock ritual must name the exact lock path.",
    )
    _pin(
        RITUALS,
        "held lock whose holder is no longer alive, paired with an unfinished exit report",
        "The crash-signal pair must be named explicitly, and it is not an *absent* report: "
        "`ranger drain start` writes the report as it takes the lock, so every locked vault "
        "has one — the signal is a report that was never finished.",
    )
    _pin(
        RITUALS,
        "Nothing in the drain ever removes a lock file itself",
        "The stale lock ritual must forbid automated removal — an operator-only `rm`.",
    )


def test_rituals_doc_names_the_approval_and_cap_stall_ritual():
    _pin(
        RITUALS,
        "monitor merges a `done` PR without any human-approval check",
        "The rituals doc must state the auto_merge merge policy monitor enforces.",
    )
    _pin(
        RITUALS,
        "No drain component, executor, or portage agent ever applies the `human-approved` label",
        "The prohibition on automated self-approval must be explicit.",
    )
    _pin(
        RITUALS,
        "`merged` / `in-flight` / `awaiting-human-approval` / `monitor-timeout`",
        "The cap-stall ritual must name the pushed bucket's full substate split as it reads "
        "in the report.",
    )


def test_rituals_doc_names_the_corrupt_state_file_ritual():
    _pin(
        RITUALS,
        "refuses this by name rather than resetting it blind",
        "The corrupt-state ritual must name the refuse-by-name behavior, not a blind reset.",
    )
    _pin(
        RITUALS,
        "`ranger drain finish`",
        "The corrupt-state ritual must name how the run is closed out.",
    )


def test_rituals_doc_describes_degraded_trust_mode():
    _pin(
        RITUALS,
        "`degraded: true`",
        "The degraded-trust mode must be named by its exact flag.",
    )
    _pin(
        RITUALS,
        "no PR tail, no monitor, and no in-flight cap",
        "The degraded-trust mode description must name what is absent.",
    )


def test_no_ritual_instructs_a_vault_write_outside_the_lore_cli():
    text = RITUALS.read_text()
    forbidden = ("Edit ", "edit the record", "> ~/.local", "sed -i", "write directly to")
    offenders = [phrase for phrase in forbidden if phrase in text]
    assert not offenders, (
        f"operator-rituals.md appears to instruct a non-CLI vault write: {offenders}"
    )
    # Every vault mutation named in the doc must go through `lore record update`.
    assert "lore record update" in text


# --- discoverability ----------------------------------------------------------


def test_drain_skill_is_discoverable_by_the_capabilities_loader():
    """Convention discovery, not a hand-listed entry: `skills/<name>/SKILL.md`.

    A skill directory without a `SKILL.md` is never selectable, so `trailhead
    install` would ship a plugin whose whole coordinator loop is invisible.
    """
    skills = load_manifest(MANIFEST).skills
    assert skills.get("execute") == "skills/execute", (
        "ranger's execute skill must be discoverable as a selectable capability — "
        f"expected `execute -> skills/execute`, got {skills!r}"
    )


def test_drain_agent_is_discoverable_by_the_capabilities_loader():
    subagents = load_manifest(MANIFEST).subagents
    assert subagents.get("execute") == "agents/execute.md", (
        "ranger's execute agent must be discoverable as a selectable subagent — "
        f"expected `execute -> agents/execute.md`, got {subagents!r}"
    )


# --- no outcome leaves a task in a state nothing re-derives --------------------


def test_skill_records_the_outcome_from_the_file_never_from_a_command_string():
    _pin(
        SKILL,
        "--outcome-file",
        "`$(cat …)` interpolates agent-written text into a command string, which the "
        "skill's own ground rules forbid; the verb recomputes the path itself.",
    )
    _pin(
        SKILL,
        "Never pass the file's contents on the command line",
        "The prohibition has to be stated, not just modeled by the snippet — an editor "
        "reaching for `$(cat …)` needs a reason not to.",
    )


def test_skill_pins_a_missing_agent_outcome_as_crashed_not_failed():
    _pin(
        SKILL,
        "missing or empty file is a different bucket",
        "An agent that wrote nothing died, timed out, or never ran: its workspace is "
        "preserved and its run claim still stands, which is the crashed ritual. The failed "
        "ritual's recovery assumes an outcome line to read.",
    )


def test_skill_pins_the_failed_status_edge_back_to_ready():
    _pin(
        SKILL,
        "--status ready --label craft/branch=worktree-<slug>",
        "A dispatched task that failed is left `in-progress` by the run claim, and nothing "
        "re-derives an `in-progress` task: the drain queue derives from `ready` and the "
        "refine sweep from `open`/`blocked`. The label rides along because work may exist "
        "on the branch.",
    )
    _pin(
        SKILL,
        "re-derived by **nothing**",
        "The reason the release edge exists must be stated where the edge is written.",
    )


def test_skill_pins_that_a_pre_dispatch_skip_never_left_ready():
    _pin(
        SKILL,
        "the task never left `ready`",
        "A `SKIPPED` or a `FAILED` synthesized before §4.4's claim has no claim to release "
        "— saying so is what stops an editor inventing a status write for it.",
    )


def test_skill_pins_resolving_a_slot_exactly_once():
    _pin(
        SKILL,
        "Resolve each slot exactly once",
        "Resolving a slot `inflight expire` already reclaimed overwrites its "
        "`monitor-timeout` line with an empty branch and sha — the only record of the "
        "timed-out PR.",
    )


def test_rituals_doc_gives_the_failed_ritual_its_restore_command():
    _pin(
        RITUALS,
        "lore record update task/<name> --vault <vault> --status ready "
        "--label craft/branch=worktree-<slug>",
        "Ritual 1 must carry the exact command for the state it restores, like ritual 3 "
        "does; naming a state with no command sends the operator to a vault edit.",
    )
    _pin(
        RITUALS,
        "back at `ready` with `craft/branch` asserted",
        "Ritual 1's opening claim must match what the loop actually wrote — an operator "
        "told the record is untouched looks for a state that is not there.",
    )
    _pin(
        RITUALS,
        "left `in-progress` is re-derived by nothing",
        "The whole reason the restore command exists: no queue derives an `in-progress` "
        "task.",
    )


def test_rituals_doc_crashed_ritual_covers_the_agent_crash_too():
    _pin(
        RITUALS,
        "dispatched executor agent left no outcome file at all",
        "`record` buckets an agent that wrote nothing as `crashed`; without this entry "
        "point named, the report's crashed line points at a ritual that only mentions a "
        "dead coordinator.",
    )


def test_rituals_doc_names_the_concrete_unfinished_report_marker():
    _pin(
        RITUALS,
        "An absent report is not the signal",
        "`report.start` writes the report as the lock is taken, so an operator looking for "
        "an absent file never finds the crash.",
    )
    _pin(
        RITUALS,
        "only `ranger drain finish` writes",
        "The marker an operator checks has to be a concrete, findable one.",
    )


def test_rituals_doc_names_the_degraded_in_flight_terminal_state():
    _pin(
        RITUALS,
        "pushed tasks stay under **In flight** for the life of the report",
        "With no monitor to resolve them, a degraded run's pushed lines never leave the "
        "`in-flight` substate; unsaid, a finished degraded report reads as a stalled drain.",
    )
