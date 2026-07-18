# PrintVault

Self-hosted organizer for 3D-print files. PrintVault provides a browser UI for libraries, metadata, projects, archive workflows and STL, OBJ and 3MF previews.

## Features

- Logical projects and folders, tags, search, favourites and archive workflows
- Safe upload, download and metadata handling for 3D-print assets
- Browser previews for STL, OBJ and 3MF files
- German and English UI
- OIDC login with viewer, editor and admin roles
- MariaDB support for production plus SQLite for local development
- Responsive desktop and mobile interface

## Quick start

Requirements: Docker Engine with Docker Compose and an OIDC provider. The production container uses an unprivileged user, a read-only filesystem and file-mounted secrets.

```bash
git clone https://github.com/kanuracer/printvault.git
cd printvault
cp .env.example .env
mkdir -p models archive data thumbnails secrets
```

Edit `.env` and replace the example OIDC URLs with your own HTTPS issuer and public callback URL. Create these files in `secrets/`; they are ignored by Git:

```text
secrets/database_url
secrets/oidc_client_id
secrets/oidc_client_secret
secrets/session_secret
```

For a local SQLite deployment, `secrets/database_url` may contain:

```text
sqlite:////var/lib/printvault/printvault.sqlite3
```

Generate a fresh session secret locally, then store it only in `secrets/session_secret`:

```bash
openssl rand -hex 32 > secrets/session_secret
```

Start the container:

```bash
docker compose up --build -d
docker compose ps
```

The example Compose file publishes only `127.0.0.1:8080`. Put an HTTPS reverse proxy in front of it and register `<public-origin>/api/auth/callback` at your OIDC provider. For rootless or NAS deployments, make mounted writable directories accessible to container UID/GID `99:100`.

## Security model

- No local-password, guest or API-key authentication path
- OIDC Authorization Code flow with PKCE, validated ID tokens and secure cookies
- Role-based access: `printvault_viewer`, `printvault_editor`, `printvault_admin`
- File operations constrained to configured container library roots
- Archive-first deletion; permanent deletion requires administrator access and is audited
- Database, OIDC and session values are read from files, never committed values

Read [deployment notes](docs/deployment.md) and the [security guide](docs/security.md) before exposing an instance publicly.

## Development

Requirements: Python 3.12+, Node 22+ and Docker Compose.

```bash
cd backend && python -m pytest
cd ../frontend && npm ci && npm test -- --run && npm run build
cd .. && docker build -t printvault:local .
```

## Supported formats

Preview and metadata extraction target `.stl`, `.obj` and `.3mf`. Other files can be stored and downloaded, but may not have a viewer.

## License

This project is licensed under [AGPL-3.0-only](LICENSE). You may fork and modify it, but network users of a modified version must be offered the corresponding source code under the same license. The PrintVault name and logos are not granted as trademark rights.
