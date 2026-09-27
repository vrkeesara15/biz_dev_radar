"""LLM cost accounting (SPEC 8 cost guard): USD from token usage and a price table.

Prices are configuration (Settings.llm_prices, USD per million tokens per model id);
nothing here guesses a price: an unknown model raises so a config gap fails loudly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

MILLION = Decimal(1_000_000)
MICRO = Decimal(1_000_000)
_QUANT = Decimal("0.000001")


class UnknownModelPriceError(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> ModelPrice:
        inp = Decimal(str(values["input"]))
        out = Decimal(str(values["output"]))
        read = Decimal(str(values.get("cache_read", inp * Decimal("0.1"))))
        write = Decimal(str(values.get("cache_write", inp * Decimal("1.25"))))
        for name, value in (
            ("input", inp),
            ("output", out),
            ("cache_read", read),
            ("cache_write", write),
        ):
            if value < 0:
                raise ValueError(f"{name} price must be >= 0")
        return cls(input=inp, output=out, cache_read=read, cache_write=write)


def parse_prices(table: Mapping[str, Mapping[str, Any]]) -> dict[str, ModelPrice]:
    return {model: ModelPrice.from_mapping(values) for model, values in table.items()}


def price_for(model: str, prices: Mapping[str, ModelPrice]) -> ModelPrice:
    try:
        return prices[model]
    except KeyError as exc:
        raise UnknownModelPriceError(model) from exc


def estimate_cost(
    model: str,
    prices: Mapping[str, ModelPrice],
    *,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    """USD, rounded to a micro-dollar. `tokens_in` are the UNCACHED input tokens."""
    for name, value in (
        ("tokens_in", tokens_in),
        ("tokens_out", tokens_out),
        ("cache_read_tokens", cache_read_tokens),
        ("cache_write_tokens", cache_write_tokens),
    ):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
    price = price_for(model, prices)
    total = (
        Decimal(tokens_in) * price.input
        + Decimal(tokens_out) * price.output
        + Decimal(cache_read_tokens) * price.cache_read
        + Decimal(cache_write_tokens) * price.cache_write
    ) / MILLION
    return total.quantize(_QUANT, rounding=ROUND_HALF_UP)


def to_microusd(cost_usd: Decimal) -> int:
    """Integer micro-dollars for usage_ledger.quantity (sum() is bigint in Postgres)."""
    return int((cost_usd * MICRO).to_integral_value(rounding=ROUND_HALF_UP))
