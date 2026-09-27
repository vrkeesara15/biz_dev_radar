"""Indian tender eligibility rules (SPEC 4.1, 4.2, 6, 12). Pure.

    out = evaluate_in(snapshot, CriteriaIn.from_dict(opportunity.eligibility), today)
    out.status        # pass | fail | unknown  (overall)
    out.score         # SPEC 6 stage-2 eligibility signal: pass 1, unknown 0.5, fail 0, averaged
    out.blocking      # criterion names that block submission (expired DSC, missing GeM ID)
    out.results[i]    # CriterionResult(name, status, reason, exemption_applied, ...)

Rules
- turnover: average of the last `turnover_years` (3) fiscal years in INR vs the minimum;
  fewer years, no revenue or non-INR figures -> unknown (missing data = 0.5).
- experience: years since founding (or the profile's explicit figure) vs the minimum.
- Udyam micro/small (MSE): EMD waived and turnover/experience relaxed when the tender
  allows the MSE exemption; DPIIT startup: the same when the startup flag allows.
  Udyam "medium" is not an MSE. A relaxation that the tender does not offer is noted.
- certifications: matched case-insensitively (ISO 9001 == iso_9001) and not expired.
- GeM seller ID missing/expired and DSC expired (or expiring before the due date) are
  blocking failures; a DSC that is simply not recorded is unknown, not blocking.
- EMD is informational (never fails, excluded from the score) and reports the exemption.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.core.finance import (
    AverageTurnover,
    FiscalYearRevenue,
    MixedCurrencyError,
    average_turnover,
)
from app.core.money import format_inr, parse_inr
from app.core.profile_fields import CertificationKind, RegistrationKind, UdyamCategory

MSE_EXEMPTION = "mse_udyam"
STARTUP_EXEMPTION = "dpiit_startup"
MSE_CATEGORIES = frozenset({UdyamCategory.MICRO.value, UdyamCategory.SMALL.value})


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


_SCORE = {Status.PASS: Decimal(1), Status.UNKNOWN: Decimal("0.5"), Status.FAIL: Decimal(0)}


# --- inputs --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificationIn:
    kind: str
    expires_on: date | None = None


@dataclass(frozen=True, slots=True)
class RegistrationIn:
    kind: str  # app.core.profile_fields.RegistrationKind value (dsc, gem, cppp, state_portal)
    identifier: str | None = None
    expires_on: date | None = None


@dataclass(frozen=True, slots=True)
class ProfileSnapshotIn:
    """Plain values of an Indian company profile (built by services.profiles)."""

    revenue: tuple[FiscalYearRevenue, ...] = ()
    year_founded: int | None = None
    experience_years: int | None = None  # explicit figure wins over year_founded
    udyam_number: str | None = None
    udyam_category: str | None = None  # micro | small | medium
    dpiit_number: str | None = None
    gem_seller_id: str | None = None
    certifications: tuple[CertificationIn, ...] = ()
    registrations: tuple[RegistrationIn, ...] = ()


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def is_mse(profile: ProfileSnapshotIn) -> bool:
    """Udyam-registered micro or small enterprise (medium is not an MSE)."""
    return _present(profile.udyam_number) and profile.udyam_category in MSE_CATEGORIES


def is_startup(profile: ProfileSnapshotIn) -> bool:
    return _present(profile.dpiit_number)


_SEPARATORS = re.compile(r"[\s\-:/_.,]+")
_DROP = re.compile(r"[^a-z0-9_]")
_KINDS_BY_COMPACT = {kind.value.replace("_", ""): kind.value for kind in CertificationKind}


def normalize_certification(name: str) -> str:
    """'ISO 9001', 'iso-9001', 'ISO9001' -> 'iso_9001'; unknown names keep their own form."""
    text = _SEPARATORS.sub("_", name.strip().lower())
    text = _DROP.sub("", text).strip("_")
    return _KINDS_BY_COMPACT.get(text.replace("_", ""), text)


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
        return parse_inr(str(value))


def _int(value: Any) -> int | None:
    number = _decimal(value)
    return None if number is None else int(number)


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1"}
    return bool(value)


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CriteriaIn:
    """Eligibility criteria extracted from a tender (SPEC 5.3 `eligibility` jsonb)."""

    min_avg_turnover_inr: Decimal | None = None
    turnover_years: int = 3
    min_experience_years: int | None = None
    required_certifications: tuple[str, ...] = ()
    emd_amount_inr: Decimal | None = None
    allows_mse_exemption: bool = False
    allows_startup_exemption: bool = False
    requires_gem_registration: bool = False
    requires_dsc: bool = False
    due_on: date | None = None  # bid due date; a DSC expiring before it blocks

    def __post_init__(self) -> None:
        if self.turnover_years < 1:
            raise ValueError("turnover_years must be at least 1")
        for name in ("min_avg_turnover_inr", "emd_amount_inr", "min_experience_years"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CriteriaIn:
        """Tolerant loader: money as numbers or '₹1.5 Cr' strings, flags as bool/str/int,
        certification names normalized and de-duplicated, unknown keys ignored."""
        names: list[str] = []
        for raw in payload.get("required_certifications") or ():
            normalized = normalize_certification(str(raw))
            if normalized and normalized not in names:
                names.append(normalized)
        return cls(
            min_avg_turnover_inr=_decimal(payload.get("min_avg_turnover_inr")),
            turnover_years=_int(payload.get("turnover_years")) or 3,
            min_experience_years=_int(payload.get("min_experience_years")),
            required_certifications=tuple(names),
            emd_amount_inr=_decimal(payload.get("emd_amount_inr")),
            allows_mse_exemption=_bool(payload.get("allows_mse_exemption")),
            allows_startup_exemption=_bool(payload.get("allows_startup_exemption")),
            requires_gem_registration=_bool(payload.get("requires_gem_registration")),
            requires_dsc=_bool(payload.get("requires_dsc")),
            due_on=_date(payload.get("due_on")),
        )


# --- outputs -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CriterionResult:
    name: str
    status: Status
    reason: str
    exemption_applied: str | None = None
    blocking: bool = False  # a fail that prevents submission (SPEC 4.1: expired DSC)
    informational: bool = False  # reported but never fails and not scored (EMD)
    required: str | None = None
    measured: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "reason": self.reason,
            "exemption_applied": self.exemption_applied,
            "blocking": self.blocking,
            "informational": self.informational,
            "required": self.required,
            "measured": self.measured,
        }


@dataclass(frozen=True, slots=True)
class EligibilityIn:
    results: tuple[CriterionResult, ...]
    status: Status
    score: Decimal  # 0..1, SPEC 6 eligibility signal
    blocking: tuple[str, ...] = ()
    exemptions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "score": str(self.score),
            "blocking": list(self.blocking),
            "exemptions": list(self.exemptions),
            "results": [r.as_dict() for r in self.results],
        }


# --- evaluation ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Context:
    profile: ProfileSnapshotIn
    criteria: CriteriaIn
    today: date
    mse: bool = field(init=False)
    startup: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mse", is_mse(self.profile))
        object.__setattr__(self, "startup", is_startup(self.profile))

    def relaxation(self) -> str | None:
        """Exemption that relaxes prior turnover/experience (and waives EMD), if the tender
        offers one the profile qualifies for. MSE is reported first when both apply."""
        if self.criteria.allows_mse_exemption and self.mse:
            return MSE_EXEMPTION
        if self.criteria.allows_startup_exemption and self.startup:
            return STARTUP_EXEMPTION
        return None

    def not_offered(self) -> str:
        """Suffix for a fail when the profile holds a status this tender does not honour."""
        held = []
        if self.mse and not self.criteria.allows_mse_exemption:
            held.append("Udyam MSE")
        if self.startup and not self.criteria.allows_startup_exemption:
            held.append("DPIIT startup")
        return f" ({' and '.join(held)} relaxation not offered by this tender)" if held else ""


_EXEMPTION_LABEL = {
    MSE_EXEMPTION: "Udyam micro/small enterprise",
    STARTUP_EXEMPTION: "DPIIT-recognised startup",
}


def _inr(amount: Decimal) -> str:
    return format_inr(amount, compact=True)


def _turnover(ctx: _Context) -> CriterionResult | None:
    minimum = ctx.criteria.min_avg_turnover_inr
    if minimum is None:
        return None
    name, required = "turnover", str(minimum)
    exemption = ctx.relaxation()
    measured: str | None = None
    avg: AverageTurnover | None = None
    try:
        avg = average_turnover(ctx.profile.revenue, ctx.criteria.turnover_years)
    except MixedCurrencyError:
        problem = "annual revenue mixes currencies; record every fiscal year in INR"
    else:
        problem = ""
    if avg is not None:
        measured = str(avg.amount)
    if exemption is not None:
        return CriterionResult(
            name,
            Status.PASS,
            f"prior-turnover requirement of {_inr(minimum)} relaxed for a "
            f"{_EXEMPTION_LABEL[exemption]}",
            exemption_applied=exemption,
            required=required,
            measured=measured,
        )
    if problem:
        return CriterionResult(name, Status.UNKNOWN, problem, required=required)
    if avg is None:
        return CriterionResult(
            name, Status.UNKNOWN, "no annual revenue on profile", required=required
        )
    window = f"FY{avg.fiscal_years[0]}-{avg.fiscal_years[-1]}"
    if avg.currency != "INR":
        return CriterionResult(
            name,
            Status.UNKNOWN,
            f"revenue recorded in {avg.currency}; INR figures are needed for {window}",
            required=required,
            measured=measured,
        )
    if avg.years_used < ctx.criteria.turnover_years:
        return CriterionResult(
            name,
            Status.UNKNOWN,
            f"only {avg.years_used} of {ctx.criteria.turnover_years} fiscal years recorded "
            f"({window})",
            required=required,
            measured=measured,
        )
    summary = f"average turnover {_inr(avg.amount)} over {window} vs {_inr(minimum)} required"
    if avg.amount >= minimum:
        return CriterionResult(name, Status.PASS, summary, required=required, measured=measured)
    return CriterionResult(
        name, Status.FAIL, summary + ctx.not_offered(), required=required, measured=measured
    )


def _experience_years(ctx: _Context) -> int | None:
    if ctx.profile.experience_years is not None:
        return ctx.profile.experience_years
    if ctx.profile.year_founded is not None:
        return max(ctx.today.year - ctx.profile.year_founded, 0)
    return None


def _experience(ctx: _Context) -> CriterionResult | None:
    minimum = ctx.criteria.min_experience_years
    if minimum is None:
        return None
    name, required = "experience", str(minimum)
    years = _experience_years(ctx)
    measured = None if years is None else str(years)
    exemption = ctx.relaxation()
    if exemption is not None:
        return CriterionResult(
            name,
            Status.PASS,
            f"prior-experience requirement of {minimum} years relaxed for a "
            f"{_EXEMPTION_LABEL[exemption]}",
            exemption_applied=exemption,
            required=required,
            measured=measured,
        )
    if years is None:
        return CriterionResult(
            name,
            Status.UNKNOWN,
            "years of experience unknown (set year founded on the profile)",
            required=required,
        )
    summary = f"{years} years of experience vs {minimum} required"
    if years >= minimum:
        return CriterionResult(name, Status.PASS, summary, required=required, measured=measured)
    return CriterionResult(
        name, Status.FAIL, summary + ctx.not_offered(), required=required, measured=measured
    )


def _emd(ctx: _Context) -> CriterionResult | None:
    amount = ctx.criteria.emd_amount_inr
    if amount is None or amount <= 0:
        return None
    exemption = ctx.relaxation()
    if exemption is not None:
        reason = f"EMD of {_inr(amount)} waived for a {_EXEMPTION_LABEL[exemption]}"
    else:
        reason = f"EMD of {_inr(amount)} payable" + (ctx.not_offered() or " (no exemption applies)")
    return CriterionResult(
        "emd",
        Status.PASS,
        reason,
        exemption_applied=exemption,
        informational=True,
        required=str(amount),
    )


def _certifications(ctx: _Context) -> list[CriterionResult]:
    held: dict[str, list[CertificationIn]] = {}
    for cert in ctx.profile.certifications:
        held.setdefault(normalize_certification(cert.kind), []).append(cert)
    results: list[CriterionResult] = []
    seen: set[str] = set()
    for raw in ctx.criteria.required_certifications:
        key = normalize_certification(raw)
        if key in seen:
            continue
        seen.add(key)
        name = f"certification:{key}"
        candidates = held.get(key, [])
        valid = [c for c in candidates if c.expires_on is None or c.expires_on >= ctx.today]
        if valid:
            expiry = max((c.expires_on for c in valid if c.expires_on), default=None)
            reason = f"{key} on profile" + (f", valid until {expiry}" if expiry else "")
            results.append(
                CriterionResult(
                    name,
                    Status.PASS,
                    reason,
                    required=key,
                    measured=None if expiry is None else expiry.isoformat(),
                )
            )
        elif candidates:
            latest = max(c.expires_on for c in candidates if c.expires_on)
            results.append(
                CriterionResult(
                    name,
                    Status.FAIL,
                    f"{key} expired on {latest}",
                    required=key,
                    measured=latest.isoformat(),
                )
            )
        else:
            results.append(
                CriterionResult(name, Status.FAIL, f"{key} not on profile", required=key)
            )
    return results


def _registrations(ctx: _Context, kind: RegistrationKind) -> list[RegistrationIn]:
    return [r for r in ctx.profile.registrations if r.kind == kind.value]


def _gem(ctx: _Context) -> CriterionResult | None:
    if not ctx.criteria.requires_gem_registration:
        return None
    name = "gem_registration"
    if _present(ctx.profile.gem_seller_id):
        seller = str(ctx.profile.gem_seller_id).strip()
        return CriterionResult(name, Status.PASS, f"GeM seller ID {seller}", measured=seller)
    identified = [r for r in _registrations(ctx, RegistrationKind.GEM) if _present(r.identifier)]
    live = [r for r in identified if r.expires_on is None or r.expires_on >= ctx.today]
    if live:
        seller = str(live[0].identifier).strip()
        return CriterionResult(name, Status.PASS, f"GeM seller ID {seller}", measured=seller)
    if identified:
        latest = max(r.expires_on for r in identified if r.expires_on)
        return CriterionResult(
            name,
            Status.FAIL,
            f"GeM registration expired on {latest}; renew before bidding",
            blocking=True,
            measured=latest.isoformat(),
        )
    return CriterionResult(
        name,
        Status.FAIL,
        "GeM seller ID missing (register on gem.gov.in before bidding)",
        blocking=True,
    )


def _dsc(ctx: _Context) -> CriterionResult | None:
    if not ctx.criteria.requires_dsc:
        return None
    name = "dsc"
    certs = _registrations(ctx, RegistrationKind.DSC)
    if not certs:
        return CriterionResult(
            name, Status.UNKNOWN, "no Class 3 DSC on profile (add holder and expiry)"
        )
    needed_until = max(ctx.today, ctx.criteria.due_on or ctx.today)
    dated = [c for c in certs if c.expires_on is not None]
    valid = [c for c in dated if c.expires_on is not None and c.expires_on >= needed_until]
    if valid:
        latest = max(c.expires_on for c in valid if c.expires_on)
        return CriterionResult(
            name, Status.PASS, f"DSC valid until {latest}", measured=latest.isoformat()
        )
    if len(dated) < len(certs):
        return CriterionResult(name, Status.UNKNOWN, "DSC expiry not recorded on profile")
    latest = max(c.expires_on for c in dated if c.expires_on)
    if latest >= ctx.today and ctx.criteria.due_on is not None:
        reason = f"DSC expires on {latest}, before the bid due date {ctx.criteria.due_on}"
    else:
        reason = f"DSC expired on {latest}; an expired DSC blocks submission"
    return CriterionResult(name, Status.FAIL, reason, blocking=True, measured=latest.isoformat())


def evaluate_in(
    profile: ProfileSnapshotIn, criteria: CriteriaIn, today: date | None = None
) -> EligibilityIn:
    """Per-criterion pass/fail/unknown with reasons plus the overall status and signal."""
    ctx = _Context(profile, criteria, today or date.today())
    results: list[CriterionResult] = []
    for single in (_turnover(ctx), _experience(ctx), _emd(ctx)):
        if single is not None:
            results.append(single)
    results.extend(_certifications(ctx))
    for single in (_gem(ctx), _dsc(ctx)):
        if single is not None:
            results.append(single)

    scored = [r for r in results if not r.informational]
    statuses = {r.status for r in scored}
    if Status.FAIL in statuses:
        status = Status.FAIL
    elif Status.UNKNOWN in statuses:
        status = Status.UNKNOWN
    else:
        status = Status.PASS
    if scored:
        total = sum((_SCORE[r.status] for r in scored), Decimal(0))
        score = (total / len(scored)).quantize(Decimal("0.0001")).normalize()
    else:
        score = Decimal(1)
    exemptions: list[str] = []
    for r in results:
        if r.exemption_applied and r.exemption_applied not in exemptions:
            exemptions.append(r.exemption_applied)
    return EligibilityIn(
        results=tuple(results),
        status=status,
        score=score,
        blocking=tuple(r.name for r in results if r.blocking),
        exemptions=tuple(exemptions),
    )


# --- building a snapshot from stored values --------------------------------------------------


def snapshot_from_values(
    *,
    annual_revenue: Iterable[Mapping[str, Any]] | None,
    year_founded: int | None = None,
    experience_years: int | None = None,
    udyam_number: str | None = None,
    udyam_category: str | None = None,
    dpiit_number: str | None = None,
    gem_seller_id: str | None = None,
    certifications: Iterable[tuple[str, date | None]] = (),
    registrations: Iterable[tuple[str, str | None, date | None]] = (),
) -> ProfileSnapshotIn:
    """Snapshot from company_profiles columns (annual_revenue jsonb entries as stored) and
    (kind, expires_on) / (kind, identifier, expires_on) tuples of the child rows."""
    return ProfileSnapshotIn(
        revenue=tuple(
            FiscalYearRevenue(int(e["fiscal_year"]), Decimal(str(e["amount"])), str(e["currency"]))
            for e in (annual_revenue or ())
        ),
        year_founded=year_founded,
        experience_years=experience_years,
        udyam_number=udyam_number,
        udyam_category=None if udyam_category is None else str(udyam_category),
        dpiit_number=dpiit_number,
        gem_seller_id=gem_seller_id,
        certifications=tuple(CertificationIn(str(kind), exp) for kind, exp in certifications),
        registrations=tuple(
            RegistrationIn(str(kind), ident, exp) for kind, ident, exp in registrations
        ),
    )
