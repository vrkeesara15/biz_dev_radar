"""Company-profile field rules (SPEC 4.1, 11): region gating, identifier formats, enums.

Pure. The API rejects region-foreign fields with 422 listing the field names, normalizes
identifiers before storage, and keeps ENCRYPTED_FIELDS masked in every response.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from app.core.config import Region


class LegalStructure(StrEnum):
    LLC = "llc"
    CORPORATION = "corporation"
    PVT_LTD = "pvt_ltd"
    LLP = "llp"
    PARTNERSHIP = "partnership"
    PROPRIETORSHIP = "proprietorship"
    OTHER = "other"


class SamStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    PENDING = "pending"


class UdyamCategory(StrEnum):
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"


class LocalSupplierClass(StrEnum):
    CLASS_1 = "class_1"
    CLASS_2 = "class_2"
    NON_LOCAL = "non_local"


class AddressKind(StrEnum):
    REGISTERED = "registered"
    HQ = "hq"
    BRANCH = "branch"


class MseOwnership(StrEnum):
    """SC/ST-owned or women-owned MSE (India reserved procurement share, SPEC 4.2)."""

    NONE = "none"
    SC_ST = "sc_st"
    WOMEN = "women"
    SC_ST_WOMEN = "sc_st_women"


class CodeScheme(StrEnum):
    NAICS = "naics"
    PSC = "psc"
    ALN = "aln"
    GEM = "gem"
    INDIA_CATEGORY = "india_category"


class KeywordKind(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class DeliveryModel(StrEnum):
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"
    OFFSHORE = "offshore"


class CertificationKind(StrEnum):
    # socio-economic (US set-asides, SPEC 4.2)
    EIGHT_A = "8a"
    HUBZONE = "hubzone"
    WOSB = "wosb"
    EDWOSB = "edwosb"
    SDVOSB = "sdvosb"
    VOSB = "vosb"
    SDB = "sdb"
    # security / compliance attestations (SPEC 4.5)
    FCL = "fcl"
    CMMC = "cmmc"
    FEDRAMP = "fedramp"
    SOC2 = "soc2"
    ISO_27001 = "iso_27001"
    ISO_9001 = "iso_9001"
    ISO_20000 = "iso_20000"
    CMMI = "cmmi"
    STQC = "stqc"
    CERT_IN = "cert_in"


SOCIO_ECONOMIC_CERTS: frozenset[CertificationKind] = frozenset(
    {
        CertificationKind.EIGHT_A,
        CertificationKind.HUBZONE,
        CertificationKind.WOSB,
        CertificationKind.EDWOSB,
        CertificationKind.SDVOSB,
        CertificationKind.VOSB,
        CertificationKind.SDB,
    }
)
US_ONLY_CERTS: frozenset[CertificationKind] = SOCIO_ECONOMIC_CERTS | {
    CertificationKind.FCL,
    CertificationKind.CMMC,
    CertificationKind.FEDRAMP,
}
IN_ONLY_CERTS: frozenset[CertificationKind] = frozenset(
    {CertificationKind.STQC, CertificationKind.CERT_IN}
)


def certification_allowed(region: Region | str, kind: CertificationKind | str) -> bool:
    own = Region(region)
    cert = CertificationKind(kind)
    if cert in US_ONLY_CERTS:
        return own is Region.US
    if cert in IN_ONLY_CERTS:
        return own is Region.IN
    return True


# Fields that only exist for one region (SPEC 4.1 "Region" column).
US_ONLY_FIELDS: frozenset[str] = frozenset(
    {"uei", "cage_code", "sam_status", "sam_expires_on", "ein"}
)
IN_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "pan",
        "gstin",
        "tan",
        "cin_llpin",
        "udyam_number",
        "udyam_category",
        "dpiit_number",
        "gem_seller_id",
        "local_supplier_class",
        "local_content_pct",
        # 4.2
        "net_worth_amount",
        "net_worth_currency",
        "solvency_certificate_available",
        "mse_ownership",
    }
)
# Stored AES-GCM encrypted, returned masked to the last 4 characters (SPEC 11).
ENCRYPTED_FIELDS: tuple[str, ...] = (
    "ein",
    "pan",
    "gstin",
    "tan",
    "bank_account_number",
    "bank_routing_code",
)

REGION_FIELDS: dict[Region, frozenset[str]] = {Region.US: US_ONLY_FIELDS, Region.IN: IN_ONLY_FIELDS}


def region_foreign_fields(region: Region | str, fields: Iterable[str]) -> list[str]:
    """Names in `fields` that belong exclusively to the OTHER region (sorted)."""
    own = Region(region)
    foreign: set[str] = set()
    for other, names in REGION_FIELDS.items():
        if other is not own:
            foreign |= names
    return sorted(set(fields) & foreign)


_UEI = re.compile(r"^[A-Z0-9]{12}$")
_CAGE = re.compile(r"^[A-Z0-9]{5}$")
_EIN = re.compile(r"^\d{9}$")
_PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_TAN = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")
_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _clean(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


def normalize_uei(value: str) -> str:
    """SAM Unique Entity ID: exactly 12 alphanumeric characters."""
    text = _clean(value)
    if not _UEI.match(text):
        raise ValueError("UEI must be 12 alphanumeric characters")
    return text


def normalize_cage(value: str) -> str:
    """CAGE code: exactly 5 alphanumeric characters."""
    text = _clean(value)
    if not _CAGE.match(text):
        raise ValueError("CAGE code must be 5 alphanumeric characters")
    return text


def normalize_ein(value: str) -> str:
    """Employer Identification Number, stored as NN-NNNNNNN."""
    digits = _clean(value)
    if not _EIN.match(digits):
        raise ValueError("EIN must be 9 digits (NN-NNNNNNN)")
    return f"{digits[:2]}-{digits[2:]}"


def normalize_pan(value: str) -> str:
    text = _clean(value)
    if not _PAN.match(text):
        raise ValueError("PAN must match AAAAA9999A")
    return text


def normalize_tan(value: str) -> str:
    text = _clean(value)
    if not _TAN.match(text):
        raise ValueError("TAN must match AAAA99999A")
    return text


def normalize_gstin(value: str) -> str:
    text = _clean(value)
    if not _GSTIN.match(text):
        raise ValueError("GSTIN must be 15 characters (state code + PAN + entity + Z + check)")
    if text[2:12] and not _PAN.match(text[2:12]):
        raise ValueError("GSTIN must embed a valid PAN")
    return text


NORMALIZERS = {
    "uei": normalize_uei,
    "cage_code": normalize_cage,
    "ein": normalize_ein,
    "pan": normalize_pan,
    "tan": normalize_tan,
    "gstin": normalize_gstin,
}


# --- what we sell (SPEC 4.3) -------------------------------------------------------------

US_CODE_SCHEMES: frozenset[CodeScheme] = frozenset(
    {CodeScheme.NAICS, CodeScheme.PSC, CodeScheme.ALN}
)
IN_CODE_SCHEMES: frozenset[CodeScheme] = frozenset({CodeScheme.GEM, CodeScheme.INDIA_CATEGORY})
MIN_KEYWORD_WEIGHT = 0.1
MAX_KEYWORD_WEIGHT = 5.0
MAX_SERVICE_LINE_WORDS = 150

_PSC = re.compile(r"^[A-Z0-9]{4}$")
_ALN = re.compile(r"^\d{2}\.\d{3}$")


def code_scheme_allowed(region: Region | str, scheme: CodeScheme | str) -> bool:
    own = Region(region)
    kind = CodeScheme(scheme)
    if kind in US_CODE_SCHEMES:
        return own is Region.US
    return own is Region.IN


def normalize_code(scheme: CodeScheme | str, code: str) -> str:
    """Format check per scheme (NAICS existence is checked against the bundled table by
    the caller, see app.core.reference)."""
    kind = CodeScheme(scheme)
    text = code.strip()
    if kind is CodeScheme.NAICS:
        digits = text.replace("-", "")
        if not (digits.isdigit() and len(digits) == 6):
            raise ValueError("NAICS code must be 6 digits")
        return digits
    if kind is CodeScheme.PSC:
        text = text.upper()
        if not _PSC.match(text):
            raise ValueError("PSC code must be 4 alphanumeric characters")
        return text
    if kind is CodeScheme.ALN:
        if not _ALN.match(text):
            raise ValueError("ALN must look like 12.345")
        return text
    text = " ".join(text.split())
    if not text:
        raise ValueError("code must not be empty")
    if len(text) > 200:
        raise ValueError("code must be at most 200 characters")
    return text


def normalize_keyword(term: str) -> str:
    text = " ".join(term.split()).lower()
    if not text:
        raise ValueError("keyword must not be empty")
    if len(text) > 100:
        raise ValueError("keyword must be at most 100 characters")
    return text


def validate_keyword_weight(weight: float) -> float:
    if not MIN_KEYWORD_WEIGHT <= weight <= MAX_KEYWORD_WEIGHT:
        raise ValueError(f"weight must be between {MIN_KEYWORD_WEIGHT} and {MAX_KEYWORD_WEIGHT}")
    return round(float(weight), 1)


def word_count(text: str) -> int:
    return len(text.split())


def validate_service_description(text: str, limit: int = MAX_SERVICE_LINE_WORDS) -> str:
    cleaned = text.strip()
    count = word_count(cleaned)
    if count > limit:
        raise ValueError(f"description has {count} words; the limit is {limit}")
    return cleaned
