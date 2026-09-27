"""M1-01: AES-GCM field encryption, token format, masking."""

import base64

import pytest
from app.core.crypto import (
    KEY_BYTES,
    MASK_PREFIX,
    CryptoError,
    FieldCipher,
    generate_key,
    is_encrypted,
    is_masked,
    load_key,
    mask_last4,
    parse_token,
)

KEY_B64 = base64.b64encode(bytes(range(32))).decode()


def test_round_trip_and_fresh_nonce_per_value() -> None:
    cipher = FieldCipher.from_base64(KEY_B64)
    a = cipher.encrypt("12-3456789")
    b = cipher.encrypt("12-3456789")
    assert a != b, "GCM nonce must be random per encryption"
    assert a.startswith("v1:") and b.startswith("v1:")
    assert cipher.decrypt(a) == cipher.decrypt(b) == "12-3456789"
    assert cipher.decrypt(cipher.encrypt("")) == ""
    assert cipher.decrypt(cipher.encrypt("ünïcødé ₹")) == "ünïcødé ₹"
    version, nonce, ciphertext = parse_token(a)
    assert version == "v1" and len(nonce) == 12 and len(ciphertext) == len(b"12-3456789") + 16


def test_wrong_key_or_tamper_fails() -> None:
    cipher = FieldCipher.from_base64(KEY_B64)
    other = FieldCipher.from_base64(generate_key())
    token = cipher.encrypt("ABCDE1234F")
    with pytest.raises(CryptoError):
        other.decrypt(token)
    version, nonce, ct = token.split(":")
    flipped = bytearray(base64.b64decode(ct))
    flipped[0] ^= 0x01
    tampered = ":".join((version, nonce, base64.b64encode(bytes(flipped)).decode()))
    with pytest.raises(CryptoError):
        cipher.decrypt(tampered)
    with pytest.raises(CryptoError):
        cipher.decrypt("v2:" + token[3:])
    for bad in ("", "v1:", "v1:a", "v1:a:b:c", "plaintext", "v1:!!!:###", "v1:AAAA:AAAA"):
        with pytest.raises(CryptoError):
            cipher.decrypt(bad)
        assert not is_encrypted(bad)
    assert is_encrypted(token)
    assert not is_encrypted(None)


def test_associated_data_binds_the_context() -> None:
    cipher = FieldCipher.from_base64(KEY_B64)
    token = cipher.encrypt("secret", associated_data="profile:1:ein")
    assert cipher.decrypt(token, associated_data="profile:1:ein") == "secret"
    with pytest.raises(CryptoError):
        cipher.decrypt(token, associated_data="profile:2:ein")
    with pytest.raises(CryptoError):
        cipher.decrypt(token)


def test_key_loading() -> None:
    assert len(load_key(KEY_B64)) == KEY_BYTES
    assert load_key(base64.urlsafe_b64encode(bytes(range(32))).decode()) == bytes(range(32))
    assert len(load_key(generate_key())) == KEY_BYTES
    with pytest.raises(CryptoError, match="32 bytes"):
        load_key(base64.b64encode(b"short").decode())
    with pytest.raises(CryptoError, match="base64"):
        load_key("not base64!!")
    with pytest.raises(CryptoError):
        FieldCipher(b"short")


def test_mask_last4() -> None:
    assert mask_last4("12-3456789") == MASK_PREFIX + "6789" == "•••••6789"
    assert mask_last4("ABCDE1234F") == "•••••234F"
    assert mask_last4("1234") == "•••••"
    assert mask_last4("12") == "•••••"
    assert mask_last4("  ") is None
    assert mask_last4(None) is None
    assert mask_last4("12-3456789", visible=2) == "•••••89"
    assert is_masked("•••••6789") and is_masked("•••••")
    assert not is_masked("12-3456789") and not is_masked(None) and not is_masked(1234)
