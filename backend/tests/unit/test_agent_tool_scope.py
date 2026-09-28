"""M5-12 (SPEC 11): "tool access for agents is limited to read-only retrieval and writing
to the pursuit's own records".

This module parses every file under app/agents and fails when an agent writes through
anything `app.agents.tools` does not name, so a future agent cannot quietly gain a new
write path. The runtime half (ScopeViolation) is tested in
tests/integration/test_agent_scope.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.agents.tools import (
    ALLOWED_SERVICE_IMPORTS,
    ALLOWED_WRITE_FUNCTIONS,
    ALLOWED_WRITE_MODELS,
    FORBIDDEN_AGENT_IMPORTS,
    RUNTIME_WRITE_MODELS,
    RUNTIME_WRITE_MODULES,
    TOOL_NAMES,
    PursuitTools,
    tool_names,
)

AGENTS_DIR = Path(__file__).resolve().parents[2] / "app" / "agents"
# a session method that writes rows
SESSION_WRITES = {"add", "add_all", "delete", "merge"}
# sqlalchemy constructs that write
SQL_WRITES = {"insert", "update", "delete"}


def agent_modules() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py")


def module_name(path: Path) -> str:
    return f"app.agents.{path.stem}"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _called_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _model_names(node: ast.expr) -> list[str]:
    """Class names constructed or referenced in a write call's arguments."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _called_name(child.func)
            if name and name[:1].isupper():
                names.append(name)
        elif isinstance(child, ast.Name) and child.id[:1].isupper():
            names.append(child.id)
    return names


def test_every_agent_module_is_scanned() -> None:
    names = {module_name(p) for p in agent_modules()}
    assert "app.agents.drafters" in names and "app.agents.red_team" in names
    assert len(names) >= 10


def write_offenders(tree: ast.Module, allowed: set[str]) -> list[str]:
    """Model writes in `tree` that `allowed` does not permit."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node.func)
        is_session_write = isinstance(node.func, ast.Attribute) and name in SESSION_WRITES
        is_sql_write = isinstance(node.func, ast.Name) and name in SQL_WRITES
        if not (is_session_write or is_sql_write):
            continue
        args = ast.Tuple(elts=list(node.args), ctx=ast.Load())
        for model in dict.fromkeys(_model_names(args)):
            if model not in allowed:
                offenders.append(f"{name}({model})")
    return offenders


def service_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.services"):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("path", agent_modules(), ids=lambda p: p.stem)
def test_an_agent_only_writes_models_the_tool_policy_names(path: Path) -> None:
    allowed = set(ALLOWED_WRITE_MODELS)
    if module_name(path) in RUNTIME_WRITE_MODULES:
        allowed |= set(RUNTIME_WRITE_MODELS)
    offenders = write_offenders(_tree(path), allowed)
    assert not offenders, (
        f"{module_name(path)} writes {sorted(set(offenders))}; "
        "add it to app.agents.tools.ALLOWED_WRITE_MODELS only if it is keyed on the "
        "run's own pursuit"
    )


@pytest.mark.parametrize("path", agent_modules(), ids=lambda p: p.stem)
def test_an_agent_only_imports_services_the_tool_policy_names(path: Path) -> None:
    imported = service_imports(_tree(path))
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.services"), (
                    f"{module_name(path)} imports the whole service module {alias.name}; "
                    "import the named function so the allowlist can see it"
                )
    extra = imported - ALLOWED_SERVICE_IMPORTS
    assert not extra, (
        f"{module_name(path)} imports {sorted(extra)} from app.services; "
        "add it to app.agents.tools.ALLOWED_SERVICE_IMPORTS after checking it cannot "
        "write outside the run's own pursuit"
    )


@pytest.mark.parametrize("path", agent_modules(), ids=lambda p: p.stem)
def test_an_agent_never_imports_the_api_layer_or_a_raw_http_client(path: Path) -> None:
    for node in ast.walk(_tree(path)):
        names = []
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            for forbidden in FORBIDDEN_AGENT_IMPORTS:
                assert not (name == forbidden or name.startswith(f"{forbidden}.")), (
                    f"{module_name(path)} imports {name}"
                )


def test_every_allowed_write_function_is_actually_used_or_named() -> None:
    """The allowlist is a policy, not a wish list: each entry must exist in app.services."""
    import app.services.collab as collab
    import app.services.documents as documents
    import app.services.drafts as drafts
    import app.services.pursuits as pursuits

    found = set()
    for module in (drafts, pursuits, documents, collab):
        found.update(name for name in ALLOWED_WRITE_FUNCTIONS if hasattr(module, name))
    assert found == set(ALLOWED_WRITE_FUNCTIONS), sorted(ALLOWED_WRITE_FUNCTIONS - found)


def test_every_write_helper_accepts_a_scope() -> None:
    """A write the allowlist names must be refusable: it takes the run's PursuitScope."""
    import inspect

    from app.services.collab import create_task_from_placeholder
    from app.services.documents import parse_and_store
    from app.services.drafts import add_comment, create_task, save_version
    from app.services.pursuits import store_artifact

    for fn in (
        save_version,
        create_task,
        add_comment,
        store_artifact,
        create_task_from_placeholder,
    ):
        assert "scope" in inspect.signature(fn).parameters, fn.__name__
    # parse_and_store writes parsed text and chunks of a document the collector already
    # resolved from the run's own opportunity, so it is scoped by its caller
    assert "document" in inspect.signature(parse_and_store).parameters


def test_the_read_only_tool_set_is_exactly_three_tools() -> None:
    assert TOOL_NAMES == ("kb_search", "read_document", "read_requirements")
    assert tool_names() == TOOL_NAMES
    for name in TOOL_NAMES:
        assert callable(getattr(PursuitTools, name))


# --- the scanner has to be able to fail ------------------------------------------------------

BAD_WRITE = """
from sqlalchemy import delete
from app.models import AuditLog, Requirement


async def step(ctx):
    ctx.session.add(Requirement(req_id="R-001"))
    ctx.session.add(AuditLog(action="sneaky"))
    await ctx.session.execute(delete(AuditLog))
"""

BAD_IMPORT = """
from app.services.audit import write_audit
from app.services.drafts import save_version
"""


def test_the_model_scan_catches_a_write_outside_the_policy() -> None:
    offenders = write_offenders(ast.parse(BAD_WRITE), set(ALLOWED_WRITE_MODELS))
    assert sorted(set(offenders)) == ["add(AuditLog)", "delete(AuditLog)"]
    assert "add(Requirement)" not in offenders


def test_the_import_scan_catches_a_service_outside_the_policy() -> None:
    extra = service_imports(ast.parse(BAD_IMPORT)) - ALLOWED_SERVICE_IMPORTS
    assert extra == {"write_audit"}


def test_the_runtime_tables_are_only_writable_by_the_runner() -> None:
    offenders = write_offenders(
        ast.parse("def f(s):\n    s.add(AgentRun())\n"), set(ALLOWED_WRITE_MODELS)
    )
    assert offenders == ["add(AgentRun)"]
    allowed = set(ALLOWED_WRITE_MODELS) | set(RUNTIME_WRITE_MODELS)
    assert write_offenders(ast.parse("def f(s):\n    s.add(AgentRun())\n"), allowed) == []
