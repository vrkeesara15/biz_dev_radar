"""Money parsing, Indian/US formatting and FX conversion (SPEC 5.3, 12). Pure, Decimal only.

    parse_inr("Rs. 1.2 Cr")                 -> Decimal("12000000")
    format_inr(Decimal("1250000"))          -> "₹12,50,000"
    format_inr(Decimal("12000000"), compact=True) -> "₹1.2 Cr"
    to_usd(Decimal("1000000"), "INR")       -> Conversion(usd=Decimal("11900.00"),
                                                rate=Decimal("0.0119"), currency="INR")

Portal text carries the currency marker (₹, Rs., INR), Indian grouping (12,50,000), the
"/-" suffix and lakh/crore words; the first number after the marker is the amount and a
multiplier is honoured only when it immediately follows that number ("25,000/- (Rupees
Twenty Five Thousand only)" is 25000). Unparseable text returns None, never raises.
FX rates come from Settings.fx_rates (FX_RATES JSON env) until a rates job lands.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.config import get_settings

THOUSAND = Decimal("1000")
LAKH = Decimal("100000")
MILLION = Decimal("1000000")
CRORE = Decimal("10000000")
BILLION = Decimal("1000000000")
CENTS = Decimal("0.01")
CURRENCIES = ("USD", "INR")
SYMBOLS = {"INR": "₹", "USD": "$"}

_INR_MARKER = re.compile(r"(₹|\brs\.?|\binr\b|\brupees?\b)", re.IGNORECASE)
_USD_MARKER = re.compile(r"(\$|\busd\b|\bus\$|\bdollars?\b)", re.IGNORECASE)
_INDIAN_WORDS = re.compile(r"\b(crores?|cr|lakhs?|lacs?)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)(?!\.\d)")
_INR_MULTIPLIERS: tuple[tuple[str, Decimal], ...] = (
    ("crores?|cr", CRORE),
    ("lakhs?|lacs?|l", LAKH),
    ("billions?|bn|b", BILLION),
    ("millions?|mn|m", MILLION),
    ("thousands?|k", THOUSAND),
)
_USD_MULTIPLIERS: tuple[tuple[str, Decimal], ...] = (
    ("billions?|bn|b", BILLION),
    ("millions?|mn|m", MILLION),
    ("thousands?|k", THOUSAND),
)


def _suffix_regex(table: tuple[tuple[str, Decimal], ...]) -> re.Pattern[str]:
    alternatives = "|".join(f"(?P<m{i}>{words})" for i, (words, _) in enumerate(table))
    return re.compile(rf"^\s*\.?\s*(?:{alternatives})(?![a-z])", re.IGNORECASE)


_INR_SUFFIX = _suffix_regex(_INR_MULTIPLIERS)
_USD_SUFFIX = _suffix_regex(_USD_MULTIPLIERS)


def _extract(
    text: str | None,
    *,
    reject: re.Pattern[str],
    strip: re.Pattern[str],
    suffix: re.Pattern[str],
    table: tuple[tuple[str, Decimal], ...],
) -> Decimal | None:
    if not text:
        return None
    cleaned = " ".join(text.replace("\u00a0", " ").split())
    if reject.search(cleaned):
        return None
    cleaned = strip.sub(" ", cleaned)
    match = _NUMBER.search(cleaned)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the regex only admits valid literals
        return None
    tail = suffix.match(cleaned[match.end() :])
    if tail is None:
        return value
    for index, (_, factor) in enumerate(table):
        if tail.group(f"m{index}"):
            value *= factor
            break
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def parse_inr(text: str | None) -> Decimal | None:
    """'₹12,50,000', 'Rs. 1.2 Cr', '12.5 Lakh', '1,20,000/-', 'INR 5 crore' -> Decimal."""
    return _extract(
        text, reject=_USD_MARKER, strip=_INR_MARKER, suffix=_INR_SUFFIX, table=_INR_MULTIPLIERS
    )


def parse_usd(text: str | None) -> Decimal | None:
    """'$1,234.56', 'USD 1.2M', '$3 billion' -> Decimal. Indian markers make it None."""
    if text and _INDIAN_WORDS.search(text):
        return None
    return _extract(
        text, reject=_INR_MARKER, strip=_USD_MARKER, suffix=_USD_SUFFIX, table=_USD_MULTIPLIERS
    )


def _check_currency(currency: str) -> str:
    code = currency.strip().upper()
    if code not in CURRENCIES:
        raise ValueError(f"unsupported currency {currency!r}")
    return code


def parse_amount(text: str | None, currency: str) -> Decimal | None:
    return parse_inr(text) if _check_currency(currency) == "INR" else parse_usd(text)


# --- formatting ----------------------------------------------------------------------------


def group_indian(digits: str) -> str:
    """'1250000' -> '12,50,000' (last three digits, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])


