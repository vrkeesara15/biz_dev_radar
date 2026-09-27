"""Profile autofill (SPEC 4 "pre-fill from the company website, a capability statement
upload and SAM.gov entity data, then a human confirms each field"; SPEC 10.3
POST /profiles/{id}/autofill). Pure: schemas and deterministic mapping only.

A `Suggestion` is one proposed value for one profile field. `field` is a dotted path the
frontend applies through the normal endpoints (never written here):

    legal_name, website, phone, year_founded, legal_structure,     PUT /profiles/{id}
    uei, cage_code, sam_status, sam_expires_on
    dba_names[], addresses[]                                        append + PUT
    codes.naics[], codes.psc[]         value {code, is_primary}     POST /codes
    service_lines[]                    value = ServiceLineIn body   POST /service-lines
    certifications[]                   value = CertificationIn body POST /certifications
    past_performance[]                 value = PastPerformanceIn    POST /past-performance
    personnel[]                        value = PersonnelIn body     POST /personnel
    boilerplate[]                      value = BoilerplateIn body   POST /boilerplate

`AutofillExtraction` is the JSON schema the Haiku-class model fills from a web page or a
capability statement; `suggestions_from_extraction` turns it into suggestions with the
model's per-field confidence (default 0.6 website / 0.7 PDF). `map_sam_entity` maps a
SAM.gov Entity API v3 record deterministically with confidence 0.95.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Region
from app.core.profile_fields import (
    MAX_SERVICE_LINE_WORDS,
    CertificationKind,
    CodeScheme,
    LegalStructure,
    PerformanceRole,
    code_scheme_allowed,
    normalize_code,
    region_foreign_fields,
)
from app.core.reference import is_valid_naics

Source = Literal["website", "capability_pdf", "uei"]

SAM_SOURCE_REF = "sam_entity_api"
SAM_CONFIDENCE = 0.95
SAM_SELF_ASSERTED_CONFIDENCE = 0.8
DEFAULT_CONFIDENCE: dict[str, float] = {
    "website": 0.6,
    "capability_pdf": 0.7,
    "uei": SAM_CONFIDENCE,
}

_UEI_RE = re.compile(r"^[A-Z0-9]{12}$")
_CAGE_RE = re.compile(r"^[A-Z0-9]{5}$")
_PHONE_RE = re.compile(r"[\d+][\d\s().-]{6,}\d")
_YEAR_RE = re.compile(r"(18|19|20)\d{2}")

# SAM entityStructureCode -> LegalStructure (Entity Management API v3 code list)
ENTITY_STRUCTURE: dict[str, LegalStructure] = {
    "2L": LegalStructure.CORPORATION,  # Corporate Entity (Not Tax Exempt)
    "8H": LegalStructure.CORPORATION,  # Corporate Entity (Tax Exempt)
    "2J": LegalStructure.PROPRIETORSHIP,  # Sole Proprietorship
    "2K": LegalStructure.PARTNERSHIP,  # Partnership or Limited Liability Partnership
    "2A": LegalStructure.OTHER,  # U.S. Government Entity
    "X6": LegalStructure.OTHER,  # International Organization
}
# SAM SBA business type codes -> certification kinds (sbaBusinessTypeList = SBA certified)
SBA_BUSINESS_TYPES: dict[str, CertificationKind] = {
    "A6": CertificationKind.EIGHT_A,
    "XX": CertificationKind.HUBZONE,
    "8W": CertificationKind.WOSB,
    "8E": CertificationKind.EDWOSB,
    "QF": CertificationKind.SDVOSB,
    "A5": CertificationKind.VOSB,
    "27": CertificationKind.SDB,
}
COUNTRY_ISO3_TO_2: dict[str, str] = {
    "USA": "US",
    "IND": "IN",
    "CAN": "CA",
    "GBR": "GB",
    "AUS": "AU",
    "DEU": "DE",
    "FRA": "FR",
    "SGP": "SG",
    "ARE": "AE",
}


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: Any
    source: Source
    source_ref: str
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def root(self) -> str:
        return self.field[:-2] if self.field.endswith("[]") else self.field


# --- LLM extraction schema ------------------------------------------------------------------------


class ExtractedAddress(BaseModel):
    line1: str
    line2: str | None = None
    city: str
    state: str | None = None
    postal_code: str | None = None
    country: str = Field(description="ISO 3166-1 alpha-2 country code, e.g. US or IN")


class ExtractedServiceLine(BaseModel):
    name: str
    description: str = Field(description="What the company delivers in this line, <= 150 words")
    page: int | None = None


class ExtractedCertification(BaseModel):
    kind: CertificationKind
    level: str | None = Field(default=None, description="CMMC level, ISO edition, FCL level")
    evidence: str | None = Field(default=None, description="the phrase that states it")
    page: int | None = None


class ExtractedPastPerformance(BaseModel):
    title: str
    customer: str
    scope: str
    role: PerformanceRole = PerformanceRole.PRIME
    period_start: str | None = Field(default=None, description="YYYY-MM-DD or YYYY")
    period_end: str | None = Field(default=None, description="YYYY-MM-DD or YYYY")
    naics: str | None = None
    page: int | None = None


class ExtractedPerson(BaseModel):
    name: str
    role: str
    page: int | None = None


class FieldConfidence(BaseModel):
    field: str = Field(description="a scalar field name, e.g. legal_name, phone, year_founded")
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = None


class AutofillExtraction(BaseModel):
    """Facts about ONE company, read from its web page or capability statement. Every
    field is optional: omit what the text does not state; never guess."""

    legal_name: str | None = None
    dba_names: list[str] = Field(default_factory=list)
    addresses: list[ExtractedAddress] = Field(default_factory=list)
    website: str | None = None
    phone: str | None = None
    year_founded: int | None = None
    legal_structure: LegalStructure | None = None
    uei: str | None = None
    cage_code: str | None = None
    naics_codes: list[str] = Field(default_factory=list)
    psc_codes: list[str] = Field(default_factory=list)
    service_lines: list[ExtractedServiceLine] = Field(default_factory=list)
    certifications: list[ExtractedCertification] = Field(default_factory=list)
    past_performance: list[ExtractedPastPerformance] = Field(default_factory=list)
    personnel: list[ExtractedPerson] = Field(default_factory=list)
    company_overview: str | None = Field(
        default=None, description="2-4 sentence company overview in the company's own words"
    )
    field_confidence: list[FieldConfidence] = Field(default_factory=list)


# --- helpers ------------------------------------------------------------------------------------


def is_valid_uei(value: str | None) -> bool:
    return bool(value) and bool(_UEI_RE.match(str(value).strip().upper()))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] if text else None


def _iso2(country: str | None) -> str | None:
    if not country:
        return None
    text = country.strip().upper()
    if len(text) == 2 and text.isalpha():
        return text
    if text in COUNTRY_ISO3_TO_2:
        return COUNTRY_ISO3_TO_2[text]
    names = {"UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "INDIA": "IN"}
    return names.get(text)


def _year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1800 <= value <= 2100 else None
    match = _YEAR_RE.search(str(value))
    return int(match.group(0)) if match else None


def _date(value: str | None) -> str | None:
    """'YYYY-MM-DD' or 'YYYY' -> ISO date string (YYYY -> January 1st), else None."""
    if not value:
        return None
    text = value.strip()
    try:
        if len(text) == 4 and text.isdigit():
            return date(int(text), 1, 1).isoformat()
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _truncate_words(text: str, limit: int = MAX_SERVICE_LINE_WORDS) -> str:
    words = text.split()
    return " ".join(words[:limit])


def _paragraph_html(text: str) -> str:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(f"<p>{p.strip()}</p>" for p in escaped.split("\n\n") if p.strip())


def _code(scheme: CodeScheme, value: str) -> str | None:
    try:
        code = normalize_code(scheme, value)
    except ValueError:
        return None
    if scheme is CodeScheme.NAICS and not is_valid_naics(code):
        return None
    return code


# --- extraction -> suggestions --------------------------------------------------------------------


def suggestions_from_extraction(
    extraction: AutofillExtraction,
    *,
    source: Source,
    source_ref: Callable[[int | None], str],
    default_confidence: float | None = None,
) -> list[Suggestion]:
    """Deterministic mapping of the model's output to suggestions. Values are shaped like
    the write bodies of the endpoints that will store them, so accepting a suggestion is a
    plain POST/PUT; invalid codes, years and countries are dropped instead of guessed."""
    base = _clamp(DEFAULT_CONFIDENCE[source] if default_confidence is None else default_confidence)
    per_field: dict[str, FieldConfidence] = {fc.field: fc for fc in extraction.field_confidence}

    def conf(name: str, page: int | None = None) -> tuple[float, str]:
        fc = per_field.get(name)
        confidence = _clamp(fc.confidence) if fc else base
        ref_page = page if page is not None else (fc.page if fc else None)
        return confidence, source_ref(ref_page)

    out: list[Suggestion] = []

    def add(field: str, value: Any, name: str | None = None, page: int | None = None) -> None:
        confidence, ref = conf(name or field, page)
        out.append(
            Suggestion(
                field=field, value=value, source=source, source_ref=ref, confidence=confidence
            )
        )

    if name := _clean(extraction.legal_name, 300):
        add("legal_name", name)
    for dba in extraction.dba_names:
        if cleaned := _clean(dba, 300):
            add("dba_names[]", cleaned, "dba_names")
    for address in extraction.addresses:
        country = _iso2(address.country)
        line1, city = _clean(address.line1, 300), _clean(address.city, 120)
        if not (country and line1 and city):
            continue
        add(
            "addresses[]",
            {
                "kind": "hq",
                "line1": line1,
                "line2": _clean(address.line2, 300),
                "city": city,
                "state": _clean(address.state, 120),
                "postal_code": _clean(address.postal_code, 20),
                "country": country,
            },
            "addresses",
        )
    if website := _clean(extraction.website, 500):
        if not website.lower().startswith(("http://", "https://")):
            website = "https://" + website
        add("website", website)
    if (phone := _clean(extraction.phone, 40)) and _PHONE_RE.search(phone):
        add("phone", phone)
    if (year := _year(extraction.year_founded)) is not None:
        add("year_founded", year)
    if extraction.legal_structure is not None:
        add("legal_structure", extraction.legal_structure.value)
    if extraction.uei and is_valid_uei(extraction.uei):
        add("uei", extraction.uei.strip().upper())
    if extraction.cage_code and _CAGE_RE.match(extraction.cage_code.strip().upper()):
        add("cage_code", extraction.cage_code.strip().upper())
    seen: set[str] = set()
    for i, raw in enumerate(extraction.naics_codes):
        code = _code(CodeScheme.NAICS, raw)
        if code and code not in seen:
            seen.add(code)
            add("codes.naics[]", {"code": code, "is_primary": i == 0}, "naics_codes")
    seen.clear()
    for raw in extraction.psc_codes:
        code = _code(CodeScheme.PSC, raw)
        if code and code not in seen:
            seen.add(code)
            add("codes.psc[]", {"code": code, "is_primary": False}, "psc_codes")
    for line in extraction.service_lines:
        line_name, description = _clean(line.name, 200), _clean(line.description, 4000)
        if line_name and description:
            add(
                "service_lines[]",
                {"name": line_name, "description": _truncate_words(description)},
                "service_lines",
                line.page,
            )
    for cert in extraction.certifications:
        value: dict[str, Any] = {"kind": cert.kind.value}
        if level := _clean(cert.level, 32):
            value["level"] = level
        if evidence := _clean(cert.evidence, 2000):
            value["notes"] = evidence
        add("certifications[]", value, "certifications", cert.page)
    for pp in extraction.past_performance:
        title, customer, scope = (
            _clean(pp.title, 300),
            _clean(pp.customer, 300),
            _clean(pp.scope, 4000),
        )
        if not (title and customer and scope):
            continue
        value = {"title": title, "customer": customer, "role": pp.role.value, "scope": scope}
        if start := _date(pp.period_start):
            value["period_start"] = start
        if end := _date(pp.period_end):
            value["period_end"] = end
        if pp.naics and (naics := _code(CodeScheme.NAICS, pp.naics)):
            value["naics"] = naics
        add("past_performance[]", value, "past_performance", pp.page)
    for person in extraction.personnel:
        person_name, role = _clean(person.name, 200), _clean(person.role, 200)
        if person_name and role:
            add("personnel[]", {"name": person_name, "role": role}, "personnel", person.page)
    if overview := (extraction.company_overview or "").strip():
        add(
            "boilerplate[]",
            {
                "kind": "company_overview",
                "title": "Company overview",
                "body": _paragraph_html(overview),
            },
            "company_overview",
        )
    return out


# --- SAM.gov Entity API ---------------------------------------------------------------------------


def _get(mapping: Any, *path: str) -> Any:
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def map_sam_entity(payload: Any, *, uei: str) -> tuple[list[Suggestion], list[str]]:
    """Entity Management API v3 (`/entity-information/v3/entities?ueiSAM=`) -> suggestions:
    legal name, DBA, physical address, CAGE, website, start year, legal structure,
    registration status + expiry, NAICS/PSC lists and SBA-certified business types."""
    entities = _get(payload, "entityData")
    if not isinstance(entities, list) or not entities:
        return [], [f"SAM.gov has no entity registration for UEI {uei}"]
    entity = entities[0]
    reg = _get(entity, "entityRegistration") or {}
    core = _get(entity, "coreData") or {}
    warnings: list[str] = []
    out: list[Suggestion] = []

    def add(field: str, value: Any, confidence: float = SAM_CONFIDENCE) -> None:
        out.append(
            Suggestion(
                field=field,
                value=value,
                source="uei",
                source_ref=SAM_SOURCE_REF,
                confidence=confidence,
            )
        )

    found_uei = str(reg.get("ueiSAM") or "").strip().upper()
    if found_uei and found_uei != uei.upper():
        warnings.append(f"SAM.gov returned UEI {found_uei} for the requested {uei}")
    if is_valid_uei(found_uei):
        add("uei", found_uei)
    if name := _clean(reg.get("legalBusinessName"), 300):
        add("legal_name", name)
    if dba := _clean(reg.get("dbaName"), 300):
        add("dba_names[]", dba)
    cage = str(reg.get("cageCode") or "").strip().upper()
    if _CAGE_RE.match(cage):
        add("cage_code", cage)
    status = str(reg.get("registrationStatus") or "").strip().lower()
    if status:
        add("sam_status", "active" if status == "active" else "inactive")
    if expires := _date(reg.get("registrationExpirationDate")):
        add("sam_expires_on", expires)
    if url := _clean(_get(core, "entityInformation", "entityURL"), 500):
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        add("website", url)
    if (year := _year(_get(core, "entityInformation", "entityStartDate"))) is not None:
        add("year_founded", year)
    structure_code = str(_get(core, "generalInformation", "entityStructureCode") or "").upper()
    structure_desc = str(_get(core, "generalInformation", "entityStructureDesc") or "")
    if structure_code in ENTITY_STRUCTURE:
        structure = ENTITY_STRUCTURE[structure_code]
        if (
            structure is LegalStructure.PARTNERSHIP
            and "limited liability" in structure_desc.lower()
        ):
            structure = LegalStructure.LLP
        add("legal_structure", structure.value, 0.85)
    address = _get(core, "physicalAddress") or {}
    country = _iso2(address.get("countryCode"))
    line1, city = _clean(address.get("addressLine1"), 300), _clean(address.get("city"), 120)
    if country and line1 and city:
        postal = _clean(address.get("zipCode"), 20)
        plus4 = _clean(address.get("zipCodePlus4"), 4)
        if postal and plus4:
            postal = f"{postal}-{plus4}"
        add(
            "addresses[]",
            {
                "kind": "hq",
                "line1": line1,
                "line2": _clean(address.get("addressLine2"), 300),
                "city": city,
                "state": _clean(address.get("stateOrProvinceCode"), 120),
                "postal_code": postal,
                "country": country,
            },
        )
    goods = _get(entity, "assertions", "goodsAndServices") or {}
    primary = str(goods.get("primaryNaics") or "").strip()
    seen: set[str] = set()
    for row in goods.get("naicsList") or []:
        code = _code(CodeScheme.NAICS, str(_get(row, "naicsCode") or ""))
        if code and code not in seen:
            seen.add(code)
            add("codes.naics[]", {"code": code, "is_primary": code == primary})
    if primary and primary not in seen and (code := _code(CodeScheme.NAICS, primary)):
        add("codes.naics[]", {"code": code, "is_primary": True})
    seen.clear()
    for row in goods.get("pscList") or []:
        code = _code(CodeScheme.PSC, str(_get(row, "pscCode") or ""))
        if code and code not in seen:
            seen.add(code)
            add("codes.psc[]", {"code": code, "is_primary": False})
    types = _get(core, "businessTypes") or {}
    seen_kinds: set[str] = set()
    for row in types.get("sbaBusinessTypeList") or []:
        kind = SBA_BUSINESS_TYPES.get(str(_get(row, "sbaBusinessTypeCode") or "").upper())
        if kind is None or kind.value in seen_kinds:
            continue
        seen_kinds.add(kind.value)
        value: dict[str, Any] = {"kind": kind.value, "issued_by": "SBA"}
        if entry := _date(_get(row, "certificationEntryDate")):
            value["issued_on"] = entry
        if exit_date := _date(_get(row, "certificationExitDate")):
            value["expires_on"] = exit_date
        add("certifications[]", value)
    for row in types.get("businessTypeList") or []:
        kind = SBA_BUSINESS_TYPES.get(str(_get(row, "businessTypeCode") or "").upper())
        if kind is None or kind.value in seen_kinds:
            continue
        seen_kinds.add(kind.value)
        add(
            "certifications[]",
            {
                "kind": kind.value,
                "notes": f"self-asserted in SAM.gov: {_get(row, 'businessTypeDesc')}",
            },
            SAM_SELF_ASSERTED_CONFIDENCE,
        )
    return out, warnings


# --- region gating --------------------------------------------------------------------------------


def filter_for_region(
    suggestions: Iterable[Suggestion], region: Region | str
) -> tuple[list[Suggestion], list[Suggestion]]:
    """(kept, dropped): suggestions for the other region's fields are never offered."""
    own = Region(region)
    kept: list[Suggestion] = []
    dropped: list[Suggestion] = []
    for suggestion in suggestions:
        root = suggestion.root
        foreign = bool(region_foreign_fields(own, {root}))
        if root.startswith("codes."):
            scheme = root.split(".", 1)[1]
            foreign = not code_scheme_allowed(own, scheme)
        (dropped if foreign else kept).append(suggestion)
    return kept, dropped
