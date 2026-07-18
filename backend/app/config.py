"""Validated environment configuration for the PrintVault backend."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
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
    database_url: str = Field(default="sqlite:///./printvault.sqlite3", repr=False)
    database_url_file: Path | None = None

    library_models_root: Path
    library_archive_root: Path
    data_root: Path
    thumbnails_root: Path

    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_id_file: Path | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_client_secret_file: Path | None = None
    oidc_redirect_uri: str | None = None
    oidc_groups_claim: str = "groups"
    session_secret: SecretStr | None = None
    session_secret_file: Path | None = None

    @field_validator(
        "library_models_root",
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

    @field_validator(
        "database_url_file",
        "oidc_client_id_file",
        "oidc_client_secret_file",
        "session_secret_file",
        mode="before",
    )
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

    @staticmethod
    def _read_file_value(path: Path, label: str) -> str:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError(f"{label} file value could not be read") from error
        if not value:
            raise ValueError(f"{label} file value must not be empty")
        return value

    @staticmethod
    def _nonempty_secret(value: SecretStr | None, label: str) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError(f"{label} must not be empty")
        return value

    @model_validator(mode="after")
    def apply_file_values_and_validate(self) -> "Settings":
        if self.database_url_file is not None:
            self.database_url = self.validate_database_url(
                self._read_file_value(self.database_url_file, "DATABASE_URL_FILE")
            )
        if self.oidc_client_id_file is not None:
            self.oidc_client_id = self._read_file_value(self.oidc_client_id_file, "OIDC_CLIENT_ID_FILE")
        if self.oidc_client_secret_file is not None:
            self.oidc_client_secret = SecretStr(
                self._read_file_value(self.oidc_client_secret_file, "OIDC_CLIENT_SECRET_FILE")
            )
        if self.session_secret_file is not None:
            self.session_secret = SecretStr(
                self._read_file_value(self.session_secret_file, "SESSION_SECRET_FILE")
            )

        if self.oidc_client_id is not None:
            self.oidc_client_id = self.oidc_client_id.strip()
            if not self.oidc_client_id:
                raise ValueError("OIDC client ID must not be empty")
        self.oidc_client_secret = self._nonempty_secret(self.oidc_client_secret, "OIDC client secret")
        self.session_secret = self._nonempty_secret(self.session_secret, "session secret")

        roots = (
            self.library_models_root,
            self.library_archive_root,
            self.data_root,
            self.thumbnails_root,
        )
        if len(set(roots)) != len(roots):
            raise ValueError("mounted roots must be unique")

        if self.environment in {"staging", "production"}:
            if self.database_url_file is None:
                raise ValueError("DATABASE_URL_FILE is required in staging and production")
            required = {
                "issuer URL": self.oidc_issuer_url,
                "client ID": self.oidc_client_id,
                "client secret file": self.oidc_client_secret_file,
                "redirect URI": self.oidc_redirect_uri,
                "session secret file": self.session_secret_file,
            }
            missing = [name for name, value in required.items() if value is None or not str(value).strip()]
            if missing:
                raise ValueError(
                    "OIDC/session configuration is required in staging and production: missing "
                    + ", ".join(missing)
                )
            for name, value in (
                ("OIDC issuer URL", self.oidc_issuer_url),
                ("OIDC redirect URI", self.oidc_redirect_uri),
            ):
                parsed = urlparse(value or "")
                if parsed.scheme != "https" or not parsed.netloc:
                    raise ValueError(f"{name} must be an absolute HTTPS URL")

        return self
