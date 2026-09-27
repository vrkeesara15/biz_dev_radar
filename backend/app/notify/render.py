"""Email rendering (SPEC 7): Jinja2 HTML + text templates and the context they read.

    ctx = email_context(payload, recipient=..., settings=..., now=...,
                        tenant_id=..., event_type="high_fit_match")
    mail = render_email("high_fit_match", ctx)      # RenderedEmail(subject, html, text)

Every message shows the title, buyer, value (core.money), the response date in the user's
own zone with a countdown (core.display_time), the fit score with up to three rationale
bullets, the top gap, the deep link and the signed one-click actions, and ends with the
CAN-SPAM footer: a per-category unsubscribe link, an unsubscribe-from-everything link and
the postal address.

Templates live in app/notify/templates/<name>.{subject.txt,html,txt}; `<name>.html` and
`<name>.txt` extend `base.html` / `base.txt` and override the `lead` (and sometimes
`extra`) block only, so the layout and the footer are defined once. An event type with no
template of its own falls back to `generic`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import Settings
from app.core.display_time import countdown, render_tz
from app.core.money import format_money
from app.core.preferences import NotificationEvent
from app.notify.unsubscribe import ALL, unsubscribe_url

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
GENERIC = "generic"
MAX_RATIONALE_BULLETS = 3
APP_NAME = "BidRadar"

# event type -> template stem (SPEC 7's event table)
TEMPLATES: dict[str, str] = {
    NotificationEvent.HIGH_FIT_MATCH.value: "high_match",
    NotificationEvent.DIGEST.value: "digest",
    NotificationEvent.AMENDMENT.value: "amendment",
    NotificationEvent.DEADLINE_REMINDER.value: "deadline_reminder",
    NotificationEvent.AGENT_QUESTION.value: "draft_ready",
    NotificationEvent.APPROVAL_REQUEST.value: "draft_ready",
    NotificationEvent.PURSUIT_UPDATE.value: "draft_ready",
    NotificationEvent.REGISTRATION_EXPIRY.value: "registration_expiring",
    "adapter_failing": "adapter_failing",
    "adapter.failing": "adapter_failing",
}

# human label used in the footer ("...alert you about high-fit matches")
CATEGORY_LABELS: dict[str, str] = {
    NotificationEvent.HIGH_FIT_MATCH.value: "high-fit matches",
    NotificationEvent.DIGEST.value: "digests",
    NotificationEvent.AMENDMENT.value: "amendments on opportunities you track",
    NotificationEvent.DEADLINE_REMINDER.value: "deadline reminders",
    NotificationEvent.AGENT_QUESTION.value: "agent questions",
    NotificationEvent.APPROVAL_REQUEST.value: "approval requests",
    NotificationEvent.PURSUIT_UPDATE.value: "pursuit updates",
    NotificationEvent.REGISTRATION_EXPIRY.value: "expiring registrations",
    "adapter_failing": "source health alerts",
}

ACTION_LABELS: tuple[tuple[str, str], ...] = (
    ("pursue", "Pursue"),
    ("watch", "Watch"),
    ("pass", "Pass"),
    ("assign", "Assign"),
)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def template_for(event_type: str) -> str:
    return TEMPLATES.get(event_type, GENERIC)


def category_label(event_type: str) -> str:
    return CATEGORY_LABELS.get(event_type, event_type.replace("_", " "))


@lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    env.policies["json.dumps_kwargs"] = {"sort_keys": True}
    return env


def _collapse(text: str) -> str:
    """Tidy the plain-text part: no leading blank line, never 3+ blank lines in a row."""
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def render_email(event_type: str, context: Mapping[str, Any]) -> RenderedEmail:
    """Render the three parts for one event; unknown events use the generic template."""
    env = environment()
    stem = template_for(event_type)
    ctx = dict(context)
    subject = " ".join(env.get_template(f"{stem}.subject.txt").render(ctx).split())
    ctx["subject"] = subject
    return RenderedEmail(
        subject=subject,
        html=env.get_template(f"{stem}.html").render(ctx),
        text=_collapse(env.get_template(f"{stem}.txt").render(ctx)),
    )


# --- context ---------------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def money_display(payload: Mapping[str, Any]) -> str | None:
    """'$1,200,000' / '₹1.2 Cr' / '$500,000 - $1,200,000' from the payload's value fields."""
    if isinstance(payload.get("value"), str) and payload["value"].strip():
        return str(payload["value"]).strip()
    currency = str(payload.get("value_currency") or payload.get("currency") or "USD").upper()
    low = _decimal(payload.get("value_amount") or payload.get("value_min"))
    high = _decimal(payload.get("value_max"))
    if low is None and high is None:
        return None
    if low is not None and high is not None and low != high:
        return f"{format_money(low, currency)} - {format_money(high, currency)}"
    return format_money(low if low is not None else Decimal(high or 0), currency)


