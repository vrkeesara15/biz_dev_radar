"""Markdown -> sanitised HTML for draft bodies (SPEC 8, 10.4 TipTap editor). Pure.

The drafters write Markdown; the workspace stores and edits HTML. Both directions go
through `sanitize_html`, an allowlist clean (nh3, the Rust ammonia bindings), so neither
a model nor a writer can smuggle script, style, iframes, event handlers or javascript:
URLs into a draft that is later exported or rendered.

    body_html = markdown_to_html(body_markdown)      # agent output
    body_html = sanitize_html(user_html)             # writer's TipTap output
    body_text = html_to_text(body_html)              # for grounding and diffs
"""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

# Tags a proposal section needs and nothing more (no img: exports embed nothing remote).
ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "s",
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "a",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    # `rel` is managed by nh3 itself (link_rel below), so it is not listed here
    "a": {"href", "title"},
    # the workspace marks citations and [NEEDS INPUT] chips with a class / data token
    "span": {"class", "data-token"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "code": {"class"},
}
ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}

# commonmark, no raw HTML pass-through and no typographic rewriting of quotes/dashes
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})


def markdown_to_html(markdown: str) -> str:
    """Render Markdown to HTML and sanitise the result."""
    return sanitize_html(_MD.render(markdown or ""))


def sanitize_html(html: str) -> str:
    """Allowlist-clean HTML (tags, attributes and URL schemes)."""
    return nh3.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    ).strip()
