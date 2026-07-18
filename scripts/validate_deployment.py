#!/usr/bin/env python3
"""Validate PrintVault's non-negotiable Docker Compose security topology."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FILE_ENVIRONMENTS = {
    "PRINTVAULT_DATABASE_URL_FILE",
    "PRINTVAULT_OIDC_CLIENT_ID_FILE",
    "PRINTVAULT_OIDC_CLIENT_SECRET_FILE",
    "PRINTVAULT_SESSION_SECRET_FILE",
}


def environment_mapping(environment: Any, service_name: str, errors: list[str]) -> dict[str, str | None]:
    """Normalize Compose mapping/list environment forms without resolving variables."""
    if environment is None:
        return {}
    if isinstance(environment, dict):
        return {str(key): None if value is None else str(value) for key, value in environment.items()}
    if isinstance(environment, list):
        normalized: dict[str, str | None] = {}
        for item in environment:
            if not isinstance(item, str) or "=" not in item:
                errors.append(f"service {service_name}: environment entries must be KEY=value strings")
                continue
            key, value = item.split("=", 1)
            normalized[key] = value
        return normalized
    errors.append(f"service {service_name}: environment must be a mapping or list")
    return {}


def is_sensitive_database_or_oidc_key(key: str) -> bool:
    normalized = key.upper()
    database_secret = (
        ("DATABASE" in normalized or normalized.startswith("DB_"))
        and any(token in normalized for token in ("URL", "PASSWORD", "PASS", "SECRET"))
    )
    oidc_secret = "OIDC" in normalized and any(
        token in normalized for token in ("SECRET", "PASSWORD", "PASS")
    )
    return database_secret or oidc_secret


def validate_compose(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["Compose document must be a mapping"]

    services = document.get("services")
    if not isinstance(services, dict) or not services:
        return ["Compose document must declare at least one service"]
    if "printvault" not in services or not isinstance(services.get("printvault"), dict):
        errors.append("Compose document must declare a printvault service")

    for service_name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"service {service_name}: definition must be a mapping")
            continue
        if "expose" in service:
            errors.append(f"service {service_name}: expose must not be declared")
        ports = service.get("ports", [])
        if not isinstance(ports, list):
            errors.append(f"service {service_name}: ports must be a list")
        elif any(not isinstance(port, str) or not port.startswith("127.0.0.1:") for port in ports):
            errors.append(f"service {service_name}: published ports must bind loopback only")

        environment = environment_mapping(service.get("environment"), service_name, errors)
        if service_name != "printvault":
            continue
        if service.get("env_file") != [".env"]:
            errors.append("service printvault: env_file must contain only .env")
        for variable in REQUIRED_FILE_ENVIRONMENTS:
            value = environment.get(variable)
            if not value:
                errors.append(f"service printvault: {variable} must be set to an absolute secret-file path")
            elif not value.startswith("/"):
                errors.append(f"service printvault: {variable} must be an absolute secret-file path")
        for key, value in environment.items():
            if is_sensitive_database_or_oidc_key(key) and not key.upper().endswith("_FILE"):
                errors.append(
                    f"service printvault: {key} is a literal database/OIDC secret; use a *_FILE variable instead"
                )
            elif is_sensitive_database_or_oidc_key(key) and not value:
                errors.append(f"service printvault: {key} must name a secret-file path")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compose_file", nargs="?", default="compose.yaml", type=Path)
    arguments = parser.parse_args()
    try:
        document = yaml.safe_load(arguments.compose_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"compose file not found: {arguments.compose_file}", file=sys.stderr)
        return 2
    except yaml.YAMLError as error:
        print(f"invalid YAML: {error}", file=sys.stderr)
        return 2

    errors = validate_compose(document)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated secure deployment topology: {arguments.compose_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
