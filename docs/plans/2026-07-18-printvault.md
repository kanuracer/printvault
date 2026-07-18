# PrintVault Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Self-hosted 3D-print-file library for Unraid: writable model/project/archive libraries, fast metadata search, WebGL previews, OIDC-only access, MariaDB and SQLite support, local slicer launching, Docker/Dockhand deployment.

**Architecture:** Monorepo: FastAPI API/worker owns filesystem access, index state, audit log, and DB; React/TypeScript SPA owns responsive dark/light UI and Three.js previews. API discovers only configured library roots, resolves paths safely, and performs archive/delete as controlled filesystem transactions. Container joins external `web_net`; reverse proxy reaches service by Docker DNS. No `ports:` or `expose:` in Compose.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, MariaDB/MySQL + SQLite dialects, React 19, TypeScript, Vite, i18next, Three.js, Vitest, pytest, Docker Compose.

**Deployment facts:**
- Slug/DB: `printvault`
- Internal hostname: `printvault.kanuracer.de`
- Pi-hole DNS target: reverse proxy `10.0.0.14`
- App network: external Docker bridge `web_net`; proxy target `printvault:8080`
- Unraid library roots: `/mnt/user/appdata/printvault/{modelle,projekte,archiv}`
- App data: `/mnt/user/appdata/printvault/{data,thumbnails,backups}`
- DB: MariaDB `printvault`; SQLite configurable for local/dev
- OIDC groups: `printvault_admin`, `printvault_editor`, `printvault_viewer`

---

### Task 1: Repository contract and documentation index

**Objective:** Establish project conventions and targeted documentation lookup before application code.

**Files:**
- Create: `README.md`
- Create: `docs/index.json`
- Create: `.gitignore`
- Create: `.env.example`

**Steps:**
1. Write README with supported formats, security model, local slicer-helper limitation, install/development commands.
2. Create JSON docs index. Every document entry has `id`, `file`, `summary`, `topics`, `status`.
3. Add secret, build, cache, SQLite and screenshot exclusions to `.gitignore`.
4. Add non-secret environment variable names/examples only.
5. Verify JSON: `python3 -m json.tool docs/index.json >/dev/null`.

### Task 2: Container and compose foundation

**Objective:** Build reproducible API + frontend runtime image and Dockhand-safe Compose topology.

**Files:**
- Create: `compose.yaml`
- Create: `Dockerfile`
- Create: `docker/nginx.conf`
- Create: `docker/entrypoint.sh`

**Steps:**
1. Multi-stage build frontend assets and Python API image.
2. Serve SPA and `/api` from one container on port 8080.
3. Set `user: "99:100"`, read/write only explicit mounted library/data roots.
4. Use mounted secret file via `DATABASE_URL_FILE`; never put DB password in Compose.
5. Join only external `web_net`; define neither `ports:` nor `expose:`.
6. Validate: `docker compose -f compose.yaml config` and YAML parser.

### Task 3: Backend configuration and database abstraction

**Objective:** Support MariaDB production and SQLite development without conditional business logic.

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/config.py`
- Create: `backend/app/db.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Test: `backend/tests/test_config.py`

**Steps:**
1. Write failing tests for validated library roots, DB URL file precedence, SQLite URL acceptance, and OIDC mandatory configuration.
2. Implement pydantic-settings config; reject absent OIDC issuer/client ID and library roots outside configured absolute paths.
3. Implement SQLAlchemy engine/session for MariaDB and SQLite.
4. Add Alembic baseline and migration command.
5. Verify pytest and an SQLite migration smoke.

### Task 4: Domain models and RBAC

**Objective:** Persist libraries, assets, tags, audit events and OIDC users.

**Files:**
- Create: `backend/app/models/*.py`
- Create: `backend/app/services/rbac.py`
- Create: `backend/alembic/versions/0001_initial.py`
- Test: `backend/tests/test_models.py`
- Test: `backend/tests/test_rbac.py`

