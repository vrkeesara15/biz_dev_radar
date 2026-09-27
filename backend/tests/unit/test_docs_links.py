"""M7-12: the documentation link checker.

Docs rot silently: a file gets renamed, a heading gets reworded, and the link that
pointed at it keeps rendering as a link. This module is what fails the build instead.

It checks four things over `README.md` and `docs/**/*.md`:

1. every relative link resolves to a file that exists in the repo;
2. every `#anchor` resolves to a heading in the target file (GitHub slug rules);
3. every runbook under `docs/runbooks/` is linked from `docs/index.md`;
4. the README environment-variable table names every field of
   `app.core.config.Settings`, so a new setting cannot ship undocumented.

The CI wiring is asserted too (`.github/workflows/ci.yml`), because a checker that
nobody runs is a comment.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.core.config import SECRET_SETTINGS, Settings

# [text](target) — the target stops at the first whitespace so `(path "title")` works.
LINK_RE = re.compile(r"\[(?P<text>[^\]\n]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "//")


def _strip_code(text: str) -> list[tuple[int, str]]:
    """Numbered lines with fenced code blocks blanked and inline code removed."""
    out: list[tuple[int, str]] = []
    in_fence = False
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if match:
            token = match.group(1)
            if not in_fence:
                in_fence, fence = True, token
            elif token == fence:
                in_fence, fence = False, None
            out.append((number, ""))
            continue
        out.append((number, "" if in_fence else re.sub(r"`[^`\n]*`", "", line)))
    return out


def _slug(title: str) -> str:
    """GitHub's heading slug: drop markup, lowercase, spaces to hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", title)
    text = LINK_RE.sub(r"\g<text>", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", "-", text.strip())


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for _number, line in _strip_code(path.read_text()):
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = _slug(match.group("title"))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _links(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in _strip_code(path.read_text()):
        for match in LINK_RE.finditer(line):
            found.append((number, match.group("target")))
    return found


def _doc_files(repo_root: Path) -> list[Path]:
    docs = sorted((repo_root / "docs").rglob("*.md"))
    return [repo_root / "README.md", *docs]


def _rel(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


# --------------------------------------------------------------------------- links


def test_documentation_files_exist(repo_root: Path) -> None:
    assert (repo_root / "README.md").is_file()
    assert (repo_root / "docs" / "index.md").is_file()
    assert (repo_root / "docs" / "adapters.md").is_file()
    assert (repo_root / "docs" / "runbooks" / "broken-source.md").is_file()


def test_every_relative_link_resolves(repo_root: Path) -> None:
    """A link to a file that is not there is a broken promise, not a typo."""
    broken: list[str] = []
    for doc in _doc_files(repo_root):
        for number, target in _links(doc):
            if target.startswith(EXTERNAL_PREFIXES) or target.startswith("#"):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            base = repo_root if path_part.startswith("/") else doc.parent
            resolved = (base / path_part.lstrip("/")).resolve()
            if not resolved.exists():
                broken.append(f"{_rel(repo_root, doc)}:{number} -> {target}")
    assert not broken, "links to files that do not exist:\n  " + "\n  ".join(broken)


def test_every_anchor_resolves(repo_root: Path) -> None:
    """`#section` links break the moment somebody rewords a heading."""
    broken: list[str] = []
    cache: dict[Path, set[str]] = {}
    for doc in _doc_files(repo_root):
        for number, target in _links(doc):
            if target.startswith(EXTERNAL_PREFIXES) or "#" not in target:
                continue
            path_part, anchor = target.split("#", 1)
            if not anchor:
                continue
            if path_part:
                base = repo_root if path_part.startswith("/") else doc.parent
                resolved = (base / path_part.lstrip("/")).resolve()
            else:
                resolved = doc
            if resolved.suffix != ".md" or not resolved.is_file():
                continue
            if resolved not in cache:
                cache[resolved] = _anchors(resolved)
            if anchor.lower() not in cache[resolved]:
                broken.append(f"{_rel(repo_root, doc)}:{number} -> {target}")
    assert not broken, "anchors with no matching heading:\n  " + "\n  ".join(broken)


def test_index_links_every_runbook(repo_root: Path) -> None:
    """A runbook nobody can find from the index does not exist during an incident."""
    index = repo_root / "docs" / "index.md"
    linked = {
        (index.parent / target.split("#", 1)[0]).resolve()
        for _number, target in _links(index)
        if not target.startswith(EXTERNAL_PREFIXES)
    }
    runbooks = sorted((repo_root / "docs" / "runbooks").glob("*.md"))
    assert runbooks, "docs/runbooks is empty"
    missing = [_rel(repo_root, r) for r in runbooks if r.resolve() not in linked]
    assert not missing, f"docs/index.md does not link: {missing}"


def test_index_links_every_doc(repo_root: Path) -> None:
    index = repo_root / "docs" / "index.md"
    linked = {
        (index.parent / target.split("#", 1)[0]).resolve()
        for _number, target in _links(index)
        if not target.startswith(EXTERNAL_PREFIXES)
    }
    missing = [
        _rel(repo_root, doc)
        for doc in sorted((repo_root / "docs").rglob("*.md"))
        if doc != index and doc.resolve() not in linked
    ]
    assert not missing, f"docs/index.md does not link: {missing}"


# ------------------------------------------------------------- README env var table


def _readme_env_table(repo_root: Path) -> list[list[str]]:
    """Rows of the table under the README's "Environment variables" heading."""
    lines = (repo_root / "README.md").read_text().splitlines()
    rows: list[list[str]] = []
    inside = False
    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group("title").lower()
            inside = "environment variable" in title
            continue
        if not inside or not line.startswith("|"):
            continue
        body = re.sub(r"^\|\s*|\s*\|$", "", line.strip())
        cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body)]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells)
    return rows


