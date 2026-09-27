"""M1-11: Scanner protocol, ClamAV INSTREAM client (against a loopback fake clamd), Noop."""

from __future__ import annotations

import asyncio
import struct

import pytest
from app.core.config import Settings
from app.services.scanner import (
    ClamAVScanner,
    NoopScanner,
    Scanner,
    ScannerUnavailableError,
    ScanResult,
    get_scanner,
    parse_clamd_reply,
    scanner_from_settings,
    set_scanner,
)

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamd:
    """Speaks enough of clamd: zINSTREAM with length-prefixed chunks, one reply."""

    def __init__(self, *, reply: bytes | None = None) -> None:
        self.reply = reply
        self.received: list[bytes] = []
        self.server: asyncio.base_events.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        command = await reader.readuntil(b"\0")
        assert command == b"zINSTREAM\0"
        payload = bytearray()
        while True:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            if length == 0:
                break
            payload += await reader.readexactly(length)
        self.received.append(bytes(payload))
        if self.reply is not None:
            writer.write(self.reply)
        elif EICAR in payload:
            writer.write(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
        else:
            writer.write(b"stream: OK\0")
        await writer.drain()
        writer.close()

    async def __aenter__(self) -> FakeClamd:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self

    @property
    def port(self) -> int:
        assert self.server is not None
        return int(self.server.sockets[0].getsockname()[1])

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


async def test_noop_scanner_is_clean() -> None:
    scanner = NoopScanner()
    assert isinstance(scanner, Scanner)
    assert await scanner.scan(EICAR) == ScanResult(clean=True, signature=None, scanner="noop")


async def test_clamav_clean_and_infected_over_instream() -> None:
    async with FakeClamd() as clamd:
        scanner = ClamAVScanner(host="127.0.0.1", port=clamd.port, timeout=5)
        assert isinstance(scanner, Scanner)
        clean = await scanner.scan(b"%PDF-1.7 harmless")
        assert clean == ScanResult(clean=True, scanner="clamav")
        infected = await scanner.scan(b"prefix" + EICAR + b"suffix")
        assert infected.clean is False
        assert infected.signature == "Win.Test.EICAR_HDB-1"
        assert infected.scanner == "clamav"
        big = bytes(range(256)) * 1024  # 256 KiB -> several INSTREAM chunks
        assert (await scanner.scan(big)).clean
        assert clamd.received[-1] == big


async def test_clamav_failures_fail_closed() -> None:
    async with FakeClamd(reply=b"stream: something weird\0") as clamd:
        scanner = ClamAVScanner(host="127.0.0.1", port=clamd.port, timeout=5)
        with pytest.raises(ScannerUnavailableError):
            await scanner.scan(b"data")
    async with FakeClamd() as clamd:
        port = clamd.port
    unreachable = ClamAVScanner(host="127.0.0.1", port=port, timeout=2)
    with pytest.raises(ScannerUnavailableError):
        await unreachable.scan(b"data")


def test_parse_clamd_reply() -> None:
    assert parse_clamd_reply("stream: OK").clean
    found = parse_clamd_reply("stream: Eicar-Test-Signature FOUND")
    assert (found.clean, found.signature) == (False, "Eicar-Test-Signature")
    assert parse_clamd_reply("FOUND").signature == "unknown"
    with pytest.raises(ScannerUnavailableError):
        parse_clamd_reply("INSTREAM size limit exceeded. ERROR")


def test_scanner_from_settings() -> None:
    noop = scanner_from_settings(Settings(_env_file=None, scanner_backend="noop"))  # type: ignore[call-arg]
    assert isinstance(noop, NoopScanner)
    clam = scanner_from_settings(
        Settings(  # type: ignore[call-arg]
            _env_file=None, scanner_backend="clamav", clamav_host="clam", clamav_port=3311
        )
    )
    assert isinstance(clam, ClamAVScanner) and (clam.host, clam.port) == ("clam", 3311)
    assert clam.unix_socket is None
    sock = scanner_from_settings(
        Settings(_env_file=None, scanner_backend="clamav", clamav_unix_socket="/run/clamd")  # type: ignore[call-arg]
    )
    assert isinstance(sock, ClamAVScanner) and sock.unix_socket == "/run/clamd"
    set_scanner(noop)
    try:
        assert get_scanner() is noop
    finally:
        set_scanner(None)
