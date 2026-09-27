"""Canonical notice and contract type enums (SPEC 5.3 notice_type, 4.4 preferences). Pure."""

from __future__ import annotations

from enum import StrEnum


class NoticeType(StrEnum):
    RFI = "rfi"
    SOURCES_SOUGHT = "sources_sought"
    PRESOLICITATION = "presolicitation"
    RFP = "rfp"
    RFQ = "rfq"
    COMBINED = "combined"
    GRANT = "grant"
    FORECAST = "forecast"
    AWARD = "award"
    EOI = "eoi"
    GEM_BID = "gem_bid"
    REVERSE_AUCTION = "reverse_auction"
    CORRIGENDUM = "corrigendum"
    SPECIAL = "special"


class ContractType(StrEnum):
    FFP = "ffp"
    TM = "tm"
    COST_PLUS = "cost_plus"
    IDIQ_TASK_ORDER = "idiq_task_order"
    RATE_CONTRACT = "rate_contract"


class TeamingRole(StrEnum):
    PRIME = "prime"
    SUB = "sub"
    JV = "jv"


NOTICE_TYPE_VALUES: tuple[str, ...] = tuple(t.value for t in NoticeType)
CONTRACT_TYPE_VALUES: tuple[str, ...] = tuple(t.value for t in ContractType)
