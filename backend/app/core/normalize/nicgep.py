"""Listing-page parsers shared by the NIC GePNIC family (CPPP and the state portals). Pure.

CPPP (eprocure.gov.in) and every GePNIC state portal run the same NIC application, so
their public, captcha-free pages share three table shapes:

- tender listing   header: Sl.No | e-Published Date | Bid Submission Closing Date |
                   Tender Opening Date | Title/Ref.No./Tender Id | Organisation Name/Chain
                   [| Corrigendum]                       -> `parse_listing_table`
- home "Latest Tenders" marquee (id=activeTenders, no header):
                   Title | Reference No | Closing Date | Bid Opening Date
                                                        -> `parse_home_latest`
- organisation index  header: S.No | Organisation Name | Tender Count (count is a link to
                   that organisation's listing)          -> `parse_org_index`

Each parser returns the rows it could read plus human-readable notes for anything that
looked wrong (missing table = layout change, short rows, unparseable dates). Dates are
parsed with `core.dates.parse_in` (IST) after trying the portal's own strftime formats.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.core.dates import IST, ParsedDateTime, parse_in, to_utc
from app.core.normalize.html_tables import Cell, Row, Table, extract_tables

DEFAULT_DATE_FORMATS: tuple[str, ...] = ("%d-%b-%Y %I:%M %p", "%d-%b-%Y")
TENDER_ID_RE = re.compile(r"^\d{4}_[A-Za-z0-9]+_\d+_\d+$")
_LEADING_INDEX = re.compile(r"^\d+\.\s*")
CPPP_TOKEN_SEPARATOR = "A13h1"


# --- dates ---------------------------------------------------------------------------------


def parse_portal_datetime(
    text: str | None, *, formats: Sequence[str] = DEFAULT_DATE_FORMATS, tz: str = IST
) -> ParsedDateTime | None:
    """Try the portal's declared strftime formats first, then the generic Indian parser."""
    if not text:
        return None
    cleaned = " ".join(text.replace("\xa0", " ").split())
    if not cleaned or cleaned in {"--", "-", "NA", "N/A"}:
        return None
    for fmt in formats:
        try:
            naive = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        has_time = any(code in fmt for code in ("%H", "%I", "%M"))
        return ParsedDateTime(to_utc(naive, tz), tz, has_time)
    return parse_in(cleaned, tz)


# --- rows ----------------------------------------------------------------------------------


@dataclass(slots=True)
class TenderRow:
    """One tender as listed; every field optional except the title."""

    title: str
    reference: str | None = None
    tender_id: str | None = None
    organisation: str | None = None
    published: str | None = None
    closing: str | None = None
    opening: str | None = None
    detail_url: str | None = None
    corrigendum: bool = False
    raw_cells: list[str] = field(default_factory=list)

    @property
    def organisation_chain(self) -> list[str]:
        return split_organisation_chain(self.organisation)


@dataclass(slots=True)
class OrganisationRow:
    name: str
    count: int | None
    url: str | None


@dataclass(slots=True)
class ParseOutcome[T]:
    rows: list[T] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    found: bool = False  # the expected table exists on the page


def split_organisation_chain(value: str | None) -> list[str]:
    """'Ministry of Finance||Department of Expenditure||PPD' -> the three levels.

    GePNIC separates levels with '||' (sometimes '|' or ' > '); a plain name is one level.
    Commas are NOT separators: 'Bharat Heavy Electricals Limited, Hyderabad' is one org."""
    if not value:
        return []
    parts = re.split(r"\s*(?:\|\|+|\||>|»)\s*", value)
    return [p.strip() for p in parts if p and p.strip()]


def _header_index(header: Sequence[str], *needles: str) -> int | None:
    lowered = [h.lower() for h in header]
    for needle in needles:
        for index, text in enumerate(lowered):
            if needle in text:
                return index
    return None


def _split_title_cell(cell: Cell) -> tuple[str, str | None, str | None]:
    """Title cell 'Title/Ref/Id' (CPPP) or 'Title [ref] [id]' lines (GePNIC).

    The anchor text is the title; whatever follows it is the reference and, when the
    last token looks like a tender id (2026_ABC_123456_1 or a bare number), the id."""
    title = cell.links[0].text if cell.links and cell.links[0].text else ""
    rest = cell.text
    if title and rest.startswith(title):
        rest = rest[len(title) :]
    elif title and title in rest:
        rest = rest.replace(title, "", 1)
    if not title:
        # no anchor: first line is the title
        title = cell.lines[0] if cell.lines else cell.text
        rest = cell.text[len(title) :] if cell.text.startswith(title) else ""
    tokens = [t for t in re.split(r"[\[\]/|]+|\s{2,}", rest.strip()) if t.strip()]
    tokens = [t.strip() for t in tokens]
    if not tokens:
        return title.strip(), None, None
    tender_id: str | None = None
    if len(tokens) >= 2 and (TENDER_ID_RE.match(tokens[-1]) or tokens[-1].isdigit()):
        tender_id = tokens.pop()
    elif len(tokens) >= 2:
        tender_id = tokens.pop()  # CPPP appends an org/tender code as the last segment
    reference = "/".join(tokens) if tokens else None
    return title.strip(), reference, tender_id


def _is_header_like(row: Row, header: Sequence[str]) -> bool:
    return row.texts == list(header)


