# Phase 1 security controls

- The backend binds only to `127.0.0.1`; API requests with a different `Host` are rejected.
- A single-use launch token exchanges for an HttpOnly, SameSite=Strict session cookie and a CSRF cookie. Mutating API calls require the matching `X-MigraAI-CSRF` header.
- Session files can be encrypted with per-session AES-256-GCM keys held only in memory. Session directories are user-restricted; crash leftovers are swept at startup and sessions are wiped at shutdown, process exit, and Windows console close.
- Devin PAT values are stored as protector output, never returned in clear text, and masked to their last four characters. Windows uses current-user DPAPI; non-Windows development uses an AES-GCM key file and logs a development-only warning.
- Audit events are append-only JSONL with a SHA-256 hash chain. Access logs contain timestamps, method, path without query, status, duration, and a session ID prefix; cookies, query strings, bodies, secrets, and uploaded file content are not logged.
- `MIGRAAI_DEV_TOKEN` permits a fixed launch token for local development and automated testing only; do not use it for a production package.

## Planned

The remaining security architecture and controls are planned; see [Phase-Gate 0 §8](phase-gate-0-plan.md#8-security-architecture).
