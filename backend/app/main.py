"""PrintVault FastAPI application and browser-facing BFF authentication routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from hmac import compare_digest
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import ValidationError

from app.api import register_api
from app.api.production import build_production_dependencies, initialize_production_database
from app.auth.oidc import OIDCClient, OIDCError, new_opaque_value, new_pkce_verifier
from app.config import Settings
from app.services.rbac import role_for_groups

_STATE_COOKIE = "printvault_oidc_state"
_SESSION_COOKIE = "printvault_session"
_STATE_MAX_AGE_SECONDS = 300
_SESSION_MAX_AGE_SECONDS = 60 * 60 * 8


def _session_secret(settings: Settings) -> str:
    if settings.session_secret is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="authentication is not configured")
    secret = settings.session_secret.get_secret_value()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="authentication is not configured")
    return secret


def _serializer(settings: Settings, purpose: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(settings), salt=f"printvault.{purpose}")


def _set_secure_cookie(response: Response, key: str, value: str, *, max_age: int) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response, key: str) -> None:
    response.delete_cookie(key=key, httponly=True, secure=True, samesite="lax", path="/")


def create_app(settings: Settings | None = None, *, http_client: httpx.Client | None = None) -> FastAPI:
    """Create the API; settings and HTTP transport are injectable for isolated tests."""
    # Health must remain available even before configuration is mounted.  When
    # settings are valid, the startup hook is the production composition root:
    # it migrates/seeds before attaching any persistence-backed API routes.
    configured_settings = settings
    if configured_settings is None:
        try:
            configured_settings = Settings()
        except ValidationError:
            configured_settings = None

    production_dependencies = (
        build_production_dependencies(configured_settings) if configured_settings is not None else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if configured_settings is not None:
            initialize_production_database(configured_settings)
        yield

    app = FastAPI(title="PrintVault API", lifespan=lifespan)
    if production_dependencies is not None:
        register_api(app, production_dependencies)

    def active_settings() -> Settings:
        if settings is not None:
            return settings
        try:
            return Settings()
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="application configuration is invalid",
            ) from error

    @contextmanager
    def oidc_client(current_settings: Settings) -> Iterator[OIDCClient]:
        if http_client is not None:
            yield OIDCClient(http_client, current_settings)
            return
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            yield OIDCClient(client, current_settings)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        """Container readiness endpoint used by Docker health checks."""
        return {"status": "ok"}

    @app.get("/api/auth/login")
    def login() -> RedirectResponse:
        current_settings = active_settings()
        try:
            with oidc_client(current_settings) as client:
                metadata = client.discover()
                state = new_opaque_value()
                nonce = new_opaque_value()
                verifier = new_pkce_verifier()
                location = client.authorization_url(metadata, state=state, nonce=nonce, verifier=verifier)
        except OIDCError as error:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC login could not be started") from error

        state_cookie = _serializer(current_settings, "oidc-state").dumps(
            {"state": state, "nonce": nonce, "verifier": verifier}
        )
        response = RedirectResponse(location, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        _set_secure_cookie(response, _STATE_COOKIE, state_cookie, max_age=_STATE_MAX_AGE_SECONDS)
        return response

    @app.get("/api/auth/callback")
    def callback(request: Request, code: str | None = None, state: str | None = None) -> RedirectResponse:
        current_settings = active_settings()
        if not code or not state:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC callback is invalid")
        state_cookie = request.cookies.get(_STATE_COOKIE)
        if not state_cookie:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC callback state is invalid")
        try:
            saved = _serializer(current_settings, "oidc-state").loads(state_cookie, max_age=_STATE_MAX_AGE_SECONDS)
            expected_state = saved["state"]
            nonce = saved["nonce"]
            verifier = saved["verifier"]
        except (BadSignature, KeyError, TypeError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC callback state is invalid") from None
        if not all(isinstance(value, str) and value for value in (expected_state, nonce, verifier)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC callback state is invalid")
        if not compare_digest(state, expected_state):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OIDC callback state is invalid")

        try:
            with oidc_client(current_settings) as client:
                metadata = client.discover()
                tokens = client.exchange_code(metadata, code=code, verifier=verifier)
                claims = client.validate_id_token(metadata, id_token=tokens["id_token"], expected_nonce=nonce)
                groups_claim = claims.get(current_settings.oidc_groups_claim)
                groups = groups_claim if isinstance(groups_claim, list) and all(isinstance(group, str) for group in groups_claim) else None
                role = role_for_groups(groups)
                access_token = tokens.get("access_token")
                if role is None and not groups and isinstance(access_token, str):
                    userinfo = client.userinfo(metadata, access_token=access_token)
                    userinfo_subject = userinfo.get("sub")
                    if not isinstance(userinfo_subject, str) or not compare_digest(userinfo_subject, claims["sub"]):
                        raise OIDCError("OIDC userinfo subject is invalid")
                    groups_claim = userinfo.get(current_settings.oidc_groups_claim)
                    groups = groups_claim if isinstance(groups_claim, list) and all(isinstance(group, str) for group in groups_claim) else None
                    role = role_for_groups(groups)
        except OIDCError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC authentication failed") from error

        subject = claims["sub"]
        session_cookie = _serializer(current_settings, "session").dumps({"subject": subject, "role": role})
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        _set_secure_cookie(response, _SESSION_COOKIE, session_cookie, max_age=_SESSION_MAX_AGE_SECONDS)
        _clear_cookie(response, _STATE_COOKIE)
        return response

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout() -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        _clear_cookie(response, _SESSION_COOKIE)
        return response

    @app.get("/api/auth/me")
    def me(request: Request) -> dict[str, str]:
        current_settings = active_settings()
        session_cookie = request.cookies.get(_SESSION_COOKIE)
        if not session_cookie:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
        try:
            session: dict[str, Any] = _serializer(current_settings, "session").loads(
                session_cookie, max_age=_SESSION_MAX_AGE_SECONDS
            )
        except (BadSignature, TypeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required") from None
        subject = session.get("subject")
        role = session.get("role")
        if not isinstance(subject, str) or not subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
        if role is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PrintVault access is not granted")
        if role not in {"viewer", "editor", "admin"}:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is required")
        return {"subject": subject, "role": role}

    return app


app = create_app()
