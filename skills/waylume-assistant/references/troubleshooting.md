# Failure experience and troubleshooting

This is the single index for reusable failure lessons. Keep organization-specific cases and raw logs in the user's private data area, linked from the relevant item; never copy credentials, access tokens, passwords, private URLs, personal data, or unrestricted attachments here.

## Recording an experience

After a failure has been resolved with the user or through a verified fix, record a short entry in the private troubleshooting store with:

1. **Symptom** — what was observed, including relevant environment/build.
2. **Root cause** — confirmed cause, or explicitly “unconfirmed” with the remaining evidence gap.
3. **Resolution** — the smallest verified corrective action.
4. **Applicability** — conditions where it applies and when it must not be generalized.
5. **Evidence** — work item ID and paths/hashes or observable result, without copying sensitive source data.

Only add a reusable lesson when there is evidence for both cause and handling. Preserve uncertainty; do not blame the user or agent without evidence. If the user's instruction materially caused a failure, say so plainly in the item and add a narrowly scoped reminder. If new evidence disproves an old lesson, correct it and retain traceability.

## Lookup and use

At task start, search this index for a matching symptom or path and read only the relevant entry. Treat lessons as hints, not as authority over current source, user instructions, project rules, or new evidence. Reproduce the shortest supported diagnosis first; do not repeat a known failing action under unchanged conditions.

## Entry format

Store private entries as Markdown or structured records, for example:

```text
Symptom:
Root cause: confirmed | tentative | unknown
Resolution:
Applies when:
Does not apply when:
Evidence:
Last reviewed:
```
