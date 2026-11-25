"""Shared building blocks for the Haven backend: config, storage, auth tokens, hashing, signing, crypto.

Everything is standard library plus `cryptography` (Ed25519 signatures, Fernet encryption).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
GENESIS = "0" * 64


# ---------------------------------------------------------------- config


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")

DATA = Path(os.getenv("HAVEN_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "segments").mkdir(exist_ok=True)
(DATA / "snapshots").mkdir(exist_ok=True)

CROWD_DIR = Path(os.getenv("HAVEN_CROWD_DIR", PROJECT / "ai" / "crowd"))
FACE_DIR = Path(os.getenv("HAVEN_FACE_DIR", PROJECT / "ai" / "face"))
FRONTEND_DIR = Path(os.getenv("HAVEN_FRONTEND_DIR", PROJECT / "frontend" / "dist"))
SNAPSHOT_DIRS = [Path(p) for p in os.getenv("HAVEN_SNAPSHOT_DIRS", "").split(";") if p.strip()] or [
    CROWD_DIR / "sample_data",
    PROJECT / "runs",  # live pipeline output: crowd_risk.py ... --out runs/my_clip
    DATA / "snapshots",
]
MATCH_THRESHOLD = float(os.getenv("HAVEN_MATCH_THRESHOLD", "0.5"))
TOKEN_TTL_S = int(os.getenv("HAVEN_TOKEN_TTL_S", "3600"))
INGEST_KEY = os.getenv("HAVEN_INGEST_KEY", "")  # optional shared key for POST /events


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(n: int = 6) -> str:
    return secrets.token_hex(n)


# ---------------------------------------------------------------- canonical JSON (same rules as the crowd AI)


def canonical_json(v) -> str:
    """Keys sorted, no spaces, a float with no fractional part prints as an integer.
    Identical to ai/crowd/code/verify_events.py and frontend/src/lib/verifyEvents.mjs."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(canonical_json(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v[k]) for k in sorted(v)) + "}"
    raise TypeError(f"cannot hash {type(v).__name__}")


