"""Haven backend: API, live event stream, signed evidence, watchlist, 112 dispatch, officer alert loop.

Run:  .venv/Scripts/python -m uvicorn app:app --port 8000      (from backend/)
Then open http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, \
    WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import haven_core as hc
from haven_core import db
from fleet import Fleet

# ================================================================ live hub (WebSocket fan-out)


class Hub:
    """Two kinds of subscriber: /ws gets {kind, data} envelopes; /ws/events (the Kavrix frontend) gets one flat
    contract event per message: crowd events unchanged, plus sealed dispatch / watchlist_match events."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.flat: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def _send(self, group: set, text: str) -> None:
        for ws in list(group):
            try:
                await ws.send_text(text)
            except Exception:
                group.discard(ws)

    async def send_all(self, msg: dict) -> None:
        await self._send(self.clients, json.dumps(msg))
        flat = msg["data"]["event"] if msg["kind"] == "event" else msg["data"] if msg["kind"] == "ops" else None
        if flat is not None:
            await self._send(self.flat, json.dumps(flat))

    def publish(self, kind: str, data) -> None:
        """Thread-safe: callable from request handlers and background threads."""
        if self.loop is None:
            return
        msg = {"kind": kind, "data": data}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self.send_all(msg))
        else:
            asyncio.run_coroutine_threadsafe(self.send_all(msg), self.loop)


hub = Hub()
fleet = Fleet(hub)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    created = hc.seed_users()
    if created:
        print(f"[haven] users ready: {', '.join(created)}")
    if not db.one("SELECT email FROM users LIMIT 1"):
        print("[haven] WARNING: no users. Set COMMANDER_PASSWORD / WORKER_PASSWORD / OFFICER_PASSWORD in backend/.env")
    task = asyncio.create_task(fleet.run())
    print(f"[haven] signing key id {hc.KEY_ID}; data in {hc.DATA}")
    yield
    task.cancel()


app = FastAPI(title="Haven API", version="1.0", lifespan=lifespan)
# Only pages served from this machine (the dashboard itself, or Vite's dev server) may call the API from a browser.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
                   allow_methods=["*"], allow_headers=["*"])


# ================================================================ auth


def _claims_from(authorization: str | None, token_q: str | None) -> dict | None:
    tok = None
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:]
    elif token_q:
        tok = token_q
    return hc.read_token(tok) if tok else None


def need(*roles: str):
    def dep(authorization: str | None = Header(None), token: str | None = Query(None)) -> dict:
        c = _claims_from(authorization, token)
        if not c:
            raise HTTPException(401, "login required")
        if roles and c["role"] not in roles:
            raise HTTPException(403, f"needs role {' or '.join(roles)}")
        return c
    return Depends(dep)


ANY = need()
COMMAND = need("commander")
STAFF = need("commander", "officer")
WORKER = need("worker", "commander")


def _is_local(request: Request) -> bool:
    return request.client is not None and request.client.host in ("127.0.0.1", "::1", "localhost", "testclient")


class Login(BaseModel):
    email: str
    password: str


@app.post("/auth/login")
def login(body: Login):
    u = db.one("SELECT * FROM users WHERE email=?", (body.email,))
    if not u or not hc.check_password(body.password, u["pw_hash"], u["salt"]):
        raise HTTPException(401, "wrong email or password")
    db.audit(body.email, "login")
    return {"access_token": hc.make_token(u["email"], u["role"]), "token_type": "bearer", "role": u["role"],
            "email": u["email"], "expires_in": hc.TOKEN_TTL_S}


@app.get("/auth/me")
def me(c=ANY):
    return {"email": c["sub"], "role": c["role"]}


@app.get("/health")
def health():
    return {"ok": True, "time": hc.now_iso(), "key_id": hc.KEY_ID, "clients": len(hub.clients),
            "events": db.one("SELECT COUNT(*) n FROM events")["n"]}


# ================================================================ ops events (frontend contract chain)


def emit_ops(eid: str, type_: str, lat: float, lng: float, risk: float, **extra) -> dict:
    """Seal a dispatch / watchlist_match event into the ops hash chain (Kavrix contract) and push it live."""
    with db.lock:
        last = db.one("SELECT evidence_hash FROM ops_events ORDER BY n DESC LIMIT 1")
        e = {"id": eid, "type": type_, "lat": float(lat), "lng": float(lng), "risk": int(round(risk)),
             "timestamp": hc.now_iso(), "prev_hash": last["evidence_hash"] if last else hc.GENESIS}
        e["evidence_hash"] = hc.kavrix_seal_hash(e)
        e.update(extra)
        db.x("INSERT INTO ops_events (id, type, body, evidence_hash, created_at) VALUES (?,?,?,?,?)",
             (eid, type_, json.dumps(e), e["evidence_hash"], e["timestamp"]))
    hub.publish("ops", e)
    return e


# ================================================================ crowd events (crowd AI contract)

CROWD_TYPES = {"crowd_risk", "crowd_alert"}


def _run_head_message(run_id: str, seq: int, head: str) -> str:
    return f"haven-run-head|v1|{run_id}|{seq}|{head}"


