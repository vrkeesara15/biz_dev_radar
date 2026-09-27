from zoneinfo import ZoneInfoNotFoundError

import pytest
from app.core.timezones import known_timezones, validate_locale, validate_timezone


def test_timezones() -> None:
    assert "Asia/Kolkata" in known_timezones()
    assert validate_timezone(" America/New_York ") == "America/New_York"
    with pytest.raises(ZoneInfoNotFoundError):
        validate_timezone("Mars/Olympus")


def test_locales() -> None:
    for ok in ("en", "en-US", "hi-IN", "zh-Hant-TW", "es-419"):
        assert validate_locale(ok) == ok
    for bad in ("", "EN_us", "english", "en-us"):
        with pytest.raises(ValueError):
            validate_locale(bad)
