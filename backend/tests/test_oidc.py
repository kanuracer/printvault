from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app import main


ISSUER = "https://issuer.example.test/realms/printvault"
REDIRECT_URI = "https://printvault.example.test/api/auth/callback"
CLIENT_ID = "printvault"


def settings_for_auth(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite:///:memory:",
        library_models_root=tmp_path / "models",
        library_archive_root=tmp_path / "archive",
        data_root=tmp_path / "data",
        thumbnails_root=tmp_path / "thumbnails",
        oidc_issuer_url=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri=REDIRECT_URI,
        session_secret="test-session-secret",
    )


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def make_id_token(*, private_key: object, nonce: str, groups: list[str], subject: str = "nextcloud-user") -> str:
    import jwt

    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": subject,
            "nonce": nonce,
            "groups": groups,
            "exp": 4_102_444_800,
            "iat": 1_700_000_000,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def oidc_http_client(token_supplier, *, userinfo_groups: list[str] | None = None):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url == httpx.URL(f"{ISSUER}/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        if request.method == "GET" and request.url == httpx.URL(f"{ISSUER}/jwks"):
            return httpx.Response(200, json={"keys": [jwk]})
        if request.method == "POST" and request.url == httpx.URL(f"{ISSUER}/token"):
            return httpx.Response(200, json={"id_token": token_supplier(private_key), "access_token": "test-access-token"})
        if request.method == "GET" and request.url == httpx.URL(f"{ISSUER}/userinfo") and userinfo_groups is not None:
            assert request.headers["authorization"] == "Bearer test-access-token"
            return httpx.Response(200, json={"sub": "nextcloud-user", "groups": userinfo_groups})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def test_login_discovers_oidc_and_uses_state_nonce_and_s256_pkce(tmp_path: Path) -> None:
    client_http, requests = oidc_http_client(lambda _: "unused")
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)

    response = TestClient(app, base_url="https://printvault.example.test").get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(f"{ISSUER}/authorize?")
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["scope"] == ["openid profile email groups"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32
    assert len(query["nonce"][0]) >= 32
    assert "printvault_oidc_state" in response.headers["set-cookie"]
    assert requests[0].url == httpx.URL(f"{ISSUER}/.well-known/openid-configuration")


def test_callback_exchanges_code_with_client_secret_post_and_creates_bff_session(tmp_path: Path) -> None:
    nonce_holder: dict[str, str] = {}
    client_http, requests = oidc_http_client(
        lambda private_key: make_id_token(private_key=private_key, nonce=nonce_holder["nonce"], groups=["printvault_editor"])
    )
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    login = client.get("/api/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    nonce_holder["nonce"] = query["nonce"][0]
    callback = client.get("/api/auth/callback", params={"code": "authorization-code", "state": query["state"][0]}, follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    token_request = next(request for request in requests if request.method == "POST")
    form = parse_qs(token_request.content.decode("utf-8"))
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["authorization-code"],
        "redirect_uri": [REDIRECT_URI],
        "client_id": [CLIENT_ID],
        "client_secret": ["test-client-secret"],
        "code_verifier": [form["code_verifier"][0]],
    }
    assert b64url(hashlib.sha256(form["code_verifier"][0].encode("ascii")).digest()) == query["code_challenge"][0]
    assert "printvault_session" in callback.headers["set-cookie"]
    assert "id_token" not in callback.headers["set-cookie"]
    assert client.get("/api/auth/me").json() == {"subject": "nextcloud-user", "role": "editor"}


def test_callback_rejects_state_mismatch_before_token_exchange(tmp_path: Path) -> None:
    client_http, requests = oidc_http_client(lambda _: "unused")
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    client.get("/api/auth/login", follow_redirects=False)
    response = client.get("/api/auth/callback", params={"code": "authorization-code", "state": "wrong"})

    assert response.status_code == 400
    assert all(request.method != "POST" for request in requests)


def test_callback_rejects_id_token_with_wrong_nonce(tmp_path: Path) -> None:
    client_http, _ = oidc_http_client(
        lambda private_key: make_id_token(private_key=private_key, nonce="wrong", groups=["printvault_viewer"])
    )
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    login = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    response = client.get("/api/auth/callback", params={"code": "authorization-code", "state": state})

    assert response.status_code == 401


def test_me_returns_401_without_a_valid_session(tmp_path: Path) -> None:
    client_http, _ = oidc_http_client(lambda _: "unused")
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)

    response = TestClient(app, base_url="https://printvault.example.test").get("/api/auth/me")

    assert response.status_code == 401


def test_callback_uses_userinfo_groups_when_id_token_does_not_grant_access(tmp_path: Path) -> None:
    nonce_holder: dict[str, str] = {}
    client_http, _ = oidc_http_client(
        lambda private_key: make_id_token(private_key=private_key, nonce=nonce_holder["nonce"], groups=[]),
        userinfo_groups=["printvault_admin"],
    )
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    login = client.get("/api/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    nonce_holder["nonce"] = query["nonce"][0]
    callback = client.get(
        "/api/auth/callback", params={"code": "authorization-code", "state": query["state"][0]}, follow_redirects=False
    )

    assert callback.status_code == 303
    assert client.get("/api/auth/me").json() == {"subject": "nextcloud-user", "role": "admin"}


def test_valid_session_without_an_exact_printvault_group_is_forbidden(tmp_path: Path) -> None:
    nonce_holder: dict[str, str] = {}
    client_http, _ = oidc_http_client(
        lambda private_key: make_id_token(private_key=private_key, nonce=nonce_holder["nonce"], groups=["printvault_admin_extra"])
    )
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    login = client.get("/api/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    nonce_holder["nonce"] = query["nonce"][0]
    client.get("/api/auth/callback", params={"code": "authorization-code", "state": query["state"][0]})

    assert client.get("/api/auth/me").status_code == 403


def test_logout_clears_the_bff_session(tmp_path: Path) -> None:
    client_http, _ = oidc_http_client(lambda _: "unused")
    app = main.create_app(settings_for_auth(tmp_path), http_client=client_http)
    client = TestClient(app, base_url="https://printvault.example.test")

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert "printvault_session=\"\"" in response.headers["set-cookie"]
