#!/usr/bin/env python3
"""`make acceptance` — the SPEC 12 MVP checklist, run and printed (M7-14).

Reads `scripts/acceptance.json`: one entry per row of `docs/acceptance.md`,
each naming the pytest node ids that prove it. Rows with node ids are RUN (one
pytest process for the lot, so the suite is paid for once); rows with none are
printed as MANUAL with the runbook that carries the steps. The exit code is
non-zero if any automated row fails, so this is a gate and not a report.

    python scripts/acceptance.py                 # the automated subset
    ACCEPTANCE_WITH_E2E=1 python scripts/acceptance.py   # ... plus pnpm e2e
    python scripts/acceptance.py --list          # print the table, run nothing

What it deliberately does NOT do: re-measure core coverage (that is `make
test`'s own gate and a figure taken over a subset would be a lie), run the
load suite, or claim any box that needs a real portal, a real mailbox or a
real Office install. Those rows print MANUAL and say why.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"
MAP_PATH = Path(__file__).with_suffix(".json")

PASS = "PASS"
FAIL = "FAIL"
MANUAL = "MANUAL"
SKIPPED = "SKIP"


def out(line: str = "") -> None:
    """stdout without `print` (ruff T20 is on for this repo, as in scripts/load)."""
    sys.stdout.write(f"{line}\n")


@dataclass
class Row:
    id: str
    group: str
    box: str
    nodes: list[str] = field(default_factory=list)
    manual: str = ""
    owner: str = ""
    make: str = ""
    e2e: bool = False
    status: str = MANUAL
    detail: str = ""

    @property
    def automated(self) -> bool:
        return bool(self.nodes) or self.e2e


def load_rows(path: Path = MAP_PATH) -> list[Row]:
    data: dict[str, Any] = json.loads(path.read_text())
    return [
        Row(
            id=row["id"],
            group=row["group"],
            box=row["box"],
            nodes=list(row.get("nodes", [])),
            manual=row.get("manual", ""),
            owner=row.get("owner", ""),
            make=row.get("make", ""),
            e2e=bool(row.get("e2e", False)),
        )
        for row in data["rows"]
    ]


def _pytest_command(nodes: list[str]) -> list[str]:
    """`uv run pytest ...` when uv is on PATH, else this interpreter's pytest."""
    base = ["uv", "run", "pytest"] if shutil.which("uv") else [sys.executable, "-m", "pytest"]
    # No extra -q: backend/pyproject.toml already sets `addopts = "-ra -q"`, and a
    # second -q suppresses the counts line this script reports.
    return [*base, "-p", "no:cacheprovider", *nodes]


_SUMMARY_WORDS = ("passed", "failed", "error", "no tests ran")


def _summary_line(output: str) -> str:
    """pytest -q ends with a counts line; fall back to the last non-blank line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(word in lowered for word in _SUMMARY_WORDS):
            return line.strip("= ").strip()
    return lines[-1] if lines else "no output"


# A node id is run at most once per invocation: `tests/isolation` is the proof of
# two different rows and takes three minutes.
_NODE_RESULTS: dict[str, bool] = {}


def run_pytest(nodes: list[str], *, quiet: bool) -> tuple[bool, str]:
    """Run the nodes of one row (skipping any already run) and return (ok, summary)."""
    if not nodes:
        return True, "no node ids"
    pending = [node for node in nodes if node not in _NODE_RESULTS]
    if not pending:
        ok = all(_NODE_RESULTS[node] for node in nodes)
        return ok, "already run for an earlier row"
    proc = subprocess.run(
        _pytest_command(pending),
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0
    if not quiet and not ok:
        out(output)
    for node in pending:
        _NODE_RESULTS[node] = ok
    summary = _summary_line(output)
    if len(pending) < len(nodes):
        summary = f"{summary} (+ {len(nodes) - len(pending)} node(s) run earlier)"
    return ok and all(_NODE_RESULTS[node] for node in nodes), summary


def run_e2e(*, quiet: bool) -> tuple[bool, str]:
    if os.environ.get("ACCEPTANCE_WITH_E2E") != "1":
        return True, "not run (set ACCEPTANCE_WITH_E2E=1)"
    if shutil.which("pnpm") is None:
        return False, "pnpm is not on PATH"
    env = {**os.environ, "E2E_REQUIRE_BROWSER": "1"}
    proc = subprocess.run(
        ["pnpm", "e2e"], cwd=FRONTEND, capture_output=True, text=True, check=False, env=env
    )
    output = (proc.stdout + proc.stderr).strip()
    if not quiet and proc.returncode != 0:
        out(output)
    return proc.returncode == 0, _summary_line(output)


def evaluate(rows: list[Row], *, quiet: bool) -> None:
    for row in rows:
        if row.e2e:
            if os.environ.get("ACCEPTANCE_WITH_E2E") != "1":
                row.status = SKIPPED
                row.detail = "ACCEPTANCE_WITH_E2E is not 1"
                continue
            ok, detail = run_e2e(quiet=quiet)
            row.status = PASS if ok else FAIL
            row.detail = detail
            continue
        if not row.nodes:
            row.status = MANUAL
            row.detail = row.make or "see docs/acceptance.md"
            continue
        started = time.monotonic()
        ok, detail = run_pytest(row.nodes, quiet=quiet)
        row.status = PASS if ok else FAIL
        row.detail = f"{detail} [{time.monotonic() - started:.1f}s]"


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines or [""]


def print_table(rows: list[Row]) -> None:
    box_width = 74
    out()
    out("SPEC 12 — MVP acceptance checklist        (docs/acceptance.md)")
    out("=" * 100)
    group = ""
    for row in rows:
        if row.group != group:
            group = row.group
            out(f"\n{group}\n{'-' * len(group)}")
        head, *rest = _wrap(row.box, box_width)
        out(f"  {row.status:<6} {head}")
        for line in rest:
            out(f"  {'':<6} {line}")
        if row.detail:
            out(f"  {'':<6}   -> {row.detail}")
        if row.status == MANUAL and row.manual:
            for line in _wrap(row.manual, box_width - 3):
                out(f"  {'':<6}   .. {line}")
        out(f"  {'':<6}   owner: {row.owner}")
    out("=" * 100)


def summarise(rows: list[Row]) -> int:
    counts = {
        status: sum(1 for row in rows if row.status == status)
        for status in (PASS, FAIL, MANUAL, SKIPPED)
    }
    out(
        f"{counts[PASS]} automated row(s) green, {counts[FAIL]} red, "
        f"{counts[SKIPPED]} skipped, {counts[MANUAL]} manual "
        f"(of {len(rows)} rows)."
    )
    if counts[FAIL]:
        out("FAILED: see the rows marked FAIL above.")
        return 1
    out("The automated subset of the SPEC 12 acceptance checklist is green.")
    out("Manual rows are NOT ticked by this run: docs/acceptance.md has the steps.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the checklist, run nothing")
    parser.add_argument("--quiet", action="store_true", help="do not echo pytest output")
    args = parser.parse_args(argv)

    rows = load_rows()
    if args.list:
        for row in rows:
            row.status = MANUAL if not row.automated else "TODO"
            row.detail = ", ".join(row.nodes) or row.make
        print_table(rows)
        return 0

    evaluate(rows, quiet=args.quiet)
    print_table(rows)
    return summarise(rows)


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
