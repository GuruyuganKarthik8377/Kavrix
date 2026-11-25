"""Simulated drone fleet: nearest-drone dispatch for 112 calls, flight at cruise speed, live positions over WebSocket.

There is no drone hardware; positions and flights are simulated and labelled as such on the dashboard.
"""
from __future__ import annotations

import asyncio
import math

import haven_core as hc
from haven_core import db

# Drone docks across Hyderabad (real place names, simulated drones)
DOCKS = [
    ("D-01", "Tank Bund", 17.4239, 78.4738),
    ("D-02", "Charminar", 17.3616, 78.4747),
    ("D-03", "HITEC City", 17.4435, 78.3772),
    ("D-04", "Secunderabad", 17.4399, 78.4983),
    ("D-05", "LB Nagar", 17.3457, 78.5522),
    ("D-06", "Gachibowli", 17.4401, 78.3489),
]
CRUISE_MPS = 18.0  # ~65 km/h, typical for first-responder quadcopters
TICK_S = 1.0


class Fleet:
    def __init__(self, hub):
        self.hub = hub
        self.drones: dict[str, dict] = {}
        self.reset()

    def reset(self) -> None:
        self.drones = {
            d: {"id": d, "dock": name, "dock_lat": lat, "dock_lng": lng, "lat": lat, "lng": lng,
                "status": "idle", "battery": 100.0, "incident_id": None, "target": None, "eta_s": None,
                "crowd_risk": None, "crowd_level": None}
            for d, name, lat, lng in DOCKS
        }
        # D-01 is the drone streaming the crowd feed over Tank Bund
        self.drones["D-01"]["status"] = "on_station"

    def snapshot(self) -> list[dict]:
        return list(self.drones.values())

    def note_crowd(self, ev: dict) -> None:
        d = self.drones.get(ev.get("drone_id") or "")
        if d:
            d["crowd_risk"], d["crowd_level"] = ev.get("risk"), ev.get("level")
            if d["status"] == "idle":
                d["status"] = "on_station"

    def dispatch(self, lat: float, lng: float, kind: str, description: str, actor: str) -> dict | None:
        free = [d for d in self.drones.values() if d["status"] in ("idle", "returning") and d["battery"] > 25]
        if not free:
            return None
        best = min(free, key=lambda d: hc.haversine_m(d["lat"], d["lng"], lat, lng))
        dist = hc.haversine_m(best["lat"], best["lng"], lat, lng)
        eta = dist / CRUISE_MPS
        iid = "in_" + hc.new_id(5)
        db.x("INSERT INTO incidents (id, kind, description, lat, lng, created_at, created_by, drone_id, distance_m,"
             " eta_s, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             (iid, kind, description, lat, lng, hc.now_iso(), actor, best["id"], dist, eta, "en_route"))
        best.update(status="en_route", incident_id=iid, target=(lat, lng), eta_s=eta)
        inc = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
        self.hub.publish("dispatch", {"incident": inc, "drone": best,
                                      "candidates": [{"id": d["id"], "distance_m": round(
                                          hc.haversine_m(d["lat"], d["lng"], lat, lng))} for d in free]})
        return inc

    def recall(self, drone_id: str) -> bool:
        d = self.drones.get(drone_id)
        if not d:
            return False
        if d["incident_id"]:
            db.x("UPDATE incidents SET status='closed' WHERE id=?", (d["incident_id"],))
        d.update(status="returning", target=(d["dock_lat"], d["dock_lng"]), incident_id=None)
        return True

    def _step(self, d: dict) -> bool:
        if not d["target"]:
            return False
        tlat, tlng = d["target"]
        dist = hc.haversine_m(d["lat"], d["lng"], tlat, tlng)
        step = CRUISE_MPS * TICK_S
        d["battery"] = max(0.0, d["battery"] - 0.05)
        if dist <= step:
            d["lat"], d["lng"], d["eta_s"] = tlat, tlng, 0
            if d["status"] == "en_route":
                d["status"] = "on_scene"
                db.x("UPDATE incidents SET status='on_scene', arrived_at=? WHERE id=?", (hc.now_iso(), d["incident_id"]))
                self.hub.publish("arrival", {"drone": d, "incident": db.one("SELECT * FROM incidents WHERE id=?",
                                                                             (d["incident_id"],))})
            else:
                d["status"], d["battery"] = "idle", 100.0
            d["target"] = None
            return True
        f = step / dist
        d["lat"] += (tlat - d["lat"]) * f
        d["lng"] += (tlng - d["lng"]) * f
        d["eta_s"] = (dist - step) / CRUISE_MPS
        return True

    async def run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            moved = [d for d in self.drones.values() if self._step(d)]
            if moved:
                self.hub.publish("drones", moved)
