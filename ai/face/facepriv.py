"""Haven face & privacy core.

Rule: a face is shown only if the backend says it matched the lawful watchlist. Every other face
(unknown, still being checked, no match, briefly lost by the detector) is pixelated before the
frame leaves this process. Embeddings live only for the length of one match request; nothing about
non-matches is written anywhere.
"""
import os
import threading
import time
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import httpx
import numpy as np
from dotenv import load_dotenv

load_dotenv(".env")
warnings.filterwarnings("ignore", category=FutureWarning, module="insightface")

MATCH_COLOR = (0, 0, 255)  # BGR red


# ---------- model ----------


def load_model(name: str = "buffalo_sc", det_size: int = 640, det_thresh: float = 0.3):
    """buffalo_sc = SCRFD-500M detector + MobileFaceNet ArcFace (512-d), ~15 MB, fast on CPU.
    det_thresh is deliberately low (default model uses 0.5): blur generously, match strictly.
    Half-visible faces at the frame edge score ~0.4-0.5 and must still be blurred."""
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name=name, allowed_modules=["detection", "recognition"], providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_thresh=det_thresh, det_size=(det_size, det_size))
    return app


# ---------- blur ----------


def pixelate(frame: np.ndarray, box, blocks: int = 8, pad: float = 0.25) -> None:
    """Irreversibly pixelate a padded face box in place (coarser than Gaussian blur, which can be partly undone)."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, y1 = max(0, int(x1 - bw * pad)), max(0, int(y1 - bh * pad))
    x2, y2 = min(w, int(x2 + bw * pad)), min(h, int(y2 + bh * pad))
    if x2 <= x1 or y2 <= y1:
        return
    small = cv2.resize(frame[y1:y2, x1:x2], (blocks, blocks), interpolation=cv2.INTER_AREA)
    frame[y1:y2, x1:x2] = cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)


def to_blur(faces, shape, thresh: float = 0.45, edge: float = 0.02) -> list:
    """Detections that are faces worth blurring: confident ones, plus weaker ones cut off by the left,
    right or top edge (half-visible faces score ~0.4). Other low scores (0.3-0.45) are mostly backs of
    heads, shirts and jeans, so blurring them hides nothing and covers the scene."""
    w = shape[1]
    m = edge * w
    return [f for f in faces
            if f.det_score >= thresh or f.bbox[0] < m or f.bbox[1] < m or f.bbox[2] > w - m]


# ---------- tracking ----------


@dataclass
class Track:
    id: int
    box: np.ndarray
    status: str = "unknown"  # unknown | querying | no_match | match
    missed: int = 0
    hits: int = 1
    queries: int = 0
    next_query_at: float = 0.0
    future: Future | None = None
    review_id: str | None = None
    confidence: float | None = None


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    """Greedy IoU tracker. A lost track keeps being blurred at its last box for `max_missed` frames,
    so a brief detector miss never exposes a face. Only tracks seen in 2+ frames coast: a one-frame
    blip would otherwise leave a stale block that drifts onto bodies as the camera pans."""

    def __init__(self, iou_min: float = 0.3, max_missed: int = 15):
        self.iou_min, self.max_missed = iou_min, max_missed
        self.tracks: list[Track] = []
        self._next = 1

    def update(self, boxes) -> list[tuple[Track, int | None]]:
        pairs = sorted(
            ((iou(t.box, b), ti, bi) for ti, t in enumerate(self.tracks) for bi, b in enumerate(boxes)),
            reverse=True,
        )
        used_t, used_b, out = set(), set(), []
        for score, ti, bi in pairs:
            if score < self.iou_min:
                break
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            t = self.tracks[ti]
            t.box, t.missed, t.hits = np.asarray(boxes[bi], dtype=float), 0, t.hits + 1
            out.append((t, bi))
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.missed += 1 if t.hits >= 2 else self.max_missed + 1
                out.append((t, None))
        for bi, b in enumerate(boxes):
            if bi not in used_b:
                t = Track(self._next, np.asarray(b, dtype=float))
                self._next += 1
                self.tracks.append(t)
                out.append((t, bi))
        self.tracks = [t for t in self.tracks if t.missed <= self.max_missed]
        return [(t, bi) for t, bi in out if t.missed <= self.max_missed]


# ---------- render ----------


def render(frame: np.ndarray, tracks: list[Track]) -> None:
    """Apply the privacy rule to one frame, in place."""
    k = max(1.0, frame.shape[1] / 1920)  # keep the match box and label readable on 4K frames
    for t in tracks:
        if t.status != "match":
            pixelate(frame, t.box)
        elif t.missed == 0:
            x1, y1, x2, y2 = t.box.astype(int)
            cv2.rectangle(frame, (x1, y1), (x2, y2), MATCH_COLOR, round(2 * k))
            label = f"WATCHLIST {t.confidence:.0%} - PENDING OFFICER REVIEW"
            cv2.putText(frame, label, (x1, max(round(15 * k), y1 - round(8 * k))), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k,
                        MATCH_COLOR, round(2 * k))


# ---------- backend ----------


class Backend:
    """Thin client for the Haven API. Re-logs in once on 401 (tokens last an hour)."""

    def __init__(self, base: str, email: str, password: str):
        self.http = httpx.Client(base_url=base, timeout=30)
        self._creds = {"email": email, "password": password}
        self._lock = threading.Lock()
        self._token = ""
        self._login()

    @classmethod
    def from_env(cls, prefix: str = "WORKER") -> "Backend":
        return cls(os.getenv("HAVEN_API", "http://localhost:8000"),
                   os.environ[f"{prefix}_EMAIL"], os.environ[f"{prefix}_PASSWORD"])

    def _login(self) -> None:
        r = self.http.post("/auth/login", json=self._creds)
        r.raise_for_status()
        self._token = r.json()["access_token"]

    def _req(self, method: str, url: str, **kw) -> dict:
        for attempt in range(2):
            r = self.http.request(method, url, headers={"Authorization": f"Bearer {self._token}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                with self._lock:
                    self._login()
                continue
            r.raise_for_status()
            return r.json()

    def match(self, embedding: list[float], lat: float, lng: float, drone_id: str | None, segment_id=None) -> dict:
        return self._req("POST", "/watchlist/match", json={
            "embedding": embedding, "lat": lat, "lng": lng, "drone_id": drone_id, "segment_id": segment_id})

    def upload_segment(self, path: str, drone_id: str, started_at: str) -> dict:
        with open(path, "rb") as f:
            return self._req("POST", "/segments", data={"drone_id": drone_id, "started_at": started_at},
                             files={"file": (os.path.basename(path), f, "video/mp4")})

    def add_watchlist(self, **body) -> dict:
        return self._req("POST", "/watchlist", json=body)


# ---------- matching ----------


class Matcher:
    """Decides when a tracked face is worth one watchlist query, runs it off the frame loop,
    and records only the verdict on the track (never the embedding)."""

    def __init__(self, backend: Backend | None, drone_id: str | None, lat: float, lng: float,
                 min_score: float = 0.6, min_size: int = 40, requery_s: float = 3.0, max_queries: int = 3):
        self.backend, self.drone_id, self.lat, self.lng = backend, drone_id, lat, lng
        self.min_score, self.min_size, self.requery_s, self.max_queries = min_score, min_size, requery_s, max_queries
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.queries = self.errors = 0
        self.matches: list[dict] = []

    def step(self, track: Track, face) -> None:
        now = time.monotonic()
        if track.future is not None and track.future.done():
            self._settle(track, now)
        if self.backend is None or face is None or track.future is not None:
            return
        if track.status == "match" or track.queries >= self.max_queries or now < track.next_query_at:
            return
        x1, y1, x2, y2 = face.bbox
        if face.det_score < self.min_score or min(x2 - x1, y2 - y1) < self.min_size:
            return  # too small / blurry: a bad embedding is how false matches happen
        emb = face.normed_embedding.astype(float).tolist()
        track.queries += 1
        track.status = "querying"
        track.future = self.pool.submit(self.backend.match, emb, self.lat, self.lng, self.drone_id)
        self.queries += 1

    def _settle(self, track: Track, now: float) -> None:
        fut, track.future = track.future, None
        try:
            res = fut.result()
        except Exception:
            self.errors += 1
            track.status, track.queries = "unknown", track.queries - 1  # retry later, stay blurred
            track.next_query_at = now + self.requery_s
            return
        if res.get("match"):
            track.status, track.review_id, track.confidence = "match", res["review_id"], res["confidence"]
            self.matches.append({"track": track.id, "review_id": res["review_id"], "confidence": res["confidence"],
                                 "box": [int(v) for v in track.box]})  # where the matched face was (x1, y1, x2, y2)
        else:
            track.status, track.next_query_at = "no_match", now + self.requery_s

    def drain(self, tracks: list[Track]) -> None:
        """Wait for in-flight queries (end of video) so every match is recorded."""
        self.pool.shutdown(wait=True)
        for t in tracks:
            if t.future is not None:
                self._settle(t, time.monotonic())


# ---------- output ----------


def open_writer(path: str, fps: float, size: tuple[int, int]) -> cv2.VideoWriter:
    """H.264 when available (plays in browsers), else MPEG-4."""
    for codec in ("avc1", "mp4v"):
        w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), fps, size)
        if w.isOpened():
            return w
    raise RuntimeError(f"cannot open video writer for {path}")
