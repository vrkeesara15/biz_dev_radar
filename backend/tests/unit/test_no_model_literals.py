"""M0-03: LLM model ids live only in app/core/config.py (CLAUDE.md)."""

from pathlib import Path

ALLOWED = {Path("app/core/config.py")}
NEEDLE = "claude" + "-"  # split so this file never trips its own rule if copied under app/


def test_no_hard_coded_model_ids_outside_config(backend_root: Path) -> None:
    offenders: list[str] = []
    for path in (backend_root / "app").rglob("*.py"):
        rel = path.relative_to(backend_root)
        if rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if NEEDLE in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "model ids must come from Settings:\n" + "\n".join(offenders)
