"""M2-10: reference/buyer normalisers and the richness rule (pure)."""

from datetime import UTC, datetime

import pytest
from app.core.dedupe import Candidate, pick_winner, richness
from app.core.normalize.buyer import normalized_buyer
from app.core.normalize.reference import normalized_reference


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("47PF0018R0023", "47PF0018R0023"),
        (" 47pf-0018-r0023 ", "47PF0018R0023"),
        ("RFP No. 47PF0018R0023", "47PF0018R0023"),
        ("RFP-47PF0018R0023", "47PF0018R0023"),
        ("Tender No.: TS/2026/EDU-0042", "TS2026EDU0042"),
        ("Tender Notice No 12/2026-27", "12202627"),
        ("NIT No. 07/EE/2026", "07EE2026"),
        ("Ref: ABC-123", "ABC123"),
        ("Reference Number ABC-123", "ABC123"),
        ("Solicitation Number: W912DY-26-R-0001", "W912DY26R0001"),
        ("Bid No. GEM/2026/B/1234567", "GEM2026B1234567"),
        ("RFQ 1234", "1234"),
        ("no. 12-34", "1234"),
        ("RFP", None),
        ("", None),
        (None, None),
        ("   ", None),
        # a prefix glued to the id without a separator is part of the id
        ("RFP12345", "RFP12345"),
    ],
)
def test_normalized_reference(raw: str | None, expected: str | None) -> None:
    assert normalized_reference(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Internal Revenue Service", "internal revenue service"),
        ("  INTERNAL   REVENUE  SERVICE ", "internal revenue service"),
        (
            "Department of the Treasury, Internal Revenue Service",
            "department of the treasury internal revenue service",
        ),
        ("Acme Corp.", "acme"),
        ("Acme Corporation", "acme"),
        ("ACME INC", "acme"),
        ("Acme, Inc.", "acme"),
        ("Acme Co", "acme"),
        ("Acme Company", "acme"),
        ("Acme LLC", "acme"),
        ("Acme L.L.C.", "acme"),
        ("Tata Consultancy Services Pvt. Ltd.", "tata consultancy services"),
        ("Tata Consultancy Services Private Limited", "tata consultancy services"),
        ("The Boeing Company", "boeing"),
        ("Boeing Co.", "boeing"),
        ("Bharat Heavy Electricals Ltd", "bharat heavy electricals"),
        ("Rail Vikas Nigam Limited", "rail vikas nigam"),
        ("Acme Corp (India)", "acme corp india"),  # only trailing suffixes are dropped
        ("U.S. Army Corps of Engineers", "us army corps of engineers"),
        # a suffix word that is the whole name survives
        ("Company", "company"),
        ("", None),
        (None, None),
        ("  ,,  ", None),
    ],
)
def test_normalized_buyer(raw: str | None, expected: str | None) -> None:
    assert normalized_buyer(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # abbreviations
        ("Govt. of Tamil Nadu", "government of tamil nadu"),
        ("Government of Tamil Nadu", "government of tamil nadu"),
        ("GOVT OF TAMIL NADU", "government of tamil nadu"),
        ("Dept of Rural Development", "department of rural development"),
        ("Deptt. of Rural Development", "department of rural development"),
        ("Department of Rural Development", "department of rural development"),
        # transliterations
        ("Gramin Vikas Vibhag", "gramin vikas department"),
        ("Uttar Pradesh Jal Nigam", "uttar pradesh jal"),
        ("Uttar Pradesh Jal Corporation", "uttar pradesh jal"),
        ("Lucknow Nagar Nigam", "lucknow municipal"),
        ("Lucknow Municipal Corporation", "lucknow municipal"),
        ("Zilla Parishad, Pune", "district parishad pune"),
        ("Zila Parishad Pune", "district parishad pune"),
        ("District Parishad Pune", "district parishad pune"),
        ("Bharat Sanchar Nigam Ltd", "bharat sanchar"),
        ("Bharat Sanchar Nigam Limited", "bharat sanchar"),
        ("Rail Vikas Nigam Limited", "rail vikas"),
        # "Office of the" / "O/o" are noise on Indian portals
        ("O/o the Chief Engineer, PWD", "chief engineer pwd"),
        ("Office of the Chief Engineer (PWD)", "chief engineer pwd"),
        ("O/o Chief Engineer PWD", "chief engineer pwd"),
        ("Chief Engineer PWD", "chief engineer pwd"),
        # & and "and" are the same conjunction
        ("Dept. of Health & Family Welfare", "department of health and family welfare"),
        ("Department of Health and Family Welfare", "department of health and family welfare"),
        # Hindi / mixed names keep their Devanagari (M3-07 fixtures)
        ("रेल विकास निगम", "रेल विकास निगम"),
        (
            "मुख्य अभियंता / Chief Engineer, PWD",
            "मुख्य अभियंता chief engineer pwd",
        ),
        ("", None),
        (None, None),
    ],
)
def test_normalized_buyer_in_region(raw: str | None, expected: str | None) -> None:
    assert normalized_buyer(raw, region="in") == expected


def test_india_table_only_applies_to_the_in_region() -> None:
    # a US buyer keeps its words: "Nigam"/"Zilla" never appear, and mapping them would
    # change keys already stored for US records
    assert normalized_buyer("Rail Vikas Nigam Limited") == "rail vikas nigam"
    assert normalized_buyer("Rail Vikas Nigam Limited", region="us") == "rail vikas nigam"
    assert normalized_buyer("Rail Vikas Nigam Limited", region="in") == "rail vikas"


def test_richness_counts_non_empty_fields_and_documents() -> None:
    values = {
        "title": "Cloud",
        "description_text": None,
        "naics": [],
        "psc": ["D302"],
        "contacts": [],
        "eligibility": {},
        "place_of_performance": {"state": "NC"},
        "estimated_value_max": 0,
        "buyer_org": "   ",
    }
    # title, psc, place_of_performance, estimated_value_max (0 is a value) -> 4, + 2 docs
    assert richness(values, document_count=2) == 6
    assert richness({}, document_count=0) == 0


def test_richness_ignores_bookkeeping_fields() -> None:
    values = {"raw_ref": "raw/x", "content_hash": "abc", "version": 3, "extra": {"a": 1}}
    assert richness(values, document_count=0) == 0


def _c(name: str, rich: int, created: int) -> Candidate:
    return Candidate(id=name, richness=rich, created_at=datetime(2026, 9, created, tzinfo=UTC))


def test_pick_winner_prefers_richer_then_older() -> None:
    assert pick_winner([_c("a", 3, 1), _c("b", 7, 2)]).id == "b"
    assert pick_winner([_c("a", 5, 2), _c("b", 5, 1)]).id == "b"
    assert pick_winner([_c("a", 5, 1), _c("b", 5, 1)]).id == "a"  # stable on a full tie
    with pytest.raises(ValueError):
        pick_winner([])