def ingest_event(ev: dict, source: str = "api") -> dict:
    """Store one crowd event unchanged, check its hash and its link to the previous event, sign the new run head,
    raise an officer task for crowd_alert, and push it to every dashboard."""
    if not isinstance(ev, dict) or not isinstance(ev.get("id"), str) or ev.get("type") not in CROWD_TYPES:
        raise HTTPException(422, "event needs a string id and type crowd_risk or crowd_alert")
    for k in ("lat", "lng", "risk"):
        if not isinstance(ev.get(k), (int, float)) or isinstance(ev.get(k), bool):
            raise HTTPException(422, f"{k} must be a number")
    if db.one("SELECT id FROM events WHERE id=?", (ev["id"],)):
        return {"accepted": True, "duplicate": True, "id": ev["id"]}  # retries are normal: answer 200 again

    run_id = str(ev.get("run_id") or "default")
    seq = int(ev.get("seq", 0)) if isinstance(ev.get("seq"), int) else 0
    try:
        hash_ok = "event_hash" in ev and hc.event_digest(ev) == ev["event_hash"]
    except TypeError:
        hash_ok = False
    prev = db.one("SELECT event_hash FROM events WHERE run_id=? AND seq=?", (run_id, seq - 1)) if seq > 0 else None
    if seq == 0:
        chain_ok = ev.get("prev_hash") == hc.GENESIS
    else:
        chain_ok = bool(prev) and ev.get("prev_hash") == prev["event_hash"]  # False also flags a gap
    db.x("INSERT INTO events (id, run_id, seq, type, drone_id, risk, level, timestamp, received_at, body, event_hash,"
         " hash_ok, chain_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (ev["id"], run_id, seq, ev["type"], ev.get("drone_id"), float(ev["risk"]), ev.get("level"),
          ev.get("timestamp"), hc.now_iso(), json.dumps(ev, ensure_ascii=False), ev.get("event_hash"),
          int(hash_ok), int(chain_ok)))

    # signed run head: the backend vouches for the latest hash it has seen for this run
    run = db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    stats = db.one("SELECT COUNT(*) n, SUM(type='crowd_alert') a, MAX(risk) m, MIN(hash_ok) h, MIN(chain_ok) c "
                   "FROM events WHERE run_id=?", (run_id,))
    integrity = "ok" if stats["h"] and stats["c"] else "problem"
    if not run or seq >= (run["head_seq"] if run["head_seq"] is not None else -1):
        head, head_seq = ev.get("event_hash"), seq
    else:
        head, head_seq = run["head"], run["head_seq"]
    sig = hc.sign(_run_head_message(run_id, head_seq, head or ""))
    db.x("INSERT INTO runs (run_id, drone_id, head, head_seq, events, alerts, max_risk, first_at, updated_at, head_sig,"
         " integrity) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET head=excluded.head,"
         " head_seq=excluded.head_seq, events=excluded.events, alerts=excluded.alerts, max_risk=excluded.max_risk,"
         " updated_at=excluded.updated_at, head_sig=excluded.head_sig, integrity=excluded.integrity",
         (run_id, ev.get("drone_id"), head, head_seq, stats["n"], stats["a"] or 0, stats["m"], hc.now_iso(),
          hc.now_iso(), sig, integrity))

    if ev["type"] == "crowd_alert":
        create_task("crowd", ev["id"], ev.get("alert_kind", "crowd_alert"),
                    "Crowd-crush risk" if ev.get("alert_kind") != "surge" else "Crowd surge",
                    ev.get("message", "Crowd alert"), ev.get("level", "HIGH"), ev["lat"], ev["lng"],
                    f"/snapshot/{ev['id']}")
        fleet.note_crowd(ev)
    else:
        fleet.note_crowd(ev)
    hub.publish("event", {"event": ev, "integrity": {"hash_ok": hash_ok, "chain_ok": chain_ok}, "source": source})
    return {"accepted": True, "id": ev["id"], "hash_ok": hash_ok, "chain_ok": chain_ok}


@app.post("/events", status_code=201)
async def post_event(request: Request, x_api_key: str | None = Header(None)):
    if hc.INGEST_KEY and x_api_key != hc.INGEST_KEY:
        raise HTTPException(401, "bad ingest key")
    try:
        ev = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    res = ingest_event(ev)
    return JSONResponse(res, status_code=200 if res.get("duplicate") else 201)


@app.get("/events")
def list_events(request: Request, run_id: str | None = None, type: str | None = None, limit: int = 500,
                authorization: str | None = Header(None), token: str | None = Query(None)):
    """Events exactly as received (unchanged), oldest first. Local tools (contract_test.py) may read without login."""
    if not _is_local(request) and not _claims_from(authorization, token):
        raise HTTPException(401, "login required")
    sql, args = "SELECT body FROM events WHERE 1=1", []
    if run_id:
        sql, args = sql + " AND run_id=?", args + [run_id]
    if type:
        sql, args = sql + " AND type=?", args + [type]
    sql += " ORDER BY received_at DESC, seq DESC LIMIT ?"
    rows = db.q(sql, args + [max(1, min(limit, 5000))])
    return [json.loads(r["body"]) for r in reversed(rows)]


