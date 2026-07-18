"""OIDC discovery, Authorization Code exchange, and ID-token validation."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.config import Settings


class OIDCError(Exception):
    """An untrusted or unusable response from the configured OIDC provider."""


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


class OIDCClient:
    """Small, testable OIDC client that never persists browser tokens."""

    def __init__(self, http_client: httpx.Client, settings: Settings) -> None:
        self._http = http_client
        self._settings = settings

    def discover(self) -> OIDCMetadata:
        issuer = self._require_setting(self._settings.oidc_issuer_url, "OIDC issuer")
        try:
            response = self._http.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OIDCError("OIDC discovery failed") from error
        if not isinstance(payload, dict):
            raise OIDCError("OIDC discovery response is invalid")
        metadata = OIDCMetadata(
            issuer=self._required_payload_string(payload, "issuer"),
            authorization_endpoint=self._required_payload_string(payload, "authorization_endpoint"),
            token_endpoint=self._required_payload_string(payload, "token_endpoint"),
            jwks_uri=self._required_payload_string(payload, "jwks_uri"),
        )
        if not compare_digest(metadata.issuer, issuer):
            raise OIDCError("OIDC issuer does not match configured issuer")
        return metadata

    def authorization_url(self, metadata: OIDCMetadata, *, state: str, nonce: str, verifier: str) -> str:
        client_id = self._require_setting(self._settings.oidc_client_id, "OIDC client ID")
        redirect_uri = self._require_setting(self._settings.oidc_redirect_uri, "OIDC redirect URI")
        challenge = _pkce_challenge(verifier)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid profile email groups",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata.authorization_endpoint}?{query}"

    def exchange_code(self, metadata: OIDCMetadata, *, code: str, verifier: str) -> dict[str, Any]:
        client_id = self._require_setting(self._settings.oidc_client_id, "OIDC client ID")
        redirect_uri = self._require_setting(self._settings.oidc_redirect_uri, "OIDC redirect URI")
        secret = self._require_secret(self._settings.oidc_client_secret, "OIDC client secret")
        try:
            response = self._http.post(
                metadata.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": secret,
                    "code_verifier": verifier,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OIDCError("OIDC token exchange failed") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
            raise OIDCError("OIDC token response is invalid")
        return payload

    def validate_id_token(self, metadata: OIDCMetadata, *, id_token: str, expected_nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
            algorithm = header.get("alg")
            kid = header.get("kid")
            if algorithm not in {"RS256", "RS384", "RS512"} or not isinstance(kid, str):
                raise OIDCError("ID token header is invalid")
            response = self._http.get(metadata.jwks_uri)
            response.raise_for_status()
            jwks = response.json()
            keys = jwks.get("keys") if isinstance(jwks, dict) else None
            matching_key = next(
                (key for key in keys if isinstance(key, dict) and key.get("kid") == kid),
                None,
            )
            if matching_key is None:
                raise OIDCError("ID token signing key is unavailable")
            key = RSAAlgorithm.from_jwk(json.dumps(matching_key))
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=[algorithm],
                audience=self._require_setting(self._settings.oidc_client_id, "OIDC client ID"),
                issuer=metadata.issuer,
                options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError) as error:
            raise OIDCError("ID token validation failed") from error
        nonce = claims.get("nonce")
        subject = claims.get("sub")
        if not isinstance(nonce, str) or not compare_digest(nonce, expected_nonce):
            raise OIDCError("ID token nonce is invalid")
        if not isinstance(subject, str) or not subject:
            raise OIDCError("ID token subject is invalid")
        return claims

    @staticmethod
    def _required_payload_string(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise OIDCError("OIDC discovery response is invalid")
        return value

    @staticmethod
    def _require_setting(value: str | None, label: str) -> str:
        if not value:
            raise OIDCError(f"{label} is not configured")
        return value

    @staticmethod
    def _require_secret(value: Any, label: str) -> str:
        if value is None:
            raise OIDCError(f"{label} is not configured")
        secret = value.get_secret_value()
        if not secret:
            raise OIDCError(f"{label} is not configured")
        return secret


def new_pkce_verifier() -> str:
    """Return a high-entropy RFC 7636-compatible verifier."""
    return secrets.token_urlsafe(48)


def new_opaque_value() -> str:
    """Return a high-entropy state or nonce value."""
    return secrets.token_urlsafe(32)


def _pkce_challenge(verifier: str) -> str:
    return __import__("base64").urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
