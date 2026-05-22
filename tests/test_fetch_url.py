from server.app.pipeline import fetch_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_get_token_uses_configured_token_generator(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse({"data": {"token": "generated-token"}})

    monkeypatch.setattr(fetch_url.requests, "post", fake_post)

    token = fetch_url.get_token(
        "prod",
        {
            "token_gen": {
                "app_id": "app-from-config",
                "nonce": "nonce-from-config",
                "secret": "secret-from-config",
                "url": "http://token.example.test",
            }
        },
    )

    assert token == "generated-token"
    assert calls[0]["url"] == "http://token.example.test"
    assert calls[0]["json"]["app_id"] == "app-from-config"
    assert calls[0]["json"]["nonce"] == "nonce-from-config"
    assert calls[0]["json"]["secret"] == "secret-from-config"


def test_get_token_returns_none_without_configured_credentials(monkeypatch):
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    monkeypatch.delenv("BASECMS_NONCE", raising=False)
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN_URL", raising=False)

    assert fetch_url.get_token("prod", {}) is None
