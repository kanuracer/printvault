"""Regression tests for the PrintVault deployment security contract."""

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

    def test_accepts_the_required_secure_topology(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    environment:
      PRINTVAULT_DATABASE_URL_FILE: /run/secrets/printvault_database_url
      PRINTVAULT_OIDC_CLIENT_ID_FILE: /run/secrets/printvault_oidc_client_id
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/printvault_oidc_client_secret
      PRINTVAULT_SESSION_SECRET_FILE: /run/secrets/printvault_session_secret
    networks:
      - web_net
networks:
  web_net:
    external: true
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_oidc_client_id_and_session_secret_files(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    environment:
      PRINTVAULT_DATABASE_URL_FILE: /run/secrets/printvault_database_url
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/printvault_oidc_client_secret
    networks: [web_net]
networks:
  web_net:
    external: true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRINTVAULT_OIDC_CLIENT_ID_FILE", result.stderr)
        self.assertIn("PRINTVAULT_SESSION_SECRET_FILE", result.stderr)

    def test_rejects_legacy_unprefixed_database_url_file(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    environment:
      DATABASE_URL_FILE: /run/secrets/printvault_database_url
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/printvault_oidc_client_secret
    networks: [web_net]
networks:
  web_net:
    external: true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRINTVAULT_DATABASE_URL_FILE", result.stderr)

    def test_rejects_host_port_publication(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    ports: ["8080:8080"]
    environment:
      DATABASE_URL_FILE: /run/secrets/db
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/oidc
    networks: [web_net]
networks:
  web_net:
    external: true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ports", result.stderr)

    def test_rejects_expose(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    expose: ["8080"]
    environment:
      DATABASE_URL_FILE: /run/secrets/db
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/oidc
    networks: [web_net]
networks:
  web_net:
    external: true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expose", result.stderr)

    def test_rejects_non_external_web_net(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    environment:
      DATABASE_URL_FILE: /run/secrets/db
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/oidc
    networks: [web_net]
networks:
  web_net:
    driver: bridge
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("external", result.stderr)

    def test_rejects_literal_database_and_oidc_secrets(self) -> None:
        result = self.run_validator(
            """
services:
  printvault:
    environment:
      DATABASE_URL: mysql+pymysql://printvault:password@db/printvault
      PRINTVAULT_OIDC_CLIENT_SECRET: super-secret
    networks: [web_net]
networks:
  web_net:
    external: true
"""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL", result.stderr)
        self.assertIn("PRINTVAULT_OIDC_CLIENT_SECRET", result.stderr)

    def test_repository_compose_has_required_unraid_runtime_contract(self) -> None:
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
        self.assertEqual(service["user"], "99:100")
        self.assertEqual(service["restart"], "unless-stopped")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(
            service["tmpfs"],
            ["/tmp:mode=1777,size=640m", "/var/cache/nginx:uid=99,gid=100,mode=0750,size=640m"],
        )
        self.assertIn("healthcheck", service)
        self.assertEqual(
            service["environment"],
            {
                "PRINTVAULT_DATABASE_URL_FILE": "/run/secrets/printvault_database_url",
                "PRINTVAULT_ENVIRONMENT": "production",
                "PRINTVAULT_OIDC_ISSUER_URL": "https://pcloud.kanuracer.eu",
                "PRINTVAULT_OIDC_CLIENT_ID_FILE": "/run/secrets/printvault_oidc_client_id",
                "PRINTVAULT_OIDC_CLIENT_SECRET_FILE": "/run/secrets/printvault_oidc_client_secret",
                "PRINTVAULT_OIDC_REDIRECT_URI": "https://printvault.kanuracer.de/api/auth/callback",
                "PRINTVAULT_OIDC_GROUPS_CLAIM": "groups",
                "PRINTVAULT_SESSION_SECRET_FILE": "/run/secrets/printvault_session_secret",
                "PRINTVAULT_LIBRARY_MODELS_ROOT": "/libraries/modelle",
                "PRINTVAULT_LIBRARY_PROJECTS_ROOT": "/libraries/projekte",
                "PRINTVAULT_LIBRARY_ARCHIVE_ROOT": "/libraries/archiv",
                "PRINTVAULT_DATA_ROOT": "/var/lib/printvault",
                "PRINTVAULT_THUMBNAILS_ROOT": "/var/lib/printvault/thumbnails",
            },
        )
        self.assertEqual(
            service["volumes"][:5],
            [
                "/mnt/user/appdata/printvault/modelle:/libraries/modelle:rw",
                "/mnt/user/appdata/printvault/projekte:/libraries/projekte:rw",
                "/mnt/user/appdata/printvault/archiv:/libraries/archiv:rw",
                "/mnt/user/appdata/printvault/data:/var/lib/printvault:rw",
                "/mnt/user/appdata/printvault/thumbnails:/var/lib/printvault/thumbnails:rw",
            ],
        )
        self.assertEqual(
            service["volumes"][5:],
            [
                "/mnt/user/appdata/printvault/secrets/database_url:/run/secrets/printvault_database_url:ro",
                "/mnt/user/appdata/printvault/secrets/oidc_client_id:/run/secrets/printvault_oidc_client_id:ro",
                "/mnt/user/appdata/printvault/secrets/oidc_client_secret:/run/secrets/printvault_oidc_client_secret:ro",
                "/mnt/user/appdata/printvault/secrets/session_secret:/run/secrets/printvault_session_secret:ro",
            ],
        )

    def test_dockerfile_declares_frontend_backend_and_runtime_stages(self) -> None:
        dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM node:22-alpine AS frontend-build", dockerfile)
        self.assertIn("FROM python:3.12-slim AS backend-build", dockerfile)
        self.assertIn("COPY --from=frontend-build", dockerfile)
        self.assertIn("COPY --from=backend-build", dockerfile)
        self.assertIn("mkdir -p /workspace/backend", dockerfile)
        self.assertIn("COPY --chmod=0644 docker/nginx.conf /etc/nginx/nginx.conf", dockerfile)
        self.assertIn("USER 99:100", dockerfile)
        self.assertNotIn("EXPOSE ", dockerfile)

    def test_entrypoint_runs_database_migrations_before_api_start(self) -> None:
        entrypoint = (REPOSITORY_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("PRINTVAULT_DATABASE_URL_FILE", entrypoint)
        self.assertIn("run_migrations", entrypoint)
        self.assertLess(entrypoint.index("run_migrations"), entrypoint.index("uvicorn"))
        self.assertIn("--no-access-log", entrypoint)

    def test_docker_build_context_excludes_runtime_secrets_and_dependency_trees(self) -> None:
        dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
        for expected in (".env", "secrets/", "backend/.venv/", "frontend/node_modules/", "frontend/dist/"):
            self.assertIn(expected, dockerignore)

    def test_runtime_backend_declares_uvicorn_server_dependency(self) -> None:
        backend_manifest = (REPOSITORY_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"uvicorn[standard]', backend_manifest)

    def test_nginx_configuration_is_safe_for_the_nonroot_runtime(self) -> None:
        nginx_config = (REPOSITORY_ROOT / "docker" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("client_body_temp_path /var/cache/nginx/client_temp;", nginx_config)
        self.assertIn("proxy_temp_path /var/cache/nginx/proxy_temp;", nginx_config)
        self.assertIn("location = /api { return 308 /api/; }", nginx_config)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", nginx_config)
        self.assertIn("client_max_body_size 512m;", nginx_config)
        self.assertIn("log_format privacy_safe", nginx_config)
        self.assertIn("$request_method $uri $server_protocol", nginx_config)
        self.assertNotIn("access_log /dev/stdout;", nginx_config)


if __name__ == "__main__":
    unittest.main()
