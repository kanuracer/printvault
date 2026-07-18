"""Regression tests for the public Docker Compose security contract."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate_deployment.py"


class DeploymentConfigValidationTests(unittest.TestCase):
    def run_validator(self, compose_text: str) -> subprocess.CompletedProcess[str]:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        compose_path = temporary_directory / "compose.yaml"
        compose_path.write_text(compose_text, encoding="utf-8")
        return subprocess.run(
            ["python3", str(VALIDATOR), str(compose_path)],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def secure_service(self, extra: str = "") -> str:
        return f"""
services:
  printvault:
    env_file: [.env]
    ports: ["127.0.0.1:8080:8080"]
    environment:
      PRINTVAULT_DATABASE_URL_FILE: /run/secrets/database_url
      PRINTVAULT_OIDC_CLIENT_ID_FILE: /run/secrets/oidc_client_id
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/oidc_client_secret
      PRINTVAULT_SESSION_SECRET_FILE: /run/secrets/session_secret
{extra}
"""

    def test_accepts_loopback_only_file_secret_topology(self) -> None:
        result = self.run_validator(self.secure_service())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_required_secret_file_environment(self) -> None:
        result = self.run_validator(
            self.secure_service().replace("      PRINTVAULT_SESSION_SECRET_FILE: /run/secrets/session_secret\n", "")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRINTVAULT_SESSION_SECRET_FILE", result.stderr)

    def test_rejects_non_loopback_port(self) -> None:
        result = self.run_validator(self.secure_service().replace("127.0.0.1:8080:8080", "0.0.0.0:8080:8080"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)

    def test_rejects_expose(self) -> None:
        result = self.run_validator(self.secure_service("    expose: [\"8080\"]"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expose", result.stderr)

    def test_rejects_nonstandard_env_file(self) -> None:
        result = self.run_validator(self.secure_service().replace("env_file: [.env]", "env_file: [.env.example]"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("env_file", result.stderr)

    def test_rejects_literal_database_and_oidc_secrets(self) -> None:
        result = self.run_validator(
            self.secure_service(
                "      PRINTVAULT_DATABASE_URL: mysql+pymysql://example\n"
                "      PRINTVAULT_OIDC_CLIENT_SECRET: example-value"
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRINTVAULT_DATABASE_URL", result.stderr)
        self.assertIn("PRINTVAULT_OIDC_CLIENT_SECRET", result.stderr)

    def test_repository_compose_is_public_and_secure(self) -> None:
        compose_path = REPOSITORY_ROOT / "compose.yaml"
        result = subprocess.run(
            ["python3", str(VALIDATOR), str(compose_path)],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        service = compose["services"]["printvault"]
        self.assertEqual(service["build"]["context"], ".")
        self.assertEqual(service["env_file"], [".env"])
        self.assertEqual(service["ports"], ["127.0.0.1:${PRINTVAULT_PORT:-8080}:8080"])
        self.assertEqual(service["user"], "99:100")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertIn("healthcheck", service)
        self.assertNotIn("networks", compose)

    def test_dockerfile_declares_frontend_backend_and_runtime_stages(self) -> None:
        dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM node:22-alpine AS frontend-build", dockerfile)
        self.assertIn("FROM python:3.12-slim AS backend-build", dockerfile)
        self.assertIn("COPY --from=frontend-build", dockerfile)
        self.assertIn("COPY --from=backend-build", dockerfile)
        self.assertIn("USER 99:100", dockerfile)
        self.assertNotIn("EXPOSE ", dockerfile)

    def test_entrypoint_runs_database_migrations_before_api_start(self) -> None:
        entrypoint = (REPOSITORY_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("PRINTVAULT_DATABASE_URL_FILE", entrypoint)
        self.assertIn("run_migrations", entrypoint)
        self.assertLess(entrypoint.index("run_migrations"), entrypoint.index("uvicorn"))

    def test_docker_build_context_excludes_runtime_secrets_and_dependency_trees(self) -> None:
        dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
        for expected in (".env", "secrets/", "backend/.venv/", "frontend/node_modules/", "frontend/dist/"):
            self.assertIn(expected, dockerignore)


if __name__ == "__main__":
    unittest.main()