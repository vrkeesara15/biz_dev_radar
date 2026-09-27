"""SPEC 6 stage 1: hard filters. Pure.

    result = hard_filters(profile, opportunity, now)
    result.keep        # False -> the match is stored with band "filtered" and `reason`
    result.reason      # region | country | notice_type | blocked_buyer | past_due |
                       # status_cancelled | status_awarded | exclusion_keyword:<term>
    result.cap         # 30 when the company cannot meet the set-aside (kept for teaming)
    result.label       # "Ineligible: set-aside"
    result.checks      # every filter with passed/detail, for the breakdown jsonb

Drop when any of: region/country not allowed by the profile; notice type not wanted;
buyer blocked (normalised names, whole-word phrase match at any hierarchy level);
response date not in the future unless the notice is on recompete watch (a cancelled or
awarded notice counts as past too); an exclusion keyword appears in title or summary.
A set-aside / reservation the company cannot meet keeps the notice, caps the score at 30
and labels it (SPEC 6: "still useful for teaming"). An unknown status (no size data, an
unrecognised programme) never penalises: the eligibility signal reports it as unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.eligibility import SizeStatus, small_business_status
from app.core.matching.types import MatchOpportunity, MatchProfile
from app.core.normalize.buyer import normalized_buyer

INELIGIBLE_SET_ASIDE_CAP = 30
INELIGIBLE_SET_ASIDE_LABEL = "Ineligible: set-aside"

TERMINAL_STATUSES = frozenset({"cancelled", "awarded"})

# SAM.gov set-aside codes -> the certification (app.core.profile_fields.CertificationKind)
# that satisfies them, with the programmes that imply a broader one (EDWOSB is a WOSB,
# SDVOSB is a VOSB). Total/partial small business needs SBA size status per NAICS.
SMALL_BUSINESS_CODES = frozenset({"SBA", "SBP"})
_CERT_BY_CODE: dict[str, tuple[str, str]] = {
    # code: (label, certification kind)
    "8A": ("8(a)", "8a"),
    "8AN": ("8(a) sole source", "8a"),
    "HZC": ("HUBZone", "hubzone"),
    "HZS": ("HUBZone sole source", "hubzone"),
    "WOSB": ("WOSB", "wosb"),
    "WOSBSS": ("WOSB sole source", "wosb"),
    "EDWOSB": ("EDWOSB", "edwosb"),
    "EDWOSBSS": ("EDWOSB sole source", "edwosb"),
    "SDVOSBC": ("SDVOSB", "sdvosb"),
    "SDVOSBS": ("SDVOSB sole source", "sdvosb"),
    "SDVOSB": ("SDVOSB", "sdvosb"),
    "VSA": ("veteran-owned", "vosb"),
    "VSS": ("veteran-owned sole source", "vosb"),
    "VOSB": ("veteran-owned", "vosb"),
}
_IMPLIED_BY: dict[str, frozenset[str]] = {
    "wosb": frozenset({"wosb", "edwosb"}),
    "vosb": frozenset({"vosb", "sdvosb"}),
}
_OPEN_CODES = frozenset({"", "NONE", "N/A", "NA", "FULL", "OPEN"})

_WORD = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> str:
    return " ".join(w for w in _WORD.split(text.lower()) if w)


def _phrase_in(phrase: str, text: str) -> bool:
    """Whole-word phrase containment on normalised text."""
    return phrase != "" and f" {phrase} " in f" {text} "


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class SetAsideCheck:
    eligible: bool
    code: str | None
    detail: str

    def as_check(self) -> Check:
        return Check("set_aside", self.eligible, self.detail)


@dataclass(frozen=True, slots=True)
class FilterResult:
    keep: bool
    reason: str | None = None
    cap: int | None = None
    label: str | None = None
    ineligible_set_aside: bool = False
    checks: tuple[Check, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "keep": self.keep,
            "reason": self.reason,
            "cap": self.cap,
            "label": self.label,
            "ineligible_set_aside": self.ineligible_set_aside,
            "checks": [c.as_dict() for c in self.checks],
        }


# --- individual filters -----------------------------------------------------------------------


def region_check(profile: MatchProfile, opp: MatchOpportunity) -> Check:
    if opp.region.lower() != profile.region.lower():
        return Check("region", False, f"notice region {opp.region} is not {profile.region}")
    wanted = {c.upper() for c in profile.target_countries}
    if wanted and opp.country.upper() not in wanted:
        return Check("country", False, f"{opp.country} not in target countries")
    return Check("region", True, None)


def notice_type_check(profile: MatchProfile, opp: MatchOpportunity) -> Check:
    wanted = {t.strip().lower() for t in profile.notice_types_wanted if t.strip()}
    if wanted and opp.notice_type.lower() not in wanted:
        return Check("notice_type", False, f"{opp.notice_type} not wanted")
    return Check("notice_type", True, None)


def blocked_buyer_check(profile: MatchProfile, opp: MatchOpportunity) -> Check:
    blocked = [b for b in (normalized_buyer(name) for name in profile.blocked_buyers) if b]
    if not blocked:
        return Check("blocked_buyer", True, None)
    buyers = [b for b in (normalized_buyer(name) for name in opp.buyers) if b]
    for entry in blocked:
        for buyer in buyers:
            if buyer == entry or _phrase_in(entry, buyer):
                return Check("blocked_buyer", False, f"buyer matches blocked {entry!r}")
    return Check("blocked_buyer", True, None)


def response_due_check(opp: MatchOpportunity, now: datetime) -> Check:
    if opp.recompete_watch:
        return Check("response_due", True, "recompete watch")
    status = opp.status.lower()
    if status in TERMINAL_STATUSES:
        return Check("response_due", False, f"status_{status}")
    if opp.response_due_at is not None and opp.response_due_at <= now:
        return Check("response_due", False, "past_due")
    return Check("response_due", True, None)


def exclusion_keyword_check(profile: MatchProfile, opp: MatchOpportunity) -> Check:
    haystack = _words(opp.text)
    for raw in profile.exclude_keywords:
        term = _words(raw)
        if term and _phrase_in(term, haystack):
            return Check("exclusion_keywords", False, f"exclusion_keyword:{term}")
    return Check("exclusion_keywords", True, None)


def _size_status(profile: MatchProfile, naics: tuple[str, ...]) -> SizeStatus:
    """small if the company is small for ANY of the notice's NAICS codes; unknown when we
    cannot tell for every code."""
    statuses = {
        small_business_status(code, profile.avg_receipts_usd, profile.employee_count_total)
        for code in naics
    }
    if SizeStatus.SMALL in statuses:
        return SizeStatus.SMALL
    if statuses and statuses == {SizeStatus.OTHER_THAN_SMALL}:
        return SizeStatus.OTHER_THAN_SMALL
    return SizeStatus.UNKNOWN


def _us_set_aside(profile: MatchProfile, opp: MatchOpportunity, today: date) -> SetAsideCheck:
    code = (opp.set_aside or "").strip().upper()
    if code in _OPEN_CODES:
        return SetAsideCheck(True, None, "full and open")
    if code in SMALL_BUSINESS_CODES:
        status = _size_status(profile, opp.naics)
        if status is SizeStatus.OTHER_THAN_SMALL:
            return SetAsideCheck(
                False, code, "small business set-aside; company is other than small"
            )
        note = "small" if status is SizeStatus.SMALL else "size status unknown"
        return SetAsideCheck(True, code, f"small business set-aside; {note}")
    if code in _CERT_BY_CODE:
        label, kind = _CERT_BY_CODE[code]
        accepted = _IMPLIED_BY.get(kind, frozenset({kind}))
        if not any(profile.has_certification(k, today) for k in accepted):
            return SetAsideCheck(False, code, f"{label} set-aside; certification not on profile")
        if _size_status(profile, opp.naics) is SizeStatus.OTHER_THAN_SMALL:
            return SetAsideCheck(False, code, f"{label} set-aside; company is other than small")
        return SetAsideCheck(True, code, f"{label} set-aside; certification on profile")
    return SetAsideCheck(True, code, f"set-aside {code} not evaluated")


_IN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sc_st", re.compile(r"\bsc\s*[/\-]?\s*st\b|scheduled (caste|tribe)")),
    ("women", re.compile(r"\bwom[ae]n\b")),
    ("startup", re.compile(r"\bstart[\s\-]?ups?\b")),
    ("class_1", re.compile(r"class\s*[\-]?\s*(i|1)\b|make in india")),
    ("mse", re.compile(r"\bmses?\b|\bmsmes?\b|micro (and|&) small")),
)


def _in_reservation(profile: MatchProfile, opp: MatchOpportunity) -> SetAsideCheck:
    text = (opp.reservation or "").strip().lower()
    if not text:
        return SetAsideCheck(True, None, "no reservation")
    matched = [name for name, pattern in _IN_RULES if pattern.search(text)]
    if not matched:
        return SetAsideCheck(
            True, opp.reservation, f"reservation {opp.reservation!r} not evaluated"
        )
    ownership = profile.mse_ownership or "none"
    for name in matched:
        if name == "mse" and not profile.is_mse:
            return SetAsideCheck(False, opp.reservation, "reserved for MSEs; no Udyam micro/small")
        if name == "sc_st" and not (profile.is_mse and ownership in ("sc_st", "sc_st_women")):
            return SetAsideCheck(False, opp.reservation, "reserved for SC/ST-owned MSEs")
        if name == "women" and not (profile.is_mse and ownership in ("women", "sc_st_women")):
            return SetAsideCheck(False, opp.reservation, "reserved for women-owned MSEs")
        if name == "startup" and not (profile.dpiit_number or "").strip():
            return SetAsideCheck(False, opp.reservation, "reserved for DPIIT startups")
        if name == "class_1" and profile.local_supplier_class != "class_1":
            return SetAsideCheck(False, opp.reservation, "reserved for Class-I local suppliers")
    return SetAsideCheck(True, opp.reservation, f"reservation {opp.reservation!r} met")


def set_aside_check(profile: MatchProfile, opp: MatchOpportunity, today: date) -> SetAsideCheck:
    """US set-aside code or Indian reservation text vs the profile's statuses."""
    if opp.region.lower() == "in":
        return _in_reservation(profile, opp)
    return _us_set_aside(profile, opp, today)


# --- stage 1 -----------------------------------------------------------------------------------


def hard_filters(profile: MatchProfile, opp: MatchOpportunity, now: datetime) -> FilterResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    checks: list[Check] = []
    for check in (
        region_check(profile, opp),
        notice_type_check(profile, opp),
        blocked_buyer_check(profile, opp),
        response_due_check(opp, now),
        exclusion_keyword_check(profile, opp),
    ):
        checks.append(check)
        if not check.passed:
            reason = check.detail if check.name in ("response_due", "exclusion_keywords") else None
            return FilterResult(False, reason or check.name, checks=tuple(checks))
    set_aside = set_aside_check(profile, opp, now.date())
    checks.append(set_aside.as_check())
    if not set_aside.eligible:
        return FilterResult(
            True,
            None,
            cap=INELIGIBLE_SET_ASIDE_CAP,
            label=INELIGIBLE_SET_ASIDE_LABEL,
            ineligible_set_aside=True,
            checks=tuple(checks),
        )
    return FilterResult(True, None, checks=tuple(checks))
