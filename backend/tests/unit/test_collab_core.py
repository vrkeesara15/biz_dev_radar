"""[NEEDS INPUT] reading and the collaboration vocabulary (SPEC 8, 9; M6-07). Pure."""

from __future__ import annotations

from app.core.collab import (
    TARGET_TYPES,
    TASK_SOURCES,
    TASK_STATUSES,
    find_placeholders,
    placeholder_key,
    task_title,
)


def test_placeholders_are_found_in_order_and_deduplicated() -> None:
    text = (
        "Rate for [NEEDS INPUT: labor category] at [NEEDS INPUT: price].\n"
        "Confirm [NEEDS INPUT: labor category] again and [NEEDS INPUT: EMD amount]."
    )
    assert find_placeholders(text) == ["labor category", "price", "EMD amount"]


def test_matching_is_case_and_space_tolerant() -> None:
    assert find_placeholders("[needs input: price]") == ["price"]
    assert find_placeholders("[ NEEDS   INPUT :   past   performance ]") == ["past performance"]
    # the same ask in different casing is still one task
    assert find_placeholders("[NEEDS INPUT: Price] and [NEEDS INPUT: price]") == ["Price"]


def test_a_bare_placeholder_gets_a_generic_label() -> None:
    assert find_placeholders("fill this in: [NEEDS INPUT]") == ["unspecified input"]
    assert find_placeholders("[NEEDS INPUT] and [NEEDS INPUT: price]") == [
        "price",
        "unspecified input",
    ]


def test_nothing_is_found_in_ordinary_text() -> None:
    assert find_placeholders(None) == []
    assert find_placeholders("") == []
    assert find_placeholders("The needs input of the buyer are listed in [Annexure A].") == []
    # a stray bracket must not swallow the rest of the document
    assert find_placeholders("[NEEDS INPUT: price\nand more text]") == []


def test_a_placeholder_is_capped_and_never_spans_a_line() -> None:
    long_label = "x" * 500
    assert find_placeholders(f"[NEEDS INPUT: {long_label}]") == []
    ok_label = "y" * 200
    assert find_placeholders(f"[NEEDS INPUT: {ok_label}]") == [ok_label]


def test_task_titles_and_keys() -> None:
    assert task_title("labor category") == "Provide: labor category"
    assert task_title("  spaced   out  ") == "Provide: spaced out"
    assert task_title("") == "Provide: unspecified input"
    assert len(task_title("z" * 400)) == 300
    assert placeholder_key(" Labor  Category ") == placeholder_key("labor category")


def test_vocabulary_inventory() -> None:
    assert TASK_STATUSES == ("open", "done")
    assert TASK_SOURCES == ("agent", "user")
    assert "pursuit" in TARGET_TYPES
    assert "draft_section" in TARGET_TYPES
    assert len(set(TARGET_TYPES)) == len(TARGET_TYPES)
