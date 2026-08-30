from __future__ import annotations

import json
import unittest
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from uc_advanced_automations.remote_auth import (
    API_KEY_NAME,
    RemoteAuthError,
    create_persistent_api_key,
)


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def text(self) -> str:
        return self._body


class _FakeSession:
    def __init__(
        self,
        *,
        post: list[_FakeResponse],
        get: list[_FakeResponse] | None = None,
        delete: list[_FakeResponse] | None = None,
    ) -> None:
        self._responses: dict[str, Iterator[_FakeResponse]] = {
            "POST": iter(post),
            "GET": iter(get or []),
            "DELETE": iter(delete or []),
        }
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def _next(self, method: str, url: str) -> _FakeResponse:
        self.calls.append((method, url))
        return next(self._responses[method])

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._next("POST", url)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._next("GET", url)

    def delete(self, url: str, **kwargs: Any) -> _FakeResponse:
        return self._next("DELETE", url)


class RemoteAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_replaces_existing_named_key_after_422(self) -> None:
        session = _FakeSession(
            post=[
                _FakeResponse(422, '{"message":"API key name already exists"}'),
                _FakeResponse(201, '{"api_key":"replacement-secret"}'),
            ],
            get=[
                _FakeResponse(
                    200,
                    '[{"key_id":"existing-key-id","name":"Advanced Automations"}]',
                )
            ],
            delete=[_FakeResponse(200, "{}")],
        )

        with patch(
            "uc_advanced_automations.remote_auth.aiohttp.ClientSession",
            return_value=session,
        ):
            endpoints, api_key = await create_persistent_api_key(
                "192.168.1.50",
                "1234",
            )

        self.assertEqual(api_key, "replacement-secret")
        self.assertEqual(endpoints.websocket_url, "ws://192.168.1.50/ws")
        self.assertEqual(
            session.calls,
            [
                ("POST", "http://192.168.1.50/api/auth/api_keys"),
                ("GET", "http://192.168.1.50/api/auth/api_keys"),
                ("DELETE", "http://192.168.1.50/api/auth/api_keys/existing-key-id"),
                ("POST", "http://192.168.1.50/api/auth/api_keys"),
            ],
        )

    async def test_does_not_retry_unrelated_422(self) -> None:
        session = _FakeSession(
            post=[_FakeResponse(422, '{"message":"validation failed"}')],
            get=[_FakeResponse(200, '[{"key_id":"other","name":"Other Integration"}]')],
        )

        with patch(
            "uc_advanced_automations.remote_auth.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(RemoteAuthError) as raised:
                await create_persistent_api_key("192.168.1.50", "1234")

        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(
            session.calls,
            [
                ("POST", "http://192.168.1.50/api/auth/api_keys"),
                ("GET", "http://192.168.1.50/api/auth/api_keys"),
            ],
        )

    async def test_custom_key_name_is_matched_exactly(self) -> None:
        custom_name = f"{API_KEY_NAME} test"
        session = _FakeSession(
            post=[
                _FakeResponse(422, "{}"),
                _FakeResponse(201, '{"api_key":"custom-secret"}'),
            ],
            get=[
                _FakeResponse(
                    200,
                    json.dumps([{"key_id": "custom-id", "name": custom_name}]),
                )
            ],
            delete=[_FakeResponse(200, "{}")],
        )

        with patch(
            "uc_advanced_automations.remote_auth.aiohttp.ClientSession",
            return_value=session,
        ):
            _, api_key = await create_persistent_api_key(
                "remote.local",
                "1234",
                key_name=custom_name,
            )

        self.assertEqual(api_key, "custom-secret")
        self.assertIn(
            ("DELETE", "http://remote.local/api/auth/api_keys/custom-id"),
            session.calls,
        )


if __name__ == "__main__":
    unittest.main()