def _dir_run_id(d: Path) -> str | None:
    """The run_id of a pipeline output folder: from summary.json (written at the end of a run) or, while a live
    run is still going, from the first line of events.jsonl (written and flushed per event)."""
    try:
        s = d / "summary.json"
        if s.exists():
            return json.loads(s.read_text(encoding="utf-8")).get("run_id")
        e = d / "events.jsonl"
        if e.exists():
            with open(e, encoding="utf-8") as f:
                return json.loads(f.readline()).get("run_id")
    except Exception:
        pass
    return None


def _snapshot_path(ev: dict) -> Path | None:
    rel = ev.get("heatmap")
    if not rel or ".." in rel:
        return None
    run_id = ev.get("run_id") or "default"
    up = hc.DATA / "snapshots" / run_id / rel
    if up.exists():
        return up
    for base in hc.SNAPSHOT_DIRS:
        if not base.exists():
            continue
        for d in [base, *[p for p in base.iterdir() if p.is_dir()]]:
            if _dir_run_id(d) == run_id and (d / rel).exists():
                return d / rel
    return None


def _get_event(event_id: str) -> dict:
    row = db.one("SELECT body FROM events WHERE id=?", (event_id,))
    if not row:
        raise HTTPException(404, "no such event")
    return json.loads(row["body"])


@app.get("/snapshot/{event_id}")
def snapshot(event_id: str, _c=ANY):
    p = _snapshot_path(_get_event(event_id))
    if not p:
        raise HTTPException(404, "snapshot file not available on the backend")
    return FileResponse(p, media_type="image/jpeg")


@app.post("/snapshots", status_code=201)
async def upload_snapshot(run_id: str = Form(...), path: str = Form(...), file: UploadFile = File(...),
                          x_api_key: str | None = Header(None)):
    """A producer can upload the snapshot images next to its events."""
    if hc.INGEST_KEY and x_api_key != hc.INGEST_KEY:
        raise HTTPException(401, "bad ingest key")
    if ".." in path or ".." in run_id or path.startswith(("/", "\\")):
        raise HTTPException(400, "bad path")
    dest = hc.DATA / "snapshots" / run_id / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"stored": True, "sha256": hc.sha256_file(dest)}


@app.get("/verify/{event_id}")
def verify_event(event_id: str, _c=ANY):
    """Re-checks one event: its own hash, its link to the previous event, its snapshot bytes, and the signed run head."""
    ops = db.one("SELECT n, body FROM ops_events WHERE id=?", (event_id,))
    if ops:
        e = json.loads(ops["body"])
        prev = db.one("SELECT evidence_hash FROM ops_events WHERE n<? ORDER BY n DESC LIMIT 1", (ops["n"],))
        h_ok = hc.kavrix_seal_hash(e) == e["evidence_hash"]
        l_ok = e["prev_hash"] == (prev["evidence_hash"] if prev else hc.GENESIS)
        return {"valid": h_ok and l_ok, "id": event_id, "checks": {"evidence_hash": h_ok, "chain_link": l_ok}}
    ev = _get_event(event_id)
    hash_ok = ev.get("event_hash") == hc.event_digest(ev)
    seq, run_id = ev.get("seq", 0), ev.get("run_id") or "default"
    if seq == 0:
        chain_ok = ev.get("prev_hash") == hc.GENESIS
    else:
        p = db.one("SELECT event_hash FROM events WHERE run_id=? AND seq=?", (run_id, seq - 1))
        chain_ok = bool(p) and p["event_hash"] == ev.get("prev_hash")
    sp = _snapshot_path(ev)
    snap_ok = (hc.sha256_file(sp) == ev.get("evidence_hash")) if sp else None
    run = db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    head_ok = bool(run) and hc.verify_sig(_run_head_message(run_id, run["head_seq"], run["head"] or ""),
                                          run["head_sig"])
    valid = hash_ok and chain_ok and snap_ok is not False and head_ok
    return {"valid": valid, "id": event_id, "checks": {"event_hash": hash_ok, "chain_link": chain_ok,
            "snapshot": snap_ok, "signed_head": head_ok},
            "run": {"run_id": run_id, "head": run and run["head"], "head_seq": run and run["head_seq"],
                    "signature": run and run["head_sig"], "key_id": hc.KEY_ID}}


@app.get("/runs")
def runs(_c=ANY):
    return db.q("SELECT * FROM runs ORDER BY updated_at DESC")


