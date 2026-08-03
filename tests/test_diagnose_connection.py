import asyncio

from tests import diagnose_connection as dc


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *_args, **_kwargs):
        return self._response


def test_test_authentication_accepts_http_201_with_token(monkeypatch):
    response = _FakeResponse(201, {"token": "abc123"})

    monkeypatch.setattr(
        dc.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(response),
    )

    ok, msg = asyncio.run(
        dc.test_authentication(
            host="https://apstra.example.com",
            username="admin",
            password="secret",
            ssl_verify=False,
        )
    )

    assert ok is True
    assert "HTTP 201" in msg
    assert "token obtained" in msg


def test_test_authentication_rejects_2xx_without_token(monkeypatch):
    response = _FakeResponse(200, {"message": "ok"})

    monkeypatch.setattr(
        dc.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(response),
    )

    ok, msg = asyncio.run(
        dc.test_authentication(
            host="https://apstra.example.com",
            username="admin",
            password="secret",
            ssl_verify=False,
        )
    )

    assert ok is False
    assert "no token" in msg
