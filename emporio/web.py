"""Local chat page: a small HTTP server in front of the agent.

Uses only the standard library (``http.server``). The page in ``static/``
sends each message to ``POST /api/message`` and shows the answer. Every
browser tab is a separate conversation, kept in memory.
"""

import argparse
import json
import logging
import re
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from emporio.agent import Agent, create_client
from emporio.config import ConfigError, load_settings
from emporio.data_loader import DataValidationError, load_store_data

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/estilo.css": ("estilo.css", "text/css; charset=utf-8"),
    "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
}

DEFAULT_PORT = 8000
MAX_MESSAGE_LENGTH = 2000
MAX_BODY_BYTES = 16_000
# Old conversations are dropped beyond this, so memory stays bounded.
MAX_CONVERSATIONS = 200
CONVERSATION_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{8,64}")


class BadRequest(ValueError):
    """The page sent something the server cannot accept."""


class ChatApp:
    """Keeps one agent per conversation and turns messages into JSON answers."""

    def __init__(self, new_agent: Callable[[], Agent], reference_date: date | None = None) -> None:
        """Create the app.

        Args:
            new_agent: Builds an agent with an empty history.
            reference_date: Shown on the page when the date is fixed for a demo.
        """
        self._new_agent = new_agent
        self._reference_date = reference_date
        self._agents: OrderedDict[str, Agent] = OrderedDict()
        self._lock = threading.Lock()

    def info(self) -> dict[str, Any]:
        """Return what the page shows besides the conversation.

        Returns:
            The reference date as DD/MM/YYYY, or None when today is real.
        """
        reference = self._reference_date
        return {"reference_date": reference.strftime("%d/%m/%Y") if reference else None}

    def handle_message(self, body: dict[str, Any]) -> dict[str, Any]:
        """Answer one message from the page.

        Args:
            body: JSON body with ``conversation_id`` and ``message``.

        Returns:
            The answer text, the tool calls formatted for display, and
            whether the agent failed to reach the model.

        Raises:
            BadRequest: If the conversation id or the message is invalid.
        """
        conversation_id = body.get("conversation_id")
        message = body.get("message")
        if not isinstance(conversation_id, str) or not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
            raise BadRequest("conversation_id inválido")
        if not isinstance(message, str) or not message.strip():
            raise BadRequest("mensagem vazia")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise BadRequest(f"mensagem com mais de {MAX_MESSAGE_LENGTH} caracteres")

        reply = self._agent_for(conversation_id).reply(message.strip())
        return {
            "reply": reply.text,
            "tool_calls": [call.describe() for call in reply.tool_calls],
            "failed": reply.failed,
        }

    def _agent_for(self, conversation_id: str) -> Agent:
        with self._lock:
            agent = self._agents.get(conversation_id)
            if agent is None:
                agent = self._agents[conversation_id] = self._new_agent()
                if len(self._agents) > MAX_CONVERSATIONS:
                    self._agents.popitem(last=False)
            self._agents.move_to_end(conversation_id)
            return agent


def create_server(app: ChatApp, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create the HTTP server for the chat page.

    Args:
        app: Conversations and message handling.
        port: TCP port; 0 picks a free one (used in tests).
        host: Address to listen on. The default accepts only local connections.

    Returns:
        A server ready for ``serve_forever()``.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/info":
                return self._send_json(app.info())
            if path not in STATIC_FILES:
                return self._send_json({"error": "não encontrado"}, 404)
            name, content_type = STATIC_FILES[path]
            self._send(200, (STATIC_DIR / name).read_bytes(), content_type)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/message":
                return self._send_json({"error": "não encontrado"}, 404)
            try:
                answer = app.handle_message(self._read_json())
            except BadRequest as error:
                return self._send_json({"error": str(error)}, 400)
            except Exception:
                logger.exception("Unexpected error while answering a message")
                return self._send_json({"error": "erro interno"}, 500)
            self._send_json(answer)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise BadRequest("pedido grande demais")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as error:
                raise BadRequest("JSON inválido") from error
            if not isinstance(body, dict):
                raise BadRequest("JSON inválido")
            return body

        def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    """Start the chat page from the command line (``python -m emporio``).

    Args:
        argv: Command-line arguments; defaults to ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Empório da Música: chat local com o agente.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"porta (padrão: {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        settings = load_settings()
        data = load_store_data()
    except (ConfigError, DataValidationError) as error:
        print(f"Não foi possível iniciar: {error}", file=sys.stderr)
        return 1

    client = create_client(settings)
    app = ChatApp(lambda: Agent(client, settings, data), settings.reference_date)
    try:
        server = create_server(app, args.port)
    except OSError as error:
        print(f"Não foi possível usar a porta {args.port}: {error.strerror}. Tente --port 8080.", file=sys.stderr)
        return 1

    print(f"Empório da Música rodando em http://localhost:{server.server_port}  (Ctrl+C para encerrar)")
    if settings.reference_date:
        print(f"Data de referência: {settings.reference_date:%d/%m/%Y}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()
    return 0
