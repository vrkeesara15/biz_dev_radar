"""Small, dependency-free HTML table extractor for portal listing pages (M3). Pure.

    for table in extract_tables(html):
        if table.has_id("table") or "list_table" in table.classes: ...
        for row in table.rows:
            row.cells[4].text, row.cells[4].links

Indian portals (CPPP, GePNIC) render listings as nested `<table>` layouts. Tables are
returned in document order, each with its attributes, its rows and, per cell, the
collapsed text, the text lines (``<br>`` separated) and the anchors (href, text) it
contains. Text inside a nested table belongs to the nested table only, so a layout
table's cell does not swallow the listing it wraps. Never raises on broken markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", unescape(text).replace("\xa0", " ")).strip()


@dataclass(slots=True)
class Link:
    href: str
    text: str


@dataclass(slots=True)
class Cell:
    text: str = ""
    lines: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    is_header: bool = False


@dataclass(slots=True)
class Row:
    cells: list[Cell] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def texts(self) -> list[str]:
        return [c.text for c in self.cells]

    @property
    def is_header(self) -> bool:
        return bool(self.cells) and all(c.is_header for c in self.cells)


@dataclass(slots=True)
class Table:
    attrs: dict[str, str] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)

    @property
    def id(self) -> str | None:
        return self.attrs.get("id")

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def has_id(self, value: str) -> bool:
        return self.id == value

    @property
    def header(self) -> list[str]:
        """Texts of the first row made of <th> cells (or of the first row when none)."""
        for row in self.rows:
            if row.is_header:
                return row.texts
        return self.rows[0].texts if self.rows else []

    @property
    def body(self) -> list[Row]:
        """Rows that are not pure header rows."""
        return [row for row in self.rows if not row.is_header]


class _Frame:
    __slots__ = ("cell", "cell_line", "link", "link_text", "row", "table")

    def __init__(self, attrs: dict[str, str]) -> None:
        self.table = Table(attrs=attrs)
        self.row: Row | None = None
        self.cell: Cell | None = None
        self.cell_line: list[str] = []
        self.link: Link | None = None
        self.link_text: list[str] = []


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._stack: list[_Frame] = []
        self._skip = 0

    # -- helpers
    @property
    def _top(self) -> _Frame | None:
        return self._stack[-1] if self._stack else None

    def _close_cell(self, frame: _Frame) -> None:
        if frame.cell is None:
            return
        self._flush_line(frame)
        frame.cell.text = _clean(" ".join(frame.cell.lines))
        if frame.row is not None:
            frame.row.cells.append(frame.cell)
        frame.cell = None

    def _flush_line(self, frame: _Frame) -> None:
        if frame.cell is None:
            return
        line = _clean("".join(frame.cell_line))
        if line:
            frame.cell.lines.append(line)
        frame.cell_line = []

    def _close_row(self, frame: _Frame) -> None:
        self._close_cell(frame)
        if frame.row is not None:
            if frame.row.cells:
                frame.table.rows.append(frame.row)
            frame.row = None

    def _close_link(self, frame: _Frame) -> None:
        if frame.link is None:
            return
        frame.link.text = _clean("".join(frame.link_text))
        if frame.cell is not None:
            frame.cell.links.append(frame.link)
        frame.link = None
        frame.link_text = []

    # -- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        attributes = {k: (v or "") for k, v in attrs}
        if tag == "table":
            self._stack.append(_Frame(attributes))
            return
        frame = self._top
        if frame is None:
            return
        if tag == "tr":
            self._close_row(frame)
            frame.row = Row(attrs=attributes)
        elif tag in ("td", "th"):
            if frame.row is None:  # GePNIC emits <td> straight under <table>/<span>
                frame.row = Row()
            self._close_cell(frame)
            frame.cell = Cell(is_header=tag == "th")
        elif tag == "br" and frame.cell is not None:
            self._flush_line(frame)
        elif tag == "a" and frame.cell is not None:
            self._close_link(frame)
            frame.link = Link(href=unescape(attributes.get("href", "")).strip(), text="")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(self._skip - 1, 0)
            return
        frame = self._top
        if frame is None:
            return
        if tag == "table":
            self._close_row(frame)
            self.tables.append(frame.table)
            self._stack.pop()
        elif tag == "tr":
            self._close_row(frame)
        elif tag in ("td", "th"):
            self._close_link(frame)
            self._close_cell(frame)
        elif tag == "a":
            self._close_link(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        frame = self._top
        if self._skip or frame is None or frame.cell is None:
            return
        frame.cell_line.append(data)
        if frame.link is not None:
            frame.link_text.append(data)

    def close(self) -> None:
        super().close()
        while self._stack:  # unclosed tables at EOF still count
            frame = self._stack.pop()
            self._close_row(frame)
            self.tables.append(frame.table)


def extract_tables(html: str | None) -> list[Table]:
    """Every table in the document (innermost tables first when nested)."""
    if not html:
        return []
    parser = _TableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient; keep what was parsed
        pass
    return parser.tables


def find_table(
    html: str | None, *, table_id: str | None = None, css_class: str | None = None
) -> Table | None:
    """First table matching id or class."""
    for table in extract_tables(html):
        if table_id is not None and table.has_id(table_id):
            return table
        if css_class is not None and css_class in table.classes:
            return table
    return None


def tables_with_header(html: str | None, *needles: str) -> list[Table]:
    """Tables whose header row mentions every needle (case-insensitive substring)."""
    wanted = [n.lower() for n in needles]
    out: list[Table] = []
    for table in extract_tables(html):
        header = " | ".join(table.header).lower()
        if all(n in header for n in wanted):
            out.append(table)
    return out
