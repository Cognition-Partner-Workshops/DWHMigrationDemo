# MigraAI Installation Guide

MigraAI is a portable, local Windows app for planning, converting, validating and packaging data-warehouse migrations. It runs from an unzipped folder, serves its UI on `127.0.0.1`, and needs no installer.

## Requirements

| Item | Requirement |
|---|---|
| Operating system | Windows x64. The portable build has been tested only on Windows Server 2022, using an Administrator account and starting `start.cmd` without elevation. Windows 10/11 and a true standard-user account have not been tested yet. |
| Architecture | 64-bit (x64). The bundle contains the Windows x64 embedded Python 3.12 runtime. |
| Admin rights | Not required by design: nothing is installed or registered. Use the checklist below to confirm this on your own laptop. |
| Browser | Any current browser set as the Windows default. MigraAI opens it automatically; you can also paste the printed link into any browser on the same machine. |
| Disk space | About 150 MB for the ZIP (the CI build is about 145 MiB) plus the unzipped folder, which is larger. Leave extra room under `%LOCALAPPDATA%` for knowledge-base versions, logs and session files. |
| Network for conversion and planning | Outbound HTTPS to your Devin API domain (the API base URL in Settings, default `https://api.devin.ai`). |
| Network for KB refresh only | Outbound HTTPS to the vendor documentation sites listed in [operations-guide.md](operations-guide.md). |

MigraAI never connects to a source or target warehouse. Without a Devin token it still runs; conversion is disabled and planning creates a local draft.

The embedding model and the seed knowledge base are inside the ZIP, so normal use does not download a model.

## Get the portable ZIP

The ZIP is built by the `portable` job in CI and published as the workflow artifact `migraai-portable-windows`.

1. Open the repository's **Actions** tab, select a successful **CI** run, and download the `migraai-portable-windows` artifact. With the GitHub CLI:

   ```sh
   gh run download <run-id> -n migraai-portable-windows
   ```

2. The artifact contains one file, `MigraAI-portable-0.1.0-win64.zip`. If your browser saves the artifact as `migraai-portable-windows.zip`, extract that first.
3. Check the SHA-256 of `MigraAI-portable-0.1.0-win64.zip` in PowerShell:

   ```powershell
   Get-FileHash .\MigraAI-portable-0.1.0-win64.zip -Algorithm SHA256
   ```

4. Compare the hash with the value recorded by the person who released the build. CI does not publish a separate checksum file. The `portable` job log prints `SHA256 digest of uploaded artifact zip is ...`, but that digest is for the artifact wrapper ZIP, not for the inner `MigraAI-portable-0.1.0-win64.zip`.

## Install and first run

1. Extract `MigraAI-portable-0.1.0-win64.zip` to a folder you own, for example under your user profile. It creates a `MigraAI` folder. Do not run it from inside the ZIP.
2. Double-click `start.cmd` (do not use "Run as administrator"). A console window opens and prints:

   ```text
   MigraAI running at http://127.0.0.1:<port>/?launch=<token>
   ```

   The port is a free port picked at random on each start. The token is a new random value on each start.
3. When the server is ready, your default browser opens that link. The launch token is single-use: the browser exchanges it for a session cookie and the page reloads at `/`. Opening the same link again shows "The launch link is invalid or has already been used."
4. Keep the console window open while you work. Closing it, or pressing Ctrl+C in it, stops MigraAI and wipes the session files.

### What you see on the first run

- The top bar shows **Local Mode** with "Your data stays on this machine" (no Devin token is saved yet) and a **Settings** button.
- The **System Selection** section with **Source Database**, **Target Database** and **Confirm**.
- Sections **2 Migration Planning**, **Resources Confirmation** and **Convert Objects** become usable after you confirm a source and target.
- On the first start MigraAI creates `%LOCALAPPDATA%\MigraAI` and copies the bundled seed knowledge base into it as version 1. This takes a few seconds.

### Enter the Devin settings

Open **Settings**. The dialog has these fields:

| Field in Settings | API field (`PUT /api/settings`) | Notes |
|---|---|---|
| Devin personal access token | `devin_pat` | Write-only. After saving, the dialog shows only the last four characters of the saved token. Tick **Clear saved token** to remove it. |
| Organization ID | `devin_org_id` | Required for `cog_` tokens and must start with `org-`. "Not needed for legacy `apk_` keys". |
| API base URL | `devin_api_base_url` | Default `https://api.devin.ai`. Use your enterprise API domain if it is different. |
| Confidence threshold | `confidence_threshold` | 0.5 to 1.0, default 0.85. |
| Max concurrent sessions | `max_concurrent_sessions` | 1 to 50, default 4. |
| ACU cap per run | `acu_cap_per_run` | Empty means "No cap". See the operations guide: conversion sessions are currently started without an ACU limit. |

Select **Save settings**. Token types:

- `cog_...` (Devin v3 API): needs an Organization ID.
- `apk_...` (legacy Devin v1 API): no Organization ID needed.

