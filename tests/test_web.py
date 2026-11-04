import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from emporio import web
from emporio.agent import AgentReply, ToolCall
from emporio.web import BadRequest, ChatApp, create_server

CONVERSATION = "11111111-aaaa-bbbb-cccc-222222222222"
OTHER_CONVERSATION = "33333333-aaaa-bbbb-cccc-444444444444"


class FakeAgent:
    """Echoes the messages it received, so tests can see each conversation's history."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def reply(self, message: str) -> AgentReply:
        self.messages.append(message)
        return AgentReply(
            text=" | ".join(self.messages),
            tool_calls=[ToolCall("search_products", '{"query": "takamine gd20", "category": null}')],
        )


def message(text: str, conversation_id: str = CONVERSATION) -> dict[str, Any]:
    return {"conversation_id": conversation_id, "message": text}


@pytest.fixture
def app() -> ChatApp:
    return ChatApp(FakeAgent, reference_date=date(2026, 2, 20))


class TestChatApp:
    def test_returns_reply_and_formatted_tool_calls(self, app: ChatApp) -> None:
        answer = app.handle_message(message("Tem o Takamine GD20?"))
        assert answer == {
            "reply": "Tem o Takamine GD20?",
            "tool_calls": ['search_products(query="takamine gd20")'],
            "failed": False,
        }

    def test_same_conversation_keeps_history(self, app: ChatApp) -> None:
        app.handle_message(message("Oi"))
        assert app.handle_message(message("Quanto custa?"))["reply"] == "Oi | Quanto custa?"

    def test_conversations_do_not_mix(self, app: ChatApp) -> None:
        app.handle_message(message("Oi"))
        assert app.handle_message(message("Olá", OTHER_CONVERSATION))["reply"] == "Olá"

    @pytest.mark.parametrize(
        "body",
        [
            message("   "),
            message("Oi", "curto"),
            message("Oi", "../../etc/passwd"),
            {"message": "Oi"},
            message("x" * (web.MAX_MESSAGE_LENGTH + 1)),
        ],
    )
    def test_rejects_invalid_messages(self, app: ChatApp, body: dict[str, Any]) -> None:
        with pytest.raises(BadRequest):
            app.handle_message(body)

    def test_oldest_conversation_is_dropped(self, app: ChatApp, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web, "MAX_CONVERSATIONS", 1)
        app.handle_message(message("Oi"))
        app.handle_message(message("Olá", OTHER_CONVERSATION))
        assert app.handle_message(message("De novo"))["reply"] == "De novo"

    def test_info_shows_reference_date(self, app: ChatApp) -> None:
        assert app.info() == {"reference_date": "20/02/2026"}
        assert ChatApp(FakeAgent).info() == {"reference_date": None}


class TestToolCallDescription:
    def test_omits_empty_arguments(self) -> None:
        call = ToolCall("search_products", '{"category": "Violões", "max_price": 1000, "query": null}')
        assert call.describe() == 'search_products(category="Violões", max_price=1000)'

    def test_no_arguments(self) -> None:
        assert ToolCall("list_active_promotions", "{}").describe() == "list_active_promotions()"


@pytest.fixture
def base_url(app: ChatApp) -> Iterator[str]:
    server = create_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def post(url: str, body: bytes) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class TestHttpServer:
    def test_serves_the_chat_page(self, base_url: str) -> None:
        with urllib.request.urlopen(base_url + "/") as response:
            assert "Empório da Música" in response.read().decode()
            assert response.headers["Content-Type"].startswith("text/html")

    def test_message_endpoint(self, base_url: str) -> None:
        status, answer = post(base_url + "/api/message", json.dumps(message("Oi")).encode())
        assert status == 200
        assert answer["reply"] == "Oi"

    def test_invalid_json_is_a_bad_request(self, base_url: str) -> None:
        status, answer = post(base_url + "/api/message", b"{not json")
        assert status == 400
        assert "error" in answer

    def test_unknown_path_is_not_found(self, base_url: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base_url + "/../emporio/config.py")
        assert error.value.code == 404

    def test_listens_only_locally(self, app: ChatApp) -> None:
        server = create_server(app, port=0)
        try:
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.server_close()
