from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def config_values(tmp_path: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "development",
        "database_url": "sqlite:///:memory:",
        "library_models_root": tmp_path / "models",
        "library_projects_root": tmp_path / "projects",
        "library_archive_root": tmp_path / "archive",
        "data_root": tmp_path / "data",
        "thumbnails_root": tmp_path / "thumbnails",
    }
    values.update(overrides)
    return values


def production_values(tmp_path: Path, **overrides: object) -> dict[str, object]:
    database_url_file = tmp_path / "database-url"
    client_id_file = tmp_path / "oidc-client-id"
    client_secret_file = tmp_path / "oidc-client-secret"
    session_secret_file = tmp_path / "session-secret"
    database_url_file.write_text("mysql+pymysql://user:password@db/printvault\n", encoding="utf-8")
    client_id_file.write_text("printvault\n", encoding="utf-8")
    client_secret_file.write_text("client-secret\n", encoding="utf-8")
    session_secret_file.write_text("session-secret\n", encoding="utf-8")
    values = config_values(
        tmp_path,
        environment="production",
        database_url_file=database_url_file,
        oidc_issuer_url="https://issuer.example.test/realms/printvault",
        oidc_client_id_file=client_id_file,
        oidc_client_secret_file=client_secret_file,
        oidc_redirect_uri="https://printvault.example.test/api/auth/callback",
        session_secret_file=session_secret_file,
    )
    values.update(overrides)
    return values


def test_accepts_sqlite_url_for_local_development(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, **config_values(tmp_path))

    assert settings.database_url == "sqlite:///:memory:"


def test_test_environment_retains_sqlite_support_without_oidc_configuration(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, **config_values(tmp_path, environment="test"))

    assert settings.database_url == "sqlite:///:memory:"


def test_file_values_override_direct_values_without_exposing_their_contents(tmp_path: Path) -> None:
    database_url_file = tmp_path / "database-url"
    client_id_file = tmp_path / "client-id"
    client_secret_file = tmp_path / "client-secret"
    session_secret_file = tmp_path / "session-secret"
    database_url_file.write_text("sqlite:///from-mounted-file.sqlite3\n", encoding="utf-8")
    client_id_file.write_text("from-file-client\n", encoding="utf-8")
    client_secret_file.write_text("from-file-client-secret\n", encoding="utf-8")
    session_secret_file.write_text("from-file-session-secret\n", encoding="utf-8")

    settings = Settings(
        _env_file=None,
        **config_values(
            tmp_path,
            database_url="sqlite:///ignored.sqlite3",
            database_url_file=database_url_file,
            oidc_client_id="ignored-client",
            oidc_client_id_file=client_id_file,
            oidc_client_secret="ignored-client-secret",
            oidc_client_secret_file=client_secret_file,
            session_secret="ignored-session-secret",
            session_secret_file=session_secret_file,
        ),
    )

    assert settings.database_url == "sqlite:///from-mounted-file.sqlite3"
    assert settings.oidc_client_id == "from-file-client"
    assert settings.oidc_client_secret.get_secret_value() == "from-file-client-secret"
    assert settings.session_secret.get_secret_value() == "from-file-session-secret"
    assert "from-mounted-file.sqlite3" not in repr(settings)
    assert "from-file-client-secret" not in repr(settings)
    assert "from-file-session-secret" not in repr(settings)


@pytest.mark.parametrize("field_name", ["database_url_file", "oidc_client_id_file", "oidc_client_secret_file", "session_secret_file"])
def test_rejects_unreadable_or_empty_file_values(tmp_path: Path, field_name: str) -> None:
    empty_file = tmp_path / field_name
    empty_file.write_text("\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="file value"):
        Settings(_env_file=None, **config_values(tmp_path, **{field_name: empty_file}))


@pytest.mark.parametrize("root_name,bad_root", [("library_models_root", "relative/models"), ("data_root", "")])
def test_rejects_relative_or_empty_mounted_roots(
    tmp_path: Path, root_name: str, bad_root: str
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **config_values(tmp_path, **{root_name: bad_root}))


def test_rejects_duplicate_mounted_roots(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unique"):
        Settings(
            _env_file=None,
            **config_values(
                tmp_path,
                thumbnails_root=tmp_path / "data",
            ),
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "database_url_file",
        "oidc_issuer_url",
        "oidc_client_id_file",
        "oidc_client_secret_file",
        "oidc_redirect_uri",
        "session_secret_file",
    ],
)
def test_production_requires_file_backed_secrets_and_database_url(tmp_path: Path, missing_field: str) -> None:
    values = production_values(tmp_path)
    values.pop(missing_field)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_staging_and_production_reject_direct_database_urls(tmp_path: Path, environment: str) -> None:
    values = production_values(tmp_path, environment=environment)
    values.pop("database_url_file")
    values["database_url"] = "mysql+pymysql://user:password@db/printvault"

    with pytest.raises(ValidationError, match="DATABASE_URL_FILE"):
        Settings(_env_file=None, **values)


def test_production_requires_absolute_https_issuer_and_redirect_urls(tmp_path: Path) -> None:
    values = production_values(tmp_path, oidc_issuer_url="issuer.example.test", oidc_redirect_uri="/callback")

    with pytest.raises(ValidationError, match="absolute HTTPS"):
        Settings(_env_file=None, **values)
