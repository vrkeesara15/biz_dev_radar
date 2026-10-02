"""Railway prep: the managed-Postgres DSN normaliser and the app-role DSN helper.

These are the two pure pieces of the bootstrap. The statements it runs are covered by
`tests/integration/test_bootstrap_db.py` against the compose Postgres.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings, normalize_database_url
from app.jobs.bootstrap_db import APP_ROLE, BootstrapError, derive_app_url

# ------------------------------------------------------------------ normalize_database_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Heroku-style, still what Railway's Postgres plugin emits for DATABASE_URL.
        (
            "postgres://user:pw@host:5432/railway",
            "postgresql+asyncpg://user:pw@host:5432/railway",
        ),
        # The driverless form SQLAlchemy would hand to psycopg2.
        (
            "postgresql://user:pw@postgres.railway.internal:5432/railway",
            "postgresql+asyncpg://user:pw@postgres.railway.internal:5432/railway",
        ),
        # Already correct: untouched.
        (
            "postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar",
            "postgresql+asyncpg://bidradar:bidradar@localhost:5433/bidradar",
        ),
        # Another driver is somebody's deliberate choice; leave it alone.
        (
            "postgresql+psycopg://user:pw@host/db?sslmode=require",
            "postgresql+psycopg://user:pw@host/db?sslmode=require",
        ),
        # Not Postgres at all.
        ("redis://localhost:6380/0", "redis://localhost:6380/0"),
        ("", ""),
        ("not-a-dsn", "not-a-dsn"),
    ],
)
def test_scheme_is_normalised_to_asyncpg(given: str, expected: str) -> None:
    assert normalize_database_url(given) == expected


@pytest.mark.parametrize("mode", ["require", "verify-full", "disable", "prefer"])
def test_sslmode_becomes_ssl_for_asyncpg(mode: str) -> None:
    """`sslmode` is a libpq keyword; SQLAlchemy forwards it to asyncpg.connect(), which
    raises TypeError. asyncpg spells it `ssl` and takes the same vocabulary."""
    out = normalize_database_url(
        f"postgres://u:p@maglev.proxy.rlwy.net:21234/railway?sslmode={mode}"
    )
    assert out == f"postgresql+asyncpg://u:p@maglev.proxy.rlwy.net:21234/railway?ssl={mode}"
    assert "sslmode" not in out


def test_other_query_parameters_survive_the_rename() -> None:
    out = normalize_database_url("postgres://u:p@h/db?sslmode=require&application_name=bidradar")
    assert out == "postgresql+asyncpg://u:p@h/db?ssl=require&application_name=bidradar"


def test_internal_railway_host_gets_no_tls_added() -> None:
    """Railway's private network needs no TLS, and the DSN it issues says nothing about
    it. Inventing `ssl=require` here would break the internal connection."""
    out = normalize_database_url("postgresql://postgres:pw@postgres.railway.internal:5432/railway")
    assert "ssl" not in out


def test_settings_normalise_both_dsns() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgres://bidradar_app:pw@postgres.railway.internal:5432/railway",
        database_url_owner="postgresql://postgres:pw@maglev.proxy.rlwy.net:1/railway?sslmode=require",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.database_url_owner.endswith("?ssl=require")


# -------------------------------------------------------------------------- derive_app_url


def test_derive_app_url_swaps_the_login_and_keeps_everything_else() -> None:
    owner = "postgres://postgres:ownerpw@postgres.railway.internal:5432/railway"
    assert derive_app_url(owner, "apppw") == (
        f"postgresql+asyncpg://{APP_ROLE}:apppw@postgres.railway.internal:5432/railway"
    )


def test_derive_app_url_normalises_and_keeps_the_query_string() -> None:
    owner = "postgresql://postgres:pw@maglev.proxy.rlwy.net:21234/railway?sslmode=require"
    assert derive_app_url(owner, "s3cret") == (
        f"postgresql+asyncpg://{APP_ROLE}:s3cret@maglev.proxy.rlwy.net:21234/railway?ssl=require"
    )


def test_derive_app_url_percent_encodes_the_password() -> None:
    """A generated password with `@` or `/` in it would otherwise re-split the DSN."""
    url = derive_app_url("postgres://postgres:pw@host:5432/db", "p@ss/wo rd:1")
    assert url == f"postgresql+asyncpg://{APP_ROLE}:p%40ss%2Fwo%20rd%3A1@host:5432/db"


def test_derive_app_url_handles_a_dsn_with_no_credentials() -> None:
    assert derive_app_url("postgresql://host:5432/db", "pw") == (
        f"postgresql+asyncpg://{APP_ROLE}:pw@host:5432/db"
    )


def test_derive_app_url_accepts_a_custom_role() -> None:
    url = derive_app_url("postgres://postgres:pw@host/db", "pw2", app_role="other_app")
    assert url.startswith("postgresql+asyncpg://other_app:pw2@")


@pytest.mark.parametrize("bad", ["", None])
def test_derive_app_url_refuses_an_empty_password(bad: str | None) -> None:
    with pytest.raises(BootstrapError, match="APP_DB_PASSWORD"):
        derive_app_url("postgres://postgres:pw@host/db", bad or "")


def test_derive_app_url_refuses_a_non_dsn() -> None:
    with pytest.raises(BootstrapError, match="not a DSN"):
        derive_app_url("host:5432/db", "pw")