@app.get("/runs/{run_id}/verify")
def verify_run(run_id: str, expect_head: str | None = None, _c=ANY):
    """Walks the whole chain of a run server-side and checks the backend's signature on its head."""
    rows = db.q("SELECT body FROM events WHERE run_id=? ORDER BY seq", (run_id,))
    if not rows:
        raise HTTPException(404, "no such run")
    problems, prev, snaps = [], hc.GENESIS, 0
    for i, r in enumerate(rows):
        ev = json.loads(r["body"])
        if ev.get("seq") != i:
            problems.append({"seq": ev.get("seq"), "kind": "gap", "message": f"expected seq {i}, got {ev.get('seq')}"})
        if hc.event_digest(ev) != ev.get("event_hash"):
            problems.append({"seq": ev.get("seq"), "kind": "edited", "message": "event content does not match its hash"})
        if ev.get("prev_hash") != prev:
            problems.append({"seq": ev.get("seq"), "kind": "chain", "message": "prev_hash does not link to the event before"})
        sp = _snapshot_path(ev)
        if sp:
            snaps += 1
            if hc.sha256_file(sp) != ev.get("evidence_hash"):
                problems.append({"seq": ev.get("seq"), "kind": "snapshot", "message": "snapshot image was modified"})
        prev = ev.get("event_hash")
    run = db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    sig_ok = hc.verify_sig(_run_head_message(run_id, run["head_seq"], run["head"] or ""), run["head_sig"])
    if not sig_ok:
        problems.append({"seq": None, "kind": "signature", "message": "signed head does not verify"})
    if run["head"] != prev:
        problems.append({"seq": None, "kind": "head", "message": "stored chain does not end at the signed head"})
    if expect_head and expect_head != prev:
        problems.append({"seq": None, "kind": "expected_head", "message": "chain head differs from the trusted head"})
    return {"ok": not problems, "events": len(rows), "snapshots_checked": snaps, "head": prev,
            "signature": run["head_sig"], "key_id": hc.KEY_ID, "public_key": hc.PUBLIC_KEY_HEX, "problems": problems}


@app.get("/pubkey")
def pubkey():
    return {"alg": "Ed25519", "public_key_hex": hc.PUBLIC_KEY_HEX, "key_id": hc.KEY_ID,
            "run_head_message": "haven-run-head|v1|{run_id}|{seq}|{head}",
            "segment_message": "haven-segment|v1|{id}|{drone_id}|{started_at}|{sha256}|{prev_hash}"}


# ================================================================ watchlist + human review (face AI contract)


class WatchlistIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    category: str
    legal_basis: str = Field(min_length=3)
    consent_ref: str = Field(min_length=1)
    expires_at: str
    embedding: list[float] = Field(min_length=128, max_length=2048)


def _parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@app.post("/watchlist", status_code=201)
def add_watchlist(body: WatchlistIn, c=COMMAND):
    if body.category not in ("missing_person", "wanted_offender"):
        raise HTTPException(422, "category must be missing_person or wanted_offender")
    exp = _parse_dt(body.expires_at)
    if exp <= datetime.now(timezone.utc) or (exp - datetime.now(timezone.utc)).days > 90:
        raise HTTPException(422, "expires_at must be in the future and at most 90 days away")
    wid = "wl_" + hc.new_id(5)
    enc = hc.FERNET.encrypt(json.dumps(body.embedding).encode())  # encrypted at rest
    db.x("INSERT INTO watchlist (id, label, category, legal_basis, consent_ref, expires_at, created_at, created_by,"
         " emb_enc) VALUES (?,?,?,?,?,?,?,?,?)",
         (wid, body.label, body.category, body.legal_basis, body.consent_ref, exp.isoformat(), hc.now_iso(),
          c["sub"], enc))
    db.audit(c["sub"], "watchlist.add", wid, {"label": body.label, "category": body.category,
                                               "legal_basis": body.legal_basis, "consent_ref": body.consent_ref})
    hub.publish("watchlist", {"id": wid})
    return {"id": wid, "label": body.label, "category": body.category, "expires_at": exp.isoformat()}


@app.get("/watchlist")
def get_watchlist(_c=STAFF):
    return db.q("SELECT id, label, category, legal_basis, consent_ref, expires_at, created_at, created_by, active "
                "FROM watchlist ORDER BY created_at DESC")


@app.delete("/watchlist/{wid}")
def remove_watchlist(wid: str, c=COMMAND):
    db.x("UPDATE watchlist SET active=0, emb_enc=NULL WHERE id=?", (wid,))  # embedding destroyed
    db.audit(c["sub"], "watchlist.remove", wid)
    hub.publish("watchlist", {"id": wid})
    return {"removed": wid}


class MatchIn(BaseModel):
    embedding: list[float] = Field(min_length=128, max_length=2048)
    lat: float | None = None
    lng: float | None = None
    drone_id: str | None = None
    segment_id: str | None = None


MATCH_STATS = {"queries": 0, "matches": 0}


@app.post("/watchlist/match")
def match(body: MatchIn, c=WORKER):
    """Compares one embedding with the active, unexpired watchlist. No match -> nothing is stored at all."""
    MATCH_STATS["queries"] += 1
    best, best_s = None, -1.0
    now = datetime.now(timezone.utc)
    for w in db.q("SELECT id, label, category, expires_at, emb_enc FROM watchlist WHERE active=1"):
        if not w["emb_enc"] or _parse_dt(w["expires_at"]) <= now:
            continue
        s = hc.cosine(body.embedding, json.loads(hc.FERNET.decrypt(w["emb_enc"])))
        if s > best_s:
            best, best_s = w, s
    if not best or best_s < hc.MATCH_THRESHOLD:
        return {"match": False}
    MATCH_STATS["matches"] += 1
    conf = round(min(1.0, max(0.0, best_s)), 4)
    rid = _create_review(best["id"], conf, body.lat, body.lng, body.drone_id, body.segment_id, c["sub"])
    return {"match": True, "review_id": rid, "confidence": conf}


