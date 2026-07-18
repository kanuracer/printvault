# Deployment

## Prerequisites

- Docker Engine with Docker Compose
- An HTTPS reverse proxy
- An OIDC provider configured for the callback URL

## Configure

Copy the example configuration and create local directories:

```bash
cp .env.example .env
mkdir -p models archive data thumbnails secrets
```

Set your OIDC issuer URL and public callback URL in `.env`. The callback must be exactly:

```text
https://<your-public-host>/api/auth/callback
```

Create these files under `secrets/`:

- `database_url` — database connection URL; SQLite example: `sqlite:////var/lib/printvault/printvault.sqlite3`
- `oidc_client_id` — OIDC client identifier
- `oidc_client_secret` — OIDC client secret
- `session_secret` — a unique random value; generate with `openssl rand -hex 32`

Do not place those values in `.env`, Compose YAML, logs or Git.

## Start

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 printvault
```

The included Compose configuration binds the app to loopback only. Configure an HTTPS reverse proxy to forward your public origin to `http://127.0.0.1:8080` and preserve the original host and HTTPS scheme.

## Storage

`models`, `archive`, `data` and `thumbnails` are bind mounts. Back them up before upgrades. The container runs as UID/GID `99:100`; mounted writable directories must be writable by that identity.

## Update

1. Back up the mounted storage directories and database.
2. Pull a tagged source release or update the checked-out revision.
3. Run `docker compose up --build -d`.
4. Check `docker compose ps` and `/health` through the proxy.

Database migrations run before the API starts. If migration fails, the container exits instead of serving a partially initialized application.
