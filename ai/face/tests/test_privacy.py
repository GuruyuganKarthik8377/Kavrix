"""Offline checks of the privacy rules (no model, no network). Run: uv run python tests/test_privacy.py"""
import os
import sys
import threading
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import facepriv as fp  # noqa: E402


def face(box, score=0.9):
    return SimpleNamespace(bbox=np.array(box, float), det_score=score, normed_embedding=np.ones(512) / np.sqrt(512))


class FakeBackend:
    def __init__(self, verdicts):
        self.verdicts, self.calls = list(verdicts), 0

    def match(self, emb, lat, lng, drone_id, segment_id=None):
        assert len(emb) == 512
        self.calls += 1
        v = self.verdicts.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


def settle(m, t):
    t.future.result()
    m.step(t, None)


def noisy():
    return np.random.default_rng(0).integers(0, 255, (200, 300, 3), dtype=np.uint8)


BOX = [100, 60, 160, 140]

# pixelate: region coarsened, outside untouched
f = noisy()
before = f.copy()
fp.pixelate(f, BOX, blocks=8, pad=0)
roi = f[60:140, 100:160].reshape(-1, 3)
assert len(np.unique(roi, axis=0)) <= 64
assert (f[:, :90] == before[:, :90]).all() and (f[150:] == before[150:]).all()

# tracker: identity kept across small motion, lost track persists then drops
tr = fp.Tracker(max_missed=3)
(t1, _), = tr.update([BOX])
(t1b, i), = tr.update([[104, 62, 164, 142]])
assert t1b is t1 and i == 0
for k in range(1, 4):
    (t, i), = tr.update([])
    assert t is t1 and i is None and t.missed == k
assert tr.update([]) == [] and tr.tracks == []
tr.update([BOX])  # seen in one frame only (a blip): dropped at once, no stale blur left behind
assert tr.update([]) == [] and tr.tracks == []

# to_blur: confident faces kept, weak middle-of-frame detections (shirts, backs of heads) dropped,
# weak faces cut off by the left/right/top edge kept
class F:
    def __init__(self, score, box):
        self.det_score, self.bbox = score, np.array(box, float)
kept = fp.to_blur([F(0.8, [500, 500, 560, 570]), F(0.35, [500, 500, 600, 700]), F(0.4, [0, 300, 40, 360]),
                   F(0.4, [1890, 300, 1920, 360]), F(0.4, [700, 0, 760, 30])], (1080, 1920, 3))
assert [f.det_score for f in kept] == [0.8, 0.4, 0.4, 0.4]

# render: every non-match status is pixelated, including a briefly lost track; a match is not
for status, missed in (("unknown", 0), ("querying", 0), ("no_match", 0), ("unknown", 2)):
    f = noisy()
    t = fp.Track(1, np.array(BOX, float), status=status, missed=missed)
    fp.render(f, [t])
    assert len(np.unique(f[70:130, 110:150].reshape(-1, 3), axis=0)) <= 64, status
f, orig = noisy(), noisy()
fp.render(f, [fp.Track(1, np.array(BOX, float), status="match", confidence=0.9)])
assert (f[80:120, 115:145] == orig[80:120, 115:145]).all()

# matcher: quality gate, single query, requery cap, match stops querying, embedding never kept
gate = threading.Event()


class SlowBackend(FakeBackend):
    def match(self, *a, **k):
        gate.wait(5)
        return super().match(*a, **k)


be = SlowBackend([{"match": False}, {"match": False}, {"match": True, "review_id": "r1", "confidence": 0.81}])
m = fp.Matcher(be, "D1", 17.36, 78.47, requery_s=100.0, max_queries=3)
t = fp.Track(1, np.array(BOX, float))
m.step(t, face(BOX, score=0.3))           # unclear face
m.step(t, face([0, 0, 20, 20]))           # too small
assert be.calls == 0 and t.status == "unknown"
m.step(t, face(BOX)); assert t.status == "querying"
m.step(t, face(BOX)); m.step(t, face(BOX))  # still in flight -> no second query
gate.set(); settle(m, t)
assert t.status == "no_match" and be.calls == 1
m.step(t, face(BOX)); assert be.calls == 1  # requery waits requery_s
t.next_query_at = 0; m.step(t, face(BOX)); settle(m, t)
t.next_query_at = 0; m.step(t, face(BOX)); settle(m, t)
assert t.status == "match" and t.review_id == "r1" and be.calls == 3
m.step(t, face(BOX)); assert be.calls == 3 and t.future is None
assert not any(isinstance(v, np.ndarray) and v.size == 512 for v in vars(t).values())
assert [{k: v for k, v in x.items() if k != "box"} for x in m.matches] == [{"track": 1, "review_id": "r1", "confidence": 0.81}]
assert len(m.matches[0]["box"]) == 4  # where the matched face was, for the run report

# matcher: backend error -> stays blurred, retried later, doesn't burn a query
be = FakeBackend([RuntimeError("down"), {"match": False}])
m = fp.Matcher(be, "D1", 17.36, 78.47, requery_s=0.0, max_queries=1)
t = fp.Track(2, np.array(BOX, float))
m.step(t, face(BOX))
try:
    t.future.result()
except RuntimeError:
    pass
m.step(t, None)
assert t.status == "unknown" and t.queries == 0 and m.errors == 1
m.step(t, face(BOX)); settle(m, t)
assert t.status == "no_match" and be.calls == 2
t.next_query_at = 0; m.step(t, face(BOX))
assert be.calls == 2 and t.future is None  # max_queries=1 reached: no more queries, stays blurred

# blur-only: never queries
m = fp.Matcher(None, None, 0, 0)
t = fp.Track(3, np.array(BOX, float))
m.step(t, face(BOX))
assert t.status == "unknown" and m.queries == 0

# enrolment template: unit-length mean; contact sheet pads the last row
import enroll as en  # noqa: E402
import tempfile  # noqa: E402
import cv2  # noqa: E402
t = en.unit([np.array([1.0, 0, 0]), np.array([0, 1.0, 0])])
assert abs(np.linalg.norm(t) - 1) < 1e-9 and abs(t[0] - t[1]) < 1e-9
sheet = os.path.join(tempfile.mkdtemp(), "s.jpg")
en.contact_sheet([np.full((112, 112, 3), 200, np.uint8)] * 3, sheet, cols=2)
assert cv2.imread(sheet).shape == (224, 224, 3)

print("privacy checks OK")
