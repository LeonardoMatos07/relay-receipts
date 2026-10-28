from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from relay_receipts import ses
from relay_receipts.ports import RelayUnavailableError
from relay_receipts.receipt import Receipt
from relay_receipts.relay import HttpRelay


class FakeRelayServer:
    """A real HTTP server that answers like Relay's POST /v1/receipts."""

    def __init__(self) -> None:
        self.status = 202
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                owner.requests.append(
                    {
                        "path": self.path,
                        "key": self.headers.get("x-api-key"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                self.send_response(owner.status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FakeRelayServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def relay_server() -> Iterator[FakeRelayServer]:
    with FakeRelayServer() as server:
        yield server


@pytest.fixture
def receipt(load: Any) -> Receipt:
    return ses.normalize(load("ses_bounce"))[0]


def test_posts_the_receipt_with_the_api_key(
    relay_server: FakeRelayServer, receipt: Receipt
) -> None:
    relay = HttpRelay(relay_server.url, lambda: "secret")

    assert relay.deliver(receipt) == "recorded"

    request = relay_server.requests[0]
    assert request["path"] == "/v1/receipts"
    assert request["key"] == "secret"
    assert request["body"] == receipt.to_json()


def test_works_without_an_api_key(relay_server: FakeRelayServer, receipt: Receipt) -> None:
    HttpRelay(relay_server.url, lambda: None).deliver(receipt)
    assert relay_server.requests[0]["key"] is None


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (202, "recorded"),
        (200, "duplicate"),
        (404, "unmatched"),
        (400, "rejected"),
        (422, "rejected"),
    ],
)
def test_maps_relays_answers(
    relay_server: FakeRelayServer, receipt: Receipt, status: int, outcome: str
) -> None:
    relay_server.status = status
    assert HttpRelay(relay_server.url, lambda: None).deliver(receipt) == outcome


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
def test_anything_else_is_worth_retrying(
    relay_server: FakeRelayServer, receipt: Receipt, status: int
) -> None:
    relay_server.status = status
    with pytest.raises(RelayUnavailableError, match=str(status)):
        HttpRelay(relay_server.url, lambda: None).deliver(receipt)


def test_an_unreachable_relay_is_worth_retrying(receipt: Receipt) -> None:
    with pytest.raises(RelayUnavailableError):
        HttpRelay("http://127.0.0.1:1", lambda: None, timeout=1).deliver(receipt)


def test_the_url_must_be_http() -> None:
    with pytest.raises(ValueError, match="http"):
        HttpRelay("file:///etc/passwd", lambda: None)
