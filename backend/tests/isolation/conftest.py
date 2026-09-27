from typing import Any

import pytest
from fastapi import FastAPI

from tests.billing_fake import fake_providers


@pytest.fixture(autouse=True)
async def _clean(clean_db):  # type: ignore[no-untyped-def]
    """Every isolation test starts from empty tables."""
    return clean_db


@pytest.fixture(autouse=True)
def _billing(app: FastAPI) -> Any:
    """Network-free billing providers so POST /billing/checkout answers 201 here instead
    of 503; the harness then checks the body for cross-tenant leaks like any other route."""
    app.state.billing_providers = fake_providers()
    return app.state.billing_providers
