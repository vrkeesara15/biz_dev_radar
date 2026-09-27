"""Paid aggregator feeds (SPEC 2 row 11, SPEC 11 "paid aggregators only through licensed
APIs with the tenant's or our licence"; M2-17).

`PaidFeedAdapter` is the base for licensed feeds: the licence key is never a setting or a
literal, it is a Secret Manager REFERENCE (`licence_secret_ref`, e.g.
`projects/bidradar/secrets/highergov-key/versions/latest`) resolved at run time through a
`SecretResolver`; the value is held only on the adapter instance and never logged or put
in health messages. A tenant-supplied key means one adapter instance per tenant with that
tenant's reference (the data it returns is then tenant-scoped, not global: the ingest
pipeline must be given a tenant sink before any of these is enabled).

All six subclasses are documented stubs (`enabled = False`, health not_implemented).
"""

from __future__ import annotations

import os
from typing import ClassVar, Protocol

from app.adapters.base import AdapterHealth, AdapterStatus
from app.adapters.registry import register
from app.adapters.stubs import DOCS, StubAdapter


class SecretResolver(Protocol):
    def resolve(self, ref: str) -> str | None: ...


class EnvSecretResolver:
    """Local/dev resolver: the reference is an environment variable name."""

    def resolve(self, ref: str) -> str | None:
        return os.environ.get(ref) or None


class GcpSecretResolver:  # pragma: no cover - needs google-cloud-secret-manager + ADC
    """Cloud resolver for `projects/*/secrets/*/versions/*` references."""

    def resolve(self, ref: str) -> str | None:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        payload: bytes = client.access_secret_version(name=ref).payload.data
        return payload.decode("utf-8") or None


def resolver_for(ref: str | None) -> SecretResolver:
    if ref and ref.startswith("projects/"):
        return GcpSecretResolver()
    return EnvSecretResolver()


class PaidFeedAdapter(StubAdapter):
    """Base for licensed feeds: licence by reference, never by value."""

    vendor_url: ClassVar[str] = ""
    licence_note: ClassVar[str] = ""
    # the settings/env name a platform-wide licence would use (tenant keys override)
    default_secret_ref: ClassVar[str] = ""

    def __init__(
        self,
        *,
        licence_secret_ref: str | None = None,
        resolver: SecretResolver | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.licence_secret_ref = licence_secret_ref or self.default_secret_ref or None
        self.tenant_id = tenant_id
        self._resolver = resolver or resolver_for(self.licence_secret_ref)
        self._licence: str | None = None

    def licence_key(self) -> str | None:
        """Resolve (once) the licence key; None when no reference or an empty secret."""
        if self._licence is None and self.licence_secret_ref:
            self._licence = self._resolver.resolve(self.licence_secret_ref)
        return self._licence

    @property
    def has_licence(self) -> bool:
        return bool(self.licence_key())

    def health(self) -> AdapterHealth:
        licence = "licence reference set" if self.licence_secret_ref else "no licence reference"
        return AdapterHealth(
            AdapterStatus.NOT_IMPLEMENTED,
            None,
            f"{self.display_name}: {self.reason}; {licence} (see {DOCS})",
        )

    def __repr__(self) -> str:  # never the key itself
        return f"<{type(self).__name__} source_id={self.source_id} ref={self.licence_secret_ref!r}>"


@register
class HigherGovAdapter(PaidFeedAdapter):
    """HigherGov: US federal + SLED opportunities, awards, contacts (REST, per-seat API key).
    Best SLED coverage for US tenants; also carries incumbent data. Endpoint family
    /api-external/opportunity/ with `api_key`, `captured_date` filters, page tokens."""

    source_id = "highergov"
    region = "us"
    schedule = "0 */2 * * *"
    display_name = "HigherGov"
    portal_url = "https://www.highergov.com/"
    vendor_url = "https://www.highergov.com/api-external/docs/"
    licence_note = "tenant or platform API key; redistribution limited to the licensee's users"
    default_secret_ref = "HIGHERGOV_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


@register
class GovSpendAdapter(PaidFeedAdapter):
    """GovSpend: US SLED bids + purchase orders (API on enterprise plans)."""

    source_id = "govspend"
    region = "us"
    schedule = "0 */3 * * *"
    display_name = "GovSpend"
    portal_url = "https://govspend.com/"
    vendor_url = "https://govspend.com/"
    licence_note = "enterprise API add-on; terms forbid resale of raw records"
    default_secret_ref = "GOVSPEND_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


@register
class BidNetAdapter(PaidFeedAdapter):
    """BidNet Direct: SLED bid notices for ~1,600 US agencies (vendor subscription API)."""

    source_id = "bidnet"
    region = "us"
    schedule = "0 */3 * * *"
    display_name = "BidNet Direct"
    portal_url = "https://www.bidnetdirect.com/"
    vendor_url = "https://www.bidnetdirect.com/"
    licence_note = "supplier subscription; documents need the subscriber's login (never automated)"
    default_secret_ref = "BIDNET_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


@register
class TenderTigerAdapter(PaidFeedAdapter):
    """TenderTiger: Indian tender aggregator (central, state, PSU) with an XML/JSON feed."""

    source_id = "tendertiger"
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "TenderTiger"
    portal_url = "https://www.tendertiger.com/"
    vendor_url = "https://www.tendertiger.com/"
    licence_note = "subscription feed; attribution to TenderTiger required on display"
    default_secret_ref = "TENDERTIGER_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


@register
class Tender247Adapter(PaidFeedAdapter):
    """Tender247: Indian tenders incl. GeM/CPPP mirrors with structured EMD/fee fields."""

    source_id = "tender247"
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "Tender247"
    portal_url = "https://www.tender247.com/"
    vendor_url = "https://www.tender247.com/"
    licence_note = "subscription feed; per-user licence"
    default_secret_ref = "TENDER247_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


@register
class BidAssistAdapter(PaidFeedAdapter):
    """BidAssist: Indian tender search with bid documents and result data (API on request)."""

    source_id = "bidassist"
    region = "in"
    schedule = "0 */3 * * *"
    display_name = "BidAssist"
    portal_url = "https://bidassist.com/"
    vendor_url = "https://bidassist.com/"
    licence_note = "API access by agreement; documents are the portal's, link back required"
    default_secret_ref = "BIDASSIST_API_KEY"
    reason = "licensed feed; client not written (no licence on file)"


PAID_FEEDS: tuple[type[PaidFeedAdapter], ...] = (
    HigherGovAdapter,
    GovSpendAdapter,
    BidNetAdapter,
    TenderTigerAdapter,
    Tender247Adapter,
    BidAssistAdapter,
)
