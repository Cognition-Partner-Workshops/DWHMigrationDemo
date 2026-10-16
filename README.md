![MigraAI logo](docs/assets/logo.png)

# MigraAI

MigraAI is a local platform for planning, converting, and validating enterprise data-warehouse migrations on Windows. Its portable architecture combines a FastAPI backend, React frontend, SQLite-based knowledge base, and LangGraph orchestration. Devin API provides conversion voting and escalation; object code and context are sent to Devin cloud as described in the Phase-Gate 0 plan.

**Status:** Phases 1–7 complete (see the Phase-Gate 0 plan §12).

## Run locally (development)

Install Python 3.12, Node.js 20, and `uv`. From the repository root, install backend dependencies with `uv sync --project backend --group dev`, then install and build the frontend with `cd frontend && npm ci && npm run build`. Start the backend with `MIGRAAI_DEV_TOKEN=devtoken uv run --project backend --python 3.12 python -m migraai --no-browser --port 8765 --web-dir frontend/dist`, then open `http://127.0.0.1:8765/?launch=devtoken`. The development token is for local testing only.

## Supported platforms

- **Sources:** Teradata, Oracle, SQL Server, Snowflake, Synapse, Db2 Warehouse, Hive, Netezza
- **Targets:** Databricks, Redshift, Snowflake, Fabric, BigQuery, Synapse, ClickHouse

## Documentation

- [Phase-Gate 0 plan](docs/phase-gate-0-plan.md)
- [Architecture](docs/architecture.md)
- [Agents](docs/agents.md)
- [Security](docs/security.md)
- [Knowledge base design](docs/kb-design.md)
- [Database schema](docs/db-schema.md)
- [API](docs/api.md)
- [Test strategy](docs/test-strategy.md)
- [Installation guide](docs/install-guide.md)
- [User guide](docs/user-guide.md)
- [Operations guide](docs/operations-guide.md)
- [Report templates](docs/report-templates/README.md)
- [Diagrams](docs/diagrams/)
- [Original requirements](docs/requirements/MigraAI-Requirements.docx)

## Repository layout

- `docs/` — project plan, requirements, diagrams, and documentation
- `backend/` — FastAPI application and tests
- `frontend/` — React application and static build
- `testpacks/` — synthetic migration test packs
- `packaging/windows/` — portable Windows packaging builder
