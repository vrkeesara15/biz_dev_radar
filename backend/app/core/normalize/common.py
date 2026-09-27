"""Source-agnostic pure helpers: HTML to text, file names from headers/URLs."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "table",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "blockquote",
    "pre",
    "hr",
}
_SKIP_TAGS = {"script", "style", "head", "title", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None) -> str | None:
    """Readable plain text from an HTML fragment; None when nothing is left.

    Block tags become newlines, list items get a dash, whitespace is collapsed and
    entities decoded. Plain text passes through unchanged (minus whitespace tidy-up).
    """
    if html is None:
        return None
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return cleaned or None


_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:[\w-]+)?''([^;]+)", re.IGNORECASE)
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def file_name_from_content_disposition(header: str | None) -> str | None:
    """RFC 6266: prefer filename*=UTF-8''..., then filename=...; strip any path part."""
    if not header:
        return None
    match = _FILENAME_STAR.search(header) or _FILENAME.search(header)
    if not match:
        return None
    name = unquote(match.group(1)).strip().strip('"')
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or None


_GENERIC_SEGMENTS = {"download", "file", "files", "attachment", "view", "get", "content"}


def file_name_from_url(url: str) -> str | None:
    """Last meaningful path segment (skips generic tails like /download); None if empty."""
    path = unquote(urlsplit(url).path)
    segments = [seg for seg in path.split("/") if seg]
    while segments and segments[-1].lower() in _GENERIC_SEGMENTS:
        segments.pop()
    return segments[-1] if segments else None
