import pytest


@pytest.fixture(autouse=True)
async def _clean(clean_db):  # type: ignore[no-untyped-def]
    """Every integration test starts from empty tables."""
    return clean_db
