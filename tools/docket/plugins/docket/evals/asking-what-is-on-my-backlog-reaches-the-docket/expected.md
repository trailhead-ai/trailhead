# asking-what-is-on-my-backlog-reaches-the-docket

## Under test

The claim: a plain request for the backlog by its old name is handled by the docket skill, which runs the docket report, instead of a user-level skill named backlog. The prose under test is the trigger wording in `skills/docket/SKILL.md`. The fixture is the shared docket scaffold (`evals/docket-scaffold.sh`) plus a decoy user-level `backlog` skill, with the old skill's description, planted at `$(dirname "$HOME")/config/skills/backlog/SKILL.md`: the run's config dir, the only place the runner's session sees a user-level skill. Without the decoy the docket would be the only skill and routing could not fail.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/docket/plugins/docket`, `--ablation none`, `--scaffold`, 3 runs.
- A run scores the fraction of graders that pass and passes only at 1.0. The case passes at 2 of 3 runs.

## Pass condition

Every grader holds.

| Grader |
|---|
| docket-skill-handled-it |
| old-backlog-skill-was-not-used |
| docket-report-ran |

## Limitations

- Phrases both descriptions claim ("what's next", "what should I work on") are not covered; retiring the old skill is what settles them.
- Real-host routing against the actual old skill stays a manual check.
