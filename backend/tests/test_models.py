from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Asset, AssetTag, AuditEvent, HelperDevice, HelperJob, HelperPairingCode, Library, LibraryExcludeRule, SlicerProfile, Tag, UserPreference


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_asset_persists_only_a_normalized_relative_path() -> None:
    session = make_session()
    library = Library(key="models", root_name="Models")
    asset = Asset(
        library=library,
        relative_path="parts\\bracket.stl",
        format="stl",
        byte_size=42,
        sha256="a" * 64,
        favorite=True,
    )
    session.add(asset)
    session.commit()

    stored = session.get(Asset, asset.id)
    assert stored is not None
    assert stored.relative_path == "parts/bracket.stl"
    assert stored.library.key == "models"
    assert stored.sha256 == "a" * 64
    assert stored.format == "stl"
    assert stored.byte_size == 42
    assert stored.favorite is True
    assert stored.created_at is not None
    assert stored.updated_at is not None
    assert not hasattr(stored, "absolute_path")


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.stl", "parts/../../outside.stl", "C:\\models\\part.stl", ""])
def test_asset_rejects_absolute_or_escaping_relative_paths(path: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        Asset(relative_path=path, format="stl", byte_size=1)


@pytest.mark.parametrize(
    "pattern",
    ["/etc/passwd", "../outside.stl", "parts/../../outside.stl", "C:\\models\\*.stl", "file:///tmp/*.stl", r"\\server\share\*.stl", ""],
)
def test_library_exclude_rule_rejects_absolute_or_escaping_patterns(pattern: str) -> None:
    with pytest.raises(ValueError, match="exclude pattern"):
        LibraryExcludeRule(pattern=pattern)


def test_library_exclude_rule_persists_a_normalized_relative_glob_pattern() -> None:
    session = make_session()
    library = Library(key="models", root_name="Models")
    rule = LibraryExcludeRule(library=library, pattern="./parts/**/*.stl")
    session.add(rule)
    session.commit()

    stored = session.get(LibraryExcludeRule, rule.id)
    assert stored is not None
    assert stored.pattern == "parts/**/*.stl"


def test_library_key_and_root_name_are_unique() -> None:
    session = make_session()
    session.add_all(
        [
            Library(key="models", root_name="Models"),
            Library(key="projects", root_name="Projects"),
        ]
    )
    session.commit()

    session.add(Library(key="models", root_name="More Models"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_tag_names_are_unique_case_insensitively() -> None:
    session = make_session()
    session.add(Tag(name="Functional"))
    session.commit()

    session.add(Tag(name="functional"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_asset_tag_connects_asset_and_tag() -> None:
    session = make_session()
    library = Library(key="models", root_name="Models")
    asset = Asset(library=library, relative_path="cube.stl", format="stl", byte_size=1)
    tag = Tag(name="Calibration")
    session.add_all([asset, tag])
    session.commit()

    session.add(AssetTag(asset_id=asset.id, tag_id=tag.id))
    session.commit()

    assert [assigned.name for assigned in asset.tags] == ["Calibration"]


def test_audit_event_captures_actor_action_optional_asset_and_json_metadata() -> None:
    session = make_session()
    event = AuditEvent(
        actor_subject="oidc|alice",
        action="asset.downloaded",
        metadata_json={"ip": "192.0.2.1", "download": True},
    )
    session.add(event)
    session.commit()

    stored = session.get(AuditEvent, event.id)
    assert stored is not None
    assert stored.actor_subject == "oidc|alice"
    assert stored.action == "asset.downloaded"
    assert stored.asset_id is None
    assert stored.metadata_json == {"ip": "192.0.2.1", "download": True}
    assert stored.created_at is not None


def test_user_preferences_are_scoped_to_subject_and_key() -> None:
    session = make_session()
    session.add(UserPreference(subject="oidc|alice", key="grid-density", value={"columns": 4}))
    session.commit()

    session.add(UserPreference(subject="oidc|alice", key="grid-density", value={"columns": 5}))
    with pytest.raises(IntegrityError):
        session.commit()


def test_slicer_profile_stores_named_json_configuration() -> None:
    session = make_session()
    profile = SlicerProfile(
        name="Prusa Draft",
        owner_subject="oidc|alice",
        configuration={"layer_height": 0.28, "infill": 15},
    )
    session.add(profile)
    session.commit()

    stored = session.get(SlicerProfile, profile.id)
    assert stored is not None
    assert stored.name == "Prusa Draft"
    assert stored.owner_subject == "oidc|alice"
    assert stored.configuration == {"layer_height": 0.28, "infill": 15}
    assert stored.created_at is not None


def test_helper_device_pairing_code_and_job_bind_to_the_same_subject() -> None:
    session = make_session()
    library = Library(key="models", root_name="Models")
    asset = Asset(library=library, relative_path="part.stl", format="stl", byte_size=12, sha256="a" * 64)
    device = HelperDevice(
        device_id="device_abc",
        owner_subject="oidc|alice",
        name="Alice Laptop",
        credential_hash="b" * 64,
    )
    pairing = HelperPairingCode(
        owner_subject="oidc|alice",
        code_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add_all([asset, device, pairing])
    session.flush()
    job = HelperJob(
        owner_subject="oidc|alice",
        request_id_hash="d" * 64,
        device_id=device.id,
        asset_id=asset.id,
        profile_id="orca",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(job)
    session.commit()

    stored = session.get(HelperJob, job.id)
    assert stored is not None
    assert stored.owner_subject == "oidc|alice"
    assert stored.device.device_id == "device_abc"
    assert stored.asset.relative_path == "part.stl"
    assert stored.profile_id == "orca"


def test_domain_migration_creates_all_domain_tables(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    database_path = tmp_path / "printvault.sqlite3"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    table_names = set(inspect(create_engine(f"sqlite:///{database_path}")).get_table_names())
    assert {
        "libraries",
        "assets",
        "tags",
        "asset_tags",
        "audit_events",
        "helper_devices",
        "helper_jobs",
        "helper_pairing_codes",
        "library_exclude_rules",
        "user_preferences",
        "slicer_profiles",
    } <= table_names
