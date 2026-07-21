from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HttpError(RuntimeError):
    """Raised when a helper HTTP operation fails."""


@dataclass(frozen=True)
class RequestContext:
    origin: str
    bearer_token: str
    user_id: str
    device_id: str


class HttpClient:
    """Small mockable HTTP layer."""

    def redeem_job(self, ctx: RequestContext, request_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def download_asset(self, ctx: RequestContext, asset_url: str, destination: Path) -> None:
        raise NotImplementedError


class UrllibHttpClient(HttpClient):
    def __init__(self, timeout: float = 15.0, ssl_context: ssl.SSLContext | None = None) -> None:
        self._timeout = timeout
        self._ssl_context = ssl_context or ssl.create_default_context()

    def redeem_job(self, ctx: RequestContext, request_id: str) -> dict[str, Any]:
        url = f"{ctx.origin}/api/helper/jobs/redeem"
        payload = json.dumps(
            {
                "request_id": request_id,
                "device_id": ctx.device_id,
                "user_id": ctx.user_id,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=payload,
            method="POST",
            headers=self._headers(ctx.bearer_token, include_json=True),
        )
        return self._request_json(request)

    def register_device(self, *, origin: str, pairing_code: str, device_name: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url=f"{origin}/api/helper/devices/register",
            data=json.dumps({"pairing_code": pairing_code, "device_name": device_name}).encode("utf-8"),
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return self._request_json(request)

    def download_asset(self, ctx: RequestContext, asset_url: str, destination: Path) -> None:
        request = urllib.request.Request(
            url=asset_url,
            method="GET",
            headers=self._headers(ctx.bearer_token, include_json=False),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as response:
                destination.write_bytes(response.read())
        except OSError as exc:
            raise HttpError(f"Asset download failed: {exc.__class__.__name__}") from exc

    def _request_json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise HttpError(f"HTTP request failed with status {exc.code}") from exc
        except OSError as exc:
            raise HttpError(f"HTTP request failed: {exc.__class__.__name__}") from exc

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError("HTTP response was not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise HttpError("HTTP response JSON must be an object")
        return payload

    def _headers(self, bearer_token: str, include_json: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers
