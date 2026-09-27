"""Non-LLM services the pipeline steps need (storage, virus scanner, OCR, polite HTTP),
bundled so AgentRunner can hand them to every StepContext.

    services = services_from_settings(settings)                 # production defaults
    services = AgentServices(settings, storage=router, scanner=NoopScanner(), ...)  # tests
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.adapters.http import PoliteClient, StorageArchiver
from app.core.config import Region, Settings, get_settings
from app.core.parsing import OCR
from app.services.ocr import ocr_from_settings
from app.services.scanner import Scanner, scanner_from_settings
from app.services.storage import Storage, StorageRouter

HttpFactory = Callable[[], PoliteClient]


@dataclass(slots=True)
class AgentServices:
    settings: Settings
    storage: StorageRouter
    scanner: Scanner
    ocr: OCR | None = None
    http_factory: HttpFactory | None = None
    # the region whose bucket holds parsed opportunity text (opportunities are global;
    # the collector archives downloads in the opportunity's region)
    _clients: list[PoliteClient] = field(default_factory=list, repr=False)

    def storage_for(self, region: Region | str) -> Storage:
        return self.storage.for_region(region)

    def http(self, region: Region | str = Region.US) -> PoliteClient:
        """A polite client archiving raw bodies in the region's bucket (one per call site;
        callers close it, or `close()` closes every client handed out)."""
        if self.http_factory is not None:
            client = self.http_factory()
        else:
            client = PoliteClient(
                settings=self.settings, archiver=StorageArchiver(self.storage_for(region))
            )
        self._clients.append(client)
        return client

    def close(self) -> None:
        for client in self._clients:
            client.close()
        self._clients.clear()


def services_from_settings(
    settings: Settings | None = None,
    *,
    storage: StorageRouter | None = None,
    scanner: Scanner | None = None,
    ocr: OCR | None = None,
    http_factory: HttpFactory | None = None,
) -> AgentServices:
    settings = settings or get_settings()
    return AgentServices(
        settings=settings,
        storage=storage or StorageRouter(settings),
        scanner=scanner or scanner_from_settings(settings),
        ocr=ocr if ocr is not None else ocr_from_settings(settings),
        http_factory=http_factory,
    )
