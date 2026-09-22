"""Pulse token refresh — Cognito refresh grant, then the Claris token exchange.

WHY TWO STEPS
-------------
The token Pulse accepts is NOT the Cognito token. Captured from Studio's login,
two distinct ID tokens exist:

    Cognito  iss=https://cognito-idp.us-west-2.amazonaws.com/us-west-2_JtNIWdiHP
             aud=1orc9knial20pdfguri4mn40pm
    Pulse    iss=claris.com
             aud=30henrpro070ji15vqg01918h2      (= the `mag.cid` cookie)

Cognito authenticates the user; Claris then mints its own ID token from the
Cognito ones. So refreshing is:

    1. Cognito InitiateAuth / REFRESH_TOKEN_AUTH
         -> a fresh Cognito AccessToken + IdToken (the refresh token itself is
            long-lived — a Cognito default of 30 days — and REFRESH_TOKEN_AUTH
            does not return a new one unless rotation is enabled)
    2. POST {PULSE_BASE_URL}/auth/token with those tokens
         -> the `claris.com` ID token that `--pulse live` sends as its Bearer

Step 2's response shape is CONFIRM-ENDPOINT: the request was captured from the
browser, the response was not. Rather than hardcode a field name, the platform
token is located by matching the `aud` of the token already in `judge/.env` —
which is by definition the audience Pulse accepts. Run `--probe` to confirm the
whole chain against the real service before relying on it in a run.

    python judge/pulse_auth.py --probe

Configure in judge/.env:

    PULSE_REFRESH_TOKEN=<Cognito refresh token>
    PULSE_COGNITO_CLIENT_ID=1orc9knial20pdfguri4mn40pm
    PULSE_COGNITO_REGION=us-west-2
    PULSE_AUTH_EXCHANGE_PATH=/auth/token     # default

A Cognito refresh token mints access tokens for weeks. It is a far more
sensitive credential than the hour-long ID token — keep it in judge/.env only.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

COGNITO_INITIATE_AUTH = "AWSCognitoIdentityProviderService.InitiateAuth"
DEFAULT_COGNITO_REGION = "us-west-2"
DEFAULT_EXCHANGE_PATH = "/auth/token"
# Where the authorization code is redeemed when the `mag.uri` cookie is absent.
DEFAULT_AUTHORIZE_PATH = "/authorization"
_COGNITO_CONTENT_TYPE = "application/x-amz-json-1.1"


class PulseRefreshError(RuntimeError):
    """Refresh failed. The message names which of the two steps broke."""


def _claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature."""

    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        part = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def expected_audience(token: str) -> str | None:
    """The `aud` of a token Pulse already accepted.

    Used to pick the platform token out of an exchange response that also
    carries the Cognito tokens — matching on audience rather than on a field
    name means a response-schema change upstream does not break refresh.
    """

    aud = _claims(token).get("aud")
    if isinstance(aud, list):
        return str(aud[0]) if aud else None
    return str(aud) if aud else None


def _audience_of(token: str) -> set[str]:
    aud = _claims(token).get("aud")
    if isinstance(aud, list):
        return {str(a) for a in aud}
    return {str(aud)} if aud else set()


def _find_platform_token(payload: Any, expected_aud: str) -> str | None:
    """Walk a JSON body for an ID token whose `aud` is the one Pulse accepts."""

    if isinstance(payload, str):
        claims = _claims(payload)
        if claims.get("token_use") == "id" and expected_aud in _audience_of(payload):
            return payload
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            found = _find_platform_token(value, expected_aud)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_platform_token(value, expected_aud)
            if found:
                return found
    return None


def _post(client: Any, owns: bool, step: str, url: str, **kwargs) -> Any:
    """POST and turn transport failures into a PulseRefreshError.

    Without this a corp proxy or a TLS-inspection CA surfaces as a raw httpx
    traceback from inside a token refresh, which reads like a code fault rather
    than the network-policy problem it usually is.
    """

    try:
        return client.post(url, **kwargs)
    except Exception as exc:
        name = type(exc).__name__
        hint = ""
        if "Proxy" in name:
            hint = (
                " A proxy rejected the request — check HTTPS_PROXY and whether "
                f"{urlparse(url).netloc} is allowed through it."
            )
        elif "SSL" in name or "Certificate" in name:
            hint = (
                " TLS verification failed — on a corp machine set PULSE_CA_BUNDLE "
                "to the inspection CA, or install the `truststore` package."
            )
        elif "Timeout" in name or "Connect" in name:
            hint = f" Could not reach {urlparse(url).netloc}."
        raise PulseRefreshError(f"{step} failed: {name}: {exc}.{hint}") from exc
    finally:
        if owns:
            client.close()


