# Phase 1 API

All endpoints are local to `127.0.0.1`. Except for health and launch-token exchange, APIs require the `migraai_session` cookie. `POST`, `PUT`, `PATCH`, and `DELETE` also require `X-MigraAI-CSRF` matching the `migraai_csrf` cookie.

| Method and path | Behavior |
|---|---|
| `GET /api/health` | Open readiness response: `{"status":"ready","mode":"local","version":"0.1.0","devin_configured":false}` |
| `GET /api/auth/launch?token=…` | Exchanges a single-use launch token for session and CSRF cookies; returns `303` to `/`, or `403` for invalid/reused token. |
| `GET /api/platforms` | Returns the eight source and seven target `{id,name}` entries. |
| `GET /api/settings` | Returns settings, PAT status as `{"configured":true,"masked":"••••abcd"}`, and `token_kind` (`v3`, `legacy`, or `none`); never returns the PAT. |
| `PUT /api/settings` | Accepts supplied settings fields; empty `devin_pat` clears the stored token. Returns the same masked representation. |
| `POST /api/session/confirm` | Accepts `{"source":"teradata","target":"databricks"}`, starts planning, and returns the confirmed pair, session ID, and plan state. Unknown IDs or identical source/target return `422`. |

Authentication failures return `401`, CSRF failures `403`, and invalid Host headers `400`. Upload and conversion endpoints are not implemented; see [Phase-Gate 0 §10](phase-gate-0-plan.md#10-api-specification-summary).

## Phase 2 knowledge base

| Method and path | Behavior |
|---|---|
| `GET /api/kb/status` | Returns the active KB version, creation time, document/chunk counts, last refresh summary, current refresh progress (or `null`), rollback versions, and `seed_error` (an error count or `null`). |
| `POST /api/kb/refresh` | Starts a background refresh and returns `202`; returns `409` if a refresh is already running. |
| `DELETE /api/kb/refresh` | Requests cancellation of the active refresh; returns `409` if none is running. |
| `POST /api/kb/rollback` | Activates the previous retained version; returns `409` if none is available or a refresh is running. |
| `GET /api/kb/search?q=…&source=…&target=…&section=…&k=8` | Returns hybrid BM25/vector results with chunk text and source metadata. |

All KB endpoints use the same session-cookie, CSRF, and local Host protections described above.

## Phase 2 planning

| Method and path | Behavior |
|---|---|
| `POST /api/plan` | Starts or regenerates a plan for the confirmed pair. A current-version Devin plan is reused unless `{"regenerate":true}` is supplied. Missing or unsupported tokens produce a local draft. |
| `GET /api/plan` | Returns job progress, plan, origin, fallback reason, KB version, and stale status. |
| `DELETE /api/plan/job` | Cancels the active plan job and terminates the Devin session when supported. |
| `GET /api/plan.pdf` | Downloads the latest plan as a PDF, or returns `404` if none exists. |
| `GET /api/resources` | Returns the confirmed source platform's catalog with upload availability state. |
| `PATCH /api/resources/{id}` | Updates per-session resource availability; state is encrypted and cleared on session replacement. |

A successful `POST /api/session/confirm` also starts the plan flow and includes its state in the response. Devin credentials are never returned by the settings API. `token_kind` is `v3`, `legacy`, or `none`; `apk_` keys use Devin v1 without an organization ID, while `cog_` keys use Devin v3 and require an organization ID.
