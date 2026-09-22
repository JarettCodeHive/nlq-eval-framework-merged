"""Pulse token refresh: Cognito refresh grant + the Claris token exchange.

Endpoint shapes were captured from Studio's login on 2026-09-21. The exchange
*response* shape was not observed, so these tests pin the behaviour that makes
that safe: the platform token is found by audience, never by field name.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from judge.pulse_auth import (
    DEFAULT_EXCHANGE_PATH,
    PulseRefreshError,
    build_cognito_provider,
    claris_exchange,
    cognito_refresh,
    expected_audience,
)

COGNITO_AUD = "1orc9knial20pdfguri4mn40pm"
PLATFORM_AUD = "30henrpro070ji15vqg01918h2"
BASE_URL = "https://api-qa.platform.claris.com"


def _jwt(**claims) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.signature"


def _platform_token(minutes: int = 60) -> str:
    return _jwt(
        iss="claris.com",
        aud=[PLATFORM_AUD],
        token_use="id",
        exp=int(time.time()) + minutes * 60,
    )


def _cognito_token(token_use: str = "id") -> str:
    return _jwt(
        iss="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_JtNIWdiHP",
        aud=COGNITO_AUD,
        token_use=token_use,
        exp=int(time.time()) + 3600,
    )


def _mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- audience -------------------------------------------------------------


def test_expected_audience_unwraps_a_list_claim():
    assert expected_audience(_platform_token()) == PLATFORM_AUD


def test_expected_audience_is_none_for_a_non_jwt():
    assert expected_audience("not-a-jwt") is None


# --- step 1: Cognito ------------------------------------------------------


def test_cognito_refresh_sends_the_refresh_token_auth_flow():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["target"] = request.headers["x-amz-target"]
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "AuthenticationResult": {
                    "AccessToken": _cognito_token("access"),
                    "IdToken": _cognito_token("id"),
                    "ExpiresIn": 3600,
                }
            },
        )

    result = cognito_refresh(
        region="us-west-2",
        client_id=COGNITO_AUD,
        refresh_token="refresh-abc",
        client=_mock(handler),
    )
    assert seen["url"] == "https://cognito-idp.us-west-2.amazonaws.com/"
    assert seen["target"] == "AWSCognitoIdentityProviderService.InitiateAuth"
    assert seen["content_type"] == "application/x-amz-json-1.1"
    assert seen["body"] == {
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "ClientId": COGNITO_AUD,
        "AuthParameters": {"REFRESH_TOKEN": "refresh-abc"},
    }
    assert result["IdToken"]


def test_cognito_not_authorized_names_the_likely_cause():
    def handler(request):
        return httpx.Response(
            400, json={"__type": "NotAuthorizedException", "message": "Invalid"}
        )

    with pytest.raises(PulseRefreshError, match="expired or"):
        cognito_refresh(
            region="us-west-2",
            client_id=COGNITO_AUD,
            refresh_token="stale",
            client=_mock(handler),
        )


def test_cognito_200_without_tokens_is_an_error_not_a_silent_none():
    def handler(request):
        return httpx.Response(200, json={"ChallengeName": "NEW_PASSWORD_REQUIRED"})

    with pytest.raises(PulseRefreshError, match="AuthenticationResult"):
        cognito_refresh(
            region="us-west-2",
            client_id=COGNITO_AUD,
            refresh_token="r",
            client=_mock(handler),
        )


# --- step 2: the Claris exchange -----------------------------------------


def _exchange(handler):
    return claris_exchange(
        base_url=BASE_URL,
        cognito_client_id=COGNITO_AUD,
        access_token=_cognito_token("access"),
        id_token=_cognito_token("id"),
        refresh_token="refresh-abc",
        expected_aud=PLATFORM_AUD,
        client=_mock(handler),
    )


def test_exchange_posts_the_cognito_tokens_to_auth_token():
    seen = {}
    wanted = _platform_token()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"token": wanted})

    assert _exchange(handler) == wanted
    assert seen["url"] == BASE_URL + DEFAULT_EXCHANGE_PATH
    assert set(seen["body"]) == {
        "clientID",
        "access_token",
        "id_token",
        "refresh_token",
    }


def test_exchange_picks_the_platform_token_not_the_cognito_one():
    """The response carries both. Selecting by `aud` is what makes it right —
    returning the Cognito token would 401 against Pulse on every question."""
    wanted = _platform_token()

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": _cognito_token("access"),
                "id_token": _cognito_token("id"),  # right shape, WRONG audience
                "session": {"platform_token": wanted},
            },
        )

    assert _exchange(handler) == wanted


def test_exchange_finds_a_bare_token_in_a_text_body():
    wanted = _platform_token()
    assert _exchange(lambda r: httpx.Response(200, text=wanted)) == wanted


def test_exchange_without_a_matching_audience_says_where_to_look():
    def handler(request):
        return httpx.Response(200, json={"id_token": _cognito_token("id")})

    with pytest.raises(PulseRefreshError, match="PULSE_AUTH_EXCHANGE_PATH"):
        _exchange(handler)


def test_exchange_non_200_is_reported_with_the_url():
    def handler(request):
        return httpx.Response(403, text="forbidden")

    with pytest.raises(PulseRefreshError, match="auth/token"):
        _exchange(handler)


# --- step 3: following the authorization URL ------------------------------
#
# /auth/token answers {"URL": ...} rather than a token, so the chain has a third
# hop. The token can arrive in the body, in a redirect fragment, or as a cookie —
# all three are exercised here because which one it is was not observable.

AUTHORIZE_URL = "https://api-qa.platform.claris.com/authorization?code=abc"


def _chain(handler):
    """Run the exchange with a shared client, as the provider does."""
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return claris_exchange(
        base_url=BASE_URL,
        cognito_client_id=COGNITO_AUD,
        access_token=_cognito_token("access"),
        id_token=_cognito_token("id"),
        refresh_token="refresh-abc",
        expected_aud=PLATFORM_AUD,
        client=client,
    )


def test_exchange_follows_the_url_and_reads_the_token_from_the_body():
    wanted = _platform_token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(200, json={"URL": AUTHORIZE_URL})
        return httpx.Response(200, json={"id_token": wanted})

    assert _chain(handler) == wanted


def test_exchange_reads_the_token_out_of_a_redirect_fragment():
    """An OIDC implicit response puts the token in the fragment, which never
    reaches the server — it has to be read off the redirect chain."""
    wanted = _platform_token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(200, json={"URL": AUTHORIZE_URL})
        if request.url.path == "/authorization":
            return httpx.Response(
                302,
                headers={
                    "location": f"https://studio-qa.platform.claris.com/#id_token={wanted}"
                },
            )
        return httpx.Response(200, text="landed")

    assert _chain(handler) == wanted


def test_exchange_reads_the_token_out_of_a_cookie():
    wanted = _platform_token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(200, json={"URL": AUTHORIZE_URL})
        return httpx.Response(
            200, text="ok", headers={"set-cookie": f"mag.t={wanted}; Path=/"}
        )

    assert _chain(handler) == wanted


def test_exchange_says_it_followed_the_url_when_the_token_is_nowhere():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(200, json={"URL": AUTHORIZE_URL})
        return httpx.Response(200, json={"nothing": "useful"})

    with pytest.raises(PulseRefreshError, match="Redeemed the authorization code"):
        _chain(handler)


def test_cookies_from_the_exchange_travel_to_the_authorize_hop():
    """The authorization call is authenticated by the mag.* cookies the exchange
    sets, so both hops must share one cookie jar."""
    seen = {}
    wanted = _platform_token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(
                200,
                json={"URL": AUTHORIZE_URL},
                headers={"set-cookie": "mag.s=session-value; Path=/"},
            )
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={"id_token": wanted})

    assert _chain(handler) == wanted
    assert "mag.s=session-value" in seen["cookie"]


def test_exchange_redeems_a_bare_authorization_code_at_the_mag_uri_endpoint():
    """QA returns `{"URL": "?code=..."}` — a query string, not a URL. It belongs
    on the authorization endpoint named by the `mag.uri` cookie. Joining it to
    /auth/token instead would re-POST the exchange and never mint a token."""
    wanted = _platform_token()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(
                200,
                json={"URL": "?code=173ec8c3ee08c65c600baa63f08a7f824b54ac82"},
                headers={
                    "set-cookie": "mag.uri=https://api-qa.platform.claris.com/authorization; Path=/"
                },
            )
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"id_token": wanted})

    assert _chain(handler) == wanted
    assert seen["url"] == (
        "https://api-qa.platform.claris.com/authorization"
        "?code=173ec8c3ee08c65c600baa63f08a7f824b54ac82"
    )


def test_bare_code_falls_back_to_the_default_authorize_path(monkeypatch):
    """Without the cookie, /authorization on the API host is the fallback."""
    monkeypatch.delenv("PULSE_AUTHORIZE_PATH", raising=False)
    wanted = _platform_token()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            return httpx.Response(200, json={"URL": "?code=abc"})
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"id_token": wanted})

    assert _chain(handler) == wanted
    assert seen["url"] == "https://api-qa.platform.claris.com/authorization?code=abc"


def test_exchange_seeds_the_mag_cookies_before_posting():
    """A browser already holds mag.cid/mag.uri/mag.non when it calls /auth/token.
    Without them the returned code is orphaned and redemption fails with
    "[DAL-08] invalid authorization code"."""
    import urllib.parse

    wanted = _platform_token()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DEFAULT_EXCHANGE_PATH:
            seen["cookie"] = request.headers.get("cookie", "")
            return httpx.Response(200, json={"URL": "?code=abc"})
        return httpx.Response(200, json={"id_token": wanted})

    assert _chain(handler) == wanted
    jar = urllib.parse.unquote(seen["cookie"])
    assert f"mag.cid={PLATFORM_AUD}" in jar
    assert "mag.uri=https://api-qa.platform.claris.com/authorization" in jar
    assert "mag.non=" in jar


def test_seeded_nonce_is_fresh_16_bytes_and_percent_encoded():
    """The nonce is echoed into the token's `nonce` claim, so it must be new per
    refresh — a fixed one would make every token look like a replay."""
    import base64 as b64
    import urllib.parse

    from judge.pulse_auth import seed_mag_cookies

    nonces = set()
    for _ in range(3):
        client = httpx.Client()
        nonce = seed_mag_cookies(
            client,
            platform_aud=PLATFORM_AUD,
            authorize_base="https://api-qa.platform.claris.com/authorization",
        )
        nonces.add(nonce)
        assert len(b64.b64decode(nonce)) == 16
        stored = client.cookies.get("mag.non")
        assert urllib.parse.unquote(stored) == nonce
    assert len(nonces) == 3, "a fresh nonce per call"


# --- provider assembly ---------------------------------------------------


def _settings(token: str | None = None, **kw):
    from judge.pulse_client import PulseSettings

    return PulseSettings(
        base_url=BASE_URL,
        auth_token=token if token is not None else _platform_token(),
        org_id=4104,
        **kw,
    )


def test_provider_is_none_when_no_refresh_token_is_configured(monkeypatch):
    monkeypatch.delenv("PULSE_REFRESH_TOKEN", raising=False)
    assert build_cognito_provider(_settings()) is None


def test_provider_demands_the_cognito_client_id(monkeypatch):
    monkeypatch.setenv("PULSE_REFRESH_TOKEN", "refresh-abc")
    monkeypatch.delenv("PULSE_COGNITO_CLIENT_ID", raising=False)
    with pytest.raises(PulseRefreshError, match="PULSE_COGNITO_CLIENT_ID"):
        build_cognito_provider(_settings())


def test_provider_uses_the_same_tls_settings_as_the_chat_client(monkeypatch):
    """Refresh must verify TLS the way the requests it authenticates do.
    A CA bundle configured for the platform has to apply to /auth/token too,
    or refresh breaks on a corp network while the chat client works."""
    monkeypatch.setenv("PULSE_REFRESH_TOKEN", "refresh-abc")
    monkeypatch.setenv("PULSE_COGNITO_CLIENT_ID", COGNITO_AUD)
    settings = _settings(ca_bundle="/etc/corp-ca.pem")
    assert build_cognito_provider(settings) is not None

    from judge.pulse_client import resolve_tls_verify

    assert resolve_tls_verify(settings) == "/etc/corp-ca.pem"


def test_provider_refuses_when_the_current_token_has_no_audience(monkeypatch):
    """Without a known audience the refreshed token cannot be identified, so
    failing here beats silently sending Cognito's token to Pulse."""
    monkeypatch.setenv("PULSE_REFRESH_TOKEN", "refresh-abc")
    monkeypatch.setenv("PULSE_COGNITO_CLIENT_ID", COGNITO_AUD)
    with pytest.raises(PulseRefreshError, match="aud"):
        build_cognito_provider(_settings(token="not-a-jwt"))


# --- transport failures ---------------------------------------------------


class _FakeProxyError(Exception):
    """Stands in for httpx.ProxyError without importing httpx internals."""


def test_transport_failure_is_a_refresh_error_not_a_raw_traceback():
    """A proxy refusing the call is a network-policy problem. Surfacing it as an
    httpx traceback from inside a token refresh reads like a code fault."""

    def handler(request):
        raise _FakeProxyError("403 Forbidden")

    with pytest.raises(PulseRefreshError) as err:
        cognito_refresh(
            region="us-west-2",
            client_id=COGNITO_AUD,
            refresh_token="r",
            client=_mock(handler),
        )
    message = str(err.value)
    assert "Cognito refresh failed" in message
    assert "cognito-idp.us-west-2.amazonaws.com" in message


def test_transport_failure_in_the_exchange_names_that_step():
    """Each step names itself, so a failure says which half of the chain broke."""

    def handler(request):
        raise _FakeProxyError("403 Forbidden")

    with pytest.raises(PulseRefreshError, match="Claris token exchange failed"):
        _exchange(handler)
