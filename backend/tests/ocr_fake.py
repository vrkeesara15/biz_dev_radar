"""FakeOCR for tests: returns canned text per call and records every image it saw."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.parsing.types import DEFAULT_OCR_LANGUAGES


@dataclass
class FakeOCR:
    texts: list[str] = field(default_factory=lambda: ["OCR PAGE TEXT"])
    calls: list[tuple[int, str]] = field(default_factory=list)  # (png size, languages)

    def image_to_text(self, png: bytes, *, languages: str = DEFAULT_OCR_LANGUAGES) -> str:
        assert png.startswith(b"\x89PNG"), "OCR must receive PNG bytes"
        self.calls.append((len(png), languages))
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]
