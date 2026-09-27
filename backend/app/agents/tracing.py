"""LLM tracing hook (SPEC 10.1: Langfuse). No-op unless LANGFUSE_* keys are set; the
langfuse package is optional and import-guarded, so tests and the API never need it."""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from app.core.config import Settings, get_settings

log = structlog.get_logger(__name__)


class Tracer(Protocol):
    def step_started(self, *, run_id: str, step_id: str, agent: str, attempt: int) -> None: ...

    def step_finished(
        self,
        *,
        run_id: str,
        step_id: str,
        agent: str,
        status: str,
        model: str | None,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None: ...

    def flush(self) -> None: ...


class NoopTracer:
    def step_started(self, *, run_id: str, step_id: str, agent: str, attempt: int) -> None:
        return None

    def step_finished(self, **kwargs: Any) -> None:
        return None

    def flush(self) -> None:
        return None


class LangfuseTracer:
    """One Langfuse trace per run, one generation per step (tokens + cost as usage)."""

    def __init__(self, client: Any | None = None, *, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        if client is None:
            try:
                from langfuse import Langfuse
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("langfuse is not installed") from exc
            client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        self.client = client
        self._traces: dict[str, Any] = {}
        self._generations: dict[str, Any] = {}

    def _trace(self, run_id: str) -> Any:
        if run_id not in self._traces:
            self._traces[run_id] = self.client.trace(id=run_id, name="agent_run")
        return self._traces[run_id]

    def step_started(self, *, run_id: str, step_id: str, agent: str, attempt: int) -> None:
        trace = self._trace(run_id)
        self._generations[step_id] = trace.generation(
            id=step_id, name=agent, metadata={"attempt": attempt}
        )

    def step_finished(
        self,
        *,
        run_id: str,
        step_id: str,
        agent: str,
        status: str,
        model: str | None,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        error: str | None = None,
    ) -> None:
        generation = self._generations.pop(step_id, None)
        if generation is None:
            self.step_started(run_id=run_id, step_id=step_id, agent=agent, attempt=1)
            generation = self._generations.pop(step_id)
        generation.end(
            model=model,
            usage={"input": tokens_in, "output": tokens_out, "total_cost": cost_usd},
            level="ERROR" if status == "failed" else "DEFAULT",
            status_message=error,
            metadata={"status": status},
        )

    def flush(self) -> None:
        flush = getattr(self.client, "flush", None)
        if callable(flush):
            flush()


def tracer_from_settings(settings: Settings | None = None) -> Tracer:
    settings = settings or get_settings()
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            return LangfuseTracer(settings=settings)
        except RuntimeError as exc:
            log.warning("tracing.disabled", error=str(exc))
    return NoopTracer()
