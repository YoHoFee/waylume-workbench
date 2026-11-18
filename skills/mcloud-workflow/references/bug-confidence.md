# BUG responsibility, confidence, and batch execution

Use this route after resolving the existing BUG record. Keep findings, confidence, reproduction, repair, and acceptance on that original BUG record. If multiple BUGs share a real-device batch, link each record to one shared batch context; do not create duplicate BUG tasks. This guidance does not grant platform, Git, build, or device authorization.

## Responsibility scope

1. Before relying on responsibility or touching code/device work, read the workbench's persisted `scope-catalog.json` (or its documented read-only scope endpoint) and match the BUG's `related_ids` to stable scope IDs. Record the catalog version, matched scope IDs, source evidence and current responsibility conclusion in the original BUG's `responsibility_evidence` / report text, using only supported dashboard fields.
2. Reuse still-applicable confirmed scope evidence for later BUGs and resumes; do not reread the official requirement table for every BUG. For each new BUG, add the scope match/evidence or candidate relationship to its own record. An assignee, a past code change, or an author's history does not expand ownership. A changed responsibility boundary requires fresh evidence.
3. A `candidate` is unconfirmed and cannot support repair or reproduction until its cited source evidence resolves it. If the catalog is missing, a scope is unknown/suspected out of scope, or evidence conflicts, stop scope-dependent repair and reproduction. Report the concrete gap, evidence and recommendation on the original BUG panel. Do not guess an owner or convert an unconfirmed candidate into responsibility. Explicit user assignment may authorize handling that BUG, but does not silently rewrite the persistent scope boundary.

Maintain scope evidence through `dashboardctl.py scope-upsert --expected-catalog-revision N --input <single-scope.json>`; read the current catalog revision first. This command validates evidence and status under the shared lock and atomically updates the directory revision and entry version. Supply a stable ID and the documented fields in the dashboard README; do not promote a candidate without authoritative new evidence. A new BUG within an existing boundary only needs a BUG-to-scope association and relevant evidence, not a new scope. Associate via the existing `related_ids`, preserving other links; when using `upsert`, use its documented `--expected-store-revision` CAS and preserve required record fields. A conflict requires rereading, not overwriting.

## Dual-confidence route

Read the relevant current code and original issue materials after responsibility is established. Record two separate coarse evidence judgments:

- **C — cause/understanding confidence:** how well the materials, code path, causal link and counterevidence support the explanation. C is not statistical probability.
- **F — safe-repair confidence:** how confidently the smallest proposed change is correct, contained, reversible, and verifiable within confirmed scope.

Also record **severity** and **repair invasiveness** separately. Neither is a confidence score or authorization. Do not average C and F into one number or let high C conceal low F. State the evidence and gaps briefly; do not imply precision beyond the chosen threshold band.

1. At **C >= 90**, skip pre-fix reproduction and proceed to repair assessment; post-fix acceptance remains required.
2. At **C < 90**, make one valid reproduction attempt when the setup and device/package are available. Record identity, setup and result. Do not repeat the same attempt without changed conditions or new evidence.
3. If reproduced, update C only from the new evidence. Reproduction proves the symptom, not its cause. If causal explanation or repair direction remains materially unclear, retain the unresolved report and wait; do not try speculative alternatives.
4. If not reproduced, C **> 80** permits a clearly labeled tentative cause and the smallest repair may be considered, but only if F and all A-route conditions below are met. C **<= 80** is a wait-for-direction boundary. Exactly 80 follows the conservative wait path.
5. A tentative cause remains tentative even if the repair later passes acceptance. Do not mark it reproduced or treat the repair as proof of the original root cause. If post-fix acceptance fails or cannot be obtained, stop and report the evidence; do not blindly iterate.

## A / B route

**A — automatic execution** requires all of these conditions:

- Responsibility is confirmed by current scope evidence and the work is within that boundary.
- The issue is a local, simple, unambiguous correction; it does not materially change another team's framework, public contract, shared container, or established business rule.
- The understanding evidence satisfies the reproduction branch above, and **F >= 90**.
- The result has a targeted, available verification path.
- Existing or current-turn authorization covers every operation needed for the planned outcome.

When A is established and authorization already covers the steps, complete the repair, targeted independent review, batch acceptance if applicable, latest-upstream integration assessment and any required revalidation, then Chinese commit/push and authorized platform handoff. Do not ask again for an already granted step. If any condition changes, stop at the changed boundary and reassess.

**B — user decision required** includes high C with low F, a complex or materially multi-file logic change, a change to another team's framework/public contract/shared container, unresolved ownership, or any material ambiguity. Report the bounded scope, C/F evidence, severity, invasiveness, concrete risk, smallest proposed change and recommendation on the original BUG record. A high C value alone never converts B to A. A ready A item may continue while another BUG waits for B.

Existing task-specific restrictions take precedence. In particular, a paused monitor does not start or schedule inspections, and its read-only collection role does not gain repair, device, Git or platform-write authority from this route.

## Batch real-device validation

- Batch only BUGs that need pre-fix reproduction or post-fix device acceptance; a C >= 90 BUG that needs no reproduction is not added to the reproduction queue solely for batching.
- Freeze the reproduction batch membership when its first reproduction begins. A newly discovered BUG is recorded immediately but waits for the next batch. Split only for a specific urgent severity or conflicting package baseline, and record the reason.
- Use one serial device owner for the batch and record the shared confirmed baseline/package identity. Read-only analysis may run in parallel, but there is one code writer for shared source.
- After fixes, perform one integrated build/install for the batch. Then accept each BUG separately against its own expected observation and evidence; do not merge per-BUG results. Do not weaken or skip a BUG's required verification.
- Once the whole batch is finished, close the business app and restore the full-screen black Photos image once, with a fresh screenshot. Do not perform per-BUG teardown that disrupts the batch.
- Keep each BUG's C/F, severity/invasiveness, reproduction result, fix and acceptance evidence in that original BUG Markdown/context. Put shared baseline, owner, frozen membership, integrated build/package identity and batch-level closeout in one linked shared batch context. Use documented report operations only; do not invent fields, CLI flags or a second same-BUG task.

## Report notes

Use Markdown in the existing BUG report operation. Do not add a backend schema. Keep the initial assessment distinct from later evidence:

```markdown
## BUG triage
- Responsibility decision / scope IDs / catalog version / evidence:
- Severity / repair invasiveness:
- Initial C and basis (materials / code / causality / counterevidence):
- Initial F and basis (scope / containment / alternatives / verification):
- Reproduction status: not attempted / skipped at C>=90 / attempted, not reproduced / reproduced
- Reproduction setup and observed evidence:
- Updated C/F and basis:
- Minimal repair direction and A/B decision:
- Batch ID / frozen membership / shared baseline (if batched):
- Post-fix acceptance evidence and result: pending / passed / failed / unavailable
- Next step or blocker:
```

Post-fix acceptance is a separate result and must remain pending until supported by attributable evidence. Use a screenshot for a stable visual state and a continuous recording for a dynamic flow; static review does not establish device acceptance.