**Steps:**
1. Model `Library`, `Asset`, `Tag`, `AssetTag`, `AuditEvent`, `UserPreference`, `SlicerProfile`.
2. Use stable relative paths; never persist client-controlled absolute paths as authority.
3. Map OIDC `groups` claim exactly to admin/editor/viewer. Missing role denies access.
4. Add migrations and test MariaDB-compatible columns/indexes.
5. Test all role/action matrix entries, especially permanent delete restricted to admin.

### Task 5: OIDC-only BFF auth middleware

**Objective:** Authenticate through Nextcloud OIDC Authorization Code Flow with PKCE and reject non-OIDC access.

**Files:**
- Create: `backend/app/auth/oidc.py`
- Create: `backend/app/auth/session.py`
- Create: `backend/app/auth/dependencies.py`
- Test: `backend/tests/test_oidc.py`

**Steps:**
1. Discover/cache provider metadata and JWKS from `https://pcloud.kanuracer.eu/.well-known/openid-configuration`.
2. Implement server-side Authorization Code Flow with PKCE. Registered redirect URI is exactly `https://printvault.kanuracer.de/api/auth/callback`.
3. Exchange code server-side using confidential-client `client_secret_post`; secret is read only from `PRINTVAULT_OIDC_CLIENT_SECRET_FILE`/root-only Docker secret.
4. Validate issuer, audience/client ID, expiry, subject and `groups` claim; create short-lived encrypted HttpOnly/Secure/SameSite=Lax app session.
5. Return 401 for missing/invalid session and 403 for valid identity without mapped role.
6. Add test fixtures for login-state/nonce/PKCE validation, callback failure, group mapping and logout.
7. Do not implement password auth, API keys, guest mode, token storage in browser localStorage or bypass flags.

### Task 6: Safe writable filesystem service

**Objective:** Index and mutate only mounted model/project/archive roots.

**Files:**
- Create: `backend/app/services/filesystem.py`
- Create: `backend/app/services/archive.py`
- Test: `backend/tests/test_filesystem.py`
- Test: `backend/tests/test_archive.py`

**Steps:**
1. Implement canonical path resolution with `Path.resolve()` and root containment checks.
2. Reject symlinks, traversal, hidden unsafe roots and unsupported mutations.
3. Archive means atomic move into archive root retaining source library and relative path metadata.
4. Restore returns item to recorded source location only after collision policy confirmation.
5. Permanent deletion is admin-only and writes immutable audit record.
6. Test traversal, symlink escape, cross-root move, collisions, archive/restore/delete.

### Task 7: Indexing, extraction and thumbnails

**Objective:** Discover STL/OBJ/3MF, extract size/hash/geometry metadata, detect duplicates and generate cached previews.

**Files:**
- Create: `backend/app/services/indexer.py`
- Create: `backend/app/services/metadata.py`
- Create: `backend/app/services/thumbnails.py`
- Create: `backend/app/worker.py`
- Test: `backend/tests/test_indexer.py`
- Test fixtures: `backend/tests/fixtures/*`

**Steps:**
1. Add scan command and debounced background queue.
2. Extract file hash, format, file size, modified time, bounding box and triangle count when format supports it.
3. Group identical content hashes as duplicates without deleting anything automatically.
4. Read embedded 3MF thumbnails when present; render deterministic headless fallback previews for STL/OBJ/3MF.
5. Cache thumbnails by content hash under configured thumbnails root.
6. Test index idempotency, changed files, removed files, duplicate grouping and thumbnail fallback.

### Task 8: Asset and library API

**Objective:** Deliver paginated library browsing and controlled asset commands.

**Files:**
- Create: `backend/app/api/libraries.py`
- Create: `backend/app/api/assets.py`
- Create: `backend/app/api/tags.py`
- Create: `backend/app/api/audit.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_api_assets.py`

**Steps:**
1. Add routes for libraries, search/filter/sort, asset details/download, tags, favorite, duplicate groups.
2. Add editor routes for tag assignment, moves, archive, restore and scan start.
3. Add admin-only permanent delete and audit routes.
4. Stream downloads with safe attachment headers; never serve arbitrary paths.
5. Add role-specific integration tests including 401/403 cases.

### Task 9: React app shell, i18n and theme

**Objective:** Provide polished dark-first UI with user-selectable light theme and no visible hardcoded strings.