def _create_review(watchlist_id: str, conf: float, lat: float | None, lng: float | None, drone_id: str | None,
                   segment_id: str | None, actor: str, source: str = "live") -> str:
    """A match becomes a pending review for an officer, an audit entry and a sealed watchlist_match event.
    Used by the live matcher and by the recorded-run replay, so both take exactly the same path."""
    rid = "rv_" + hc.new_id(5)
    db.x("INSERT INTO reviews (id, watchlist_id, confidence, lat, lng, drone_id, segment_id, status, created_at)"
         " VALUES (?,?,?,?,?,?,?,?,?)", (rid, watchlist_id, conf, lat, lng, drone_id, segment_id, "pending", hc.now_iso()))
    db.audit(actor, "watchlist.match", rid, {"watchlist_id": watchlist_id, "confidence": conf, "drone_id": drone_id,
                                             "source": source})
    rv = _review(rid)
    hub.publish("review", rv)
    emit_ops(rid, "watchlist_match", lat if lat is not None else 17.385, lng if lng is not None else 78.4867,
             conf * 100, review_id=rid, label=rv["label"], category=rv["category"], drone_id=drone_id,
             status="pending")
    return rid


def _review(rid: str) -> dict | None:
    return db.one("SELECT r.*, w.label, w.category, w.legal_basis FROM reviews r LEFT JOIN watchlist w"
                  " ON w.id = r.watchlist_id WHERE r.id=?", (rid,))


@app.get("/reviews")
def reviews(status: str | None = None, _c=need("commander", "officer", "worker")):
    sql = ("SELECT r.*, w.label, w.category, w.legal_basis FROM reviews r LEFT JOIN watchlist w"
           " ON w.id = r.watchlist_id")
    args = ()
    if status:
        sql, args = sql + " WHERE r.status=?", (status,)
    return db.q(sql + " ORDER BY r.created_at DESC", args)


class Decision(BaseModel):
    decision: str  # confirm | reject
    note: str | None = None


@app.post("/reviews/{rid}")
def decide(rid: str, body: Decision, c=STAFF):
    """Human in the loop: a watchlist match only becomes an alert after an officer confirms it."""
    r = _review(rid)
    if not r:
        raise HTTPException(404, "no such review")
    if r["status"] != "pending":
        raise HTTPException(409, f"already {r['status']}")
    if body.decision not in ("confirm", "reject"):
        raise HTTPException(422, "decision must be confirm or reject")
    status = "confirmed" if body.decision == "confirm" else "rejected"
    db.x("UPDATE reviews SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
         (status, c["sub"], hc.now_iso(), body.note, rid))
    db.audit(c["sub"], f"review.{body.decision}", rid, {"note": body.note})
    r = _review(rid)
    if status == "confirmed":
        what = "Missing person located" if r["category"] == "missing_person" else "Wanted person sighted"
        create_task("watchlist", rid, "watchlist_match", what,
                    f"{r['label']} ({r['confidence']:.0%} match, confirmed by {c['sub']}). Drone {r['drone_id'] or '-'}.",
                    "HIGH", r["lat"], r["lng"], None)
    hub.publish("review", r)
    return r


# ================================================================ evidence segments (privacy-safe video)


@app.post("/segments", status_code=201)
async def upload_segment(drone_id: str = Form(...), started_at: str = Form(...), file: UploadFile = File(...),
                         c=WORKER):
    sid = "sg_" + hc.new_id(6)
    dest = hc.DATA / "segments" / f"{sid}.mp4"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    digest = hc.sha256_file(dest)
    with db.lock:
        last = db.one("SELECT seg_hash FROM segments WHERE drone_id=? ORDER BY uploaded_at DESC LIMIT 1", (drone_id,))
        prev = last["seg_hash"] if last else hc.GENESIS
        msg = f"haven-segment|v1|{sid}|{drone_id}|{started_at}|{digest}|{prev}"
        seg_hash = hc.hashlib.sha256(msg.encode()).hexdigest()
        sig = hc.sign(msg)
        db.x("INSERT INTO segments (id, drone_id, started_at, uploaded_at, filename, size, sha256, prev_hash, seg_hash,"
             " sig, uploaded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             (sid, drone_id, started_at, hc.now_iso(), file.filename, dest.stat().st_size, digest, prev, seg_hash,
              sig, c["sub"]))
    db.audit(c["sub"], "segment.upload", sid, {"sha256": digest, "drone_id": drone_id})
    threading.Thread(target=_viewing_copy, args=(dest,), daemon=True).start()
    seg = db.one("SELECT * FROM segments WHERE id=?", (sid,))
    hub.publish("segment", seg)
    return {"id": sid, "sha256": digest, "signature": sig, "key_id": hc.KEY_ID}


def _viewing_copy(src: Path) -> None:
    """Browsers cannot play OpenCV's mp4v. Make an H.264 copy for viewing; the signed original is never touched."""
    try:
        import subprocess
        import imageio_ffmpeg
        dst = src.with_suffix(".view.mp4")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264",
                        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dst)],
                       check=True, timeout=300)
    except Exception as e:  # viewing copy is a convenience; evidence stays valid without it
        print(f"[haven] viewing copy failed for {src.name}: {e}")