def cognito_refresh(
    *,
    region: str,
    client_id: str,
    refresh_token: str,
    timeout_s: float = 30.0,
    verify: Any = True,
    client: Any | None = None,
) -> dict[str, str]:
    """Step 1 — exchange the refresh token for fresh Cognito tokens.

    Returns Cognito's `AuthenticationResult`. Note it contains no RefreshToken:
    REFRESH_TOKEN_AUTH reuses the one you sent unless the pool rotates them.
    """

    url = f"https://cognito-idp.{region}.amazonaws.com/"
    body = {
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "ClientId": client_id,
        "AuthParameters": {"REFRESH_TOKEN": refresh_token},
    }
    headers = {
        "Content-Type": _COGNITO_CONTENT_TYPE,
        "X-Amz-Target": COGNITO_INITIATE_AUTH,
    }

    owns = client is None
    if owns:
        import httpx

        client = httpx.Client(timeout=timeout_s, verify=verify)
    resp = _post(
        client,
        owns,
        "Cognito refresh",
        url,
        content=json.dumps(body),
        headers=headers,
    )

    if resp.status_code != 200:
        # Cognito puts a machine-readable reason in __type, e.g.
        # NotAuthorizedException when the refresh token has expired or been
        # revoked by a global sign-out.
        detail = resp.text[:300]
        raise PulseRefreshError(
            f"Cognito refresh failed ({resp.status_code}): {detail!r}. "
            "If this is NotAuthorizedException the refresh token is expired or "
            "revoked — sign in to Studio again and re-capture it."
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise PulseRefreshError(
            f"Cognito returned a non-JSON 200: {resp.text[:200]!r}"
        ) from exc

    result = payload.get("AuthenticationResult") or {}
    if not result.get("IdToken") or not result.get("AccessToken"):
        raise PulseRefreshError(
            "Cognito 200 carried no AuthenticationResult.IdToken/AccessToken: "
            f"{str(payload)[:200]!r}"
        )
    return result


def _find_url(payload: Any, base: str = "") -> str | None:
    """Find a followable URL in a decoded body.

    `/auth/token` answers `{"URL": "..."}` rather than handing back a token: it
    points at the authorization endpoint that actually mints the `claris.com`
    token (the same place the `mag.uri` cookie names). Matching on shape rather
    than on the key name keeps this working if the casing changes.

    Accepts absolute, protocol-relative and root-relative forms, resolving the
    latter two against `base` — an API that returns `/authorization?...` is as
    common as one that returns the full URL, and treating only absolute URLs as
    real is how this first came back "no URL to follow" for a body that had one.
    """

    if isinstance(payload, str):
        value = payload.strip()
        if not value:
            return None
        if value.startswith(("https://", "http://")):
            return value
        if value.startswith("//") or value.startswith("/"):
            return urljoin(base, value) if base else None
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            found = _find_url(value, base)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_url(value, base)
            if found:
                return found
    return None


def _tokens_in_url(url: str) -> list[str]:
    """Pull JWT-shaped values out of a URL's query string and fragment.

    An OIDC implicit/hybrid response puts the token in the fragment, which never
    reaches the server — so it has to be read off the redirect chain rather than
    out of a response body.
    """

    parsed = urlparse(url)
    found: list[str] = []
    for blob in (parsed.query, parsed.fragment):
        if not blob:
            continue
        for _, values in parse_qs(blob).items():
            for value in values:
                if value.count(".") == 2 and value.startswith("ey"):
                    found.append(value)
    return found


def _rank(token: str) -> int:
    """Prefer an ID token over an access token for the same audience.

    The authorization flow can hand back both. Only the ID token carries the
    identity claims Pulse authorizes on — `custom:mag.userId`, `email` — so
    taking whichever appeared first in the response is not good enough. The
    access token is kept as a fallback rather than discarded, because it may
    still be accepted, but the caller is told which kind it got.
    """

    use = _claims(token).get("token_use")
    if use == "id":
        return 0
    if use == "access":
        return 1
    return 2


def _token_from_authorize(client: Any, url: str, expected_aud: str) -> str | None:
    """Follow the authorization URL and return the best token for the audience.

    Collects every candidate — final body, each redirect hop's query and
    fragment, then the cookie jar — and ranks them, rather than returning the
    first match. Ordering matters here: an access token often appears before the
    ID token, and returning it would silently drop the identity claims.
    """

    resp = client.get(url, follow_redirects=True)

    candidates: list[str] = []

    try:
        payload: Any = resp.json()
    except ValueError:
        payload = resp.text.strip()
    body_token = _find_platform_token(payload, expected_aud)
    if body_token:
        candidates.append(body_token)

    hops = [str(r.url) for r in resp.history] + [str(resp.url)]
    for hop in resp.history:
        location = hop.headers.get("location")
        if location:
            hops.append(location)
    for hop in hops:
        for candidate in _tokens_in_url(hop):
            if expected_aud in _audience_of(candidate):
                candidates.append(candidate)

    for value in client.cookies.values():
        if expected_aud in _audience_of(value):
            candidates.append(value)

    if not candidates:
        return None
    return sorted(set(candidates), key=_rank)[0]


def _resolve_next_url(
    payload: Any, *, exchange_url: str, authorize_base: str
) -> str | None:
    """Turn whatever `/auth/token` returned into a URL that can be fetched.

    Observed on QA: `{"URL": "?code=<authorization code>"}` — a bare query
    string, not a URL. It belongs on the authorization endpoint, which the
    `mag.uri` cookie names, NOT on `/auth/token`. Joining `?code=...` to the
    exchange URL would re-POST the exchange with a code attached and never reach
    the endpoint that mints the token.

    Absolute and root-relative forms are still handled, resolved against the
    exchange URL, so a shape change upstream does not need a code change.
    """

    found = _find_url(payload, base=exchange_url)
    if found:
        return found

    query = _find_query_string(payload)
    if query:
        return urljoin(authorize_base, query)
    return None


def _find_query_string(payload: Any) -> str | None:
    """Find a bare `?a=b` value in a decoded body."""

    if isinstance(payload, str):
        value = payload.strip()
        return value if value.startswith("?") and len(value) > 1 else None
    if isinstance(payload, dict):
        for value in payload.values():
            found = _find_query_string(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_query_string(value)
            if found:
                return found
    return None


def authorize_base_for(client: Any, base_url: str) -> str:
    """Where the authorization code should be redeemed.

    Prefers the `mag.uri` cookie the exchange just set — the platform tells us
    its own endpoint, so nothing is hardcoded and a different environment works
    without reconfiguration. Falls back to `PULSE_AUTHORIZE_PATH` on `base_url`.
    """

    from_cookie = ""
    try:
        from_cookie = (client.cookies.get("mag.uri") or "").strip()
    except Exception:
        from_cookie = ""
    if from_cookie.startswith(("https://", "http://")):
        return from_cookie

    path = (os.getenv("PULSE_AUTHORIZE_PATH") or DEFAULT_AUTHORIZE_PATH).strip()
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def seed_mag_cookies(
    client: Any,
    *,
    platform_aud: str,
    authorize_base: str,
    nonce: str | None = None,
) -> str:
    """Set the `mag.*` cookies the authorization flow carries its state in.

    This is what a browser has before it ever calls `/auth/token`: the cookies
    are set by the authorization navigation, which is a document request and so
    never appears under DevTools' Fetch/XHR filter. Without them the code that
    `/auth/token` returns is orphaned and redeeming it gives
    "[DAL-08] invalid authorization code".

    They are client-supplied state rather than server session keys — proven by
    `mag.non` arriving back byte-identical as the `nonce` claim of the minted
    token. So they can be seeded directly instead of replaying a login:

        mag.cid  the platform client id, which is also the audience Pulse
                 accepts — so it is read off the token already in judge/.env
        mag.uri  where the authorization code gets redeemed
        mag.non  a fresh nonce, 16 random bytes base64-encoded, matching the
                 shape observed in two separate logins

    Returns the nonce actually used, so a caller can assert the minted token
    echoes it — a cheap check that the token came from THIS request and is not a
    replay.
    """

    nonce = nonce or base64.b64encode(os.urandom(16)).decode()
    host = urlparse(authorize_base).netloc or urlparse(authorize_base).path
    for name, value in (
        ("mag.cid", platform_aud),
        ("mag.uri", authorize_base),
        # Percent-encoded, matching the browser: base64 contains "+" and "=",
        # which are not safe raw in a cookie value.
        ("mag.non", quote(nonce, safe="")),
    ):
        client.cookies.set(name, value, domain=host, path="/")
    return nonce


def claris_exchange(
    *,
    base_url: str,
    cognito_client_id: str,
    access_token: str,
    id_token: str,
    refresh_token: str,
    expected_aud: str,
    path: str = DEFAULT_EXCHANGE_PATH,
    timeout_s: float = 30.0,
    verify: Any = True,
    client: Any | None = None,
) -> str:
    """Step 2 — swap the Cognito tokens for the platform ID token Pulse wants."""

    url = base_url.rstrip("/") + path
    body = {
        "clientID": cognito_client_id,
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": refresh_token,
    }

    owns = client is None
    if owns:
        import httpx

        # follow_redirects: the authorization hop is a redirect chain, and the
        # cookies /auth/token sets have to travel with it — so one client, one
        # cookie jar, across both calls.
        client = httpx.Client(timeout=timeout_s, verify=verify, follow_redirects=True)
    try:
        # The browser already holds these when it calls /auth/token; without them
        # the returned code has no state to bind to. Seeded before the POST, and
        # carried on to the redemption by the shared cookie jar.
        nonce = seed_mag_cookies(
            client,
            platform_aud=expected_aud,
            authorize_base=authorize_base_for(client, base_url),
        )
        resp = _post(
            client,
            False,  # closed by this function's finally, not by _post
            "Claris token exchange",
            url,
            json=body,
            headers={"Accept": "application/json, text/plain, */*"},
        )

        if resp.status_code != 200:
            raise PulseRefreshError(
                f"Claris token exchange failed ({resp.status_code}) at {url}: "
                f"{resp.text[:300]!r}"
            )

        try:
            payload: Any = resp.json()
        except ValueError:
            payload = resp.text.strip()
            if expected_aud in _audience_of(payload):
                return payload

        token = _find_platform_token(payload, expected_aud)
        if token:
            return token

        # The exchange hands back an authorization URL rather than a token.
        # Following it is the third login request; the cookies just set are what
        # authenticate it.
        authorize_base = authorize_base_for(client, base_url)
        next_url = _resolve_next_url(
            payload, exchange_url=url, authorize_base=authorize_base
        )
        if next_url:
            token = _token_from_authorize(client, next_url, expected_aud)
            if token:
                # Compared after unquoting: the nonce is echoed back in
                # whatever encoding it travelled in, and httpx percent-encodes
                # the cookie value itself, so a raw comparison reports a
                # mismatch between two identical values.
                got = _claims(token).get("nonce")
                if got and unquote(str(got)) != unquote(nonce):
                    # Not fatal — the token is still valid for Pulse — but it
                    # means this token was not minted for this request.
                    print(
                        f"[pulse-auth] warning: minted token nonce {got!r} does "
                        f"not match the one sent {nonce!r}",
                        file=__import__("sys").stderr,
                    )
                return token
            raise PulseRefreshError(
                f"Redeemed the authorization code at {next_url[:160]} but found no "
                f"ID token with aud={expected_aud} in the response body, the "
                "redirect chain or the cookies."
            )

        # Show the body, not just its keys: one more run then says exactly what
        # the platform returned, instead of another round of guessing.
        preview = (
            payload[:400] if isinstance(payload, str) else json.dumps(payload)[:400]
        )
        raise PulseRefreshError(
            f"Claris exchange succeeded but carried neither an ID token with "
            f"aud={expected_aud} nor a followable URL.\n"
            f"  body: {preview}\n"
            "If that body names an endpoint, point PULSE_AUTH_EXCHANGE_PATH (the "
            "exchange) or PULSE_AUTHORIZE_PATH (where the code is redeemed) at it."
        )
    finally:
        if owns:
            client.close()


def build_cognito_provider(settings: Any) -> Any | None:
    """A token provider that walks both steps, or None if not configured.

    Takes the whole `PulseSettings` rather than a URL and a token, so refresh
    resolves TLS exactly the way the chat client does — `PULSE_CA_BUNDLE`,
    `PULSE_VERIFY_TLS` and the OS trust store all apply. Refreshing through a
    different TLS path than the requests it authenticates is how this first
    failed: the chat client verified fine while refresh hit
    CERTIFICATE_VERIFY_FAILED on the same host.

    The audience to look for comes from the token already in `judge/.env`, so the
    provider adapts to whichever environment it points at rather than hardcoding
    a client id.
    """

    refresh_token = (os.getenv("PULSE_REFRESH_TOKEN") or "").strip()
    if not refresh_token:
        return None

    client_id = (os.getenv("PULSE_COGNITO_CLIENT_ID") or "").strip()
    if not client_id:
        raise PulseRefreshError(
            "PULSE_REFRESH_TOKEN is set but PULSE_COGNITO_CLIENT_ID is not. It is "
            "the `clientID` in Studio's POST to /auth/token (observed on QA: "
            "1orc9knial20pdfguri4mn40pm)."
        )
    region = (os.getenv("PULSE_COGNITO_REGION") or DEFAULT_COGNITO_REGION).strip()
    path = (os.getenv("PULSE_AUTH_EXCHANGE_PATH") or DEFAULT_EXCHANGE_PATH).strip()

    aud = expected_audience(settings.auth_token)
    if not aud:
        raise PulseRefreshError(
            "Cannot read `aud` from PULSE_AUTH_TOKEN, so the refreshed platform "
            "token could not be identified. Paste a current token into judge/.env."
        )

    from judge.pulse_client import resolve_tls_verify

    verify = resolve_tls_verify(settings)
    base_url = settings.base_url

    def provider() -> str:
        result = cognito_refresh(
            region=region,
            client_id=client_id,
            refresh_token=refresh_token,
            verify=verify,
        )
        return claris_exchange(
            base_url=base_url,
            cognito_client_id=client_id,
            access_token=result["AccessToken"],
            id_token=result["IdToken"],
            refresh_token=refresh_token,
            expected_aud=aud,
            path=path,
            verify=verify,
        )

    return provider


def _describe(value: str) -> str:
    """Describe a possible JWT by its claims rather than dumping the token.

    The point of the dump is to find WHERE the platform token appears, which its
    `iss`/`aud`/`token_use` answer. Printing whole tokens into a terminal would
    scatter live credentials for no extra information.
    """

    claims = _claims(value)
    if not claims:
        return f"{value[:60]}{'…' if len(value) > 60 else ''}"
    aud = claims.get("aud")
    return (
        f"JWT(iss={claims.get('iss')!r} aud={aud!r} "
        f"token_use={claims.get('token_use')!r} len={len(value)})"
    )


def _dump() -> int:
    """Walk the chain printing every hop, so the token can be located.

    Exists because the refresh flow was reverse-engineered from a browser trace:
    the request shapes were observable, the responses were not. This makes the
    responses observable instead of inferring them one failed run at a time.
    """

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import httpx

    from judge.config import load_env
    from judge.pulse_client import load_pulse_settings, resolve_tls_verify

    load_env()
    settings = load_pulse_settings()
    aud = expected_audience(settings.auth_token)
    verify = resolve_tls_verify(settings)
    client_id = (os.getenv("PULSE_COGNITO_CLIENT_ID") or "").strip()
    region = (os.getenv("PULSE_COGNITO_REGION") or DEFAULT_COGNITO_REGION).strip()
    refresh_token = (os.getenv("PULSE_REFRESH_TOKEN") or "").strip()
    if not (client_id and refresh_token):
        print("PULSE_REFRESH_TOKEN and PULSE_COGNITO_CLIENT_ID must be set")
        return 2

    print(f"looking for a JWT with aud={aud}\n")

    print("--- step 1: Cognito REFRESH_TOKEN_AUTH")
    result = cognito_refresh(
        region=region, client_id=client_id, refresh_token=refresh_token, verify=verify
    )
    for key in ("AccessToken", "IdToken"):
        print(f"  {key}: {_describe(result.get(key, ''))}")

    with httpx.Client(timeout=30.0, verify=verify, follow_redirects=False) as client:
        url = settings.base_url.rstrip("/") + (
            os.getenv("PULSE_AUTH_EXCHANGE_PATH") or DEFAULT_EXCHANGE_PATH
        )
        print(f"\n--- step 2: POST {url}")
        resp = client.post(
            url,
            json={
                "clientID": client_id,
                "access_token": result["AccessToken"],
                "id_token": result["IdToken"],
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json, text/plain, */*"},
        )
        print(f"  status  : {resp.status_code}")
        print(f"  body    : {resp.text[:400]}")
        for name in ("location", "set-cookie"):
            if name in resp.headers:
                print(f"  {name}: {resp.headers[name][:300]}")
        print("  cookies now in jar:")
        for name, value in client.cookies.items():
            print(f"    {name} = {_describe(value)}")

        try:
            payload: Any = resp.json()
        except ValueError:
            payload = resp.text.strip()
        next_url = _resolve_next_url(
            payload,
            exchange_url=url,
            authorize_base=authorize_base_for(client, settings.base_url),
        )
        if not next_url:
            print("\n  no URL/code to follow — the chain ends here.")
            return 1

        print(f"\n--- step 3: GET {next_url}")
        hop_url = next_url
        for hop in range(6):
            r = client.get(hop_url)
            print(f"  [{hop}] {r.status_code} {hop_url[:140]}")
            for name in ("location", "set-cookie", "content-type"):
                if name in r.headers:
                    print(f"        {name}: {r.headers[name][:300]}")
            if r.text.strip():
                print(f"        body: {r.text[:300]}")
            for candidate in _tokens_in_url(hop_url):
                print(f"        token in url: {_describe(candidate)}")
            location = r.headers.get("location")
            if r.status_code in (301, 302, 303, 307, 308) and location:
                hop_url = urljoin(hop_url, location)
                continue
            break

        print("\n  cookies at the end:")
        for name, value in client.cookies.items():
            print(f"    {name} = {_describe(value)}")

    print(
        "\nLook for a JWT above whose aud matches the one at the top. Wherever it "
        "appears — body, a Location, or a cookie — is what the code should read."
    )
    return 0


def _probe() -> int:
    """Run the chain once and report, without touching a real evaluation run."""

    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from judge.config import load_env

    load_env()
    from judge.pulse_client import MissingPulseCredentials, load_pulse_settings

    try:
        settings = load_pulse_settings()
    except MissingPulseCredentials as exc:
        print(exc)
        return 2

    aud = expected_audience(settings.auth_token)
    print(f"platform audience to match : {aud}")
    print(f"exchange base_url          : {settings.base_url}")
    print(f"tls verify                 : {settings.httpx_verify()!r}")

    try:
        provider = build_cognito_provider(settings)
    except PulseRefreshError as exc:
        print(f"\nNOT CONFIGURED: {exc}")
        return 2
    if provider is None:
        print("\nPULSE_REFRESH_TOKEN is not set — nothing to probe.")
        return 2

    print("\nstep 1  Cognito REFRESH_TOKEN_AUTH")
    print("step 2  POST {base_url}/auth/token")
    try:
        token = provider()
    except PulseRefreshError as exc:
        print(f"\nFAILED: {exc}")
        return 1

    claims = _claims(token)
    exp = claims.get("exp")
    when = datetime.fromtimestamp(exp, timezone.utc) if exp else None
    left = (when - datetime.now(timezone.utc)).total_seconds() / 60 if when else None
    use = claims.get("token_use")
    print("\nSUCCESS — refreshed platform token:")
    print(f"  iss   : {claims.get('iss')}")
    print(f"  aud   : {claims.get('aud')}")
    print(f"  use   : {use}")
    if use != "id":
        print(
            f"\n  WARNING: this is a {use!r} token, not an ID token. The token that\n"
            "  has been working carries identity claims (custom:mag.userId, email)\n"
            "  that this one lacks, so Pulse may reject it. Verify with one live\n"
            "  question before trusting a full run."
        )
    print(
        f"  exp   : {when} ({left:.0f} min from now)" if when else "  exp   : unknown"
    )
    print("\nRefresh works. A run can now outlive its token.")
    print("Paste this into judge/.env as PULSE_AUTH_TOKEN to use it immediately:")
    print(f"\n{token}\n")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--probe",
        action="store_true",
        help="run the refresh chain once and report the result",
    )
    ap.add_argument(
        "--dump",
        action="store_true",
        help="walk the chain printing every hop, to locate where the token lands "
        "(tokens are summarised by their claims, never printed in full)",
    )
    parsed = ap.parse_args()
    if parsed.dump:
        raise SystemExit(_dump())
    if not parsed.probe:
        ap.print_help()
        raise SystemExit(0)
    raise SystemExit(_probe())