def parse_listing_table(html: str | None, *, table: Table | None = None) -> ParseOutcome[TenderRow]:
    """The 6/7-column tender listing (CPPP latest active tenders, GePNIC organisation
    listing). Rows without a title are skipped with a note."""
    out: ParseOutcome[TenderRow] = ParseOutcome()
    if table is None:
        table = _listing_table(html)
    if table is None:
        out.notes.append(
            "no tender listing table (header with 'Title' and 'Closing') on the page "
            "(layout change?)"
        )
        return out
    out.found = True
    header = table.header
    i_title = _header_index(header, "title")
    i_pub = _header_index(header, "published")
    i_close = _header_index(header, "closing")
    i_open = _header_index(header, "opening")
    i_org = _header_index(header, "organisation", "organization")
    i_corr = _header_index(header, "corrigendum")
    if i_title is None:
        out.notes.append(f"listing header has no title column: {header}")
        return out
    skipped = 0
    for row in table.body:
        if _is_header_like(row, header):
            continue
        cells = row.cells
        if len(cells) <= i_title or not cells[i_title].text:
            skipped += 1
            continue
        title, reference, tender_id = _split_title_cell(cells[i_title])
        if not title:
            skipped += 1
            continue

        def text_at(index: int | None, cells: list[Cell] = cells) -> str | None:
            if index is None or index >= len(cells):
                return None
            value = cells[index].text
            return value if value and value not in {"--", "-"} else None

        corr_text = text_at(i_corr)
        out.rows.append(
            TenderRow(
                title=title,
                reference=reference,
                tender_id=tender_id,
                organisation=text_at(i_org),
                published=text_at(i_pub),
                closing=text_at(i_close),
                opening=text_at(i_open),
                detail_url=cells[i_title].links[0].href if cells[i_title].links else None,
                corrigendum=bool(corr_text),
                raw_cells=row.texts,
            )
        )
    if skipped:
        out.notes.append(f"{skipped} listing row(s) without a title skipped")
    return out


def _listing_table(html: str | None) -> Table | None:
    for table in extract_tables(html):
        header = " | ".join(table.header).lower()
        if "title" in header and ("closing" in header or "tender id" in header):
            return table
    return None


def parse_home_latest(
    html: str | None, *, table_id: str = "activeTenders"
) -> ParseOutcome[TenderRow]:
    """GePNIC front page 'Latest Tenders' marquee: Title | Reference No | Closing | Opening."""
    out: ParseOutcome[TenderRow] = ParseOutcome()
    table = next((t for t in extract_tables(html) if t.has_id(table_id)), None)
    if table is None:
        out.notes.append(f"no '{table_id}' latest-tenders table on the home page (layout change?)")
        return out
    out.found = True
    skipped = 0
    for row in table.rows:
        cells = row.cells
        if len(cells) < 3:
            skipped += 1
            continue
        title = _LEADING_INDEX.sub("", cells[0].links[0].text if cells[0].links else cells[0].text)
        if not title:
            skipped += 1
            continue
        out.rows.append(
            TenderRow(
                title=title.strip(),
                reference=cells[1].text or None,
                closing=cells[2].text or None,
                opening=cells[3].text if len(cells) > 3 and cells[3].text else None,
                detail_url=cells[0].links[0].href if cells[0].links else None,
                raw_cells=row.texts,
            )
        )
    if skipped:
        out.notes.append(f"{skipped} latest-tender row(s) without a title skipped")
    return out


def parse_org_index(html: str | None) -> ParseOutcome[OrganisationRow]:
    """S.No | Organisation Name | Tender Count (count links to the organisation listing)."""
    out: ParseOutcome[OrganisationRow] = ParseOutcome()
    table: Table | None = None
    for candidate in extract_tables(html):
        joined = " | ".join(candidate.header).lower()
        if "organisation" in joined and "count" in joined:
            table = candidate
            break
    if table is None:
        out.notes.append("no organisation index table ('Organisation Name' + 'Tender Count')")
        return out
    out.found = True
    header = table.header
    i_name = _header_index(header, "organisation", "organization")
    i_count = _header_index(header, "count")
    assert i_name is not None and i_count is not None
    for row in table.body:
        if _is_header_like(row, header):
            continue
        cells = row.cells
        if len(cells) <= max(i_name, i_count) or not cells[i_name].text:
            continue
        count_cell = cells[i_count]
        digits = re.sub(r"\D", "", count_cell.text)
        out.rows.append(
            OrganisationRow(
                name=cells[i_name].text,
                count=int(digits) if digits else None,
                url=count_cell.links[0].href if count_cell.links else None,
            )
        )
    return out


# --- CPPP detail-link token -----------------------------------------------------------------


def decode_cppp_token(url: str | None) -> dict[str, str]:
    """eprocure.gov.in/cppp/tendersfullview/<token>: 'A13h1'-separated base64 segments.

    Observed layout: [internal tender id, hash, hash, unix expiry, reference, tender code].
    Returns {"internal_id", "expires", "reference", "code"} for the segments that decode;
    an unrelated URL gives {}. The internal id is the stable external_id."""
    if not url or "tendersfullview/" not in url:
        return {}
    token = url.rsplit("tendersfullview/", 1)[1].split("?", 1)[0]
    segments = token.split(CPPP_TOKEN_SEPARATOR)
    decoded: list[str] = []
    for segment in segments:
        try:
            decoded.append(base64.b64decode(segment, validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            decoded.append("")
    out: dict[str, str] = {}
    if decoded and decoded[0].isdigit():
        out["internal_id"] = decoded[0]
    if len(decoded) > 3 and decoded[3].isdigit():
        out["expires"] = decoded[3]
    if len(decoded) > 4 and decoded[4]:
        out["reference"] = decoded[4]
    if len(decoded) > 5 and decoded[5]:
        out["code"] = decoded[5]
    return out
