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

### `compose.yaml` example: published image

The checked-in `compose.yaml` builds from local source. To run the published immutable image instead, replace it with this example:

```yaml
services:
  printvault:
    image: ghcr.io/kanuracer/printvault:0.1.0
    container_name: printvault
    user: "99:100"
    restart: unless-stopped
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:mode=1777,size=640m
      - /var/cache/nginx:uid=99,gid=100,mode=0750,size=640m
    env_file:
      - .env
    environment:
      PRINTVAULT_DATABASE_URL_FILE: /run/secrets/database_url
      PRINTVAULT_OIDC_CLIENT_ID_FILE: /run/secrets/oidc_client_id
      PRINTVAULT_OIDC_CLIENT_SECRET_FILE: /run/secrets/oidc_client_secret
      PRINTVAULT_SESSION_SECRET_FILE: /run/secrets/session_secret
    volumes:
      - ./models:/libraries/models:rw
      - ./archive:/libraries/archive:rw
      - ./data:/var/lib/printvault:rw
      - ./thumbnails:/var/lib/printvault/thumbnails:rw
      - ./secrets/database_url:/run/secrets/database_url:ro
      - ./secrets/oidc_client_id:/run/secrets/oidc_client_id:ro
      - ./secrets/oidc_client_secret:/run/secrets/oidc_client_secret:ro
      - ./secrets/session_secret:/run/secrets/session_secret:ro
    ports:
      - "127.0.0.1:8080:8080"
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

`0.1.0` is immutable. `latest` is also available, but use a version tag for production. The registry image is public; no `docker login` is needed.

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
docker compose pull
docker compose up -d
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
