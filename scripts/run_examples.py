"""Generate the example conversations in examples/ by running the real agent.

Run from the project root, with OPENAI_API_KEY in .env:

    python -m scripts.run_examples
"""

import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from emporio.agent import Agent, AgentReply, create_client
from emporio.config import load_settings
from emporio.data_loader import load_store_data
from emporio.prompt import WEEKDAYS_PT
from emporio.rules import STORE_TZ

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

# A fixed Friday morning, so "is the store open?" and the return deadline of
# order 7 (delivered on 17/02) give the same answer on every run.
EXAMPLE_TIME = datetime(2026, 2, 20, 10, 0, tzinfo=STORE_TZ)


@dataclass(frozen=True)
class Scenario:
    """One scripted conversation: the customer's messages and what it shows."""

    file_name: str
    title: str
    shows: str
    messages: tuple[str, ...]


SCENARIOS = (
    Scenario(
        file_name="01-violoes-ate-1000.md",
        title="Violões até R$ 1.000",
        shows="consulta ao catálogo com filtro de preço, depois preço no PIX, parcelamento e frete de um modelo.",
        messages=(
            "Quais opções de violões disponíveis custando até R$1000?",
            "Gostei do Yamaha C40. Quanto fica no PIX? E em quantas vezes posso parcelar no cartão? Moro em Campo Grande.",
        ),
    ),
    Scenario(
        file_name="02-takamine-gd20.md",
        title="Preço do Takamine GD20",
        shows="consulta de preço e estoque de um produto específico, e parcelamento.",
        messages=(
            "Quanto custa o Takamine GD20?",
            "Tem em estoque? Dá pra parcelar sem juros?",
        ),
    ),
    Scenario(
        file_name="03-devolucao-arrependimento.md",
        title="Arrependimento de compra",
        shows="situação não trivial: o agente pede número do pedido e e-mail, consulta o pedido, "
        "aplica o prazo de 7 dias da política de devolução e abre um chamado para a equipe.",
        messages=(
            "Me arrependi da minha compra, posso devolver meu pedido?",
            "É o pedido 7, e-mail leticia.rocha@jmail.com",
            "Sim, quero devolver. O produto está na embalagem, nem usei.",
        ),
    ),
    Scenario(
        file_name="04-endereco-e-horario.md",
        title="Endereço e horário da loja",
        shows="informações gerais respondidas pelas políticas da loja, sem chamar ferramentas.",
        messages=(
            "Qual o endereço da loja?",
            "E vocês abrem no sábado? Até que horas?",
        ),
    ),
    Scenario(
        file_name="05-fora-do-escopo.md",
        title="Fora do escopo",
        shows="pedido de acessório (a loja vende só instrumentos) e uma pergunta sem relação com a loja.",
        messages=(
            "Vocês vendem cordas para violão?",
            "Entendi. Aproveitando, qual vai ser a previsão do tempo amanhã em Campo Grande?",
        ),
    ),
)


def describe_example_time() -> str:
    """Return the fixed example time in Portuguese, e.g. "sexta-feira, 20/02/2026, 10:00"."""
    return f"{WEEKDAYS_PT[EXAMPLE_TIME.weekday()]}, {EXAMPLE_TIME:%d/%m/%Y, %H:%M}"


def format_turn(message: str, reply: AgentReply) -> str:
    """Format one customer message and the agent's answer as Markdown.

    Args:
        message: What the customer typed.
        reply: The agent's answer and the tools it called.

    Returns:
        The turn, with each tool call on its own line before the answer.
    """
    parts = [f"**Cliente:** {message}"]
    if reply.tool_calls:
        parts.append("\n>\n".join(f"> `tool: {call.describe()}`" for call in reply.tool_calls))
    parts.append(f"**Agente:** {reply.text}")
    return "\n\n".join(parts)


def run_scenario(scenario: Scenario, new_agent: Callable[[], Agent]) -> str:
    """Run one scenario with a fresh agent and return the Markdown transcript.

    Args:
        scenario: The scripted conversation.
        new_agent: Creates an agent with an empty history.

    Returns:
        The whole transcript.

    Raises:
        RuntimeError: If the API fails, so an error message never ends up
            saved as an example.
    """
    agent = new_agent()
    turns = []
    for message in scenario.messages:
        reply = agent.reply(message)
        if reply.failed:
            raise RuntimeError(f"{scenario.file_name}: the API call failed ({reply.text})")
        turns.append(format_turn(message, reply))
    header = (
        f"# {scenario.title}\n\n"
        f"**O que mostra:** {scenario.shows}\n\n"
        f"*Gerada por `scripts/run_examples.py` com o agente de verdade. "
        f"Data e hora simuladas: {describe_example_time()} (horário de Campo Grande).*"
    )
    return "\n\n---\n\n".join([header, *turns]) + "\n"



def main() -> int:
    """Run every scenario and write one Markdown file per conversation.

    Returns:
        The process exit code.
    """
    settings = load_settings()
    client = create_client(settings)
    data = load_store_data()
    EXAMPLES_DIR.mkdir(exist_ok=True)
    # Tickets go to a throwaway folder: the examples must not leave files in tickets/.
    with tempfile.TemporaryDirectory() as tickets_dir:
        for scenario in SCENARIOS:
            transcript = run_scenario(
                scenario,
                lambda: Agent(client, settings, data, clock=lambda: EXAMPLE_TIME, tickets_dir=Path(tickets_dir)),
            )
            (EXAMPLES_DIR / scenario.file_name).write_text(transcript, encoding="utf-8")
            print(f"Saved examples/{scenario.file_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
