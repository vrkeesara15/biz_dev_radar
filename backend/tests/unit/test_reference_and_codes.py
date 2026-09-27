"""M1-03: bundled 2022 NAICS table, code/keyword/service-line validation edge cases."""

import pytest
from app.core.config import Region
from app.core.profile_fields import (
    MAX_SERVICE_LINE_WORDS,
    CodeScheme,
    code_scheme_allowed,
    normalize_code,
    normalize_keyword,
    validate_keyword_weight,
    validate_service_description,
    word_count,
)
from app.core.reference import is_valid_naics, naics_codes, naics_title


def test_naics_table_is_the_full_2022_list() -> None:
    table = naics_codes()
    assert len(table) >= 1000
    assert all(len(code) == 6 and code.isdigit() for code in table)
    assert table["541511"] == "Custom Computer Programming Services"
    assert table["541512"] == "Computer Systems Design Services"
    assert naics_title("236220") == "Commercial and Institutional Building Construction"
    assert naics_title("928120") == "International Affairs"
    assert (
        is_valid_naics("541519") and not is_valid_naics("541510") and not is_valid_naics("999999")
    )
    assert naics_codes() is table  # cached


def test_code_normalization() -> None:
    assert normalize_code("naics", " 541-511 ") == "541511"
    with pytest.raises(ValueError, match="6 digits"):
        normalize_code(CodeScheme.NAICS, "54151")
    with pytest.raises(ValueError):
        normalize_code("naics", "54151A")
    assert normalize_code("psc", "d302") == "D302"
    with pytest.raises(ValueError):
        normalize_code("psc", "D30")
    assert normalize_code("aln", "93.778") == "93.778"
    with pytest.raises(ValueError):
        normalize_code("aln", "93778")
    assert normalize_code("gem", "  Computer   Software  ") == "Computer Software"
    assert normalize_code("india_category", "IT Services") == "IT Services"
    with pytest.raises(ValueError):
        normalize_code("gem", "   ")
    with pytest.raises(ValueError):
        normalize_code("gem", "x" * 201)
    with pytest.raises(ValueError):
        normalize_code("cpv", "123")


def test_code_scheme_region() -> None:
    assert code_scheme_allowed(Region.US, "naics") and code_scheme_allowed("us", "psc")
    assert code_scheme_allowed("us", CodeScheme.ALN) and not code_scheme_allowed("in", "naics")
    assert code_scheme_allowed("in", "gem") and code_scheme_allowed("in", "india_category")
    assert not code_scheme_allowed("us", "gem")


def test_keywords() -> None:
    assert normalize_keyword("  Cloud   Migration ") == "cloud migration"
    with pytest.raises(ValueError):
        normalize_keyword(" ")
    with pytest.raises(ValueError):
        normalize_keyword("k" * 101)
    assert validate_keyword_weight(0.1) == 0.1 and validate_keyword_weight(5) == 5.0
    assert validate_keyword_weight(2.34) == 2.3
    for bad in (0, 0.09, 5.1, -1):
        with pytest.raises(ValueError):
            validate_keyword_weight(bad)


def test_service_line_description_word_limit() -> None:
    assert word_count("") == 0 and word_count("one  two\nthree") == 3
    ok = " ".join(["word"] * MAX_SERVICE_LINE_WORDS)
    assert validate_service_description(f"  {ok}  ") == ok
    with pytest.raises(ValueError, match="151 words"):
        validate_service_description(" ".join(["word"] * 151))
    assert (
        validate_service_description("hyphenated-words count-as one")
        == "hyphenated-words count-as one"
    )
