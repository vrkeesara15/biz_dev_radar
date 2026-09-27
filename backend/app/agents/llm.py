"""LLM access (CLAUDE.md: every model call goes through here).

    llm = AnthropicLLM(settings)                       # or FakeLLM in tests
    result = await llm.complete_json(
        model=model_for(AgentRole.SUMMARY),
        system=system_prompt("Summarise the notice in five lines."),
        messages=[{"role": "user", "content": untrusted_block("notice", text)}],
        schema=Summary,
        cache_blocks=[CacheBlock(solicitation_text)],   # cache_control ephemeral
    )
    result.parsed  # a validated Summary

JSON outputs use a forced tool call (`emit`, strict input_schema = the Pydantic JSON
schema). Output that does not validate is fed back with the validation error and retried
up to `LLM_OUTPUT_RETRIES` extra times, then `InvalidOutput` is raised (the runner flags
the step). Cost is computed from Settings.llm_prices.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.llm_cost import ModelPrice, estimate_cost, parse_prices

log = structlog.get_logger(__name__)

EMIT_TOOL = "emit"
Message = Mapping[str, Any]
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CacheBlock:
    """A large stable block (solicitation text, profile) placed in the system prompt with
    cache_control ephemeral. Keep them byte-stable between calls to get cache hits."""

    text: str
    ttl: str | None = None  # None = 5 min, "1h" for long pipelines


@dataclass(slots=True)
class LLMResult:
    parsed: Any
    raw_text: str
    tokens_in: int
    tokens_out: int
    cache_read_tokens: int
    cost_usd: Decimal
    model: str
    cache_write_tokens: int = 0
    attempts: int = 1
    stop_reason: str | None = None
    request_id: str | None = None
    raw: Any = field(default=None, repr=False)


class InvalidOutput(LLMError):  # noqa: N818 - domain name used across agents
    """The model never produced output matching the schema within the retry budget.
    `usage` carries the tokens/cost of the failed attempts so the step still meters them."""

    def __init__(
        self, schema: str, attempts: int, last_error: str, usage: LLMResult | None = None
    ) -> None:
        super().__init__(f"{schema}: invalid output after {attempts} attempts: {last_error}")
        self.schema = schema
        self.attempts = attempts
        self.last_error = last_error
        self.usage = usage


class LLMClient(Protocol):
    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        schema: type[T],
        cache_blocks: Sequence[CacheBlock] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...

    async def complete_text(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        cache_blocks: Sequence[CacheBlock] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...


def system_blocks(system: str, cache_blocks: Sequence[CacheBlock]) -> list[dict[str, Any]]:
    """System prompt as content blocks: the fixed text first, then each cached block."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
    for block in cache_blocks[:4]:  # the API allows at most 4 breakpoints
        control: dict[str, Any] = {"type": "ephemeral"}
        if block.ttl:
            control["ttl"] = block.ttl
        blocks.append({"type": "text", "text": block.text, "cache_control": control})
    return blocks


def emit_tool(schema: type[BaseModel]) -> dict[str, Any]:
    json_schema = schema.model_json_schema()
    json_schema.setdefault("additionalProperties", False)
    return {
        "name": EMIT_TOOL,
        "description": f"Emit the final {schema.__name__} result. Call exactly once.",
        "input_schema": json_schema,
        "strict": True,
    }


def validation_feedback(error: str) -> str:
    return (
        "Your previous output did not match the required schema:\n"
        f"{error}\n"
        f"Call the `{EMIT_TOOL}` tool again with a corrected, complete input."
    )


