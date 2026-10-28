"""HTTP client for Relay's `POST /v1/receipts`. Standard library only, so the Lambda package has no
third-party dependencies."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import lru_cache

from relay_receipts.ports import RelayOutcome, RelayUnavailableError
from relay_receipts.receipt import Receipt

DEFAULT_TIMEOUT_SECONDS = 5.0


class HttpRelay:
    def __init__(
        self,
        base_url: str,
        api_key: Callable[[], str | None],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("RELAY_URL must start with http:// or https://")
        self._url = f"{base_url.rstrip('/')}/v1/receipts"
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_environment(cls) -> HttpRelay:
        parameter = os.environ.get("RELAY_API_KEY_PARAM")
        return cls(os.environ["RELAY_URL"], lambda: _parameter(parameter) if parameter else None)

    def deliver(self, receipt: Receipt) -> RelayOutcome:
        headers = {"content-type": "application/json"}
        key = self._api_key()
        if key:
            headers["x-api-key"] = key
        request = urllib.request.Request(  # noqa: S310 (scheme checked in __init__)
            self._url, data=json.dumps(receipt.to_json()).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return "duplicate" if response.status == 200 else "recorded"
        except urllib.error.HTTPError as error:
            return self._on_error(error)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise RelayUnavailableError(f"cannot reach relay: {error}") from error

    @staticmethod
    def _on_error(error: urllib.error.HTTPError) -> RelayOutcome:
        if error.code == 404:
            return "unmatched"
        if error.code in (400, 422):
            return "rejected"
        # 401/403 (wrong credentials), 429 and 5xx all deserve another try, and an alarm.
        raise RelayUnavailableError(f"relay answered {error.code}") from error


@lru_cache(maxsize=4)
def _parameter(name: str) -> str:
    import boto3

    client = boto3.client("ssm")
    value: str = client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return value
