"""Virus scanning before parsing or storing an upload (SPEC section 11).

    result = await get_scanner().scan(data)
    if not result.clean: reject and audit

ClamAVScanner speaks clamd's INSTREAM protocol over TCP or a Unix socket (no client library
needed); NoopScanner is for local dev and tests only and says so in its name.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import ScannerBackend, Settings, get_settings

CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT = 60.0


class ScannerUnavailableError(RuntimeError):
    """The scanner could not be reached or answered garbage; uploads must fail closed."""


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    signature: str | None = None
    scanner: str = "noop"


@runtime_checkable
class Scanner(Protocol):
    name: str

    async def scan(self, data: bytes) -> ScanResult: ...


class NoopScanner:
    name = "noop"

    async def scan(self, data: bytes) -> ScanResult:
        return ScanResult(clean=True, scanner=self.name)


class ClamAVScanner:
    name = "clamav"

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 3310,
        unix_socket: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.unix_socket = unix_socket or None
        self.timeout = timeout

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.unix_socket:
            return await asyncio.open_unix_connection(self.unix_socket)
        return await asyncio.open_connection(self.host, self.port)

    async def scan(self, data: bytes) -> ScanResult:
        try:
            return await asyncio.wait_for(self._scan(data), self.timeout)
        except ScannerUnavailableError:
            raise
        except (OSError, asyncio.IncompleteReadError, TimeoutError) as exc:
            raise ScannerUnavailableError(f"clamd unreachable: {exc}") from exc

    async def _scan(self, data: bytes) -> ScanResult:
        reader, writer = await self._connect()
        try:
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(data), CHUNK_SIZE):
                chunk = data[offset : offset + CHUNK_SIZE]
                writer.write(struct.pack("!I", len(chunk)) + chunk)
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            raw = await reader.readuntil(b"\0")
        finally:
            writer.close()
            await writer.wait_closed()
        return parse_clamd_reply(raw.rstrip(b"\0").decode("utf-8", "replace"))


def parse_clamd_reply(reply: str) -> ScanResult:
    """'stream: OK' -> clean; 'stream: Eicar-Test-Signature FOUND' -> infected."""
    text = reply.strip()
    if text.endswith("OK"):
        return ScanResult(clean=True, scanner="clamav")
    if text.endswith("FOUND"):
        body = text[: -len("FOUND")].strip()
        signature = body.split(":", 1)[1].strip() if ":" in body else body
        return ScanResult(clean=False, signature=signature or "unknown", scanner="clamav")
    raise ScannerUnavailableError(f"unexpected clamd reply: {text!r}")


def scanner_from_settings(settings: Settings | None = None) -> Scanner:
    settings = settings or get_settings()
    if settings.scanner_backend is ScannerBackend.CLAMAV:
        return ClamAVScanner(
            host=settings.clamav_host,
            port=settings.clamav_port,
            unix_socket=settings.clamav_unix_socket or None,
        )
    return NoopScanner()


_scanner: Scanner | None = None


def get_scanner() -> Scanner:
    global _scanner
    if _scanner is None:
        _scanner = scanner_from_settings()
    return _scanner


def set_scanner(scanner: Scanner | None) -> None:
    global _scanner
    _scanner = scanner