Any other prefix is saved but not treated as configured, so conversion stays disabled. After saving a token, the top bar shows "Conversions are sent to Devin cloud".

The same dialog has a **Knowledge base** section with **Refresh** and **Roll back**; see [operations-guide.md](operations-guide.md).

## Folder layout and data location

The portable folder is built by `packaging/windows/build_portable.py`:

```text
MigraAI\
  start.cmd                  sets MIGRAAI_MODEL_DIR and MIGRAAI_SEED_KB, then runs python -m migraai
  README.txt
  python\                    Windows x64 embedded Python 3.12 (plus the Visual C++ runtime DLLs)
  app\
    site-packages\           MigraAI and all dependencies (Windows wheels)
    models\                  embedding model BAAI/bge-small-en-v1.5 (FastEmbed cache)
    kb\kb-v1.db              seed knowledge base
    third-party-notices\     msvc-runtime-LICENSE.txt
  web\                       built React UI
  docs\                      guide PDFs and Report-Templates.pdf, diagrams\ (PNG), report-samples\
```

All data written at run time goes under `%LOCALAPPDATA%\MigraAI`:

| Path | Contents |
|---|---|
| `app.db` | Settings (the token is DPAPI-protected) and saved migration plans |
| `kb\kb-v<N>.db`, `kb\state.json` | Knowledge-base versions and which one is active |
| `logs\audit.jsonl` | Hash-chained audit log |
| `logs\access.log` | HTTP access log |
| `sessions\<uuid>\` | Encrypted files for the current session; wiped on shutdown |

The unzipped `MigraAI` folder is only read, not written to.

## Upgrade and uninstall

Nothing is registered with Windows: no installer, services, scheduled tasks, Run keys, firewall rules or Start-menu entries.

**Upgrade**

1. Close the MigraAI console window.
2. Delete the old `MigraAI` folder and extract the new ZIP.
3. Run `start.cmd`. Settings, saved plans, logs and KB versions in `%LOCALAPPDATA%\MigraAI` are kept. If the new ZIP has a different seed KB, MigraAI adds it as a new KB version on start, and you can still roll back to the earlier version.

**Uninstall**

1. Close the MigraAI console window.
2. Delete the `MigraAI` folder.
3. Delete the data folder:

   ```powershell
   Remove-Item -Recurse -Force "$env:LOCALAPPDATA\MigraAI"
   ```

## Standard-user checklist

Run this on the customer laptop with a standard (non-admin) Windows account. It has not been run on a standard-user account yet, so record the results.

1. **No UAC prompt.** Double-click `start.cmd`. No User Account Control prompt appears and the browser opens MigraAI.
2. **No services, scheduled tasks, Run keys or firewall rules.** In PowerShell, capture a baseline before the first start and compare it after you stop MigraAI:

   ```powershell
   $out = "$env:USERPROFILE\migraai-check"
   New-Item -ItemType Directory -Force $out | Out-Null
   Get-Service | Select-Object Name, Status | Export-Csv "$out\services-before.csv" -NoTypeInformation
   Get-ScheduledTask | Select-Object TaskPath, TaskName | Export-Csv "$out\tasks-before.csv" -NoTypeInformation
   Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run, HKLM:\Software\Microsoft\Windows\CurrentVersion\Run -ErrorAction SilentlyContinue | Out-File "$out\run-before.txt"
   Get-NetFirewallRule | Select-Object Name, DisplayName | Export-Csv "$out\firewall-before.csv" -NoTypeInformation
   ```

   Repeat with `-after` file names, then compare, for example:

   ```powershell
   Compare-Object (Import-Csv "$out\services-before.csv" | ForEach-Object Name) (Import-Csv "$out\services-after.csv" | ForEach-Object Name)
   Compare-Object (Import-Csv "$out\tasks-before.csv" | ForEach-Object TaskName) (Import-Csv "$out\tasks-after.csv" | ForEach-Object TaskName)
   Compare-Object (Get-Content "$out\run-before.txt") (Get-Content "$out\run-after.txt")
   Compare-Object (Import-Csv "$out\firewall-before.csv" | ForEach-Object Name) (Import-Csv "$out\firewall-after.csv" | ForEach-Object Name)
   ```

   Every comparison should print nothing. MigraAI listens on `127.0.0.1` only, so Windows Firewall should not ask to allow it.
3. **Data only under `%LOCALAPPDATA%`.** While MigraAI runs, check where it writes:

   ```powershell
   Get-ChildItem "$env:LOCALAPPDATA\MigraAI" -Recurse | Select-Object FullName
   Get-ChildItem <path-to>\MigraAI -Recurse | Where-Object LastWriteTime -gt (Get-Date).AddHours(-1)
   ```

   The first command lists `app.db`, `kb`, `logs` and `sessions`. The second should list nothing new inside the portable folder.
4. **Confirm works.** Choose **Teradata** as **Source Database** and **Snowflake** as **Target Database**, then select **Confirm**.
5. **Mock conversion and Download work without Devin.** Close MigraAI, then start it from a Command Prompt in the `MigraAI` folder with the test-only mock Devin enabled:

   ```bat
   set MIGRAAI_TEST_MOCK_DEVIN=1
   start.cmd
   ```

   Confirm Teradata to Snowflake, upload `tables.sql` and `views.sql` from `testpacks\teradata` with **Browse files** and **Upload Files**, select **Convert Items**, tick "I understand that this code leaves this computer" and select **Send to Devin**. The consent dialog names the host as `local mock`; nothing leaves the machine. When the run is done, select **Download Converted Files** and check that `<name>-converted.zip` downloads. Close the console afterwards: `MIGRAAI_TEST_MOCK_DEVIN` is for testing only and must never be set in production.
6. **Wipe on close.** After closing the console window, `%LOCALAPPDATA%\MigraAI\sessions` is empty.

## Troubleshooting

### The browser does not open

MigraAI asks Windows to open the default browser once the server is ready. If nothing opens (no default browser set, or a policy blocks it), copy the `MigraAI running at ...` link from the console into a browser on the same machine. Use the link only once.

If the page says "Open MigraAI using the launch link printed by the local server." or "The launch link is invalid or has already been used.", the token was already used, for example by the tab that opened automatically. Close the console window and run `start.cmd` again to get a new link. Each start accepts one launch.

### The port is in use

`start.cmd` lets the app pick a free port on each start, so a port conflict does not happen with the default launch. A conflict can only occur if you start MigraAI yourself with a fixed `--port`; then the console shows a bind error and exits. Pick another port, or omit `--port`. Only `127.0.0.1` and `localhost` are accepted for `--host`.

### A DLL load error

The embedding model runs on ONNX Runtime, which needs the Microsoft Visual C++ runtime DLLs (`msvcp140.dll`, `msvcp140_1.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`). The ZIP bundles them in `python\` and `app\site-packages\onnxruntime\capi\`, so no system-wide Visual C++ install is needed. A DLL load error means these files are missing from the unzipped folder, typically because extraction was incomplete or endpoint protection removed files. Check that the four DLLs exist in both folders. If they are missing, extract the ZIP again to a new folder and ask your security team to allow the folder. CI checks the bundle's DLL dependencies on every build (`packaging/windows/check_dll_deps.py`).

### A proxy blocks Devin

Calls to Devin go straight from the MigraAI process to the API base URL. Symptoms when a proxy or firewall blocks them: Devin sessions fail to start, or planning falls back to a local draft.

- MigraAI uses the proxy from the `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` environment variables, or a fixed proxy from the Windows Internet settings. It does not support PAC files or proxy auto-detection. To set a proxy for one run, open a Command Prompt in the `MigraAI` folder, `set HTTPS_PROXY=http://<proxy-host>:<port>`, then run `start.cmd`.
- If your proxy inspects TLS, MigraAI does not use the Windows certificate store; it uses its own bundled CA list. Set `SSL_CERT_FILE` to a PEM file with your corporate root CA before running `start.cmd`.
- Ask your network team to allow outbound HTTPS to the Devin API domain.