def _table_names(rows: list[list[str]]) -> set[str]:
    return {cells[0].strip("`") for cells in rows if cells[0].startswith("`")}


def test_readme_env_table_covers_every_setting(repo_root: Path) -> None:
    """The env table is generated from config.py; this is what keeps it generated."""
    rows = _readme_env_table(repo_root)
    assert rows, "no environment-variable table found in README.md"
    documented = _table_names(rows)
    expected = {name.upper() for name in Settings.model_fields}
    missing = sorted(expected - documented)
    unknown = sorted(documented - expected)
    assert not missing, f"README env table is missing settings: {missing}"
    assert not unknown, f"README env table documents unknown settings: {unknown}"


def test_readme_env_table_has_purpose_default_and_flags(repo_root: Path) -> None:
    rows = _readme_env_table(repo_root)
    thin = [cells[0] for cells in rows if len(cells) < 5 or not cells[1]]
    assert not thin, f"env rows need purpose, default, required-in-prod and secret: {thin}"


def test_readme_env_table_marks_every_secret(repo_root: Path) -> None:
    """SECRET_SETTINGS is the list Terraform builds Secret Manager entries from."""
    rows = _readme_env_table(repo_root)
    marked = {
        cells[0].strip("`") for cells in rows if len(cells) >= 5 and "yes" in cells[4].lower()
    }
    expected = {name.upper() for name in SECRET_SETTINGS}
    assert marked == expected, (
        f"secret column drifted from SECRET_SETTINGS: "
        f"missing {sorted(expected - marked)}, extra {sorted(marked - expected)}"
    )


# ----------------------------------------------------------------------------- CI


@pytest.fixture(scope="module")
def ci_workflow(repo_root: Path) -> dict[str, Any]:
    data = yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_text())
    assert isinstance(data, dict)
    return data


def test_ci_runs_the_docs_link_checker(ci_workflow: dict[str, Any]) -> None:
    """M7-12 acceptance: the docs link checker passes in CI."""
    runs = "\n".join(
        str(step.get("run", ""))
        for job in ci_workflow["jobs"].values()
        for step in job.get("steps", [])
    )
    assert "tests/unit/test_docs_links.py" in runs, (
        "no CI step runs the docs link checker; add a `docs-links` step to ci.yml"
    )
