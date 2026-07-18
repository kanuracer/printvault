"""Validated environment configuration for the PrintVault backend."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    """Runtime settings loaded from ``PRINTVAULT_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="PRINTVAULT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "sqlite:///./printvault.sqlite3"
    database_url_file: Path | None = None

    library_models_root: Path
    library_projects_root: Path
    library_archive_root: Path
    data_root: Path
    thumbnails_root: Path

    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret_file: Path | None = None
    oidc_redirect_uri: str | None = None
    oidc_groups_claim: str = "groups"

    @field_validator(
        "library_models_root",
        "library_projects_root",
        "library_archive_root",
        "data_root",
        "thumbnails_root",
        mode="before",
    )
    @classmethod
    def validate_mounted_root(cls, value: object) -> Path:
        raw_value = str(value).strip() if value is not None else ""
        if not raw_value:
            raise ValueError("mounted roots must not be empty")
        root = Path(raw_value)
        if not root.is_absolute():
            raise ValueError("mounted roots must be absolute")
        return root.resolve(strict=False)

    @field_validator("database_url_file", "oidc_client_secret_file", mode="before")
    @classmethod
    def blank_optional_path_is_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("database URL must not be empty")
        try:
            backend = make_url(value.strip()).get_backend_name()
        except Exception as error:
            raise ValueError("database URL is invalid") from error
        if backend not in {"sqlite", "mysql", "mariadb"}:
            raise ValueError("database URL must use SQLite or MariaDB/MySQL")
        return value.strip()

    @field_validator("oidc_groups_claim")
    @classmethod
    def validate_groups_claim(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("OIDC groups claim must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def apply_database_url_file_and_validate(self) -> "Settings":
        if self.database_url_file is not None:
            try:
                database_url = self.database_url_file.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ValueError("DATABASE_URL_FILE could not be read") from error
            self.database_url = self.validate_database_url(database_url)

        roots = (
            self.library_models_root,
            self.library_projects_root,
            self.library_archive_root,
            self.data_root,
            self.thumbnails_root,
        )
        if len(set(roots)) != len(roots):
            raise ValueError("mounted roots must be unique")

        if self.environment != "development":
            required = {
                "issuer URL": self.oidc_issuer_url,
                "client ID": self.oidc_client_id,
                "redirect URI": self.oidc_redirect_uri,
                "client secret file": self.oidc_client_secret_file,
            }
            missing = [name for name, value in required.items() if value is None or not str(value).strip()]
            if missing:
                raise ValueError(f"OIDC configuration is required outside development: missing {', '.join(missing)}")
            for name, value in (
                ("OIDC issuer URL", self.oidc_issuer_url),
                ("OIDC redirect URI", self.oidc_redirect_uri),
            ):
                parsed = urlparse(value or "")
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError(f"{name} must be an absolute HTTPS URL")

        return self
