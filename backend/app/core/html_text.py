"""HTML -> plain text (pure). Used for TipTap boilerplate bodies when they are chunked
into the knowledge base and for company web pages in profile autofill. Scripts, styles
and markup are dropped; block elements become line breaks; entities are decoded."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})
_BLOCK = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "hr",
        "dt",
        "dd",
        "figcaption",
    }
)
_BLANK_RE = re.compile("[ \\t\\f\\v\\u00a0]+")
_LINES_RE = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None and data.strip():
            self.title = " ".join(data.split())
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML fragment or page, block elements separated by newlines."""
    if not html:
        return ""
    if "<" not in html:
        return _tidy(unescape(html))
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return _tidy("".join(parser.parts))


def html_title(html: str) -> str | None:
    parser = _TextExtractor()
    parser.feed(html or "")
    parser.close()
    return parser.title


def _tidy(text: str) -> str:
    lines = [_BLANK_RE.sub(" ", line).strip() for line in text.replace("\r", "").split("\n")]
    return _LINES_RE.sub("\n\n", "\n".join(lines)).strip()
