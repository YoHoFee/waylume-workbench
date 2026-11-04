---
name: waylume-assistant
description: Coordinate work, bugs, and user-delegated tasks through the Waylume workbench; use when an agent must resume, update, or hand off a stored item.
metadata:
  short-description: Work with the Waylume assistant workbench
---

# Waylume Assistant

Use the workbench as the shared task router and durable context for the user and their agents. Treat each item as its own workstream: read its current record and context before acting, update that item during meaningful progress, and leave a concise, evidence-based handoff. Do not conflate company-assigned work with user-delegated tasks or BUGs.

## Start and act

1. Follow the host's current user instructions and repository instructions. Resolve the exact workbench item before resuming; do not create a duplicate when an item already exists.
2. Read the item's latest context, pending user follow-ups, and applicable user profile/responsibility scope from the configured personal data directory (`~/.waylume` by default). If the workbench API/CLI is unavailable, say so and use only a documented, safe fallback; do not edit its storage behind a running service.
3. Determine responsibility before changing code or external records. Make only the requested work and the smallest necessary integration. Preserve another owner's logic; record and report adjacent risks instead of opportunistically fixing them.
4. Keep status factual. “In progress” requires an active agent owner; otherwise use the workbench's paused/queued state. Never fabricate session IDs, verification, approvals, completion, or external updates.
5. Report meaningful milestones to the original item. Preserve earlier evidence and decisions; append or revise the current summary without deleting useful history. Apply user follow-ups to that item, mark each as handled after acting on it, and report outcome even if blocked.
6. Before closing, distinguish implementation, review, test/acceptance, Git delivery, and external platform status. State what is verified and what remains unknown.

## References

- Read [role and responsibility boundaries](references/role-and-boundaries.md) when establishing scope or coordinating parallel work.
- Read [BUG workflow](references/bug-workflow.md) for BUG intake, confidence, reproduction, repair, and acceptance.
- Read [troubleshooting](references/troubleshooting.md) only for matching prior failures; add an experience only when the cause and remedy have evidence.
- Read [memory policy](references/memory-policy.md) before recording durable knowledge or deciding where context belongs.
- Read [host integration](references/host-integration.md) when installing, discovering, or validating this Skill on Codex, Gemini/Antigravity, or DSH.

The Skill does not grant permission to access private data, operate a device, write to company systems, commit, or push. Honor explicit authorization and any applicable safety boundary at the point of action.
