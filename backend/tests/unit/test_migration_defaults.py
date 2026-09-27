"""M1-06: the migration's jsonb weight defaults equal app.core.preferences defaults."""

import importlib.util
import json
from pathlib import Path

from app.core.preferences import DEFAULT_BID_NO_BID_WEIGHTS, DEFAULT_SCORING_WEIGHTS


def _load_migration(backend_root: Path):  # type: ignore[no-untyped-def]
    path = backend_root / "migrations" / "versions" / "0002_m1_profile.py"
    spec = importlib.util.spec_from_file_location("m0002", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_weight_defaults_match_core(backend_root: Path) -> None:
    m = _load_migration(backend_root)
    assert json.loads(m.SCORING_WEIGHTS_DEFAULT) == DEFAULT_SCORING_WEIGHTS
    assert json.loads(m.BID_NO_BID_WEIGHTS_DEFAULT) == DEFAULT_BID_NO_BID_WEIGHTS
