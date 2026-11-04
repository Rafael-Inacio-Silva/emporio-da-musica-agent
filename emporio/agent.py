"""The customer service agent: conversation history and the tool-calling loop.

Each customer message goes to the OpenAI Responses API together with the
whole conversation so far. When the model asks for tools, the code runs them
and sends the results back, until the model writes the final answer.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import openai

from emporio.config import Settings, current_time
from emporio.data_loader import StoreData
from emporio.prompt import build_system_prompt
from emporio.tools import TICKETS_DIR, TOOLS, ToolContext, execute_tool

logger = logging.getLogger(__name__)

# Enough for search + quote + ticket in one answer, while stopping a model
# that keeps calling tools in circles.
MAX_TOOL_CALLS_PER_MESSAGE = 5
REQUEST_TIMEOUT_SECONDS = 30.0

# Shown to the customer; the technical cause only goes to the log.
CONNECTION_ERROR_REPLY = (
    "Desculpe, estou com dificuldade para me conectar agora. "
    "Pode tentar de novo em alguns instantes?"
)
BUSY_REPLY = "Desculpe, estou recebendo muitas mensagens neste momento. Pode tentar de novo daqui a pouco?"
GENERIC_ERROR_REPLY = (
    "Desculpe, tive um problema técnico e não consegui responder. "
    "Se preferir, fale com a nossa equipe pelo WhatsApp (67) 3321-4500."
)
EMPTY_REPLY = "Desculpe, não consegui montar a resposta. Pode repetir a pergunta de outro jeito?"


@dataclass(frozen=True)
class ToolCall:
    """A tool the model called while answering, for debugging and transcripts."""

    name: str
    arguments: str

    def describe(self) -> str:
        """Format the call for people, e.g. ``search_products(query="takamine gd20")``.

        Arguments the model left empty (null) are omitted.
        """
        try:
            arguments = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return f"{self.name}({self.arguments})"
        if not isinstance(arguments, dict):
            return f"{self.name}({self.arguments})"
        shown = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in arguments.items()
            if value is not None
        )
        return f"{self.name}({shown})"


@dataclass(frozen=True)
class AgentReply:
    """The answer to one customer message."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    failed: bool = False


def build_tool_schemas() -> list[dict[str, Any]]:
    """Describe every tool in the flat function format of the Responses API.

    ``openai.pydantic_function_tool`` produces a strict-mode schema (every
    field required, optional fields nullable, no extra properties), but wrapped
    in the Chat Completions envelope ``{"type": "function", "function": {...}}``.
    The Responses API expects the same fields at the top level.

    Returns:
        One schema per tool, with ``strict`` enabled.
    """
    schemas = []
    for tool in TOOLS.values():
        chat_format = openai.pydantic_function_tool(
            tool.args_model, name=tool.name, description=tool.description
        )
        schemas.append({"type": "function", **chat_format["function"]})
    return schemas


TOOL_SCHEMAS = build_tool_schemas()


def create_client(settings: Settings) -> openai.OpenAI:
    """Create the OpenAI client with a request timeout.

    Args:
        settings: Settings with the API key.

    Returns:
        A client that retries transient errors twice (SDK default).
    """
    return openai.OpenAI(api_key=settings.api_key, timeout=REQUEST_TIMEOUT_SECONDS)


class Agent:
    """One conversation with one customer, kept in memory."""

    def __init__(
        self,
        client: openai.OpenAI,
        settings: Settings,
        data: StoreData,
        clock: Callable[[], datetime] | None = None,
        tickets_dir: Path = TICKETS_DIR,
    ) -> None:
        """Create an agent with an empty conversation.

        Args:
            client: OpenAI client (tests pass a fake one).
            settings: Model, reasoning effort and reference date.
            data: Store data loaded at startup.
            clock: Returns "now"; defaults to Campo Grande time on the
                reference date, if one is set.
            tickets_dir: Where support tickets are written.
        """
        self._client = client
        self._settings = settings
        self._data = data
        self._clock = clock or (lambda: current_time(settings.reference_date))
        self._tickets_dir = tickets_dir
        # Explicit list of input items, sent in full on every request instead of
        # previous_response_id, so the conversation state is visible in our code.
        self.history: list[Any] = []

    def reply(self, message: str) -> AgentReply:
        """Answer one customer message, calling tools as the model asks.

        Args:
            message: Text typed by the customer.

        Returns:
            The answer and the tools called. If the API fails, a friendly
            message with ``failed=True``, and the conversation is left as it
            was before this message so the customer can simply try again.
        """
        start = len(self.history)
        self.history.append({"role": "user", "content": message})
        now = self._clock()
        context = ToolContext(data=self._data, now=now, tickets_dir=self._tickets_dir)
        instructions = build_system_prompt(now)
        tool_calls: list[ToolCall] = []

        while True:
            # After the limit the model must answer with what it already has.
            tools_allowed = len(tool_calls) < MAX_TOOL_CALLS_PER_MESSAGE
            try:
                response = self._create_response(instructions, tools_allowed)
            except openai.APIError as error:
                del self.history[start:]
                return AgentReply(text=_error_reply(error), tool_calls=tool_calls, failed=True)

            # All output items, reasoning included, go back to the model with the
            # tool results (required for reasoning models).
            self.history.extend(response.output)
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                return AgentReply(text=response.output_text.strip() or EMPTY_REPLY, tool_calls=tool_calls)

            for call in function_calls:
                tool_calls.append(ToolCall(name=call.name, arguments=call.arguments))
                result = execute_tool(call.name, call.arguments, context)
                self.history.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

    def _create_response(self, instructions: str, tools_allowed: bool) -> Any:
        # store=False: OpenAI does not keep customer conversations (policy 9).
        # Encrypted reasoning lets reasoning items be sent back without storage.
        return self._client.responses.create(
            model=self._settings.model,
            instructions=instructions,
            input=self.history,
            tools=TOOL_SCHEMAS,
            tool_choice="auto" if tools_allowed else "none",
            reasoning={"effort": self._settings.reasoning_effort},
            store=False,
            include=["reasoning.encrypted_content"],
        )


def _error_reply(error: openai.APIError) -> str:
    # Logs the error type and code only: the SDK message for an invalid key
    # contains part of the key.
    logger.error(
        "OpenAI request failed: %s (status=%s, code=%s)",
        type(error).__name__,
        getattr(error, "status_code", None),
        error.code,
    )
    if isinstance(error, openai.APIConnectionError):
        return CONNECTION_ERROR_REPLY
    if isinstance(error, openai.RateLimitError) and error.code != "insufficient_quota":
        return BUSY_REPLY
    return GENERIC_ERROR_REPLY
