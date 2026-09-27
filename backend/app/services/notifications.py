"""Delivery-receipt bookkeeping (SPEC 7; M6-05).

    updated = await record_whatsapp_receipt(DeliveryReceipt("wamid.1", "sent"))

A BSP receipt arrives on an unauthenticated webhook: there is no tenant in the request,
so the row is found by its `provider_ref` on the owner role — the same pattern the
billing webhooks use. The receipt can only ever move a delivery row WE created, and it
never moves one backwards (a `read` that lands before its `delivered` still wins,
because `opened` is the terminal state of a successful send).
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from app.core.db import Database, get_database
from app.models import NotificationDelivery
from app.models.notifications import DeliveryChannel, DeliveryStatus
from app.notify.whatsapp import DeliveryReceipt

log = structlog.get_logger(__name__)

# once a delivery is opened, a late "delivered" callback must not demote it
_RANK: dict[str, int] = {
    DeliveryStatus.QUEUED.value: 0,
    DeliveryStatus.SKIPPED.value: 0,
    DeliveryStatus.FAILED.value: 1,
    DeliveryStatus.SENT.value: 2,
    DeliveryStatus.OPENED.value: 3,
}


async def record_whatsapp_receipt(
    receipt: DeliveryReceipt,
    *,
    database: Database | None = None,
    now: datetime | None = None,
) -> bool:
    """Apply one BSP status callback. True when a delivery row changed."""
    db = database or get_database()
    moment = now or datetime.now(UTC)
    async with db.owner_session() as session:
        row = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.channel == DeliveryChannel.WHATSAPP.value,
                    NotificationDelivery.provider_ref == receipt.provider_ref,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            log.info("whatsapp.receipt_unknown", provider_ref=receipt.provider_ref)
            return False
        if _RANK.get(receipt.status, 0) < _RANK.get(row.status, 0):
            return False
        row.status = receipt.status
        if receipt.status == DeliveryStatus.SENT.value and row.sent_at is None:
            row.sent_at = moment
        if receipt.status == DeliveryStatus.OPENED.value:
            row.opened_at = moment
            if row.sent_at is None:
                row.sent_at = moment
        if receipt.status == DeliveryStatus.FAILED.value:
            row.last_error = receipt.error or "the BSP reported a failed delivery"
        return True