def _aware(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _bullets(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("rationale")
    if isinstance(raw, Mapping):
        raw = raw.get("fit_summary")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Sequence):
        return []
    return [str(b).strip() for b in raw if str(b).strip()][:MAX_RATIONALE_BULLETS]


def _top_gap(payload: Mapping[str, Any]) -> str | None:
    gap = payload.get("top_gap")
    if isinstance(gap, str) and gap.strip():
        return gap.strip()
    rationale = payload.get("rationale")
    gaps = rationale.get("gaps") if isinstance(rationale, Mapping) else payload.get("gaps")
    if isinstance(gaps, Sequence) and not isinstance(gaps, str | bytes) and gaps:
        first = gaps[0]
        if isinstance(first, Mapping):
            text = str(first.get("gap") or first.get("text") or "").strip()
            fix = str(first.get("suggested_fix") or "").strip()
            return f"{text} — {fix}" if text and fix else (text or fix or None)
        return str(first).strip() or None
    return None


def _digest_items(payload: Mapping[str, Any], user_tz: str, now: datetime) -> list[dict[str, Any]]:
    raw = payload.get("items")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        due = _aware(entry.get("response_due_at") or entry.get("due_at"))
        buyer_tz = str(entry.get("buyer_tz") or "UTC")
        items.append(
            {
                "title": str(entry.get("title") or "Untitled"),
                "buyer": entry.get("buyer"),
                "score": entry.get("score"),
                "band": entry.get("band"),
                "link": entry.get("link") or entry.get("deep_link") or "",
                "value": money_display(entry),
                "due": None if due is None else render_tz(due, buyer_tz, user_tz),
                "countdown": None if due is None else countdown(now, due),
            }
        )
    return items


def _diff(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("diff") or payload.get("changes")
    if isinstance(raw, Mapping):
        return [
            {
                "field": str(field),
                "before": "" if not isinstance(change, Mapping) else change.get("before", ""),
                "after": "" if not isinstance(change, Mapping) else change.get("after", ""),
            }
            for field, change in raw.items()
        ]
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
        return [dict(c) for c in raw if isinstance(c, Mapping)]
    return []


def email_context(
    payload: Mapping[str, Any],
    *,
    event_type: str,
    settings: Settings,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    user_tz: str = "UTC",
    locale: str = "en",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Flatten a notification payload into the template context (SPEC 7 email content)."""
    moment = now or datetime.now(UTC)
    due = _aware(payload.get("response_due_at") or payload.get("due_at"))
    buyer_tz = str(payload.get("buyer_tz") or payload.get("source_tz") or "UTC")
    raw_actions = payload.get("actions")
    actions: Mapping[str, Any] = raw_actions if isinstance(raw_actions, Mapping) else {}
    unsub = {
        "category": unsubscribe_url(
            settings, tenant_id=tenant_id, user_id=user_id, category=event_type, now=moment
        )
        if event_type in CATEGORY_LABELS
        else unsubscribe_url(
            settings, tenant_id=tenant_id, user_id=user_id, category=ALL, now=moment
        ),
        "all": unsubscribe_url(
            settings, tenant_id=tenant_id, user_id=user_id, category=ALL, now=moment
        ),
    }
    context: dict[str, Any] = {
        **{k: v for k, v in payload.items() if k not in {"actions", "recipient"}},
        "app_name": APP_NAME,
        "locale": locale,
        "event_type": event_type,
        "category_label": category_label(event_type),
        "title": payload.get("title"),
        "buyer": payload.get("buyer") or payload.get("buyer_org"),
        "value": money_display(payload),
        "due": None if due is None else render_tz(due, buyer_tz, user_tz),
        "countdown": None if due is None else countdown(moment, due),
        "score": payload.get("score"),
        "band": payload.get("band"),
        "rationale": _bullets(payload),
        "top_gap": _top_gap(payload),
        "deep_link": payload.get("deep_link"),
        "actions": [(label, str(actions[key])) for key, label in ACTION_LABELS if actions.get(key)],
        "items": _digest_items(payload, user_tz, moment),
        "diff": _diff(payload),
        "unsubscribe_url": unsub["category"],
        "unsubscribe_all_url": unsub["all"],
        "preferences_url": settings.app_base_url.rstrip("/") + "/app/settings/notifications",
        "postal_address": settings.email_postal_address,
        "subject": "",
    }
    return context