def event_digest(event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def js_json(v) -> str:
    """JSON.stringify for the values used in the Kavrix contract (strings, numbers): integral floats print as ints."""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(js_json(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def kavrix_seal_hash(e: dict) -> str:
    """Frontend contract: evidence_hash = SHA-256(prev_hash + JSON.stringify([id, type, lat, lng, risk, timestamp]))."""
    canon = js_json([e["id"], e["type"], e["lat"], e["lng"], e["risk"], e["timestamp"]])
    return hashlib.sha256((e["prev_hash"] + canon).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- keys: Ed25519 signing + Fernet encryption


def _load_or_create_signing_key() -> Ed25519PrivateKey:
    p = DATA / "signing_key.pem"
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    return key


SIGNING_KEY = _load_or_create_signing_key()
PUBLIC_KEY: Ed25519PublicKey = SIGNING_KEY.public_key()
PUBLIC_KEY_HEX = PUBLIC_KEY.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
KEY_ID = hashlib.sha256(bytes.fromhex(PUBLIC_KEY_HEX)).hexdigest()[:16]


def sign(message: str) -> str:
    return SIGNING_KEY.sign(message.encode("utf-8")).hex()


def verify_sig(message: str, sig_hex: str) -> bool:
    try:
        PUBLIC_KEY.verify(bytes.fromhex(sig_hex), message.encode("utf-8"))
        return True
    except Exception:
        return False


def _load_or_create_fernet() -> Fernet:
    p = DATA / "watchlist.key"
    if not p.exists():
        p.write_bytes(Fernet.generate_key())
    return Fernet(p.read_bytes())


FERNET = _load_or_create_fernet()

_TOKEN_SECRET_PATH = DATA / "token.secret"
if not _TOKEN_SECRET_PATH.exists():
    _TOKEN_SECRET_PATH.write_bytes(secrets.token_bytes(32))
TOKEN_SECRET = _TOKEN_SECRET_PATH.read_bytes()


# ---------------------------------------------------------------- passwords and tokens


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return dk.hex(), salt.hex()


def check_password(password: str, pw_hash: str, salt_hex: str) -> bool:
    dk, _ = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(dk, pw_hash)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(email: str, role: str) -> str:
    body = _b64(json.dumps({"sub": email, "role": role, "exp": int(time.time()) + TOKEN_TTL_S}).encode())
    mac = _b64(hmac.new(TOKEN_SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def read_token(token: str) -> dict | None:
    try:
        body, mac = token.split(".")
        good = _b64(hmac.new(TOKEN_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        claims = json.loads(_unb64(body))
        return claims if claims["exp"] > time.time() else None
    except Exception:
        return None


# ---------------------------------------------------------------- geo


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, pw_hash TEXT, salt TEXT, role TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, run_id TEXT, seq INTEGER, type TEXT, drone_id TEXT, risk REAL, level TEXT,
  timestamp TEXT, received_at TEXT, body TEXT, event_hash TEXT, hash_ok INTEGER, chain_ok INTEGER);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, drone_id TEXT, head TEXT, head_seq INTEGER, events INTEGER, alerts INTEGER,
  max_risk REAL, first_at TEXT, updated_at TEXT, head_sig TEXT, integrity TEXT);
CREATE TABLE IF NOT EXISTS watchlist (
  id TEXT PRIMARY KEY, label TEXT, category TEXT, legal_basis TEXT, consent_ref TEXT, expires_at TEXT,
  created_at TEXT, created_by TEXT, emb_enc BLOB, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, watchlist_id TEXT, confidence REAL, lat REAL, lng REAL, drone_id TEXT, segment_id TEXT,
  status TEXT, created_at TEXT, decided_by TEXT, decided_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY, drone_id TEXT, started_at TEXT, uploaded_at TEXT, filename TEXT, size INTEGER,
  sha256 TEXT, prev_hash TEXT, seg_hash TEXT, sig TEXT, uploaded_by TEXT);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, kind TEXT, description TEXT, lat REAL, lng REAL, created_at TEXT, created_by TEXT,
  drone_id TEXT, distance_m REAL, eta_s REAL, status TEXT, arrived_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, source TEXT, source_id TEXT, kind TEXT, title TEXT, message TEXT, level TEXT,
  lat REAL, lng REAL, snapshot TEXT, status TEXT, created_at TEXT, updated_at TEXT, updated_by TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS ops_events (
  n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, type TEXT, body TEXT, evidence_hash TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
  n INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT,
  prev_hash TEXT, hash TEXT);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args=()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    def audit(self, actor: str, action: str, target: str = "", detail: dict | None = None) -> dict:
        """Append to a hash-chained audit log: every privileged action is recorded and tamper-evident."""
        with self.lock:
            last = self.one("SELECT hash FROM audit ORDER BY n DESC LIMIT 1")
            prev = last["hash"] if last else GENESIS
            rec = {"ts": now_iso(), "actor": actor, "action": action, "target": target,
                   "detail": json.dumps(detail or {}, sort_keys=True), "prev_hash": prev}
            rec["hash"] = hashlib.sha256(canonical_json(rec).encode()).hexdigest()
            self.x("INSERT INTO audit (ts, actor, action, target, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                   (rec["ts"], actor, action, target, rec["detail"], prev, rec["hash"]))
            return rec


db = DB(DATA / "haven.db")


def seed_users() -> list[str]:
    """Create the commander / worker / officer logins from env vars (HAVEN .env or the face worker's .env)."""
    load_dotenv(FACE_DIR / ".env")
    created = []
    for role, prefix in (("commander", "COMMANDER"), ("worker", "WORKER"), ("officer", "OFFICER")):
        email = os.getenv(f"{prefix}_EMAIL", f"{role}@haven.test")
        pw = os.getenv(f"{prefix}_PASSWORD", "")
        if not pw:
            continue
        row = db.one("SELECT * FROM users WHERE email=?", (email,))
        if row and check_password(pw, row["pw_hash"], row["salt"]):
            continue
        h, s = hash_password(pw)
        db.x("INSERT OR REPLACE INTO users (email, pw_hash, salt, role, name) VALUES (?,?,?,?,?)",
             (email, h, s, role, role.title()))
        created.append(email)
    return created
