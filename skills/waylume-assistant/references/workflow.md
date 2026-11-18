# Workflow contract (host-neutral)

Use this reference whenever an item has a workbench workflow. It is part of the workbench product contract, not a specific agent platform's dispatch policy.

The workbench owns the state machine, required outputs, locks, role bindings and dispatch card. The host owns:

- which concrete agents play `dev`, `auditor`, `pm`, `lead`;
- how tasks and reports reach those agents;
- tool permissions and safe execution boundaries.

Do not put host-specific agent names, communication channels, or UI automation into the workbench workflow contract.

## 1. Six stages

```text
intake          接单/立项                    Lead
requirement     需求分析与 PM 结论            PM
development     开发                         Dev
audit_response  静态审计与开发回应            Auditor -> Dev
acceptance      PM 验收                      PM
delivery        交付关闭                      Lead
```

`audit_response` has two substeps:

1. `audit`: auditor submits an advisory static audit report;
2. `response`: the original dev responds to every substantive finding.

`requirement` has two substeps:

1. understanding/差异;
2. PM final decision.

Templates:

- `dev-standard`: task/work;
- `bug-standard`: bug.

Patrol creates items; it does not start a separate pipeline.

## 2. Required outputs

| Stage | Submit role | Required outputs | Advance roles |
|---|---|---|---|
| intake | lead | `scope`, `decision_owner` | lead |
| requirement | pm | `requirement_brief`, `acceptance_criteria`, `pm_decision` | pm, lead |
| development | dev | `dev_summary`, `branch`, `self_check` | dev, lead |
| audit_response | auditor, dev | `audit_report`, `dev_response` | lead |
| acceptance | pm | `pm_acceptance` | pm, lead |
| delivery | lead | `delivery_evidence` | lead |

Never write a stage gate by hand. Use `workflow submit` and `workflow advance`. Missing outputs block advance.

## 3. Role and decision contract

- **Lead** drives stage transitions and process integrity. Lead may inspect evidence and block/return for process reasons. Lead does not decide code correctness or requirement correctness.
- **Dev** owns code changes and the dev decision. The dev may accept, partially accept or reject audit findings, but must answer every substantive finding with reasons and evidence.
- **Auditor** performs static audit only. The auditor does not modify code and cannot force rework. The report must be marked `ADVISORY / 仅供参考`.
- **PM** owns requirement decisions and acceptance. PM does not write business code.
- **Delivery** is manual: the Lead must not close, push, release or deliver on the user's behalf. The pipeline ends at the delivery stage pending the user's action.
- A task has one fixed dev, one fixed auditor and one fixed PM binding. Do not create a second auditor for the same task, and do not replace a role silently. If a host session is unavailable, stop and report the blocker.

## 4. Start and drive a pipeline

```bash
# Inspect templates / current state
python3 dashboardctl.py workflow templates
python3 dashboardctl.py workflow show <ITEM_ID>

# Start a pipeline. For a legacy item that is already developed, the host may
# start at a later stage only when the host has recorded why.
python3 dashboardctl.py workflow start <ITEM_ID> \
  --template dev-standard --stage development \
  --actor-role lead --actor <host-lead> --note "<reason>"

# Work a stage
python3 dashboardctl.py workflow claim <ITEM_ID> --actor-role dev --actor <dev>
python3 dashboardctl.py workflow submit <ITEM_ID> --actor-role dev --actor <dev> \
  --output dev_summary="..." --output branch="..." --output self_check="..."
python3 dashboardctl.py workflow advance <ITEM_ID> --actor-role dev --actor <dev>

# Auditor/Dev response
python3 dashboardctl.py workflow submit <ITEM_ID> --actor-role auditor --actor <auditor> \
  --output audit_report="ADVISORY: ..."
python3 dashboardctl.py workflow submit <ITEM_ID> --actor-role dev --actor <dev> \
  --output dev_response="..."
python3 dashboardctl.py workflow advance <ITEM_ID> --actor-role lead --actor <host-lead>

# PM acceptance
python3 dashboardctl.py workflow submit <ITEM_ID> --actor-role pm --actor <pm> \
  --output pm_acceptance="..."
python3 dashboardctl.py workflow advance <ITEM_ID> --actor-role pm --actor <pm>
```

Every write action takes an optional `--expected-revision` for CAS. Prefer it when two agents may touch the same item.

## 5. Dispatch card: mandatory pre-dispatch read

Before any role is dispatched, read:

```bash
python3 dashboardctl.py dispatch-card <ITEM_ID>
```

or `GET /api/items/<ITEM_ID>/dispatch-card`.

The card is the authoritative answer for:

- current stage/substage and stage status;
- `next.action`: `dispatch`, `advance`, `unblock`, `wait_lock`, `manual_delivery`, or `start`;
- `next.role`: the role to dispatch when action is `dispatch`;
- required and missing outputs;
- current stage lock;
- fixed role bindings;
- warnings, including unbound roles and manual-delivery guard.

Rules:

1. Read the card immediately before every dispatch; do not infer the next step from chat memory.
2. If `next.action=dispatch`, dispatch `next.role` with the stage-appropriate role contract.
3. If `next.action=advance`, do not create another worker; advance the stage.
4. If `next.action=wait_lock`, do not create a second writer.
5. If `next.action=manual_delivery`, wait for the user; do not close.
6. If a role binding is missing, bind it (or report the blocker) before dispatch.
7. The card's `channel` value, when present, is an opaque host label. The host decides what it means.

## 6. Bind fixed roles

Bindings are task metadata, not workflow gates:

```bash
python3 dashboardctl.py bind-agent <ITEM_ID> --role dev \
  --actor "<host-agent-id>" --session-id "<host-session-id>" --channel "<host-label>"
python3 dashboardctl.py bind-agent <ITEM_ID> --role auditor \
  --actor "<host-agent-id>" --channel "<host-label>"
python3 dashboardctl.py bind-agent <ITEM_ID> --role pm \
  --actor "<host-agent-id>" --channel "<host-label>"
```

The host maps `dev`/`auditor`/`pm` to concrete agents and its own transport. Workbench does not prescribe that mapping.

## 7. Locks

- A stage worker should `claim` before working, `heartbeat` while active, and `submit`/`advance` or `release` at the end.
- A lock conflict means another writer owns the stage; do not steal or overwrite it.
- The workbench exposes lock owner/role/expiry in `workflow show` and the dispatch card.
- If the lock expires, the next authorized role may take over through the normal workflow API.

## 8. Status and history

- Workflow stages are separate from record `status`; do not edit `status` directly to simulate stage progress.
- Read `workflow show` and `task-context` before resuming. Preserve prior gates and history; append evidence instead of deleting it.
- Use the template's allowed rework targets. A Lead may return an item for process reasons; code/requirement decisions belong to dev/PM.
- Record real evidence. Do not claim tests, builds, device runs or approvals that were not executed.

## 9. Host adapter checklist

A host integrating the workbench must provide:

1. concrete agent/session bindings for dev/auditor/pm/lead;
2. a transport for role dispatch and audit-report return;
3. fixed role continuity for the life of the item;
4. the ability to stop safely when the host's transport or session is unavailable;
5. a manual delivery gate; no automatic close/push/release.

If the host cannot satisfy one of these, the correct behavior is to block and report, not to bypass the workflow.

## 10. Minimal operating loop

1. `task-context` / `workflow show`;
2. `dispatch-card`;
3. dispatch `next.role`;
4. role claims, works, submits outputs;
5. advance when the card says advance;
6. repeat until `manual_delivery`;
7. report status and wait for the user.