@app.get("/segments")
def segments(_c=ANY):
    return db.q("SELECT * FROM segments ORDER BY uploaded_at DESC LIMIT 200")


@app.get("/segments/{sid}/verify")
def verify_segment(sid: str, _c=ANY):
    s = db.one("SELECT * FROM segments WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "no such segment")
    p = hc.DATA / "segments" / f"{sid}.mp4"
    file_ok = p.exists() and hc.sha256_file(p) == s["sha256"]
    msg = f"haven-segment|v1|{sid}|{s['drone_id']}|{s['started_at']}|{s['sha256']}|{s['prev_hash']}"
    sig_ok = hc.verify_sig(msg, s["sig"])
    return {"valid": file_ok and sig_ok, "checks": {"file_sha256": file_ok, "signature": sig_ok}, "key_id": hc.KEY_ID}


@app.get("/segments/{sid}/video")
def segment_video(sid: str, _c=ANY):
    p = hc.DATA / "segments" / f"{sid}.mp4"
    if not p.exists():
        raise HTTPException(404)
    view = p.with_suffix(".view.mp4")
    if view.exists():  # H.264 viewing copy of the signed original
        return FileResponse(view, media_type="video/mp4", headers={"X-Haven-Copy": "viewing-copy"})
    return FileResponse(p, media_type="video/mp4")


# ================================================================ officer tasks (alert loop)


def create_task(source, source_id, kind, title, message, level, lat, lng, snapshot) -> dict:
    tid = "tk_" + hc.new_id(5)
    db.x("INSERT INTO tasks (id, source, source_id, kind, title, message, level, lat, lng, snapshot, status,"
         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (tid, source, source_id, kind, title, message, level, lat, lng, snapshot, "new", hc.now_iso(), hc.now_iso()))
    t = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    hub.publish("task", t)
    return t


@app.get("/tasks")
def tasks(status: str | None = None, _c=STAFF):
    if status:
        return db.q("SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC", (status,))
    return db.q("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200")


class TaskAction(BaseModel):
    action: str  # ack | resolve | reject
    note: str | None = None


@app.post("/tasks/{tid}")
def task_action(tid: str, body: TaskAction, c=STAFF):
    to = {"ack": "acknowledged", "resolve": "resolved", "reject": "rejected"}.get(body.action)
    if not to:
        raise HTTPException(422, "action must be ack, resolve or reject")
    if not db.one("SELECT id FROM tasks WHERE id=?", (tid,)):
        raise HTTPException(404)
    db.x("UPDATE tasks SET status=?, updated_at=?, updated_by=?, note=? WHERE id=?",
         (to, hc.now_iso(), c["sub"], body.note, tid))
    db.audit(c["sub"], f"task.{body.action}", tid, {"note": body.note})
    t = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    hub.publish("task", t)
    return t


# ================================================================ 112 call -> drone dispatch


class Call112(BaseModel):
    lat: float
    lng: float
    kind: str = "emergency"
    description: str = ""


@app.post("/dispatch/112", status_code=201)
def call_112(body: Call112, c=STAFF):
    inc = fleet.dispatch(body.lat, body.lng, body.kind, body.description, c["sub"])
    if not inc:
        raise HTTPException(409, "no drone available")
    db.audit(c["sub"], "dispatch.112", inc["id"], {"drone": inc["drone_id"], "lat": body.lat, "lng": body.lng})
    emit_ops(inc["id"], "dispatch", body.lat, body.lng, 60, drone_id=inc["drone_id"], kind=body.kind,
             description=body.description, eta_s=round(inc["eta_s"]), distance_m=round(inc["distance_m"]))
    return inc


@app.get("/drones")
def drones(_c=ANY):
    return fleet.snapshot()


@app.post("/drones/{drone_id}/recall")
def recall(drone_id: str, c=STAFF):
    if not fleet.recall(drone_id):
        raise HTTPException(404)
    db.audit(c["sub"], "drone.recall", drone_id)
    return {"recalled": drone_id}


@app.get("/incidents")
def incidents(_c=ANY):
    return db.q("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 100")


# ================================================================ audit, stats


@app.get("/audit")
def audit(limit: int = 200, _c=COMMAND):
    rows = db.q("SELECT * FROM audit ORDER BY n DESC LIMIT ?", (limit,))
    return rows


@app.get("/audit/verify")
def audit_verify(_c=COMMAND):
    prev, bad = hc.GENESIS, []
    for r in db.q("SELECT * FROM audit ORDER BY n"):
        rec = {k: r[k] for k in ("ts", "actor", "action", "target", "detail", "prev_hash")}
        if r["prev_hash"] != prev or hc.hashlib.sha256(hc.canonical_json(rec).encode()).hexdigest() != r["hash"]:
            bad.append(r["n"])
        prev = r["hash"]
    return {"ok": not bad, "entries": db.one("SELECT COUNT(*) n FROM audit")["n"], "bad": bad[:20], "head": prev}


@app.get("/stats")
def stats(_c=ANY):
    one = lambda sql: db.one(sql)["n"]  # noqa: E731
    return {
        "events": one("SELECT COUNT(*) n FROM events"),
        "alerts": one("SELECT COUNT(*) n FROM events WHERE type='crowd_alert'"),
        "runs": one("SELECT COUNT(*) n FROM runs"),
        "pending_reviews": one("SELECT COUNT(*) n FROM reviews WHERE status='pending'"),
        "open_tasks": one("SELECT COUNT(*) n FROM tasks WHERE status IN ('new','acknowledged')"),
        "segments": one("SELECT COUNT(*) n FROM segments"),
        "incidents": one("SELECT COUNT(*) n FROM incidents"),
        "watchlist": one("SELECT COUNT(*) n FROM watchlist WHERE active=1"),
        "match_queries": MATCH_STATS["queries"], "match_hits": MATCH_STATS["matches"],
        "key_id": hc.KEY_ID,
    }


# ================================================================ demo controls (replay recorded AI output)

REPLAYS: dict[str, asyncio.Task] = {}


def _recorded_runs() -> dict[str, Path]:
    out = {}
    base = hc.CROWD_DIR / "sample_data"
    if base.exists():
        for d in sorted(base.iterdir()):
            if (d / "events.jsonl").exists():
                out[d.name] = d
    for d in sorted((hc.PROJECT / "runs").glob("*")) if (hc.PROJECT / "runs").exists() else []:
        if (d / "events.jsonl").exists():
            out[d.name] = d
    return out


@app.get("/demo/recordings")
def recordings(_c=ANY):
    res = []
    for name, d in _recorded_runs().items():
        s = json.loads((d / "summary.json").read_text(encoding="utf-8")) if (d / "summary.json").exists() else {}
        res.append({"name": name, "run_id": s.get("run_id"), "events": s.get("chain_events"),
                    "alerts": s.get("alerts"), "max_risk": s.get("max_risk"), "duration_s": s.get("duration_s"),
                    "playing": name in REPLAYS and not REPLAYS[name].done()})
    return res


@app.post("/demo/replay/{name}")
async def replay(name: str, rate: float = 1.0, c=COMMAND):
    """Feeds a recorded run of the real crowd pipeline through the same ingest path as a live drone."""
    runs_ = _recorded_runs()
    if name not in runs_:
        raise HTTPException(404, "no such recording")
    if name in REPLAYS and not REPLAYS[name].done():
        REPLAYS[name].cancel()
    lines = [json.loads(l) for l in (runs_[name] / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    run_id = lines[0].get("run_id")
    db.x("DELETE FROM events WHERE run_id=?", (run_id,))  # fresh replay of the same recording
    db.x("DELETE FROM runs WHERE run_id=?", (run_id,))
    db.audit(c["sub"], "demo.replay", name, {"run_id": run_id, "events": len(lines)})
    hub.publish("replay", {"name": name, "run_id": run_id, "state": "start", "events": len(lines)})

    async def go():
        t0 = lines[0].get("video_t", 0) or 0
        start = asyncio.get_running_loop().time()
        for ev in lines:
            due = start + ((ev.get("video_t") or 0) - t0) / max(rate, 0.1)
            await asyncio.sleep(max(0.0, due - asyncio.get_running_loop().time()))
            ingest_event(ev, source=f"replay:{name}")
        hub.publish("replay", {"name": name, "run_id": run_id, "state": "done"})

    REPLAYS[name] = asyncio.create_task(go())
    return {"replaying": name, "run_id": run_id, "events": len(lines)}


@app.post("/demo/tamper/{run_id}")
def tamper(run_id: str, c=COMMAND):
    """Demo only: silently edits one stored event (risk lowered) to show the verifier catches it."""
    row = db.one("SELECT id, body FROM events WHERE run_id=? AND type='crowd_alert' ORDER BY seq LIMIT 1", (run_id,)) \
        or db.one("SELECT id, body FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,))
    if not row:
        raise HTTPException(404)
    ev = json.loads(row["body"])
    ev["risk"] = 12.5
    ev["level"] = "LOW"
    db.x("UPDATE events SET body=? WHERE id=?", (json.dumps(ev), row["id"]))
    db.audit(c["sub"], "demo.tamper", row["id"], {"run_id": run_id, "changed": "risk -> 12.5"})
    return {"tampered": row["id"], "seq": ev.get("seq")}


@app.post("/demo/reset")
def reset(c=COMMAND):
    for t in [*REPLAYS.values(), *FACE_REPLAYS.values()]:
        t.cancel()
    for tbl in ("events", "runs", "tasks", "incidents", "ops_events", "reviews"):
        db.x(f"DELETE FROM {tbl}")
    db.x("DELETE FROM watchlist WHERE emb_enc IS NULL AND label LIKE '% · recorded run'")
    fleet.reset()
    db.audit(c["sub"], "demo.reset")
    hub.publish("reset", {})
    return {"reset": True}


# ================================================================ recorded face runs (campus test)

FACE_RECORDED = hc.FACE_DIR / "recorded"
FACE_REPLAYS: dict[str, asyncio.Task] = {}


def _face_runs() -> dict[str, dict]:
    out = {}
    for d in sorted(FACE_RECORDED.glob("*")) if FACE_RECORDED.exists() else []:
        f = d / "results.json"
        if f.exists():
            try:
                out[d.name] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
    return out


@app.get("/demo/face-runs")
def face_runs(_c=ANY):
    return [{"name": n, "title": r.get("title"), "matches": len(r.get("video_matches", [])),
             "video": f"/face-recorded/{n}/{r.get('video')}", "playing": n in FACE_REPLAYS and not FACE_REPLAYS[n].done()}
            for n, r in _face_runs().items()]


def _recorded_watchlist_entry(res: dict, actor: str) -> str:
    """The recorded run has no face template (templates never leave the backend), so its watchlist entry holds no
    embedding: it can label and review recorded matches but can never match a live face."""
    e = res["enrolment"]
    label = f"{e['label']} · recorded run"
    row = db.one("SELECT id FROM watchlist WHERE label=? AND active=1", (label,))
    if row:
        return row["id"]
    wid = "wl_" + hc.new_id(5)
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    db.x("INSERT INTO watchlist (id, label, category, legal_basis, consent_ref, expires_at, created_at, created_by,"
         " emb_enc) VALUES (?,?,?,?,?,?,?,?,NULL)",
         (wid, label, e["category"], e["legal_basis"], e["consent_ref"], exp.isoformat(), hc.now_iso(), actor))
    db.audit(actor, "watchlist.add", wid, {"label": label, "category": e["category"], "recorded_run": True,
                                           "consent_ref": e["consent_ref"]})
    return wid


@app.post("/demo/face-replay/{name}")
async def face_replay(name: str, rate: float = 1.0, c=COMMAND):
    """Feeds the matches of a recorded face run through the same review path as a live worker: each becomes a pending
    review, a watchlist_match event and, after an officer confirms it, an officer task. Timed to the recording's video."""
    res = _face_runs().get(name)
    if not res:
        raise HTTPException(404, "no such recorded face run")
    if name in FACE_REPLAYS and not FACE_REPLAYS[name].done():
        FACE_REPLAYS[name].cancel()
    wid = _recorded_watchlist_entry(res, c["sub"])
    loc, drone = res["location"], res.get("drone_id")
    matches = res.get("video_matches", [])
    db.audit(c["sub"], "demo.face_replay", name, {"matches": len(matches)})
    hub.publish("replay", {"name": name, "state": "start", "events": len(matches), "kind": "face"})

    async def go():
        start = asyncio.get_running_loop().time()
        for m in matches:
            due = start + m["video_t"] / max(rate, 0.1)
            await asyncio.sleep(max(0.0, due - asyncio.get_running_loop().time()))
            _create_review(wid, round(m["confidence"], 4), loc["lat"], loc["lng"], drone, None, c["sub"],
                           source=f"recorded:{name}")
        hub.publish("replay", {"name": name, "state": "done", "kind": "face"})

    FACE_REPLAYS[name] = asyncio.create_task(go())
    return {"replaying": name, "matches": len(matches), "seconds": round(max([m["video_t"] for m in matches] or [0]) / max(rate, 0.1), 1)}


# ================================================================ WebSocket


@app.websocket("/ws")
async def ws(websocket: WebSocket, token: str | None = None):
    claims = hc.read_token(token) if token else None
    if not claims:
        # accept first: closing before accept becomes a bare HTTP 403, and the browser never sees 4401
        await websocket.accept()
        await websocket.close(code=4401, reason="login expired")
        return
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"kind": "hello", "data": {"role": claims["role"],
                                                                      "drones": fleet.snapshot()}}))
        while True:
            await websocket.receive_text()  # pings
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(websocket)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket, token: str | None = None):
    """Kavrix frontend stream: one contract event per message. Sends recent history first, then live events."""
    if not (hc.read_token(token) if token else None):
        # accept first: closing before accept becomes a bare HTTP 403, and the browser never sees 4401
        await websocket.accept()
        await websocket.close(code=4401, reason="login expired")
        return
    await websocket.accept()
    backlog = [(r["received_at"], json.loads(r["body"])) for r in
               db.q("SELECT received_at, body FROM events ORDER BY received_at DESC, seq DESC LIMIT 150")]
    backlog += [(r["created_at"], json.loads(r["body"])) for r in
                db.q("SELECT created_at, body FROM ops_events ORDER BY n DESC LIMIT 50")]
    backlog.sort(key=lambda t: (t[0], t[1].get("seq", 0)))
    for _, e in backlog:
        await websocket.send_text(json.dumps(e))
    hub.flat.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.flat.discard(websocket)


@app.get("/ops-events")
def ops_events(_c=ANY):
    return [json.loads(r["body"]) for r in db.q("SELECT body FROM ops_events ORDER BY n")]


# ================================================================ static assets + frontend


def _mount(path: str, directory: Path) -> None:
    if directory.exists():
        app.mount(path, StaticFiles(directory=directory, html=(path == "/")), name=path.strip("/") or "root")


_mount("/face-recorded", hc.FACE_DIR / "recorded")
_mount("/", hc.FRONTEND_DIR)
