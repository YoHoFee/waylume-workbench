# Execution and stop notes

Read this only when a failure or handoff needs a decision. For symptom-specific known causes and handling, route through [the troubleshooting index](troubleshooting.md); the dashboard and BUG reader remain the source for current facts, and these notes do not expand authorization.

## Stop or resume

- **Stored item exists:** read by exact ID and revision first. Continue the current phase; do not reopen a completed/transferred item without reliable reopen or reassignment evidence.
- **Same action failed with unchanged inputs:** record the attempt and stop repeating it. Resume only when evidence, environment, target, or user direction changes.
- **One valid reproduction did not reproduce:** follow [the BUG confidence route](bug-confidence.md). C above 80% permits only a tentative minimal repair when F and every A condition are met; at C 80% or lower, report the setup, package identity, evidence, C/F basis, and next question in the existing BUG record and wait. Do not repeat the same attempt without new evidence or changed conditions.
- **Known environment/device/UI symptom:** use the matching route in [the troubleshooting index](troubleshooting.md). If there is no matching entry, capture the specific blocker or one current screen/state, stop repeating blind actions, and continue only independent authorized work.
- **Record ID, revision, or write target mismatch:** stop writes for that target; reread the exact record and report the mismatch. Never guess a repair or automatic rollback.
- **Review finding:** fix only the relevant issue and ask for a focused re-review of the affected diff. Keep real review findings even when this increases effort.

These are routing decisions, not implemented retry automation. `item_context.py` only reads local dashboard record data; it does not claim work, update status, read attachment files, or call a service.

## Suggested current-state header for a context update

When an existing documented report operation accepts `context_markdown`, a concise Markdown header can be used at the top:

```markdown
## Current state
- Conclusion: Static checks passed; device verification is pending.
- Phase / owner:
- Next action:
- Blocker: none known / <specific blocker>
- Evidence and unknowns:

## Evidence and decisions
...
```

Keep the latest state at the top and retain detailed evidence and decisions below it. This is a writing aid only; use only fields and report operations present in the current dashboard README/CLI. Do not fabricate revision values, QA results, owners, or evidence.

## Planned, not available

The cost review proposed build preflight/build receipts, known device navigation recipes, and a safe platform-write adapter. No such helper was implemented in this skill. Continue with existing documented tools and project procedures; do not simulate these proposals or claim they ran.
