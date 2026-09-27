"""M1-04: notice/contract enums match SPEC 5.3; geography code normalization."""

import pytest
from app.core.geo import (
    INDIA_STATES_UTS,
    US_STATES,
    normalize_countries,
    normalize_india_states,
    normalize_names,
    normalize_us_states,
)
from app.core.notice_types import (
    CONTRACT_TYPE_VALUES,
    NOTICE_TYPE_VALUES,
    ContractType,
    NoticeType,
    TeamingRole,
)

SPEC_5_3_NOTICE_TYPES = (
    "rfi", "sources_sought", "presolicitation", "rfp", "rfq", "combined", "grant",
    "forecast", "award", "eoi", "gem_bid", "reverse_auction", "corrigendum", "special",
)  # fmt: skip


def test_notice_types_match_spec_5_3_exactly() -> None:
    assert NOTICE_TYPE_VALUES == SPEC_5_3_NOTICE_TYPES
    assert NoticeType("gem_bid") is NoticeType.GEM_BID
    assert CONTRACT_TYPE_VALUES == ("ffp", "tm", "cost_plus", "idiq_task_order", "rate_contract")
    assert ContractType("rate_contract") and {r.value for r in TeamingRole} == {
        "prime",
        "sub",
        "jv",
    }


def test_state_tables() -> None:
    assert len(US_STATES) == 56 and US_STATES["VA"] == "Virginia" and "DC" in US_STATES
    assert len(INDIA_STATES_UTS) == 36 and INDIA_STATES_UTS["KA"] == "Karnataka"
    assert INDIA_STATES_UTS["TS"] == "Telangana" and INDIA_STATES_UTS["DL"] == "Delhi"


def test_normalizers() -> None:
    assert normalize_us_states(["va", " MD", "va", "DC"]) == ["VA", "MD", "DC"]
    with pytest.raises(ValueError, match="unknown US state"):
        normalize_us_states(["ZZ"])
    with pytest.raises(ValueError, match="2-letter"):
        normalize_us_states(["Virginia"])
    assert normalize_india_states(["ka", "TS"]) == ["KA", "TS"]
    with pytest.raises(ValueError):
        normalize_india_states(["XX"])
    assert normalize_countries(["us", "IN", "us"]) == ["US", "IN"]
    with pytest.raises(ValueError):
        normalize_countries(["USA"])
    assert normalize_names([" Reston ", "reston", "Bengaluru", "  "]) == ["Reston", "Bengaluru"]
    with pytest.raises(ValueError):
        normalize_names(["x" * 201])
