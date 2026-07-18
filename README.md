# PrintVault

Self-hosted organizer for 3D-print files. Runs as a Docker/Dockhand web app on Unraid.

## Scope

- Writable libraries: models, projects, archive/trash
- Metadata, tags, search, favorites, duplicate groups and audit history
- STL, OBJ and 3MF browser preview
- MariaDB production support and SQLite development support
- OIDC-only access with `printvault_admin`, `printvault_editor`, `printvault_viewer`
- Native slicer launch through optional local helper; universal download fallback
- German and English UI via i18n locale files

## Security model

- No local passwords, guest accounts or API-key bypass
- API accepts only validated OIDC access tokens
- Filesystem actions stay inside configured library roots
- Archive first; permanent deletion is admin-only and audited
- Database credentials are Docker secrets/root-only files, never Git or Wiki content

## Deployment topology

```text
Browser → printvault.kanuracer.de → reverse proxy → Docker web_net → printvault:8080
```

Compose must not declare `ports:` or `expose:` for PrintVault. The reverse proxy resolves `printvault` through the shared external `web_net` Docker network.

## Development

Prerequisites: Python 3.12+, Node 22+, Docker Compose for container checks.

```bash
cp .env.example .env
# Backend/frontend commands added with the respective implementation tasks.
```

See [documentation index](docs/index.json) for targeted lookup and [implementation plan](docs/plans/2026-07-18-printvault.md) for current work.

## Supported formats

Initial viewer/index target: `.stl`, `.obj`, `.3mf`. Other files remain storable/downloadable and can receive type-specific support later.
