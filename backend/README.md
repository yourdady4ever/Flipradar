# FLIP RADAR backend

Python package `flipradar`: FastAPI REST API, SQLite database, data collectors, the deterministic
opportunity engine, a scheduler and the static web dashboard.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m flipradar init && python -m flipradar create-token --name laptop
python -m flipradar serve            # http://127.0.0.1:8000  (API docs: /api/docs)
python -m pytest                     # test suite (hermetic: no network access)
```

See the repository root for full documentation: `README.md`, `ARCHITECTURE.md`,
`API_DOCUMENTATION.md`, `SECURITY.md`, `CONTINUATION.md` and `docs/DATA_SOURCES.md`.
