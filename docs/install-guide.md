# Phase 1 development and portable run

## Development prerequisites

Use Python 3.12 and Node.js 20. The backend runs on `127.0.0.1:8765`; Vite proxies `/api` requests to it.

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

Open `http://127.0.0.1:5173/?launch=devtoken` for the Vite UI. `MIGRAAI_DEV_TOKEN` is a development/test convenience only.

## Build and run the portable ZIP

Build the static UI, then package the Windows x64 embedded Python runtime, backend wheel and Windows wheels:

```sh
cd frontend
npm ci
npm run build
cd ..
uv run --project backend --python 3.12 python packaging/windows/build_portable.py
```

The ZIP is created under `packaging/windows/out/`. On Windows, extract it and run `start.cmd` as a standard user. The app opens a browser at `127.0.0.1`; settings and session data are stored under `%LOCALAPPDATA%\MigraAI`. A Linux build verifies packaging only; final standard-user behavior must be checked on Windows.