def _group_western(digits: str) -> str:
    return f"{int(digits):,}"


def _split(amount: Decimal) -> tuple[bool, str, str]:
    """(negative, integer digits, two-digit fraction or '') after rounding to cents."""
    quantized = abs(Decimal(amount)).quantize(CENTS, rounding=ROUND_HALF_UP)
    whole, _, fraction = f"{quantized:f}".partition(".")
    return Decimal(amount) < 0, whole, "" if fraction == "00" else fraction


def _compact_number(value: Decimal) -> str:
    text = f"{value.quantize(CENTS, rounding=ROUND_HALF_UP):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_inr(amount: Decimal, *, compact: bool = False) -> str:
    """'₹12,50,000' / '₹12,50,000.50'; compact: '₹1.2 Cr', '₹12.5 Lakh', below a lakh full."""
    negative, whole, fraction = _split(amount)
    sign = "-" if negative else ""
    magnitude = abs(Decimal(amount))
    if compact and magnitude >= CRORE:
        return f"{sign}₹{_compact_number(magnitude / CRORE)} Cr"
    if compact and magnitude >= LAKH:
        return f"{sign}₹{_compact_number(magnitude / LAKH)} Lakh"
    text = group_indian(whole)
    return f"{sign}₹{text}.{fraction}" if fraction else f"{sign}₹{text}"


def format_usd(amount: Decimal, *, compact: bool = False) -> str:
    """'$1,234.56' / '$1,234'; compact: '$3B', '$1.2M', '$2.5K'."""
    negative, whole, fraction = _split(amount)
    sign = "-" if negative else ""
    magnitude = abs(Decimal(amount))
    if compact:
        for factor, unit in ((BILLION, "B"), (MILLION, "M"), (THOUSAND, "K")):
            if magnitude >= factor:
                return f"{sign}${_compact_number(magnitude / factor)}{unit}"
    text = _group_western(whole)
    return f"{sign}${text}.{fraction}" if fraction else f"{sign}${text}"


def format_money(amount: Decimal, currency: str, *, compact: bool = False) -> str:
    if _check_currency(currency) == "INR":
        return format_inr(amount, compact=compact)
    return format_usd(amount, compact=compact)


# --- FX ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Conversion:
    usd: Decimal
    rate: Decimal  # USD per one unit of `currency`, as used
    currency: str


def to_usd(
    amount: Decimal,
    currency: str,
    rates: Mapping[str, float | int | str | Decimal] | None = None,
) -> Conversion:
    """Convert with the currency -> USD table (Settings.fx_rates by default) and keep the rate.

    Every currency, USD included, needs a table entry (the table is the source of truth);
    a missing currency or a non-positive rate raises ValueError."""
    code = currency.strip().upper()
    table = get_settings().fx_rates if rates is None else rates
    if code not in table:
        raise ValueError(f"no USD rate for {code}")
    rate = Decimal(str(table[code]))
    if rate <= 0:
        raise ValueError(f"invalid USD rate for {code}: {rate}")
    usd = (Decimal(amount) * rate).quantize(CENTS, rounding=ROUND_HALF_UP)
    return Conversion(usd=usd, rate=rate, currency=code)
