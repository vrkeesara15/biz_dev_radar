"""Eval-suite fixtures and the pass-bar table `make eval` prints.

pytest captures stdout, so the golden-set table would be invisible on a green run. The
golden test hands the rendered table to `record_table` and `pytest_terminal_summary`
prints it after the run, which is what SPEC 12's "make eval reports ..." asks for.
"""

from __future__ import annotations

from typing import Any

import pytest

_TABLES: list[str] = []


def record_table(text: str) -> None:
    _TABLES.append(text)


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    for table in _TABLES:
        terminalreporter.write_sep("=", "agent evals (SPEC 12)")
        terminalreporter.write_line(table)
