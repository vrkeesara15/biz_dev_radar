"""M2-05: HTML to text and file-name inference helpers."""

from app.core.normalize.common import (
    file_name_from_content_disposition,
    file_name_from_url,
    html_to_text,
)
from app.core.normalize.sam import description_from_payload


def test_html_to_text_blocks_lists_entities_and_scripts() -> None:
    html = (
        "<p><b>Title</b></p><p>Intro&nbsp;text &ndash; more.</p>"
        "<ul><li>One</li><li>Two</li></ul><script>alert(1)</script><style>p{}</style>"
        "<div>Tail   with   spaces</div>"
    )
    assert (
        html_to_text(html) == "Title\n\nIntro text \u2013 more.\n\n- One\n- Two\n\nTail with spaces"
    )
    assert html_to_text("plain   text") == "plain text"
    assert html_to_text("") is None and html_to_text(None) is None
    assert html_to_text("<p></p><br/>") is None


def test_file_name_from_content_disposition() -> None:
    assert file_name_from_content_disposition('attachment; filename="SOW.pdf"') == "SOW.pdf"
    assert file_name_from_content_disposition("attachment; filename=plan.docx") == "plan.docx"
    assert (
        file_name_from_content_disposition("attachment; filename*=UTF-8''Q%26A%20v2.pdf")
        == "Q&A v2.pdf"
    )
    assert file_name_from_content_disposition('inline; filename="C:\\docs\\x.xlsx"') == "x.xlsx"
    assert file_name_from_content_disposition("inline") is None
    assert file_name_from_content_disposition(None) is None


def test_file_name_from_url() -> None:
    sam = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/0a1b2c/download?&token="
    assert file_name_from_url(sam) == "0a1b2c"
    assert file_name_from_url("https://x.gov/docs/RFP%20Final.pdf?sig=1") == "RFP Final.pdf"
    assert file_name_from_url("https://x.gov/") is None
    assert file_name_from_url("https://x.gov/download") is None


def test_description_from_payload() -> None:
    assert description_from_payload({"description": "<p>Hi<br>there</p>"}) == "Hi\nthere"
    assert description_from_payload({"description": "Description not found."}) is None
    assert description_from_payload({"description": None}) is None
    assert description_from_payload("<p>raw html body</p>") == "raw html body"
    assert description_from_payload(["unexpected"]) is None