**Files:**
- Create: `frontend/src/i18n/de.json`
- Create: `frontend/src/i18n/en.json`
- Create: `frontend/src/i18n/index.ts`
- Create: `frontend/src/theme.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles/*.css`
- Test: `frontend/src/**/*.test.tsx`

**Steps:**
1. Configure i18next with German default, English available and message interpolation/plurals.
2. Keep all visible text, aria labels, validation and empty/error states in locale JSON.
3. Implement `dark`, `light`, `system`; initial product default is dark. Persist preference per OIDC subject through API.
4. Build responsive app shell: left library nav, top search/actions, central asset grid/list, contextual inspector.
5. Test no raw user-visible literals in components via lint/test rule; test theme persistence.
6. Capture desktop 1440px, tablet 768px and mobile 390px screenshots in both themes; inspect console/network errors and overflow.

### Task 10: Web 3D viewer

**Objective:** Render STL, OBJ and 3MF safely in browser.

**Files:**
- Create: `frontend/src/features/viewer/ModelViewer.tsx`
- Create: `frontend/src/features/viewer/loaders.ts`
- Create: `frontend/src/features/viewer/ViewerControls.tsx`
- Test: `frontend/src/features/viewer/*.test.tsx`

**Steps:**
1. Load signed/authenticated asset download URL through API; enforce format and maximum viewer file size.
2. Use Three.js STLLoader, OBJLoader and ThreeMFLoader.
3. Normalize geometry, fit camera, show grid/orientation axes, orbit/pan/zoom and wireframe toggle.
4. Show localized loading, unsupported, oversized and parse-failure states.
5. Test loader dispatch; visual screenshot test real models in dark/light viewer.

### Task 11: Slicer helper protocol

**Objective:** Offer portable local slicer opening without pretending browser/Docker can launch native apps alone.

**Files:**
- Create: `docs/slicer-helper-protocol.md`
- Create: `frontend/src/features/slicer/LaunchSlicer.tsx`
- Create: `helper/README.md`
- Create: `helper/config.example.json`
- Test: `frontend/src/features/slicer/*.test.tsx`

**Steps:**
1. Implement download fallback for every client.
2. Define `printvault://` helper request protocol with signed short-lived download URL and local target command profile.
3. Support user-defined command templates for OrcaSlicer, PrusaSlicer, Cura, Bambu Studio, SuperSlicer and arbitrary slicers.
4. Never pass shell strings unescaped; helper executes explicit binary + argv.
5. Add platform-specific helper installation docs for Windows, Linux, macOS.

### Task 12: Deployment, private Wiki docs and acceptance verification

**Objective:** Deploy safely through Dockhand and document real operation.

**Files:**
- Modify: `compose.yaml`
- Create: `docs/deployment.md`
- Create: `docs/operations.md`
- Create: `docs/security.md`
- Modify: `docs/index.json`

**Steps:**
1. Create protected Docker secret for DB URL on Unraid; do not commit it.
2. Configure Dockhand stack using explicit host volumes and external `web_net`.
3. Add Pi-hole local DNS records to reverse proxy `10.0.0.14`; add reverse-proxy host to `printvault:8080` and existing matching TLS cert.
4. Configure Nextcloud OIDC client/groups once user supplies issuer/client settings.
5. Publish scrubbed private Wiki project page and keep docs index synchronized.
6. Run: backend tests, frontend tests, production build, Compose config, container health, DB migration, OIDC role checks, write/archive/delete flow, viewer, download and slicer-helper fallback.
7. Capture real browser screenshots and inspect console/network; update Wiki only after verification.

## Acceptance criteria

- OIDC-only authentication; unmapped users denied.
- `admin`, `editor`, `viewer` permissions enforced server-side.
- Model/project/archive libraries are writable only within mounted roots; archive/restore/delete audited.
- MariaDB and SQLite test suites pass.
- STL/OBJ/3MF browse and viewer work; duplicate detection and thumbnails present.
- Dark/light switch works; every visible UI string comes from locale JSON.
- Compose joins `web_net`, with no `ports:` and no `expose:`.
- Private Wiki docs and `docs/index.json` reflect verified state.
