# Security

## Authentication and authorization

PrintVault has no local-password, guest or API-key login mode. It uses OIDC Authorization Code with PKCE. Configure an HTTPS issuer and callback URL in production.

Access is determined from the configured OIDC groups claim:

- `printvault_viewer` — view and download assets
- `printvault_editor` — upload and organize assets
- `printvault_admin` — administrative and permanent-delete actions

## Secrets

Store database URLs, OIDC client values and session secrets in file-mounted secrets. Keep `secrets/` and `.env` outside version control. Rotate a value if it was ever committed, shared in logs, or exposed to an untrusted party.

## Files and deletion

The server resolves file operations only within configured library roots. Assets are archived before permanent deletion. Permanent deletion is restricted and audit events are retained.

## Network exposure

The supplied Compose file publishes only a loopback address. Use a reverse proxy that terminates TLS and forwards traffic only from the trusted proxy path. Do not expose the app directly to the internet without HTTPS.

## Reporting vulnerabilities

Do not open a public issue with proof-of-concept details or credentials. Send a minimal private report to the repository maintainers, including affected version, impact and reproduction steps. Allow time for a fix before public disclosure.
