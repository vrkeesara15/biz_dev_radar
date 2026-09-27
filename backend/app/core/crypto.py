"""Field-level encryption and masking (SPEC section 11). Pure: no settings, no I/O.

    cipher = FieldCipher.from_base64(FIELD_ENCRYPTION_KEY)     # 32 random bytes, base64
    token = cipher.encrypt("12-3456789")   # "v1:<nonce b64>:<ciphertext+tag b64>"
    cipher.decrypt(token) == "12-3456789"
    mask_last4("12-3456789") == "•••••6789"

AES-256-GCM with a fresh 96-bit nonce per value; the version prefix allows key/algorithm
rotation later (a KMS-wrapped data key drops in behind the same interface).
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12
MASK_CHAR = "•"  # •
MASK_PREFIX = MASK_CHAR * 5
VISIBLE_CHARS = 4


class CryptoError(ValueError):
    """Bad key, malformed token or failed authentication."""


def load_key(encoded: str) -> bytes:
    """Decode a base64 (standard or URL-safe) 32-byte key."""
    text = encoded.strip()
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        try:
            key = base64.urlsafe_b64decode(text)
        except (binascii.Error, ValueError) as exc:
            raise CryptoError("FIELD_ENCRYPTION_KEY must be base64") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(f"FIELD_ENCRYPTION_KEY must decode to {KEY_BYTES} bytes, got {len(key)}")
    return key


def generate_key() -> str:
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


class FieldCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise CryptoError(f"key must be {KEY_BYTES} bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded: str) -> FieldCipher:
        return cls(load_key(encoded))

    def encrypt(self, plaintext: str, *, associated_data: str | None = None) -> str:
        nonce = os.urandom(NONCE_BYTES)
        aad = associated_data.encode() if associated_data else None
        ciphertext = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return ":".join((VERSION, _b64(nonce), _b64(ciphertext)))

    def decrypt(self, token: str, *, associated_data: str | None = None) -> str:
        version, nonce, ciphertext = parse_token(token)
        if version != VERSION:
            raise CryptoError(f"unsupported ciphertext version {version!r}")
        aad = associated_data.encode() if associated_data else None
        try:
            return self._aead.decrypt(nonce, ciphertext, aad).decode("utf-8")
        except InvalidTag as exc:
            raise CryptoError("ciphertext authentication failed (wrong key or tampered)") from exc


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def parse_token(token: str) -> tuple[str, bytes, bytes]:
    parts = token.split(":")
    if len(parts) != 3 or not all(parts):
        raise CryptoError("malformed ciphertext token")
    version, nonce_b64, ct_b64 = parts
    try:
        nonce = base64.b64decode(nonce_b64, validate=True)
        ciphertext = base64.b64decode(ct_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("malformed ciphertext token") from exc
    if len(nonce) != NONCE_BYTES:
        raise CryptoError("malformed ciphertext token")
    return version, nonce, ciphertext


def is_encrypted(value: str | None) -> bool:
    if not value or not value.startswith(VERSION + ":"):
        return False
    try:
        parse_token(value)
    except CryptoError:
        return False
    return True


def mask_last4(value: str | None, *, visible: int = VISIBLE_CHARS) -> str | None:
    """'•••••1234' for any secret; values of `visible` chars or fewer show nothing."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    tail = text[-visible:] if len(text) > visible else ""
    return MASK_PREFIX + tail


def is_masked(value: object) -> bool:
    """True for a value the API produced by masking (client echoed it back unchanged)."""
    return isinstance(value, str) and value.startswith(MASK_CHAR)
