# Backend

FastAPI service for Haven. It is the only part that stores anything: events, the watchlist, reviews, officer tasks, evidence segments and the audit log live in one SQLite file (`backend/data/`, created on first start). It also serves the built website, so one process on **http://localhost:8000** is the whole system.

| File | What it does |
|---|---|
| `app.py` | The API and WebSockets: login and roles, crowd-event ingest, evidence verification, watchlist and human review, evidence segments, officer tasks, 112 dispatch, audit log, demo replays |
| `haven_core.py` | Storage, canonical-JSON hashing, Ed25519 signing, Fernet encryption of face templates, settings |
| `fleet.py` | The simulated drone fleet: nearest-drone dispatch and flight at 18 m/s |
| `smoke_test.py` | End-to-end test against a running backend (26 checks) |
| `requirements.txt` | Python dependencies (installed by `setup_haven.py`) |
| `.env.example` | The shape of `.env` (setup creates the real one with random test passwords) |

## Run it

Normally via the project root: `python3 start_haven.py`. To run only the backend:

```bash
cd backend
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000      # Windows: .venv\Scripts\python
```

Interactive API documentation: http://localhost:8000/docs. The endpoint table is in the [project README](../README.md#api).

## Roles

Three test accounts are created by `setup_haven.py` and stored in `.env` (passwords are random, printed once):

| Role | Can |
|---|---|
| `commander` | Everything: enrol or remove watchlist entries, replay demos, read the audit log, confirm or reject matches |
| `officer` | Read events, confirm or reject reviews, work officer tasks, raise 112 calls |
| `worker` | What the AI workers need: send face-match queries and evidence segments |

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `HAVEN_MATCH_THRESHOLD` | `0.5` | Cosine similarity needed for a watchlist match |
| `HAVEN_TOKEN_TTL_S` | `3600` | Login token lifetime |
| `HAVEN_INGEST_KEY` | empty | If set, `POST /events` and `/snapshots` need the `X-API-Key` header |
| `HAVEN_DATA` | `backend/data` | Where the database, signing key and uploads are stored |

## What the backend guarantees

* **Events unchanged.** A crowd event is stored exactly as received and returned exactly as stored. Its `event_hash` and its link to the previous event are checked on arrival, and the run's chain head is signed with Ed25519.
* **Nothing kept for non-matches.** `POST /watchlist/match` stores nothing when no face matches.
* **Face templates encrypted.** Watchlist embeddings are encrypted at rest and destroyed on removal.
* **A match is not an alert.** It becomes a pending review; only an officer's Confirm creates a task.
* **Audit.** Every privileged action goes into a hash-chained audit log (`GET /audit/verify`).

The signing key lives in `backend/data/`. That is acceptable for a demo; production would use a hardware security module.
