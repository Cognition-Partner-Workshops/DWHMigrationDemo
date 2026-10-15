# Phase 1 API

All endpoints are local to `127.0.0.1`. Except for health and launch-token exchange, APIs require the `migraai_session` cookie. `POST`, `PUT`, `PATCH`, and `DELETE` also require `X-MigraAI-CSRF` matching the `migraai_csrf` cookie.

| Method and path | Behavior |
|---|---|
| `GET /api/health` | Open readiness response: `{"status":"ready","mode":"local","version":"0.1.0","devin_configured":false}` |
| `GET /api/auth/launch?token=…` | Exchanges a single-use launch token for session and CSRF cookies; returns `303` to `/`, or `403` for invalid/reused token. |
| `GET /api/platforms` | Returns the eight source and seven target `{id,name}` entries. |
| `GET /api/settings` | Returns settings and PAT status as `{"configured":true,"masked":"••••abcd"}`; never returns the PAT. |
| `PUT /api/settings` | Accepts supplied settings fields; empty `devin_pat` clears the stored token. Returns the same masked representation. |
| `POST /api/session/confirm` | Accepts `{"source":"teradata","target":"databricks"}` and returns `{"session_id":"…","source":"teradata","target":"databricks"}`. Unknown IDs or identical source/target return `422`. |

Authentication failures return `401`, CSRF failures `403`, and invalid Host headers `400`. Upload, conversion, planning, and download endpoints are not implemented in Phase 1; see [Phase-Gate 0 §10](phase-gate-0-plan.md#10-api-specification-summary).
