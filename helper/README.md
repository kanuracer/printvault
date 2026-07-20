# PrintVault Helper

Stdlib-only local desktop helper vertical slice for native slicer launch.

The helper is intentionally separate from the Docker server. It is limited to `helper/` and does not modify the backend, frontend, Docker, or Compose layers.

Current slice:

- Accepts a `printvault://open?request=<id>&profile=<profile-id>` target or `request_id:profile_id`.
- Loads local JSON config with an HTTPS origin, fixed `user_id`/`device_id`, and explicit slicer profiles.
- Redeems a one-time job from an authenticated HTTPS endpoint using a bearer token from `auth.token_env`.
- Requires the returned job to match the requested profile, local user/device, configured origin, and a maximum five-minute expiry window.
- Downloads the asset into a private cache directory, verifies SHA-256, prompts visibly in the CLI, and launches the configured executable with explicit argument list substitution only.

## Linux and Windows bundles

Release assets contain `printvault-helper-linux.zip` and
`printvault-helper-windows.zip`. Both bundles are portable and require
Python 3.10+; no credentials are included.

1. Extract the matching ZIP to a private local directory.
2. Copy `config.example.json` to `config.json` and set the HTTPS origin,
   registered `user_id`/`device_id`, and slicer executable paths.
3. Set `PRINTVAULT_HELPER_TOKEN` in the environment before launch.
4. Linux: run `./printvault-helper 'printvault://open?...'`.
   Windows: run `printvault-helper.bat "printvault://open?..."`.

Build release bundles locally:

```bash
python3 helper/build_packages.py
```

## Developer CLI

```bash
export PRINTVAULT_HELPER_TOKEN=...your access token...
python3 -m helper.printvault_helper --config helper/config.json 'printvault://open?request=req-123&profile=orca'
```

Run tests:

```bash
python3 -m unittest discover -s helper/tests -v
```

Notes:

- The config origin must be HTTPS only.
- `{file}` is valid only as one complete argument in a profile template.
- The helper never executes a server-provided command or arbitrary executable path.
- This slice includes unit coverage for rejected origins, hash mismatch, expiry, foreign URLs, missing confirmation, and safe subprocess argument construction.
- It does not claim macOS or Windows end-to-end packaging coverage.

The Windows bundle is package-layout validated on Linux, but requires a real
Windows + slicer smoke test before claiming native Windows E2E support.

See [`../docs/slicer-helper-protocol.md`](../docs/slicer-helper-protocol.md) and [`config.example.json`](config.example.json).
