"""Model routing (SPEC 8): Opus-class for extraction / bid-no-bid / red-team, Sonnet-class
for drafting, Haiku-class for summaries and classification; the match rationale uses its
own setting (OQ-9). Model ids come from Settings only."""

from __future__ import annotations

from enum import StrEnum

from app.core.config import Settings, get_settings


class AgentRole(StrEnum):
    EXTRACTION = "extraction"
    BID_NO_BID = "bid_no_bid"
    RED_TEAM = "red_team"
    DRAFTING = "drafting"
    SUMMARY = "summary"
    CLASSIFICATION = "classification"
    RATIONALE = "rationale"


OPUS_ROLES = frozenset({AgentRole.EXTRACTION, AgentRole.BID_NO_BID, AgentRole.RED_TEAM})
SONNET_ROLES = frozenset({AgentRole.DRAFTING})
HAIKU_ROLES = frozenset({AgentRole.SUMMARY, AgentRole.CLASSIFICATION})


def model_for(role: AgentRole | str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    role = AgentRole(role)
    if role in OPUS_ROLES:
        return settings.llm_model_opus_class
    if role in SONNET_ROLES:
        return settings.llm_model_sonnet_class
    if role in HAIKU_ROLES:
        return settings.llm_model_haiku_class
    return settings.llm_model_rationale