## Developer setup

Use Python 3.12, uv and Node.js 20.

```sh
uv sync --project backend --group dev
cd frontend
npm ci
npm run dev
```

In another terminal, from the repository root:

```sh
MIGRAAI_DEV_TOKEN=devtoken uv run --project backend --python 3.12 python -m migraai --no-browser --port 8765
```

Open `http://127.0.0.1:5173/?launch=devtoken` for the Vite UI. Vite proxies `/api` to `127.0.0.1:8765`. To serve the built UI from the backend instead, run `npm run build` in `frontend` and add `--web-dir frontend/dist`, then open `http://127.0.0.1:8765/?launch=devtoken`.

`MIGRAAI_DEV_TOKEN` fixes the launch token. It is for development and tests only and must never be set in production.

### Run the tests

```sh
cd backend
uv run ruff check migraai tests
uv run ruff format --check migraai tests
uv run pytest -q
uv run python -m migraai.kb.validate --allow-incomplete
cd ../frontend
npm run lint
npm run typecheck
npm test
npx playwright install chromium
npm run build
npm run e2e
```

`npm run e2e` starts its own backend, serving `frontend/dist`, with the mock Devin. The full 56-pair golden matrix runs in CI's `golden` job; see `.github/workflows/ci.yml`.

### Build the portable ZIP

```sh
cd frontend
npm ci
npm run build
cd ..
uv run --project backend --python 3.12 python packaging/windows/build_portable.py
```

The build downloads the Windows embedded Python and the Visual C++ runtime wheel (checked against a pinned SHA-256), installs the Windows wheels, builds the seed KB with the embedding model, and writes `packaging/windows/out/MigraAI-portable-0.1.0-win64.zip`. A build on Linux checks the packaging only; Windows behaviour must be checked on Windows.
