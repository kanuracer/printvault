from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import ApiDependencies, ApiSession, InMemoryAssetRepository, register_api


class TestBffSessions:
    """A server-side BFF session store; clients send only an opaque cookie."""

    def __init__(self) -> None:
        self._sessions = {
            "viewer-cookie": ApiSession(subject="viewer-subject", role="viewer"),
            "editor-cookie": ApiSession(subject="editor-subject", role="editor"),
            "admin-cookie": ApiSession(subject="admin-subject", role="admin"),
            "denied-cookie": ApiSession(subject="denied-subject", role=None),
        }

    def resolve(self, request: Request) -> ApiSession | None:
        return self._sessions.get(request.cookies.get("printvault_session", ""))


@pytest.fixture
def repository() -> InMemoryAssetRepository:
    return InMemoryAssetRepository.demo()


@pytest.fixture
def client(repository: InMemoryAssetRepository) -> Iterator[TestClient]:
    app = FastAPI()
    register_api(app, ApiDependencies(repository=repository, session_resolver=TestBffSessions().resolve))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authenticated_client(client: TestClient):
    @contextmanager
    def as_role(role: str) -> Iterator[TestClient]:
        client.cookies.set("printvault_session", f"{role}-cookie")
        try:
            yield client
        finally:
            client.cookies.clear()

    return as_role
