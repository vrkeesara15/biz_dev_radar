"""FakeLLM: queued responses, recorded calls, real cost accounting. Used by every agent
test; exposed as the `fake_llm` fixture in tests/conftest.py.

    fake_llm.queue({"lines": [...]})            # next complete_json answer (validated)
    fake_llm.queue("free text")                 # next complete_text answer
    fake_llm.queue(ValueError("boom"))          # next call raises
    fake_llm.calls[0].kwargs["system"]          # what the agent sent
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.agents.llm import CacheBlock, InvalidOutput, LLMResult, Message
from app.core.config import Settings
from app.core.llm_cost import ModelPrice, estimate_cost, parse_prices
from pydantic import BaseModel, ValidationError


@dataclass(slots=True)
class FakeCall:
    method: str
    model: str
    system: str
    messages: list[dict[str, Any]]
    schema: str | None
    cache_blocks: list[CacheBlock]
    kwargs: dict[str, Any]

    @property
    def user_text(self) -> str:
        return "\n".join(str(m.get("content")) for m in self.messages if m.get("role") == "user")


@dataclass
class FakeLLM:
    responses: list[Any] = field(default_factory=list)
    calls: list[FakeCall] = field(default_factory=list)
    tokens_in: int = 1000
    tokens_out: int = 100
    cache_read_tokens: int = 0
    prices: dict[str, ModelPrice] = field(
        default_factory=lambda: parse_prices(Settings(_env_file=None).llm_prices)  # type: ignore[call-arg]
    )
    output_retries: int = 2
    # when the queue is empty complete_json answers with this (a dict) or raises
    default_json: Any = None

    def queue(self, *responses: Any) -> FakeLLM:
        self.responses.extend(responses)
        return self

    def _next(self) -> Any:
        if self.responses:
            return self.responses.pop(0)
        if self.default_json is not None:
            return self.default_json
        raise AssertionError("FakeLLM has no queued response")

    def _result(self, model: str, parsed: Any, raw_text: str, attempts: int = 1) -> LLMResult:
        cost = (
            estimate_cost(
                model,
                self.prices,
                tokens_in=self.tokens_in,
                tokens_out=self.tokens_out,
                cache_read_tokens=self.cache_read_tokens,
            )
            * attempts
        )
        return LLMResult(
            parsed=parsed,
            raw_text=raw_text,
            tokens_in=self.tokens_in * attempts,
            tokens_out=self.tokens_out * attempts,
            cache_read_tokens=self.cache_read_tokens * attempts,
            cost_usd=Decimal(cost),
            model=model,
            attempts=attempts,
            stop_reason="tool_use",
        )

    def _record(self, method: str, schema: str | None, **kwargs: Any) -> None:
        self.calls.append(
            FakeCall(
                method=method,
                model=kwargs["model"],
                system=kwargs["system"],
                messages=[dict(m) for m in kwargs["messages"]],
                schema=schema,
                cache_blocks=list(kwargs.get("cache_blocks") or ()),
                kwargs=kwargs,
            )
        )

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        schema: type[BaseModel],
        cache_blocks: Sequence[CacheBlock] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        self._record(
            "complete_json",
            schema.__name__,
            model=model,
            system=system,
            messages=messages,
            cache_blocks=cache_blocks,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        last_error = "no output"
        for attempt in range(1, self.output_retries + 2):
            candidate = self._next()
            if isinstance(candidate, BaseException):
                raise candidate
            if isinstance(candidate, BaseModel):
                candidate = candidate.model_dump(mode="json")
            try:
                parsed = schema.model_validate(candidate)
            except ValidationError as exc:
                last_error = str(exc)
                continue
            return self._result(
                model, parsed, json.dumps(candidate, sort_keys=True, default=str), attempt
            )
        attempts = self.output_retries + 1
        usage = self._result(model, None, "", attempts)
        raise InvalidOutput(schema.__name__, attempts, last_error, usage=usage)

    async def complete_text(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        cache_blocks: Sequence[CacheBlock] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        self._record(
            "complete_text",
            None,
            model=model,
            system=system,
            messages=messages,
            cache_blocks=cache_blocks,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        candidate = self._next()
        if isinstance(candidate, BaseException):
            raise candidate
        text = candidate if isinstance(candidate, str) else json.dumps(candidate)
        return self._result(model, None, text)

    def calls_for(self, schema: str) -> list[FakeCall]:
        return [c for c in self.calls if c.schema == schema]

    @staticmethod
    def is_mapping(value: Any) -> bool:
        return isinstance(value, Mapping)
