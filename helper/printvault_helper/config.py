from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when helper configuration is invalid."""


@dataclass(frozen=True)
class AuthConfig:
    token_env: str

    def bearer_token(self) -> str:
        value = os.environ.get(self.token_env, "")
        if not value:
            raise ConfigError(f"Missing bearer token in environment variable {self.token_env!r}")
        return value


@dataclass(frozen=True)
class ProfileConfig:
    profile_id: str
    label: str
    executable: Path
    args: tuple[str, ...]

    def build_command(self, asset_path: Path) -> list[str]:
        command = [str(self.executable)]
        for arg in self.args:
            if arg == "{file}":
                command.append(str(asset_path))
            else:
                command.append(arg)
        return command


@dataclass(frozen=True)
class HelperConfig:
    version: int
    origin: str
    user_id: str
    device_id: str
    auth: AuthConfig
    profiles: dict[str, ProfileConfig]

    def profile(self, profile_id: str) -> ProfileConfig:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise ConfigError(f"Unknown profile {profile_id!r}") from exc


def load_config(path: Path) -> HelperConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {path}") from exc

    if raw.get("version") != 1:
        raise ConfigError("Config version must be 1")

    origin = _validate_origin(raw.get("origin"))
    user_id = _require_non_empty_string(raw.get("user_id"), "user_id")
    device_id = _require_non_empty_string(raw.get("device_id"), "device_id")
    auth = _load_auth(raw.get("auth"))
    profiles = _load_profiles(raw.get("profiles"))
    return HelperConfig(
        version=1,
        origin=origin,
        user_id=user_id,
        device_id=device_id,
        auth=auth,
        profiles=profiles,
    )


def _load_auth(value: object) -> AuthConfig:
    if not isinstance(value, dict):
        raise ConfigError("auth must be an object")
    if value.get("type") != "bearer_env":
        raise ConfigError("auth.type must be 'bearer_env'")
    token_env = _require_non_empty_string(value.get("token_env"), "auth.token_env")
    return AuthConfig(token_env=token_env)


def _load_profiles(value: object) -> dict[str, ProfileConfig]:
    if not isinstance(value, list) or not value:
        raise ConfigError("profiles must be a non-empty array")

    profiles: dict[str, ProfileConfig] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError("profiles items must be objects")
        profile_id = _require_non_empty_string(item.get("id"), "profiles[].id")
        if profile_id in profiles:
            raise ConfigError(f"Duplicate profile id {profile_id!r}")
        label = _require_non_empty_string(item.get("label"), f"profile {profile_id}.label")
        executable = Path(_require_non_empty_string(item.get("executable"), f"profile {profile_id}.executable"))
        if not executable.is_absolute():
            raise ConfigError(f"profile {profile_id}.executable must be an absolute path")
        args = tuple(_validate_arg_templates(item.get("args"), profile_id))
        profiles[profile_id] = ProfileConfig(
            profile_id=profile_id,
            label=label,
            executable=executable,
            args=args,
        )
    return profiles


def _validate_arg_templates(value: object, profile_id: str) -> Iterable[str]:
    if not isinstance(value, list):
        raise ConfigError(f"profile {profile_id}.args must be an array")
    for index, arg in enumerate(value):
        if not isinstance(arg, str) or not arg:
            raise ConfigError(f"profile {profile_id}.args[{index}] must be a non-empty string")
        if "{file}" in arg and arg != "{file}":
            raise ConfigError(f"profile {profile_id}.args[{index}] must use '{{file}}' as a whole argument")
        yield arg


def _validate_origin(value: object) -> str:
    origin = _require_non_empty_string(value, "origin")
    parsed = urlparse(origin)
    if parsed.scheme != "https":
        raise ConfigError("origin must use https")
    if not parsed.netloc or parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ConfigError("origin must be an HTTPS origin without path, query, or fragment")
    return f"https://{parsed.netloc}"


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string")
    return value.strip()
