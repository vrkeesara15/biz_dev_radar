"""SPEC 6 stage-2 eligibility signal from the notice's extracted criteria (SPEC 5.3
`opportunity.eligibility` jsonb). Pure.

    signal = eligibility_signal(profile, opp, today)
    signal.raw                  # pass 1 / unknown 0.5 / fail 0, averaged over scored criteria
    signal.detail["criteria"]   # [{name, status pass|fail|unknown, reason, required, measured}]

US path (CriteriaUS.from_dict): SBA size status for the notice's NAICS when a small-business
requirement applies (explicit flag or a small-business/socio-economic set-aside code),
average USD receipts vs a minimum, years in business, required certifications (SPEC 4.2/4.5
kinds, normalised like core.eligibility_in), required registrations (SAM active and unexpired
from the profile, other kinds from registrations rows).
IN path: core.eligibility_in.evaluate_in over CriteriaIn.from_dict(...).
Missing data on either side scores 0.5 and is reported as unknown; a notice with no
extracted criteria yields one unknown "criteria" entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.eligibility import SizeStatus, determine_size
from app.core.eligibility_in import (
    CriteriaIn,
    ProfileSnapshotIn,
    Status,
    evaluate_in,
    normalize_certification,
)
from app.core.matching.filters import _CERT_BY_CODE, SMALL_BUSINESS_CODES
from app.core.matching.score import SignalValue
from app.core.matching.types import MatchOpportunity, MatchProfile
from app.core.money import format_usd, parse_usd

_SCORE = {Status.PASS: Decimal(1), Status.UNKNOWN: Decimal("0.5"), Status.FAIL: Decimal(0)}
SAM_ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    status: Status
    reason: str
    required: str | None = None
    measured: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "reason": self.reason,
            "required": self.required,
            "measured": self.measured,
        }


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return parse_usd(str(value))


def _int(value: Any) -> int | None:
    number = _decimal(value)
    return None if number is None else int(number)


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1"}
    return bool(value)


def _names(values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values or ():
        name = normalize_certification(str(raw))
        if name and name not in out:
            out.append(name)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class CriteriaUS:
    """Tolerant view of a US notice's extracted eligibility (keys are those the M5
    requirements extractor and grants.gov mapping produce; unknown keys are ignored)."""

    requires_small_business: bool | None = None  # None = derive from the set-aside code
    min_avg_receipts_usd: Decimal | None = None
    min_experience_years: int | None = None
    required_certifications: tuple[str, ...] = ()
    required_registrations: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CriteriaUS:
        receipts = payload.get("min_avg_receipts_usd", payload.get("min_annual_revenue_usd"))
        return cls(
            requires_small_business=_bool(payload.get("requires_small_business")),
            min_avg_receipts_usd=_decimal(receipts),
            min_experience_years=_int(payload.get("min_experience_years")),
            required_certifications=_names(payload.get("required_certifications")),
            required_registrations=tuple(
                r
                for r in (
                    str(x).strip().lower() for x in payload.get("required_registrations") or ()
                )
                if r
            ),
        )

    @property
    def empty(self) -> bool:
        return (
            self.requires_small_business is None
            and self.min_avg_receipts_usd is None
            and self.min_experience_years is None
            and not self.required_certifications
            and not self.required_registrations
        )


# --- US criteria -------------------------------------------------------------------------------


def _size(profile: MatchProfile, opp: MatchOpportunity) -> Criterion:
    name = "size_status"
    if not opp.naics:
        return Criterion(name, Status.UNKNOWN, "notice lists no NAICS code for the size standard")
    determinations = [
        determine_size(code, profile.avg_receipts_usd, profile.employee_count_total)
        for code in opp.naics
    ]
    small = [d for d in determinations if d.status is SizeStatus.SMALL]
    if small:
        d = small[0]
        return Criterion(
            name,
            Status.PASS,
            f"small under NAICS {d.naics} ({d.reason})",
            required=str(d.threshold),
            measured=str(d.measured),
        )
    if all(d.status is SizeStatus.OTHER_THAN_SMALL for d in determinations):
        d = determinations[0]
        return Criterion(
            name,
            Status.FAIL,
            f"other than small under NAICS {d.naics} ({d.reason})",
            required=str(d.threshold),
            measured=str(d.measured),
        )
    unknown = next(d for d in determinations if d.status is SizeStatus.UNKNOWN)
    return Criterion(
        name,
        Status.UNKNOWN,
        f"size status unknown for NAICS {unknown.naics}: {unknown.reason}",
        required=None if unknown.threshold is None else str(unknown.threshold),
    )


def _small_business_required(criteria: CriteriaUS, opp: MatchOpportunity) -> bool:
    if criteria.requires_small_business is not None:
        return criteria.requires_small_business
    code = (opp.set_aside or "").strip().upper()
    return code in SMALL_BUSINESS_CODES or code in _CERT_BY_CODE


def _receipts(profile: MatchProfile, minimum: Decimal) -> Criterion:
    name, required = "turnover", str(minimum)
    if profile.avg_receipts_usd is None:
        return Criterion(
            name, Status.UNKNOWN, "no USD annual revenue on profile", required=required
        )
    measured = str(profile.avg_receipts_usd)
    have = format_usd(profile.avg_receipts_usd, compact=True)
    need = format_usd(minimum, compact=True)
    summary = f"average receipts {have} vs {need} required"
    status = Status.PASS if profile.avg_receipts_usd >= minimum else Status.FAIL
    return Criterion(name, status, summary, required=required, measured=measured)


def _experience(profile: MatchProfile, minimum: int, today: date) -> Criterion:
    name, required = "experience", str(minimum)
    if profile.year_founded is None:
        return Criterion(
            name,
            Status.UNKNOWN,
            "years of experience unknown (set year founded on the profile)",
            required=required,
        )
    years = max(today.year - profile.year_founded, 0)
    status = Status.PASS if years >= minimum else Status.FAIL
    return Criterion(
        name,
        status,
        f"{years} years of experience vs {minimum} required",
        required=required,
        measured=str(years),
    )


def _certifications(
    profile: MatchProfile, required: tuple[str, ...], today: date
) -> list[Criterion]:
    held: dict[str, list[date | None]] = {}
    for cert in profile.certifications:
        held.setdefault(normalize_certification(cert.kind), []).append(cert.expires_on)
    out: list[Criterion] = []
    for key in required:
        name = f"certification:{key}"
        expiries = held.get(key)
        if expiries is None:
            out.append(Criterion(name, Status.FAIL, f"{key} not on profile", required=key))
            continue
        valid = [e for e in expiries if e is None or e >= today]
        if valid:
            latest = max((e for e in valid if e), default=None)
            reason = f"{key} on profile" + (f", valid until {latest}" if latest else "")
            out.append(
                Criterion(
                    name,
                    Status.PASS,
                    reason,
                    required=key,
                    measured=None if latest is None else latest.isoformat(),
                )
            )
        else:
            latest = max(e for e in expiries if e)
            out.append(
                Criterion(
                    name,
                    Status.FAIL,
                    f"{key} expired on {latest}",
                    required=key,
                    measured=latest.isoformat(),
                )
            )
    return out


def _sam(profile: MatchProfile, today: date) -> Criterion:
    name = "registration:sam"
    status = (profile.sam_status or "").strip().lower()
    if not status:
        rows = [r for r in profile.registrations if r.kind == "sam"]
        if not rows:
            return Criterion(name, Status.UNKNOWN, "SAM.gov registration status not on profile")
        live = [r for r in rows if r.valid_on(today)]
        if live:
            return Criterion(name, Status.PASS, "SAM.gov registration on profile")
        latest = max(r.expires_on for r in rows if r.expires_on)
        return Criterion(
            name, Status.FAIL, f"SAM.gov registration expired on {latest}", measured=str(latest)
        )
    if status != SAM_ACTIVE:
        return Criterion(name, Status.FAIL, f"SAM.gov registration is {status}", measured=status)
    if profile.sam_expires_on is not None and profile.sam_expires_on < today:
        return Criterion(
            name,
            Status.FAIL,
            f"SAM.gov registration expired on {profile.sam_expires_on}",
            measured=profile.sam_expires_on.isoformat(),
        )
    until = "" if profile.sam_expires_on is None else f", valid until {profile.sam_expires_on}"
    return Criterion(
        name,
        Status.PASS,
        "SAM.gov registration active" + until,
        measured=None if profile.sam_expires_on is None else profile.sam_expires_on.isoformat(),
    )


def _registration(profile: MatchProfile, kind: str, today: date) -> Criterion:
    if kind == "sam":
        return _sam(profile, today)
    name = f"registration:{kind}"
    rows = [r for r in profile.registrations if r.kind == kind]
    if not rows:
        return Criterion(name, Status.FAIL, f"{kind} registration not on profile", required=kind)
    live = [r for r in rows if r.valid_on(today)]
    if live:
        return Criterion(name, Status.PASS, f"{kind} registration on profile", required=kind)
    latest = max(r.expires_on for r in rows if r.expires_on)
    return Criterion(
        name,
        Status.FAIL,
        f"{kind} registration expired on {latest}",
        required=kind,
        measured=latest.isoformat(),
    )


def evaluate_us(
    profile: MatchProfile, opp: MatchOpportunity, criteria: CriteriaUS, today: date
) -> list[Criterion]:
    results: list[Criterion] = []
    if _small_business_required(criteria, opp):
        results.append(_size(profile, opp))
    if criteria.min_avg_receipts_usd is not None:
        results.append(_receipts(profile, criteria.min_avg_receipts_usd))
    if criteria.min_experience_years is not None:
        results.append(_experience(profile, criteria.min_experience_years, today))
    results.extend(_certifications(profile, criteria.required_certifications, today))
    for kind in criteria.required_registrations:
        results.append(_registration(profile, kind, today))
    return results


# --- IN criteria -------------------------------------------------------------------------------


def evaluate_india(profile: MatchProfile, opp: MatchOpportunity, today: date) -> list[Criterion]:
    criteria = CriteriaIn.from_dict(opp.eligibility)
    if criteria.due_on is None and opp.response_due_at is not None:
        criteria = CriteriaIn(
            **{**_criteria_fields(criteria), "due_on": opp.response_due_at.date()}
        )
    snapshot = profile.eligibility_in or ProfileSnapshotIn(year_founded=profile.year_founded)
    out = evaluate_in(snapshot, criteria, today)
    return [
        Criterion(r.name, r.status, r.reason, required=r.required, measured=r.measured)
        for r in out.results
        if not r.informational
    ]


def _criteria_fields(criteria: CriteriaIn) -> dict[str, Any]:
    return {
        "min_avg_turnover_inr": criteria.min_avg_turnover_inr,
        "turnover_years": criteria.turnover_years,
        "min_experience_years": criteria.min_experience_years,
        "required_certifications": criteria.required_certifications,
        "emd_amount_inr": criteria.emd_amount_inr,
        "allows_mse_exemption": criteria.allows_mse_exemption,
        "allows_startup_exemption": criteria.allows_startup_exemption,
        "requires_gem_registration": criteria.requires_gem_registration,
        "requires_dsc": criteria.requires_dsc,
        "due_on": criteria.due_on,
    }


# --- the signal --------------------------------------------------------------------------------


def eligibility_signal(profile: MatchProfile, opp: MatchOpportunity, today: date) -> SignalValue:
    if opp.region.lower() == "in":
        results = evaluate_india(profile, opp, today)
    else:
        results = evaluate_us(profile, opp, CriteriaUS.from_dict(opp.eligibility), today)
    if not results:
        results = [Criterion("criteria", Status.UNKNOWN, "no eligibility criteria extracted")]
    counts = {s: sum(1 for r in results if r.status is s) for s in Status}
    score = sum((_SCORE[r.status] for r in results), Decimal(0)) / len(results)
    note = (
        f"{len(results)} criteria: {counts[Status.PASS]} pass, {counts[Status.FAIL]} fail, "
        f"{counts[Status.UNKNOWN]} unknown"
    )
    return SignalValue.of(
        score.quantize(Decimal("0.0001")), note, criteria=[r.as_dict() for r in results]
    )
