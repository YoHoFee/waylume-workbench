# Memory and record policy

Use the narrowest durable store that serves the next agent without leaking private information.

| Information | Store | Rule |
|---|---|---|
| Stable assistant role, durable user preferences, consent boundaries | Host's long-term memory, only when the user explicitly asks to save/update it | Keep concise, stable, and actionable; do not store secrets or transient task state. |
| Current goal, decisions, owner/session, progress, evidence, blockers, follow-ups, next action | The workbench item's JSON/context record | Update the original item; preserve prior evidence and distinguish fact from inference. |
| Company requirements, responsibility maps, issue details, private attachments, authentication/configuration | User's private data directory (default `~/.waylume`) or the original approved source | Never place these in this public/generic repository or Skill files. |
| Reusable, evidence-backed failure prevention | Private troubleshooting store; public Skill only for generic process | Include symptom/root cause/remedy/applicability/evidence; no credentials or raw company material. |
| General product usage or rarely needed host facts | Reference docs or official source | Keep as reference, not active long-term memory; verify version-sensitive details. |

Do not use long-term memory as a replacement for an item context. Do not save a user's stable preference without their explicit request. A new agent should first read the generic Skill, then the configured private user profile and the exact item it is assigned; load older logs only when the item points to them or the current question requires them.

Paths must use home expansion or runtime configuration, never a person's literal username. Do not copy a private profile into this repository. On migration, preserve the prior data directory until the user verifies the migrated copy.
