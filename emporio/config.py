"""Settings read from the environment (``.env``) and the agent's notion of "now"."""

import os
from dataclasses import dataclass, field
from datetime import date, datetime

from dotenv import load_dotenv

from emporio.rules import STORE_TZ

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "low"
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


class ConfigError(ValueError):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Agent settings. ``api_key`` is excluded from ``repr`` so it never reaches logs."""

    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    reference_date: date | None = None


def load_settings() -> Settings:
    """Read the settings from environment variables, loading ``.env`` first.

    Returns:
        The validated settings.

    Raises:
        ConfigError: If the API key is missing, the reasoning effort is not
            supported or ``REFERENCE_DATE`` is not an ISO date.
    """
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")

    effort = os.getenv("OPENAI_REASONING_EFFORT", "").strip() or DEFAULT_REASONING_EFFORT
    if effort not in REASONING_EFFORTS:
        raise ConfigError(f"OPENAI_REASONING_EFFORT must be one of {', '.join(REASONING_EFFORTS)}.")

    return Settings(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
        reasoning_effort=effort,
        reference_date=parse_reference_date(os.getenv("REFERENCE_DATE", "")),
    )


def parse_reference_date(raw: str) -> date | None:
    """Parse the optional ``REFERENCE_DATE`` setting.

    Args:
        raw: Value from the environment; empty means no fixed date.

    Returns:
        The date, or None when the setting is empty.

    Raises:
        ConfigError: If the value is not in ISO format (YYYY-MM-DD).
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise ConfigError(f"REFERENCE_DATE must be an ISO date (YYYY-MM-DD), got {raw!r}.") from error


def current_time(reference_date: date | None = None, clock: datetime | None = None) -> datetime:
    """Return "now" in Campo Grande, optionally moved to a fixed reference date.

    The reference date replaces only the day: the time of day stays real, so
    opening hours still make sense in a demonstration.

    Args:
        reference_date: Day to use instead of today, from ``REFERENCE_DATE``.
        clock: Current instant; defaults to the system clock (tests pass one).

    Returns:
        A timezone-aware datetime in ``America/Campo_Grande``.
    """
    now = (clock or datetime.now(STORE_TZ)).astimezone(STORE_TZ)
    if reference_date is None:
        return now
    return now.replace(year=reference_date.year, month=reference_date.month, day=reference_date.day)
