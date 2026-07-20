from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .config import ConfigError, HelperConfig
from .http_client import HttpClient, RequestContext


class LaunchError(RuntimeError):
    """Raised when a launch request must be rejected."""


@dataclass(frozen=True)
class LaunchRequest:
    request_id: str
    profile_id: str


@dataclass(frozen=True)
class Job:
    request_id: str
    profile_id: str
    user_id: str
    device_id: str
    asset_url: str
    asset_name: str
    asset_sha256: str
    expires_at: datetime


class ConfirmDeclined(LaunchError):
    """Raised when the local user does not confirm launch."""


def parse_launch_target(value: str) -> LaunchRequest:
    if value.startswith("printvault://"):
        parsed = urlparse(value)
        query = parse_qs(parsed.query, strict_parsing=True)
        request_id = _single_query_value(query, "request")
        profile_id = _single_query_value(query, "profile")
        return LaunchRequest(request_id=request_id, profile_id=profile_id)

    parts = value.split(":", 1)
    if len(parts) != 2 or not all(parts):
        raise LaunchError("Launch target must be a printvault URI or request_id:profile_id")
    return LaunchRequest(request_id=parts[0], profile_id=parts[1])


def launch_from_target(
    config: HelperConfig,
    target: str,
    http_client: HttpClient,
    confirm: Callable[[str], str] = input,
    now: Callable[[], datetime] | None = None,
    runner: Callable[..., subprocess.Popen[str]] | None = None,
    cache_root: Path | None = None,
) -> subprocess.Popen[str]:
    launch_request = parse_launch_target(target)
    ctx = RequestContext(
        origin=config.origin,
        bearer_token=config.auth.bearer_token(),
        user_id=config.user_id,
        device_id=config.device_id,
    )
    profile = config.profile(launch_request.profile_id)
    job = _load_job(http_client.redeem_job(ctx, launch_request.request_id))
    _validate_job(config, launch_request, job, current_time=(now or _utc_now)())
    asset_path = _download_asset(http_client, ctx, job, cache_root)
    _require_confirmation(profile.label, asset_path, confirm)
    if not profile.executable.exists():
        raise LaunchError(f"Configured executable does not exist: {profile.executable}")
    if not os.access(profile.executable, os.X_OK):
        raise LaunchError(f"Configured executable is not executable: {profile.executable}")
    command = profile.build_command(asset_path)
    process_runner = runner or subprocess.Popen
    return process_runner(command, shell=False, close_fds=True)


def _load_job(payload: dict[str, object]) -> Job:
    try:
        expires_at = _parse_timestamp(_require_string(payload, "expires_at"))
        return Job(
            request_id=_require_string(payload, "request_id"),
            profile_id=_require_string(payload, "profile_id"),
            user_id=_require_string(payload, "user_id"),
            device_id=_require_string(payload, "device_id"),
            asset_url=_require_string(payload, "asset_url"),
            asset_name=_require_string(payload, "asset_name"),
            asset_sha256=_require_sha256(_require_string(payload, "asset_sha256")),
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise LaunchError(f"Job payload invalid: {exc}") from exc


def _validate_job(
    config: HelperConfig,
    launch_request: LaunchRequest,
    job: Job,
    current_time: datetime,
) -> None:
    if job.request_id != launch_request.request_id:
        raise LaunchError("Job request_id did not match launch request")
    if job.profile_id != launch_request.profile_id:
        raise LaunchError("Job profile_id did not match launch request")
    if job.user_id != config.user_id or job.device_id != config.device_id:
        raise LaunchError("Job is not bound to this device and user")
    if job.expires_at <= current_time:
        raise LaunchError("Job has expired")
    if job.expires_at - current_time > timedelta(minutes=5):
        raise LaunchError("Job expiry exceeds five minutes")
    parsed_asset = urlparse(job.asset_url)
    if parsed_asset.scheme != "https":
        raise LaunchError("Asset download URL must use https")
    expected_origin = urlparse(config.origin)
    if parsed_asset.netloc != expected_origin.netloc:
        raise LaunchError("Asset download URL must match configured origin")
    if parsed_asset.path in ("", "/"):
        raise LaunchError("Asset download URL must include a path")


def _download_asset(
    http_client: HttpClient,
    ctx: RequestContext,
    job: Job,
    cache_root: Path | None,
) -> Path:
    root = cache_root or _default_cache_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    safe_name = _safe_filename(job.asset_name)
    job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=root))
    destination = job_dir / safe_name
    http_client.download_asset(ctx, job.asset_url, destination)
    actual_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual_hash != job.asset_sha256:
        raise LaunchError("Asset SHA-256 mismatch")
    return destination


def _default_cache_root() -> Path:
    return Path.home() / ".cache" / "printvault-helper"


def _require_confirmation(label: str, asset_path: Path, confirm: Callable[[str], str]) -> None:
    prompt = (
        f"Launch {label} with asset {asset_path.name}? "
        "Type LAUNCH to continue: "
    )
    answer = confirm(prompt)
    if answer != "LAUNCH":
        raise ConfirmDeclined("Launch not confirmed")


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed.astimezone(UTC)


def _require_sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("asset_sha256 must be a 64-character lowercase hex digest")
    return value


def _safe_filename(value: str) -> str:
    name = Path(value).name
    if not name or name in {".", ".."}:
        raise LaunchError("asset_name must be a file name")
    return name


def _single_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key)
    if not values or len(values) != 1 or not values[0]:
        raise LaunchError(f"Launch URI must include exactly one {key!r} parameter")
    return values[0]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def redact_secret_text(value: str) -> str:
    value = re.sub(r"(Bearer)\s+[^\s]+", r"\1 <redacted>", value, flags=re.IGNORECASE)
    value = re.sub(r"([?&](?:token|access_token|request)=)[^&\s]+", r"\1<redacted>", value, flags=re.IGNORECASE)
    return value
