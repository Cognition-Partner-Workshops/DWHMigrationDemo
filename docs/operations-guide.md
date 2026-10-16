# MigraAI Operations Guide

This guide is for the people who run, secure and support MigraAI. For installation see [install-guide.md](install-guide.md); for day-to-day use see [user-guide.md](user-guide.md).

## Data locations and lifecycle

All run-time data lives under `%LOCALAPPDATA%\MigraAI` (or `MIGRAAI_DATA_DIR` if set). The portable folder is read-only in use.

| Path | Contents | Lifetime |
|---|---|---|
| `app.db` | Settings and saved migration plans | Kept until you delete it |
| `kb\kb-v<N>.db`, `kb\state.json` | Knowledge-base versions and the active version | The active version and the two before it are kept |
| `logs\audit.jsonl` | Audit log | Kept; never rotated or trimmed |
| `logs\access.log` | HTTP access log | Kept; never rotated or trimmed |
| `sessions\<uuid>\` | Encrypted uploads, conversion results and packages of the current session | Wiped when the session ends |

### Session folders

Confirming a source and target starts a session and creates `sessions\<uuid>\`. Every file written there (uploads, extracted ZIP members, results, the converted package) is encrypted with AES-GCM using a random 256-bit key held only in memory. Without the running process the files cannot be read.

A session folder is wiped when:

- you confirm a new source and target (the previous session is replaced);
- the app shuts down normally, including Ctrl+C;
- the console window is closed, the user logs off or Windows shuts down (a Windows console-close handler wipes `sessions\` before the process ends);
- the process exits through Python's normal exit path.

### Startup sweep

If the process was killed (for example by Task Manager or a power loss), session folders can be left behind. They stay unreadable, because their keys were only in memory, and MigraAI deletes everything under `sessions\` on the next start, before it accepts requests.

### What is not persisted

Uploads, the object inventory, conversion runs and their results are in memory and in the encrypted session folder only. Restarting the app loses them. "Re-convert selected" can merge with an earlier run only while the app stays open. Settings, saved plans, KB versions and logs are persisted.

## Security controls summary

See [security.md](security.md) for the security summary. In short:

- **Local only.** The server binds to `127.0.0.1` on a random free port. Requests with a Host header other than `127.0.0.1` or `localhost` are rejected.
- **Launch token.** Each start generates a random single-use launch token, printed in the console and opened in the browser. Exchanging it at `/api/auth/launch` creates the browser session; any later use of the token fails.
- **Cookie and CSRF.** The session cookie `migraai_session` is HttpOnly and SameSite=Strict. The `migraai_csrf` cookie is SameSite=Strict, and every state-changing API call must send the same value in the `X-MigraAI-CSRF` header. All `/api/` paths except `/api/health` and `/api/auth/launch` need a valid session.
- **PAT protection.** On Windows the Devin personal access token is encrypted with DPAPI for the current Windows user before it is stored in `app.db`. Only the same Windows user on the same machine can decrypt it. The API returns only whether a token is set and its last four characters.
- **Session keys.** Session files use AES-GCM with a per-session key that is never written to disk.
- **Secret masking and consent.** Detected secrets are replaced with `***MASKED***` before anything is sent to Devin, and the user must consent before each conversion run. A batch that still contains a secret is blocked and not sent.
- **No warehouse connection.** MigraAI never connects to a source or target warehouse. Generated runners default to a dry run.

## Audit log

The audit log is `logs\audit.jsonl`: one JSON object per line, appended and never rewritten.

| Field | Meaning |
|---|---|
| `seq` | Sequence number, starting at 1 |
| `ts` | UTC timestamp, ISO 8601 |
| `actor` | `user` or `system` |
| `action` | Event name, for example `session.confirm`, `uploads.received`, `convert.consent`, `convert.batch`, `download.package`, `settings.update`, `kb.refresh.done`, `kb.rollback` |
| `target` | What the event applies to: a session ID, a run ID, `app-settings` or `knowledge-base` |
| `details` | Event data: counts, sizes, SHA-256 digests, source and target, KB version |
| `prev_hash` | `hash` of the previous record; `0` repeated 64 times for the first record |
| `hash` | SHA-256 of `prev_hash` followed by the record without `hash`, serialised as canonical JSON (sorted keys, no spaces) |

Example record (values are illustrative):

```json
{"seq": 12, "ts": "2026-10-16T09:14:03.120455+00:00", "actor": "user", "action": "uploads.received", "target": "3f6c1a2e-...", "details": {"count": 2, "size": 18342, "sha256": "9b1e..."}, "prev_hash": "c04d...", "hash": "71aa..."}
```

### Verifying the chain

MigraAI has no command or UI for verifying the chain. The backend contains a `verify_chain` function, used by the tests, which you can call from a developer checkout:

```sh
cd backend
uv run python -c "from pathlib import Path; import os; from migraai.core.audit import verify_chain; print(verify_chain(Path(os.environ['LOCALAPPDATA']) / 'MigraAI' / 'logs' / 'audit.jsonl'))"
```

It prints `(True, None)` for an intact chain, or `(False, <seq>)` with the first record that fails. The check recomputes each `hash` and checks that `seq` is consecutive and `prev_hash` matches the previous record. The chain shows tampering inside the file; it does not protect against deleting the whole file or its last lines.

### What is never logged

Neither the audit log nor the access log records source code or file contents, file names, the Devin token, cookies, launch tokens, request bodies or query strings. Uploads are logged as counts, sizes and SHA-256 digests. Settings changes list only the names of the changed fields.

## Data egress records

### Consent records

Before a conversion run the consent dialog "Send code to Devin?" names the number of objects, the Devin host, the number of voter sessions, the maximum sessions including escalation, and the per-session time limit. The user must tick "I understand that this code leaves this computer". Each consent is written to the audit log as `convert.consent` with `object_count`, `base_run_id` (for a re-conversion) and `devin_host`. Later records `convert.start`, `convert.batch`, `convert.escalation`, `convert.blocked_secret` and `convert.complete` carry the same run ID as `target`.

### Report accounting

The session report in each converted ZIP has a section **4. Data sent to Devin**. It reads "No data was sent to Devin." when nothing was sent; otherwise it lists:

- Consent given at
- Devin host
- Objects sent
- Batches
- Sessions
- Total ACUs
- Blocked batches (batches stopped because they contained secrets; a warning follows when this is above 0)

For a re-conversion it also states how many objects were re-converted and which results were carried over from the earlier run.

### Replay sessions

Sessions answered from a local replay (the test-only `MIGRAAI_TEST_REPLAY_DEVIN`) show the state "replayed, nothing sent" in the sessions table, and the report adds a **Run** column.

## Devin settings and cost

### Settings

| Setting | UI | Default | Range | Effect |
|---|---|---|---|---|
| `confidence_threshold` | Confidence threshold | 0.85 | 0.5–1.0 | Objects below it are escalated, and are sent to expert review if still below it |
| `max_concurrent_sessions` | Max concurrent sessions | 4 | 1–50 | Maximum Devin sessions running at once in a conversion run |
| `acu_cap_per_run` | ACU cap per run | none | > 0 | Divided across the maximum session count to set an integer per-session limit (minimum 1 ACU); the estimate may exceed the cap when it is below the session count |
| `conversion_batch_size` | API only | 20 | 1–50 | Objects per conversion batch |
| `conversion_session_timeout_minutes` | API only | 45 | 5–240 | A conversion session is stopped after this time |
| `plan_acu_cap` | API only | 10 | 1–100 | ACU limit sent with the planning session |
| `plan_timeout_minutes` | API only | 30 | 5–120 | A planning session is stopped after this time and falls back to a local draft |

`devin_pat`, `devin_org_id` and `devin_api_base_url` are described in [install-guide.md](install-guide.md). API-only settings are changed with `PUT /api/settings` from an authenticated browser session.

Each conversion batch uses three voter sessions; low-confidence objects add escalation sessions. The consent dialog shows both numbers before you send.

### ACUs

The Devin v1 API (legacy `apk_` keys) returns no ACU figure, so the report shows "Total ACUs: not reported by the Devin API" and `n/a` per session. When the API returns `acus_consumed` for a session, the report shows it and the total.

### Reading the sessions table

The table in section 4 of the report has one row per Devin session:

| Column | Meaning |
|---|---|
| Run | Run ID; shown only for re-conversions or replays |
| Role | Voter or escalation session |
| Batch | Batch number |
| State | `done`, `failed`, `blocked`, `timeout`, `cancelled`, or "replayed, nothing sent" |
| ACUs | ACUs used, or `n/a` |
| Link | Link to the session in Devin |

## Knowledge base

### Seed KB

The ZIP ships `app\kb\kb-v1.db`, built from the YAML seeds at build time and embedded with the bundled model. On first start it is copied to `%LOCALAPPDATA%\MigraAI\kb\` as version 1. A later ZIP with a different seed is added as a new version on start.

### One-click refresh

In **Settings**, the **Knowledge base** section shows the active version and offers **Refresh**, **Cancel** (while a refresh runs) and **Roll back** (to the previous kept version). A refresh:

- fetches the vendor documentation pages listed in the seeds, plus up to 40 linked pages per seed on the same site;
- stops at 3,000 pages; skips pages larger than 2 MB; times out each request after 20 seconds;
- respects `robots.txt`, sends one request per second per host, and fetches from up to four hosts in parallel;
- sends the User-Agent `MigraAI-KB-Refresh/0.2 (+local; contact: admin)`;
- builds a new KB version and makes it active only if it completes; the active version and the two before it are kept.

### KB refresh network

Refresh needs outbound HTTPS to these hosts. Nothing else in MigraAI uses them.

- `learn.microsoft.com`
- `docs.snowflake.com`
- `www.ibm.com`
- `docs.aws.amazon.com`
- `clickhouse.com`
- `docs.databricks.com`
- `docs.oracle.com`
- `docs.cloud.google.com`
- `cloud.google.com`
- `hive.apache.org`
- `docs.teradata.com`

Links followed from these pages stay on the same host.

### Embedding model

The model `BAAI/bge-small-en-v1.5` (384 dimensions, run with FastEmbed on ONNX Runtime) is bundled in `app\models`. `start.cmd` points `MIGRAAI_MODEL_DIR` at it, so no model is downloaded at run time.

## Logs and health

### Access log

`logs\access.log` has one JSON object per request:

```json
{"ts": "2026-10-16T09:14:03.120455+00:00", "method": "POST", "path": "/api/uploads", "status": 200, "duration_ms": 41.37, "session_id": "3f6c1a2e"}
```

`path` never includes the query string. `session_id` is the first eight characters of the browser session ID, or `null`.

### Health endpoint

`GET /api/health` needs no session and returns:

```json
{"status": "ready", "mode": "local", "version": "0.1.0", "devin_configured": false, "conversion_configured": false}
```

`devin_configured` is true when a token is saved. `conversion_configured` is true when the token type and Organization ID are valid for conversion.

## Configuration reference

Environment variables read by the app:

| Variable | Use |
|---|---|
| `MIGRAAI_DATA_DIR` | Data folder instead of `%LOCALAPPDATA%\MigraAI` |
| `LOCALAPPDATA` | Windows: base of the default data folder |
| `USERNAME` | Windows: the only account granted access when MigraAI restricts session-folder permissions with `icacls` |
| `MIGRAAI_MODEL_DIR` | Embedding model folder; set by `start.cmd` to `app\models` |
| `MIGRAAI_SEED_KB` | Seed KB file; set by `start.cmd` to `app\kb\kb-v1.db` |
| `MIGRAAI_SEED_DIR` | Seed YAML folder used when building a KB |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | Proxy for Devin and KB refresh requests (read by the HTTP library) |
| `SSL_CERT_FILE` | CA bundle for HTTPS, for TLS-inspecting proxies (read by the HTTP library) |

Development/testing only, never in production:

| Variable | Use |
|---|---|
| `MIGRAAI_DEV_TOKEN` | Fixed launch token instead of a random one |
| `MIGRAAI_EMBEDDER` set to `hash` | Fast test embedder; search quality is not meaningful |
| `MIGRAAI_TEST_MOCK_DEVIN` | `1` uses a local mock Devin; nothing is sent |
| `MIGRAAI_TEST_MOCK_DEVIN_DIFFERENT_CANDIDATE` | `1` makes each mock voter return a differently worded candidate |
| `MIGRAAI_TEST_MOCK_SESSION_DELAY_S` | Mock session duration in seconds |
| `MIGRAAI_TEST_MOCK_SESSION_RESPONSE_DELAY_S` | Mock API response delay in seconds |
| `MIGRAAI_TEST_REPLAY_DEVIN` | Folder of recorded sessions to replay instead of calling Devin |
| `MIGRAAI_TEST_REPLAY_THEN_LIVE` | `1` replays the first run, then calls Devin |

## Release and support

### Building a release from CI

Every push and pull request runs `.github/workflows/ci.yml`. A release build needs all seven checks green:

| Check | What it does |
|---|---|
| `backend (ubuntu-latest)` | Ruff lint and format check, pytest, KB seed validation |
| `backend (windows-latest)` | The same on Windows |
| `golden` | The full source-to-target conversion matrix |
| `frontend` | Lint, type check, unit tests and production build |
| `e2e` | Playwright browser tests against the backend with the mock Devin |
| `portable` | Builds the Windows ZIP, checks DLL dependencies, smoke-tests the unzipped app, and uploads the artifact |
| `sonarqube` | SonarQube scan; runs only when `SONAR_TOKEN` is configured |

### Portable artifact and SHA-256

The `portable` job uploads `MigraAI-portable-0.1.0-win64.zip` as the artifact `migraai-portable-windows`. CI does not write a checksum file. When you release a build, download the artifact, compute the SHA-256 of the inner ZIP with `Get-FileHash ... -Algorithm SHA256`, and publish that value with the build. The digest in the job log is for the artifact wrapper, not the inner ZIP.

### Third-party notices

The Microsoft Visual C++ runtime licence is in `app\third-party-notices\msvc-runtime-LICENSE.txt`, as referenced from `README.txt`. Licences of the Python packages are inside their folders under `app\site-packages`. The ZIP has no combined notices file.

## Known limits

- No restart persistence: uploads, inventory and conversion runs are lost when the app stops.
- Windows testing has been done only on Windows Server 2022 with an Administrator account, starting `start.cmd` without elevation. Windows 10/11 and standard-user accounts are not yet tested.
- If the cap is below the maximum session count, every session receives a 1 ACU limit and the estimated run maximum can exceed the cap.
- There is no built-in audit-chain verification command, and the logs are never rotated.
- Proxy auto-configuration (PAC) is not supported.
