from datetime import date, datetime, timezone

import pytest

from emporio.config import ConfigError, Settings, current_time, load_settings, parse_reference_date
from emporio.rules import STORE_TZ


class TestReferenceDate:
    def test_empty_means_today(self) -> None:
        assert parse_reference_date("  ") is None

    def test_iso_date(self) -> None:
        assert parse_reference_date("2026-03-25") == date(2026, 3, 25)

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(ConfigError):
            parse_reference_date("25/03/2026")


class TestCurrentTime:
    def test_converts_to_campo_grande(self) -> None:
        utc = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
        assert current_time(clock=utc) == datetime(2026, 11, 4, 11, 0, tzinfo=STORE_TZ)

    def test_reference_date_keeps_time_of_day(self) -> None:
        clock = datetime(2026, 11, 4, 10, 15, tzinfo=STORE_TZ)
        assert current_time(date(2026, 2, 20), clock) == datetime(2026, 2, 20, 10, 15, tzinfo=STORE_TZ)


class TestLoadSettings:
    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An empty variable already set is not overwritten by the local .env file.
        monkeypatch.setenv("OPENAI_API_KEY", "")
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            load_settings()

    def test_reads_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-6.1-terra")
        monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
        monkeypatch.setenv("REFERENCE_DATE", "2026-02-20")
        assert load_settings() == Settings(
            api_key="sk-test", model="gpt-6.1-terra", reasoning_effort="medium", reference_date=date(2026, 2, 20)
        )

    def test_invalid_reasoning_effort_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_REASONING_EFFORT", "turbo")
        with pytest.raises(ConfigError):
            load_settings()

    def test_key_is_hidden_from_repr(self) -> None:
        assert "sk-secret" not in repr(Settings(api_key="sk-secret"))
