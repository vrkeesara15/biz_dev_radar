"""core.html_text: TipTap boilerplate and web pages -> plain text."""

from app.core.html_text import html_title, html_to_text

PAGE = """<html><head><title> Acme  Federal </title><style>p{}</style>
<script>alert('x')</script></head><body>
<nav><a href="/">Home</a></nav>
<h1>Acme Federal LLC</h1>
<p>We deliver <b>cloud</b> &amp; data services.<br>Founded 2009.</p>
<ul><li>NAICS 541512</li><li>NAICS 541519</li></ul>
<table><tr><td>Phone</td><td>+1 555 0100</td></tr></table>
<noscript>enable js</noscript>
</body></html>"""


def test_html_to_text_drops_scripts_styles_and_keeps_block_structure() -> None:
    text = html_to_text(PAGE)
    assert "alert" not in text and "p{}" not in text and "enable js" not in text
    lines = text.split("\n")
    assert "Acme Federal LLC" in lines
    assert "We deliver cloud & data services." in lines
    assert "Founded 2009." in lines
    assert "NAICS 541512" in lines and "NAICS 541519" in lines
    assert "Phone" in lines and "+1 555 0100" in lines
    assert "\n\n\n" not in text
    assert html_title(PAGE) == "Acme Federal"


def test_plain_text_and_entities_pass_through() -> None:
    assert html_to_text("") == ""
    assert html_to_text("Tom &amp; Jerry   rule") == "Tom & Jerry rule"
    assert html_to_text("<p>a</p><p>b</p>") == "a\n\nb"
    assert html_title("<p>no title</p>") is None
