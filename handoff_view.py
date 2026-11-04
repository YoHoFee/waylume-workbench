"""Read-only handoff projection; it never changes persisted records or infers permission."""

from __future__ import annotations

from typing import Any


HANDOFF_FIELDS = ("goal", "verified", "unverified", "next_step", "acceptance", "blocker", "resume_condition")


def handoff_view(record: dict[str, Any]) -> dict[str, Any]:
    explicit = record.get("handoff") if isinstance(record.get("handoff"), dict) else {}
    def text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None
    fields = {key: text(explicit.get(key)) for key in HANDOFF_FIELDS}
    fields["goal"] = fields["goal"] or text(record.get("initial_brief"))
    fields["next_step"] = fields["next_step"] or text(record.get("next_step")) or text(record.get("next_action"))
    fields["blocker"] = fields["blocker"] or text(record.get("blocked_reason"))
    summary = text(record.get("latest_brief")) or text(record.get("status_detail"))
    progress = record.get("progress") if isinstance(record.get("progress"), list) else []
    matching = next((entry for entry in reversed(progress) if isinstance(entry, dict)
                     and text(entry.get("message")) == summary and text(entry.get("at"))), None)
    # Activity expiry and user-requested pause are distinct: never infer one from the other.
    return {
        "fields": fields,
        "summary": summary,
        "summary_updated_at": text(matching.get("at")) if matching else None,
        "handoff_updated_at": text(explicit.get("updated_at")),
        "missing": [key for key in HANDOFF_FIELDS if not fields[key]],
        "platform_snapshot": {"status": record.get("platform_status"),
                              "observed_at": record.get("observed_at"),
                              "source_updated_at": record.get("source_updated_at")},
        "evidence_availability": "not_checked",
    }
