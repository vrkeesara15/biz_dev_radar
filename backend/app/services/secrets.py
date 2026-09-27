"""Secret references for tenant integrations (SPEC 7, 11).

`integrations.secret_ref` never holds a bare secret. It names one of three schemes:

    env:SLACK_WEBHOOK_ACME              a process environment variable. This is how a
                                        Secret Manager secret reaches the service on Cloud
                                        Run (mounted as an env var by Terraform, M7), so it
                                        is the production form.
    sm://projects/p/secrets/s/versions/latest
                                        a Secret Manager resource name, resolved by the
                                        platform at deploy time into the env var above.
                                        Reading it in-process needs the Secret Manager
                                        client, which arrives with M7; until then it
                                        resolves through SECRET_<NAME> if that is set and
                                        otherwise raises SecretUnavailableError.
    enc:v1:<nonce>:<ciphertext>         AES-256-GCM under FIELD_ENCRYPTION_KEY
                                        (app.core.crypto). What the settings UI writes when
                                        an owner pastes a webhook URL and the tenant has no
                                        Secret Manager entry of its own.

A resolved secret is either a bare string (the webhook URL) or a JSON object; use
`resolve_secret_mapping` to get {"webhook_url": ..., "signing_secret": ...} either way.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.core.crypto import CryptoError
from app.models.types import get_field_cipher

ENV_PREFIX = "env:"
SM_PREFIX = "sm://"
ENC_PREFIX = "enc:"
WEBHOOK_KEY = "webhook_url"

_SM_NAME = re.compile(r"/secrets/([^/]+)")


class SecretUnavailableError(LookupError):
    """The reference is well-formed but this process cannot read the secret."""


def encrypt_secret(value: str | dict[str, Any]) -> str:
    """Wrap a secret (or a bundle of them) as an `enc:` reference."""
    plaintext = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return ENC_PREFIX + get_field_cipher().encrypt(plaintext)


def secret_scheme(ref: str | None) -> str | None:
    if not ref:
        return None
    for prefix in (ENV_PREFIX, SM_PREFIX, ENC_PREFIX):
        if ref.startswith(prefix):
            return prefix.rstrip(":/")
    return None


def resolve_secret(ref: str | None) -> str | None:
    """The secret behind a reference, or None when the reference is empty."""
    if not ref:
        return None
    if ref.startswith(ENV_PREFIX):
        name = ref[len(ENV_PREFIX) :].strip()
        value = os.environ.get(name)
        if value is None:
            raise SecretUnavailableError(f"environment variable {name!r} is not set")
        return value
    if ref.startswith(SM_PREFIX):
        match = _SM_NAME.search(ref)
        name = (match.group(1) if match else "").replace("-", "_").upper()
        value = os.environ.get(f"SECRET_{name}") if name else None
        if value is None:
            raise SecretUnavailableError(
                f"Secret Manager reference {ref!r} is not mounted in this process"
            )
        return value
    if ref.startswith(ENC_PREFIX):
        try:
            return get_field_cipher().decrypt(ref[len(ENC_PREFIX) :])
        except CryptoError as exc:
            raise SecretUnavailableError(f"stored secret cannot be decrypted: {exc}") from exc
    raise SecretUnavailableError(f"unknown secret reference scheme in {ref[:12]!r}")


def resolve_secret_mapping(ref: str | None) -> dict[str, str]:
    """Resolve a reference into a {name: secret} mapping; a bare string is the webhook URL."""
    raw = resolve_secret(ref)
    if raw is None:
        return {}
    text = raw.strip()
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SecretUnavailableError("stored secret is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise SecretUnavailableError("stored secret must be a JSON object")
        return {str(k): str(v) for k, v in parsed.items() if v is not None}
    return {WEBHOOK_KEY: text}
