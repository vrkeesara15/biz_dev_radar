"""SQLAlchemy column types. EncryptedString stores AES-GCM tokens (app.core.crypto) in a
TEXT column and hands plaintext to the ORM; the API layer masks it (SPEC section 11)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.crypto import FieldCipher

_cipher: FieldCipher | None = None


def get_field_cipher() -> FieldCipher:
    """Process-wide cipher keyed from FIELD_ENCRYPTION_KEY (lazy so imports stay pure)."""
    global _cipher
    if _cipher is None:
        from app.core.config import get_settings

        _cipher = FieldCipher.from_base64(get_settings().field_encryption_key)
    return _cipher


def set_field_cipher(cipher: FieldCipher | None) -> None:
    global _cipher
    _cipher = cipher


class EncryptedString(TypeDecorator[str]):
    """Plaintext in Python, `v1:<nonce>:<ciphertext>` in the database."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return get_field_cipher().encrypt(str(value))

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return get_field_cipher().decrypt(value)
