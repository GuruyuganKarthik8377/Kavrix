"""End-to-end smoke test against a running backend (python smoke_test.py [http://localhost:8000]).

Uses the logins in backend/.env. Exercises every integration path: crowd events, verification, signed run head,
watchlist enrol / match / no-match, human review, officer tasks, evidence segments, 112 dispatch, audit chain.
"""
import io
import math
import random
import sys
import time

import httpx

import haven_core as hc  # loads backend/.env
import os

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
ok_count = 0


def check(cond, label):
    global ok_count
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    ok_count += 1
    print(f"  ok  {label}")


def login(prefix):
    r = httpx.post(f"{BASE}/auth/login", json={"email": os.environ[f"{prefix}_EMAIL"],
                                                "password": os.environ[f"{prefix}_PASSWORD"]})
    r.raise_for_status()
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {r.json()['access_token']}"}, timeout=30)


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def main():
    print(f"Haven smoke test against {BASE}")
    check(httpx.post(f"{BASE}/auth/login", json={"email": "x@y", "password": "nope"}).status_code == 401,
          "bad login refused")
    cmd, wrk, off = login("COMMANDER"), login("WORKER"), login("OFFICER")
    check(httpx.get(f"{BASE}/runs").status_code == 401, "API needs a token")

    # crowd: replay a recorded run and verify it
    r = cmd.post("/demo/replay/concert", params={"rate": 50})
    check(r.status_code == 200, "replay recorded crowd run")
    run_id = r.json()["run_id"]
    time.sleep(1.5)
    v = cmd.get(f"/runs/{run_id}/verify").json()
    check(v["ok"] and v["events"] == 15 and v["snapshots_checked"] == 15, "server-side chain + snapshots + signature")
    evs = cmd.get("/events", params={"run_id": run_id}).json()
    alert = next(e for e in evs if e["type"] == "crowd_alert")
    check(cmd.get(f"/verify/{alert['id']}").json()["valid"], "GET /verify/{id} valid")
    check(cmd.get(f"/snapshot/{alert['id']}").headers["content-type"] == "image/jpeg", "snapshot served")
    tasks = off.get("/tasks").json()
    check(any(t["source_id"] == alert["id"] for t in tasks), "crowd_alert became an officer task")
    t = next(t for t in tasks if t["source_id"] == alert["id"])
    check(off.post(f"/tasks/{t['id']}", json={"action": "ack"}).json()["status"] == "acknowledged", "officer acks")
    cmd.post(f"/demo/tamper/{run_id}")
    v = cmd.get(f"/runs/{run_id}/verify").json()
    check(not v["ok"] and any(p["kind"] == "edited" for p in v["problems"]), "tampering detected")

    # watchlist: enrol, match, no-match, review
    rng = random.Random(7)
    person = unit([rng.gauss(0, 1) for _ in range(512)])
    other = unit([rng.gauss(0, 1) for _ in range(512)])
    near = unit([p + 0.01 * rng.gauss(0, 1) for p in person])  # same person, slightly different view
    exp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3 * 86400))
    check(wrk.post("/watchlist", json={"label": "x", "category": "missing_person", "legal_basis": "test",
                                        "consent_ref": "T", "expires_at": exp, "embedding": person}).status_code == 403,
          "worker cannot enrol")
    w = cmd.post("/watchlist", json={"label": "Smoke test (synthetic)", "category": "missing_person",
                                      "legal_basis": "Smoke test", "consent_ref": "SMOKE-1", "expires_at": exp,
                                      "embedding": person})
    check(w.status_code == 201, "commander enrols watchlist entry")
    check(wrk.post("/watchlist/match", json={"embedding": other, "lat": 17.36, "lng": 78.47}).json() == {"match": False},
          "stranger: no match, nothing stored")
    m = wrk.post("/watchlist/match", json={"embedding": near, "lat": 17.36, "lng": 78.47, "drone_id": "D-02"}).json()
    check(m["match"] and m["confidence"] > 0.9, "enrolled person matches -> pending review")
    check(any(r["id"] == m["review_id"] for r in wrk.get("/reviews", params={"status": "pending"}).json()),
          "review listed as pending")
    check(cmd.post(f"/reviews/{m['review_id']}", json={"decision": "confirm"}).json()["status"] == "confirmed",
          "officer confirms match")
    check(any(t["source_id"] == m["review_id"] for t in off.get("/tasks").json()), "confirmed match -> officer task")
    cmd.delete(f"/watchlist/{w.json()['id']}")

    # evidence segments
    blob = b"\x00\x00\x00\x18ftypmp42" + bytes(rng.getrandbits(8) for _ in range(4096))
    s = wrk.post("/segments", data={"drone_id": "D-02", "started_at": hc.now_iso()},
                 files={"file": ("seg.mp4", io.BytesIO(blob), "video/mp4")})
    check(s.status_code == 201, "segment uploaded and signed")
    check(cmd.get(f"/segments/{s.json()['id']}/verify").json()["valid"], "segment signature verifies")

    # 112 dispatch
    inc = cmd.post("/dispatch/112", json={"lat": 17.385, "lng": 78.4867, "kind": "assault",
                                          "description": "smoke test"})
    check(inc.status_code == 201 and inc.json()["drone_id"], f"112 call dispatched {inc.json().get('drone_id')}")
    cmd.post(f"/drones/{inc.json()['drone_id']}/recall")

    # recorded face run (campus test): takes the same review path as a live worker
    check(wrk.post("/demo/face-replay/campus").status_code == 403, "worker cannot start a face replay")
    check(cmd.post("/demo/face-replay/does-not-exist").status_code == 404, "unknown face run refused")
    fr = cmd.post("/demo/face-replay/campus", params={"rate": 200})
    check(fr.status_code == 200 and fr.json()["matches"] >= 1, "recorded campus face run starts")
    time.sleep(1.5)
    pend = [r for r in cmd.get("/reviews", params={"status": "pending"}).json() if str(r["label"]).endswith("recorded run")]
    check(len(pend) == fr.json()["matches"] and all(0.5 <= r["confidence"] <= 1 for r in pend),
          "recorded matches arrive as pending reviews")
    check(cmd.post(f"/reviews/{pend[0]['id']}", json={"decision": "confirm"}).json()["status"] == "confirmed",
          "officer confirms a recorded match")
    check(any(t["source_id"] == pend[0]["id"] and t["title"] == "Missing person located" for t in off.get("/tasks").json()),
          "confirmed recorded match -> officer task")

    check(cmd.get("/audit/verify").json()["ok"], "audit log hash chain intact")
    cmd.post("/demo/reset")
    print(f"\nPASSED: {ok_count} checks")


if __name__ == "__main__":
    main()
