# Runbook: observability

What is wired, how to switch it on, and how to answer "what happened to this request?"
(SPEC 10.1). Everything below is **off by default**: an environment with none of these
variables set runs exactly as it did before, which
`backend/tests/unit/test_observability.py` asserts.

## The four signals

| Signal | Where it goes | Switch | Wired in |
| --- | --- | --- | --- |
| Traces (API routes, SQL, outbound HTTP, Celery tasks) | OTLP/HTTP collector → Cloud Trace | `OTEL_EXPORTER_OTLP_ENDPOINT` | `app/observability.py` |
| Errors | Sentry | `SENTRY_DSN` (+ `SENTRY_TRACES_SAMPLE_RATE`) | `app/observability.py` |
| LLM traces and cost | Langfuse | `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`) | `app/agents/tracing.py` |
| Structured logs | stdout → Cloud Logging | always on; JSON in production (`APP_ENV=production`) | `app/logging.py` |

`configure_observability(settings, app=app, component="api" \| "worker")` returns a dict
saying which of the first three are live. The API stores it on `app.state.observability`;
the worker keeps it in `app.celery_app.OBSERVABILITY`.

## Switching tracing on locally

```bash
docker run --rm -p 4318:4318 -p 55679:55679 otel/opentelemetry-collector:latest
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
make up && cd backend && uv run uvicorn app.main:app --reload
```

Spans are POSTed to `$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces`. In GCP the endpoint is the
collector sidecar on the Cloud Run service; the collector holds the Cloud Trace
credentials, the application never does.

Resource attributes on every span:

- `service.name` — `bidradar-api` or `bidradar-worker`
- `service.version`, `deployment.environment` (`APP_ENV`)
- `deployment.region` — `us` or `in`, so the two deployments never blur together

Instrumented: FastAPI (excluding `/healthz`), SQLAlchemy, httpx (so every adapter fetch
is a span under the job that made it) and, in the worker only, Celery.

## Answering "what happened to this request?"

1. Every response carries `X-Request-ID` (echoed from the caller when it sent one).
2. Every log line of that request carries `request_id`, and — once the bearer token has
   been decoded — `tenant_id` and `user_id`. In Cloud Logging:
   `jsonPayload.request_id="<id>"`.
3. Mutating requests under `/api/v1` also leave an `audit_log` row with the same
   `request_id`, the action, the object, the IP and the status (SPEC 11).
4. If Sentry is on, the event carries the same `request_id` and `tenant_id` tags.

```sql
-- the audit trail for one request id
SELECT at, action, object_type, object_id, ip, meta
FROM audit_log WHERE request_id = '<id>' ORDER BY at;
```

## Log fields

`app/core/context.py` holds three contextvars: the request id (set by
`RequestIdMiddleware` on the way in) and the tenant/user id (set by `get_current_user`
once the token verifies). `app/logging.py` copies them onto every structlog event, so no
call site has to remember. Both are cleared when the request finishes — a log line
emitted outside a request carries no principal, which is also a test.

A Celery task has no request, so its lines carry neither; the task's own arguments
(`tenant_id`, `source_id`, `request_id` of the data request) are logged explicitly by the
job entrypoints in `app/jobs/`.

## Sentry and PII

`SENTRY_DSN` is the only switch. Initialisation forces:

- `send_default_pii=False` and `max_request_body_size="never"`
- a `before_send` scrubber (`app/observability.py`) that
  - redacts any field named like a secret or an identifier (`authorization`, `cookie`,
    `stripe-signature`, `x-razorpay-signature`, `api_key`, `token`, `password`, `ein`,
    `pan`, `gstin`, `tan`, `bank_account`, `ifsc`, `email`), at any depth;
  - masks email addresses, EIN (`12-3456789`), PAN (`ABCDE1234F`), GSTIN and
    `Bearer <token>` **inside** free text, so a stack frame local or an error message
    cannot leak one;
  - tags the event with `tenant_id` and `request_id` and attaches the user as an **id
    only** — never an email.

When adding a field that holds a regulated identifier, add its name to `SENSITIVE_KEYS`
and a case to `tests/unit/test_observability.py`.

## Langfuse

`AgentRunner` opens one Langfuse trace per `agent_runs` row and one generation per
`agent_steps` row, with the model, input/output tokens and the step's `cost_usd` as
usage. The trace is tagged `tenant:<uuid>`, `kind:<run kind>` and, for pursuit work,
`pursuit:<uuid>`, so cost can be attributed per tenant and per pursuit. Without both
Langfuse keys the runner uses `NoopTracer` and nothing is sent.

The same numbers are always in the database regardless of Langfuse:
`agent_steps.cost_usd` (exact `Decimal`) and the `usage_ledger` metrics
`llm_tokens_in`, `llm_tokens_out`, `llm_cost_microusd` (monthly).

```sql
-- LLM cost per tenant this month
SELECT tenant_id, SUM(quantity)::bigint AS microusd
FROM usage_ledger
WHERE metric = 'llm_cost_microusd' AND period = to_char(now(), 'YYYY-MM')
GROUP BY tenant_id ORDER BY microusd DESC;
```

## When a signal goes quiet

| Symptom | Check |
| --- | --- |
| No spans in Cloud Trace | `app.state.observability["tracing"]`; is `OTEL_EXPORTER_OTLP_ENDPOINT` set in the revision? Is the collector sidecar healthy? Spans are batched — allow ~5 s. |
| Spans for the API but not for jobs | The worker builds its own pipeline at import; confirm the env var is on the **worker** revision too, and that `component="worker"` (Celery instrumentation is worker-only). |
| No Sentry events | `sentry_sdk.get_client().is_active()`; a DSN typo fails silently by design. |
| Sentry events with no tenant | The error happened before `get_current_user` ran (auth failure, middleware) — expected. |
| No Langfuse traces | Both keys must be set; check `app.state.observability["langfuse"]`. Cost is still in `usage_ledger` either way. |
| Logs without `tenant_id` | The route is public or the token never verified. Compare against the `audit_log` row, which is only written for an authenticated caller. |
