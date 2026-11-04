from datetime import datetime

from emporio.prompt import build_system_prompt, describe_current_time, read_prompt
from emporio.rules import STORE_TZ

FRIDAY_MORNING = datetime(2026, 2, 20, 10, 30, tzinfo=STORE_TZ)
SUNDAY_AFTERNOON = datetime(2026, 2, 22, 15, 0, tzinfo=STORE_TZ)


class TestBuildSystemPrompt:
    def test_sections_in_order(self) -> None:
        prompt = build_system_prompt(FRIDAY_MORNING)
        titles = ["# Persona", "# Workflow", "# Store policies", "# Current time"]
        positions = [prompt.index(title) for title in titles]
        assert positions == sorted(positions)

    def test_policies_inside_tags(self) -> None:
        prompt = build_system_prompt(FRIDAY_MORNING)
        inside = prompt.split("<policies>")[1].split("</policies>")[0]
        assert "4.1 Direito de Arrependimento" in inside
        assert prompt.count("<policies>") == 1

    def test_current_time_is_last(self) -> None:
        prompt = build_system_prompt(FRIDAY_MORNING)
        assert prompt.endswith(describe_current_time(FRIDAY_MORNING))


class TestDescribeCurrentTime:
    def test_open_store(self) -> None:
        text = describe_current_time(FRIDAY_MORNING)
        assert "sexta-feira, 20/02/2026, 10:30" in text
        assert "OPEN now, until 18:00" in text

    def test_closed_store_says_when_team_is_back(self) -> None:
        text = describe_current_time(SUNDAY_AFTERNOON)
        assert "CLOSED" in text
        assert "segunda-feira, 23/02 at 09:00" in text

    def test_converts_to_campo_grande_time(self) -> None:
        # 14:00 in Brasília is 13:00 in Campo Grande: Saturday closing time.
        brasilia = datetime.fromisoformat("2026-02-21T14:00:00-03:00")
        assert "13:00" in describe_current_time(brasilia)
        assert "CLOSED" in describe_current_time(brasilia)


class TestPolicyConversion:
    def test_keeps_key_facts_from_the_pdf(self) -> None:
        policies = read_prompt("policies")
        for fact in [
            "Rua 14 de Maio, 3200",
            "09:00 às 13:00",
            "parcela mínima de R$ 80,00",
            "acima de R$ 2.000,00",
            "7 (sete) dias corridos após o recebimento",
            "taxa fixa de R$ 35,00",
            "BR4K7M2X9P1BR",
            "(67) 3321-4500",
            "Prazo de retorno: 24 horas úteis",
            "90 (noventa) dias",
        ]:
            assert fact in policies, fact

    def test_has_all_ten_sections(self) -> None:
        policies = read_prompt("policies")
        for number in range(1, 11):
            assert f"\n## {number}. " in policies
