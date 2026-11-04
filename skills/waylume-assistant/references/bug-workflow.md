# BUG workflow

Use this flow for a newly reported or reopened BUG. Apply current user instructions and the project/host's issue-reader procedure; this reference is the coordination policy, not a replacement for a real issue reader.

## 1. Intake and responsibility

- Find the existing BUG item by stable issue ID and read its current context, deduplication history, responsibility evidence, attachments, and pending follow-ups. Reuse it instead of creating a second item.
- Obtain the authoritative issue description and relevant comments/history/attachments through an available approved tool. Review supplied images and videos when they affect the report. Distinguish what was actually opened from what is merely listed.
- Determine the user's responsibility from the persisted, current scope record and evidence. Do not infer ownership from an assignee, reporter, or commit history. If out of scope or materially ambiguous, stop code/device work, record why, and suggest the evidenced owner.
- Check whether it duplicates a previous fix or known issue. Verify the current build/source baseline before claiming the fix is present.

## 2. Two confidence values and severity

Record separately:

- **C — understanding/root-cause confidence:** how strongly source evidence and issue materials support the explanation.
- **F — repair safety confidence:** how likely the proposed smallest change is to fix this case without regressions.
- **Severity and invasiveness:** user impact, affected boundary, files/layers, and dependencies; do not collapse these into C or F.

Use the user's established thresholds when present. A portable default is: when C is at least 90%, a pre-fix reproduction may be skipped if evidence directly proves the path and authorization allows; when C is below 90%, make one purposeful reproduction attempt if useful and available. One failed attempt is not a reason to repeat unchanged conditions. If still not reproduced, retain that fact and the uncertainty. Do not repair when ownership is unclear, C is low, or F is insufficient. Complex changes, shared contracts, another owner's framework, or meaningful risk require a proposed scope and user decision unless explicit authorization already covers them.

## 3. Fix and verify

- Make the smallest change within confirmed ownership. Preserve unrelated edits. Check cancellation, error paths, repeated actions, stale async responses, and state restoration where relevant.
- Review only the assigned diff and necessary integration boundary. Keep review, build, reproduction, acceptance, and platform state separate.
- Choose evidence to fit behavior: screenshot for a stable static UI state; continuous video for transitions, scrolling, timing, or sequences. Logs/tests may support nonvisual assertions but cannot substitute for visual proof when appearance is the requirement. Audio correctness needs an audio-capable observation; source inspection is not evidence that sound is right.
- Batch device acceptance where multiple assigned BUGs share a build/device, but preserve distinct expected/actual observations per issue. Never run two agents against the same device at once.
- Record exact build/source identity, reproduction, repair, review, acceptance evidence, limitations, and next step in the existing item. Do not mark complete from code presence alone.

## 4. Delivery

Commit, push, issue status changes, or comments require the user's applicable authorization. Stage only intended files and verify the final diff against the responsibility boundary. If blocked, leave the item with the exact blocker, evidence, and smallest next action; do not claim completion.
