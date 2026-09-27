"""M0-01: .env.example lists every setting consumed by app/core/config.py."""

from pathlib import Path

from app.core.config import Settings


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_matches_settings(repo_root: Path) -> None:
    example = repo_root / ".env.example"
    assert example.is_file()
    keys = _env_keys(example)
    expected = {name.upper() for name in Settings.model_fields}
    missing = expected - keys
    extra = keys - expected
    assert not missing, f".env.example is missing settings: {sorted(missing)}"
    assert not extra, f".env.example has unknown settings: {sorted(extra)}"


def test_env_example_has_no_real_secrets(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text()
    for marker in ("sk-ant-", "AKIA", "BEGIN PRIVATE KEY"):
        assert marker not in text
