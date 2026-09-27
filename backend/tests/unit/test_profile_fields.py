"""M1-01: region gating and identifier formats for company profiles (SPEC 4.1)."""

import pytest
from app.core.config import Region
from app.core.profile_fields import (
    ENCRYPTED_FIELDS,
    IN_ONLY_CERTS,
    IN_ONLY_FIELDS,
    NORMALIZERS,
    SOCIO_ECONOMIC_CERTS,
    US_ONLY_CERTS,
    US_ONLY_FIELDS,
    CertificationKind,
    certification_allowed,
    normalize_cage,
    normalize_ein,
    normalize_gstin,
    normalize_pan,
    normalize_tan,
    normalize_uei,
    region_foreign_fields,
)


def test_region_sets_are_disjoint_and_cover_spec_fields() -> None:
    assert not (US_ONLY_FIELDS & IN_ONLY_FIELDS)
    assert {"uei", "cage_code", "sam_expires_on", "ein"} <= US_ONLY_FIELDS
    assert {"pan", "gstin", "tan", "cin_llpin", "udyam_number", "dpiit_number"} <= IN_ONLY_FIELDS
    assert set(ENCRYPTED_FIELDS) >= {"ein", "pan", "gstin", "tan"}
    assert "bank_account_number" in ENCRYPTED_FIELDS


def test_region_foreign_fields() -> None:
    provided = {"legal_name", "uei", "ein", "pan", "gstin", "website"}
    assert region_foreign_fields(Region.US, provided) == ["gstin", "pan"]
    assert region_foreign_fields("in", provided) == ["ein", "uei"]
    assert region_foreign_fields("us", {"legal_name"}) == []
    with pytest.raises(ValueError):
        region_foreign_fields("eu", provided)


def test_uei_and_cage() -> None:
    assert normalize_uei("abc123def456") == "ABC123DEF456"
    assert normalize_uei(" ABC1-23DE F456 ") == "ABC123DEF456"
    for bad in ("ABC123DEF45", "ABC123DEF4567", "ABC123DEF45!", ""):
        with pytest.raises(ValueError, match="12 alphanumeric"):
            normalize_uei(bad)
    assert normalize_cage("1abc2") == "1ABC2"
    for bad in ("1ABC", "1ABC23", "1AB_2"):
        with pytest.raises(ValueError, match="5 alphanumeric"):
            normalize_cage(bad)


def test_ein_pan_tan_gstin() -> None:
    assert normalize_ein("123456789") == "12-3456789"
    assert normalize_ein("12-3456789") == "12-3456789"
    with pytest.raises(ValueError):
        normalize_ein("12-345678")
    with pytest.raises(ValueError):
        normalize_ein("AB-3456789")
    assert normalize_pan("abcde1234f") == "ABCDE1234F"
    with pytest.raises(ValueError):
        normalize_pan("ABCD1234F")
    assert normalize_tan("blra12345a") == "BLRA12345A"
    with pytest.raises(ValueError):
        normalize_tan("BLR12345A")
    assert normalize_gstin("27abcde1234f1z5") == "27ABCDE1234F1Z5"
    with pytest.raises(ValueError):
        normalize_gstin("27ABCDE1234F1Y5")  # 14th char must be Z
    with pytest.raises(ValueError):
        normalize_gstin("27ABCDE1234F1Z")
    assert set(NORMALIZERS) == {"uei", "cage_code", "ein", "pan", "tan", "gstin"}


def test_certification_kinds_and_region_rules() -> None:
    assert {k.value for k in SOCIO_ECONOMIC_CERTS} == {
        "8a",
        "hubzone",
        "wosb",
        "edwosb",
        "sdvosb",
        "vosb",
        "sdb",
    }
    assert SOCIO_ECONOMIC_CERTS < US_ONLY_CERTS
    assert not (US_ONLY_CERTS & IN_ONLY_CERTS)
    assert certification_allowed("us", "8a") and not certification_allowed("in", "8a")
    assert certification_allowed(Region.IN, CertificationKind.STQC)
    assert not certification_allowed(Region.US, "cert_in")
    for both in ("soc2", "iso_27001", "iso_9001", "iso_20000", "cmmi"):
        assert certification_allowed("us", both) and certification_allowed("in", both)
    with pytest.raises(ValueError):
        certification_allowed("us", "iso_14001")
    assert {"net_worth_amount", "solvency_certificate_available", "mse_ownership"} <= IN_ONLY_FIELDS
