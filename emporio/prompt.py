"""Builds the single system instruction sent to the model on every message."""

from datetime import datetime
from functools import cache
from pathlib import Path

from emporio import rules

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

WEEKDAYS_PT = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


@cache
def read_prompt(name: str) -> str:
    """Read a Markdown prompt file from ``prompts/``, once per process.

    Args:
        name: File name without extension (``persona``, ``workflow``, ``policies``).

    Returns:
        The file content without surrounding whitespace.
    """
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def build_system_prompt(now: datetime) -> str:
    """Assemble persona, workflow, policies and the current time into one instruction.

    The fixed parts come first and the current time last, so the long
    unchanging prefix can be reused by the API's prompt caching.

    Args:
        now: Current time in Campo Grande (the reference date, if set).

    Returns:
        The text for the ``instructions`` parameter of the Responses API.
    """
    sections = [
        f"# Persona\n\n{read_prompt('persona')}",
        f"# Workflow\n\n{read_prompt('workflow')}",
        "# Store policies\n\n"
        "The official store manual. Answer policy questions from it.\n\n"
        f"<policies>\n{read_prompt('policies')}\n</policies>",
        f"# Current time\n\n{describe_current_time(now)}",
    ]
    return "\n\n".join(sections)


def describe_current_time(now: datetime) -> str:
    """Describe date, time and whether the store is open, in Campo Grande time.

    Args:
        now: Current time, timezone-aware.

    Returns:
        A few lines the model uses for greetings, opening hours and deadlines.
    """
    local = now.astimezone(rules.STORE_TZ)
    lines = [
        f"- Now in Campo Grande: {WEEKDAYS_PT[local.weekday()]}, {local:%d/%m/%Y}, {local:%H:%M}.",
        "- Use this as \"today\" for any date the customer mentions.",
    ]
    if rules.is_store_open(local):
        _, closing = rules.opening_hours_on(local.date())
        lines.append(f"- The store is OPEN now, until {closing:%H:%M}.")
    else:
        reopening = rules.next_opening(local)
        lines.append(
            f"- The store is CLOSED now. The human team is back on "
            f"{WEEKDAYS_PT[reopening.weekday()]}, {reopening:%d/%m} at {reopening:%H:%M}. "
            "You keep helping the customer meanwhile."
        )
    return "\n".join(lines)
