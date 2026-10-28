from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load() -> Any:
    def _load(name: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf8"))
        return loaded

    return _load


def sqs_event(*bodies: str) -> dict[str, Any]:
    return {
        "Records": [
            {"messageId": f"sqs-{index}", "body": body, "eventSource": "aws:sqs"}
            for index, body in enumerate(bodies)
        ]
    }
