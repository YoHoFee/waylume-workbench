---
name: waylume-assistant
description: Coordinate work, bugs, and user-delegated tasks through the Waylume workbench; use when an agent must resume, update, or hand off a stored item.
metadata:
  short-description: Work with the Waylume assistant workbench
---

# Waylume Assistant

Use the workbench as the shared task router and durable context for the user and their agents. Treat each item as its own workstream: read its current record and context before acting, update that item during meaningful progress, and leave a concise, evidence-based handoff. Do not conflate company-assigned work with user-delegated tasks or BUGs.

## Create a work assistant

When the user asks to create, initialize, or configure a work assistant, follow [create-work-assistant](skills/create-work-assistant/SKILL.md) to establish its role, guidance and supported durable memory.

## Workflow

If the item has a workflow (or the user asks to start, advance, audit, accept or deliver one), read [workflow contract](references/workflow.md) before acting. The workbench owns the six-stage state machine, required outputs, locks, role bindings and dispatch card. The host owns concrete agent identities and transport; do not put host-specific dispatch policy into the workbench record.

Always read `dashboardctl.py dispatch-card <ITEM_ID>` (or `GET /api/items/<ITEM_ID>/dispatch-card`) immediately before dispatching a role. Follow its `next.action` and `next.role`; do not infer the next step from chat memory. Delivery is manual: never close, push or release on the user's behalf.

Before dispatching a code task, read the [parallel Git worktree protocol](../../mcloud-workflow/references/parallel-git-worktree.md). The dispatch prompt must include the task's record ID, branch, base, worktree, code owner and build owner. If any field is missing, the Lead allocates it first; do not send a worker into the main checkout or let two workers share a branch.

## Start and act

1. Follow the host's current user instructions and repository instructions. Resolve the exact workbench item before resuming; do not create a duplicate when an item already exists.
2. Read the item's latest context, pending user follow-ups, and applicable user profile/responsibility scope from the configured personal data directory (`~/.waylume` by default). If the workbench API/CLI is unavailable, say so and use only a documented, safe fallback; do not edit its storage behind a running service.
3. Before choosing or using an external tool, consult the workbench tool list and follow [tool discovery and usage](references/tool-discovery.md); verify the current host’s actual tools and their instructions.
4. Determine responsibility before changing code or external records. Make only the requested work and the smallest necessary integration. Preserve another owner's logic; record and report adjacent risks instead of opportunistically fixing them.
5. Keep status factual. The working agent must choose the appropriate non-linear status (`in_progress`, `developing`, `testing`, `on_hold`, or `completed`) and report changes using the workbench agent update/report interface; testing is not a passed test. Sub-agents must follow the same rule. Active states require an active agent owner; the owner becomes stale after two hours without agent activity, after which use the workbench’s on-hold state. Never fabricate session IDs, verification, approvals, completion, or external updates.
6. Report meaningful milestones to the original item. Preserve earlier evidence and decisions; append or revise the current summary without deleting useful history. Apply user follow-ups to that item, mark each as handled after acting on it, and report outcome even if blocked.
7. Before closing, distinguish implementation, review, test/acceptance, Git delivery, and external platform status. State what is verified and what remains unknown.
8. **One-line status is mandatory before finishing.** Write a single line (≤200 characters, no line breaks) saying where the task stands now, what is next, and any blocker, with `task-result <ID> --summary "…" --message "…"` (the `--summary` flag is required; omitting it fails with `status_line_required`). Use `progress --summary` for mid-task updates or `summary <ID> "…"` to correct it. This line is what the card on the home page shows and what appears first in the detail view; the full progress and result go after it.

## Archiving source files

When you archive or register a source file/link (especially CoDesign/高保真 prototypes), put any access password or share code in that source's `note` field, never in a separate password field. Set `note_masked: true` when the note contains a password so the library hides it by default; ordinary notes stay plaintext. Use `POST /api/sources/<id>/edit` with `{note, note_masked}` (or the same keys on `/api/sources/link` when creating). The note is shown in the source card overview.

## References

- Read [workflow contract](references/workflow.md) before starting, advancing, auditing, accepting or delivering a workbench item.
- Read [parallel Git worktree protocol](../../mcloud-workflow/references/parallel-git-worktree.md) before dispatching or accepting any code task; it defines branch/worktree ownership and the mandatory dispatch context.
- Read [role and responsibility boundaries](references/role-and-boundaries.md) when establishing scope or coordinating parallel work.
- Read [BUG workflow](references/bug-workflow.md) for BUG intake, confidence, reproduction, repair, and acceptance.
- Read [troubleshooting](references/troubleshooting.md) only for matching prior failures; add an experience only when the cause and remedy have evidence.
- Read [memory policy](references/memory-policy.md) before recording durable knowledge or deciding where context belongs.
- Read [tool discovery and usage](references/tool-discovery.md) when choosing or using tools and integrations.
- Read [host integration](references/host-integration.md) when installing, discovering, or validating this Skill on Codex, Gemini/Antigravity, or DSH.

The Skill does not grant permission to access private data, operate a device, write to company systems, commit, or push. Honor explicit authorization and any applicable safety boundary at the point of action.