class AnthropicLLM:
    """Anthropic SDK implementation. `client` is injectable for tests (an object with
    `messages.create` coroutine); without one an AsyncAnthropic client is created from
    ANTHROPIC_API_KEY."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
        prices: Mapping[str, ModelPrice] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.prices = dict(prices) if prices is not None else parse_prices(self.settings.llm_prices)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            if not self.settings.anthropic_api_key:
                raise LLMError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def _cost(self, model: str, usage: Any) -> tuple[int, int, int, int, Decimal]:
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cost = estimate_cost(
            model,
            self.prices,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        return tokens_in, tokens_out, cache_read, cache_write, cost

    async def _create(self, **kwargs: Any) -> Any:
        return await self.client.messages.create(**kwargs)

    async def complete_json(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[Message],
        schema: type[T],
        cache_blocks: Sequence[CacheBlock] = (),
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        history: list[dict[str, Any]] = [dict(m) for m in messages]
        attempts_allowed = 1 + max(self.settings.llm_output_retries, 0)
        totals = [0, 0, 0, 0]
        cost_total = Decimal(0)
        last_error = "no output"
        for attempt in range(1, attempts_allowed + 1):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens or self.settings.llm_max_tokens,
                "system": system_blocks(system, cache_blocks),
                "messages": list(history),  # a copy: the SDK must not see later retries
                "tools": [emit_tool(schema)],
                "tool_choice": {"type": "tool", "name": EMIT_TOOL},
            }
            if temperature is not None:
                kwargs["temperature"] = temperature
            response = await self._create(**kwargs)
            tin, tout, cread, cwrite, cost = self._cost(model, response.usage)
            for i, v in enumerate((tin, tout, cread, cwrite)):
                totals[i] += v
            cost_total += cost
            tool_input = _tool_input(response)
            if tool_input is None:
                last_error = f"no {EMIT_TOOL} tool call in the response"
            else:
                try:
                    parsed = schema.model_validate(tool_input)
                except ValidationError as exc:
                    last_error = str(exc)[:2000]
                else:
                    return LLMResult(
                        parsed=parsed,
                        raw_text=json.dumps(tool_input, sort_keys=True, default=str),
                        tokens_in=totals[0],
                        tokens_out=totals[1],
                        cache_read_tokens=totals[2],
                        cache_write_tokens=totals[3],
                        cost_usd=cost_total,
                        model=model,
                        attempts=attempt,
                        stop_reason=getattr(response, "stop_reason", None),
                        request_id=getattr(response, "_request_id", None),
                        raw=response,
                    )
            log.warning("llm.invalid_output", model=model, schema=schema.__name__, attempt=attempt)
            history.append({"role": "assistant", "content": _assistant_content(response)})
            history.append({"role": "user", "content": validation_feedback(last_error)})
        raise InvalidOutput(
            schema.__name__,
            attempts_allowed,
            last_error,
            usage=LLMResult(
                parsed=None,
                raw_text="",
                tokens_in=totals[0],
                tokens_out=totals[1],
                cache_read_tokens=totals[2],
                cache_write_tokens=totals[3],
                cost_usd=cost_total,
                model=model,
                attempts=attempts_allowed,
            ),
        )

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
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "system": system_blocks(system, cache_blocks),
            "messages": [dict(m) for m in messages],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = await self._create(**kwargs)
        tin, tout, cread, cwrite, cost = self._cost(model, response.usage)
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        )
        return LLMResult(
            parsed=None,
            raw_text=text,
            tokens_in=tin,
            tokens_out=tout,
            cache_read_tokens=cread,
            cache_write_tokens=cwrite,
            cost_usd=cost,
            model=model,
            stop_reason=getattr(response, "stop_reason", None),
            request_id=getattr(response, "_request_id", None),
            raw=response,
        )


def _tool_input(response: Any) -> dict[str, Any] | None:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == EMIT_TOOL:
            payload = block.input
            if isinstance(payload, str):  # defensive: some transports hand back JSON text
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
            return dict(payload) if isinstance(payload, Mapping) else None
    return None


def _assistant_content(response: Any) -> list[dict[str, Any]]:
    """The assistant turn to replay before the correction request."""
    blocks: list[dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        kind = getattr(block, "type", "")
        if kind == "text" and getattr(block, "text", ""):
            blocks.append({"type": "text", "text": block.text})
        elif kind == "tool_use":
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
    return blocks or [{"type": "text", "text": "(no output)"}]


def llm_from_settings(settings: Settings | None = None) -> LLMClient | None:
    """An LLM client when a key is configured, else None (callers skip LLM work)."""
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        return None
    return AnthropicLLM(settings)
