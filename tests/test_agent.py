import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from openai.types.responses import ResponseFunctionToolCall

from emporio.agent import (
    BUSY_REPLY,
    CONNECTION_ERROR_REPLY,
    EMPTY_REPLY,
    GENERIC_ERROR_REPLY,
    MAX_TOOL_CALLS_PER_MESSAGE,
    TOOL_SCHEMAS,
    Agent,
)
from emporio.config import Settings
from emporio.data_loader import StoreData, load_store_data
from emporio.rules import STORE_TZ
from emporio.tools import TOOLS

NOW = datetime(2026, 2, 20, 10, 30, tzinfo=STORE_TZ)
REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


class FakeResponses:
    """Stands in for ``client.responses``: returns scripted responses in order."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        # Copy the input: the agent keeps appending to the same list.
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeClient:
    def __init__(self, script: list[Any]) -> None:
        self.responses = FakeResponses(script)


def text_response(text: str) -> SimpleNamespace:
    message = SimpleNamespace(type="message", role="assistant", content=[{"type": "output_text", "text": text}])
    return SimpleNamespace(output=[message], output_text=text)


def tool_response(name: str, call_id: str = "call_1", **arguments: Any) -> SimpleNamespace:
    call = ResponseFunctionToolCall(
        type="function_call", call_id=call_id, name=name, arguments=json.dumps(arguments)
    )
    return SimpleNamespace(output=[call], output_text="")


def status_error(error_class: type[openai.APIStatusError], status: int, message: str = "error") -> Exception:
    return error_class(message, response=httpx2.Response(status, request=REQUEST), body=None)


@pytest.fixture(scope="module")
def data() -> StoreData:
    return load_store_data()


def make_agent(data: StoreData, tmp_path: Path, script: list[Any]) -> tuple[Agent, FakeResponses]:
    client = FakeClient(script)
    settings = Settings(api_key="sk-test", model="gpt-6-luna", reasoning_effort="low")
    agent = Agent(client, settings, data, clock=lambda: NOW, tickets_dir=tmp_path)
    return agent, client.responses


def outputs(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in request["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]


class TestReply:
    def test_answers_without_tools(self, data: StoreData, tmp_path: Path) -> None:
        agent, responses = make_agent(data, tmp_path, [text_response("Abrimos às 9h!")])
        reply = agent.reply("Que horas vocês abrem?")
        assert reply.text == "Abrimos às 9h!"
        assert reply.tool_calls == []
        assert not reply.failed

    def test_request_settings(self, data: StoreData, tmp_path: Path) -> None:
        agent, responses = make_agent(data, tmp_path, [text_response("Oi!")])
        agent.reply("Oi")
        request = responses.requests[0]
        assert request["model"] == "gpt-6-luna"
        assert request["reasoning"] == {"effort": "low"}
        assert request["store"] is False
        assert request["tool_choice"] == "auto"
        assert "<policies>" in request["instructions"]
        assert "20/02/2026" in request["instructions"]
        assert request["input"] == [{"role": "user", "content": "Oi"}]

    def test_runs_tool_and_returns_result_with_same_call_id(self, data: StoreData, tmp_path: Path) -> None:
        script = [tool_response("search_products", call_id="call_42", query="takamine gd20"), text_response("Temos!")]
        agent, responses = make_agent(data, tmp_path, script)
        reply = agent.reply("Tem o Takamine GD20?")

        assert reply.text == "Temos!"
        assert [call.name for call in reply.tool_calls] == ["search_products"]
        [output] = outputs(responses.requests[1])
        assert output["call_id"] == "call_42"
        assert json.loads(output["output"])["products"][0]["product_id"] == 95

    def test_invalid_arguments_go_back_to_the_model(self, data: StoreData, tmp_path: Path) -> None:
        script = [tool_response("get_price_quote", product_id=95, quantity=-1), text_response("Quantas unidades?")]
        agent, responses = make_agent(data, tmp_path, script)
        agent.reply("Quero -1 violão")
        [output] = outputs(responses.requests[1])
        assert json.loads(output["output"])["error"] == "Invalid arguments"

    def test_reasoning_items_are_sent_back_before_tool_results(self, data: StoreData, tmp_path: Path) -> None:
        reasoning = SimpleNamespace(type="reasoning", id="rs_1", encrypted_content="abc")
        first = tool_response("list_active_promotions")
        first.output.insert(0, reasoning)
        agent, responses = make_agent(data, tmp_path, [first, text_response("Promoções!")])
        agent.reply("Tem promoção?")

        second_input = responses.requests[1]["input"]
        assert second_input.index(reasoning) < second_input.index(outputs(responses.requests[1])[0])

    def test_history_is_kept_between_messages(self, data: StoreData, tmp_path: Path) -> None:
        agent, responses = make_agent(data, tmp_path, [text_response("R$ 2.199,00"), text_response("Em 10x...")])
        agent.reply("Quanto custa o Takamine GD20?")
        agent.reply("E em 10x?")
        second_input = responses.requests[1]["input"]
        assert second_input[0] == {"role": "user", "content": "Quanto custa o Takamine GD20?"}
        assert second_input[-1] == {"role": "user", "content": "E em 10x?"}

    def test_empty_answer_gets_a_fallback(self, data: StoreData, tmp_path: Path) -> None:
        agent, _ = make_agent(data, tmp_path, [text_response("   ")])
        assert agent.reply("Oi").text == EMPTY_REPLY


class TestToolCallLimit:
    def test_model_must_answer_after_the_limit(self, data: StoreData, tmp_path: Path) -> None:
        script = [
            tool_response("list_active_promotions", call_id=f"call_{n}") for n in range(MAX_TOOL_CALLS_PER_MESSAGE)
        ]
        agent, responses = make_agent(data, tmp_path, [*script, text_response("Aqui está.")])
        reply = agent.reply("Tem promoção?")

        assert reply.text == "Aqui está."
        assert len(reply.tool_calls) == MAX_TOOL_CALLS_PER_MESSAGE
        assert [request["tool_choice"] for request in responses.requests] == ["auto"] * 5 + ["none"]


class TestApiErrors:
    @pytest.mark.parametrize(
        ("error", "reply_text"),
        [
            (openai.APIConnectionError(request=REQUEST), CONNECTION_ERROR_REPLY),
            (openai.APITimeoutError(request=REQUEST), CONNECTION_ERROR_REPLY),
            (status_error(openai.RateLimitError, 429), BUSY_REPLY),
            (status_error(openai.InternalServerError, 500), GENERIC_ERROR_REPLY),
        ],
    )
    def test_friendly_message(self, data: StoreData, tmp_path: Path, error: Exception, reply_text: str) -> None:
        agent, _ = make_agent(data, tmp_path, [error])
        reply = agent.reply("Oi")
        assert reply.text == reply_text
        assert reply.failed

    def test_failed_message_is_removed_from_history(self, data: StoreData, tmp_path: Path) -> None:
        script = [
            text_response("Oi!"),
            tool_response("list_active_promotions"),
            openai.APIConnectionError(request=REQUEST),
        ]
        agent, _ = make_agent(data, tmp_path, script)
        agent.reply("Oi")
        history_before = list(agent.history)
        agent.reply("Tem promoção?")
        # No dangling function_call without its output, so the next request is valid.
        assert agent.history == history_before

    def test_invalid_key_is_not_logged(
        self, data: StoreData, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        error = status_error(openai.AuthenticationError, 401, "Incorrect API key provided: sk-secret123")
        agent, _ = make_agent(data, tmp_path, [error])
        with caplog.at_level(logging.ERROR):
            reply = agent.reply("Oi")
        assert reply.text == GENERIC_ERROR_REPLY
        assert "AuthenticationError" in caplog.text
        assert "sk-secret123" not in caplog.text


class TestToolSchemas:
    def test_one_flat_strict_schema_per_tool(self) -> None:
        assert [schema["name"] for schema in TOOL_SCHEMAS] == list(TOOLS)
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            assert "function" not in schema
            assert schema["strict"] is True
            assert schema["description"]

    def test_strict_mode_requirements(self) -> None:
        for schema in TOOL_SCHEMAS:
            parameters = schema["parameters"]
            assert parameters["additionalProperties"] is False
            assert set(parameters["required"]) == set(parameters["properties"])
