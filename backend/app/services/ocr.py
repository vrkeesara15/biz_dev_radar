"""OCR implementations behind the core `OCR` protocol (SPEC 10.1: Tesseract eng+hin).

`TesseractOCR` needs the `tesseract` binary (present in the Docker image, absent on most
laptops: OQ-10) and pytesseract; both are import-guarded so the API and tests never need
them. `ocr_from_settings` returns None when OCR_BACKEND=none.
"""

from __future__ import annotations

import io
from typing import Any

from app.core.config import Settings, get_settings
from app.core.parsing.types import DEFAULT_OCR_LANGUAGES, OCR

__all__ = ["OCR", "OCRUnavailableError", "TesseractOCR", "ocr_from_settings"]


class OCRUnavailableError(RuntimeError):
    pass


class TesseractOCR:
    def __init__(self, *, languages: str = DEFAULT_OCR_LANGUAGES, tesseract_cmd: str = "") -> None:
        self.languages = languages
        self.tesseract_cmd = tesseract_cmd
        self._module: Any | None = None

    def _pytesseract(self) -> Any:
        if self._module is None:
            try:
                import pytesseract
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise OCRUnavailableError("pytesseract is not installed") from exc
            if self.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
            self._module = pytesseract
        return self._module

    def available(self) -> bool:
        try:
            self._pytesseract().get_tesseract_version()
        except Exception:
            return False
        return True

    def image_to_text(self, png: bytes, *, languages: str | None = None) -> str:
        from PIL import Image

        module = self._pytesseract()
        try:
            with Image.open(io.BytesIO(png)) as image:
                text: str = module.image_to_string(image, lang=languages or self.languages)
        except module.TesseractNotFoundError as exc:
            raise OCRUnavailableError("tesseract binary not found") from exc
        return text


def ocr_from_settings(settings: Settings | None = None) -> OCR | None:
    settings = settings or get_settings()
    if settings.ocr_backend == "tesseract":
        return TesseractOCR(languages=settings.ocr_languages, tesseract_cmd=settings.tesseract_cmd)
    return None
