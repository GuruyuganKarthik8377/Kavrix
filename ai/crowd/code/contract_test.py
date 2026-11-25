#!/usr/bin/env python3
"""
contract_test.py - check events against the Kavrix event contract (contract/event.schema.json). Standard library only.

  python contract_test.py sample_data/concert                       # a run folder or an events.jsonl file
  python contract_test.py --fetch http://localhost:8000/events      # what YOUR backend returns (JSON list or JSON lines)
  python contract_test.py --fetch http://localhost:8000/events --expect sample_data/concert
                                                                    # ... and that it matches what was sent, value for value
  python contract_test.py sample_data/concert --snapshots sample_data/concert   # also hash the snapshot images

What it checks, per event: every required field, its type and range, the type-specific fields, and that level
agrees with risk. Per run (run_id): seq runs 0, 1, 2, ... with no gap, one drone_id, video_t never goes backwards,
every prev_hash links to the previous event_hash, and every event_hash matches the event's contents (canonical JSON,
the same rule as verify_events.py and web/verifyEvents.mjs). With --expect it also checks the backend returned every
event unchanged: a stored value that was rounded or reformatted changes the hash. Exit code 0 if everything passes.
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

import verify_events

SCHEMA_VERSION = 1
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
LEVELS = ("LOW", "MEDIUM", "HIGH")
TYPES = ("crowd_risk", "crowd_alert")
ALERT_KINDS = ("surge", "sustained_high")
LEVEL_MEDIUM, LEVEL_HIGH = 35, 65


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _str(v):
    return isinstance(v, str) and len(v) > 0


# field -> (checker, description). The keys are the required fields; a test keeps this in step with the schema file.
FIELDS = {
    "schema_version": (lambda v: v == SCHEMA_VERSION and _int(v), f"must be {SCHEMA_VERSION}"),
    "id": (_str, "non-empty string"),
    "type": (lambda v: v in TYPES, f"one of {TYPES}"),
    "run_id": (_str, "non-empty string"),
    "drone_id": (_str, "non-empty string"),
    "seq": (lambda v: _int(v) and v >= 0, "integer >= 0"),
    "lat": (lambda v: _num(v) and -90 <= v <= 90, "number in [-90, 90]"),
    "lng": (lambda v: _num(v) and -180 <= v <= 180, "number in [-180, 180]"),
    "risk": (lambda v: _num(v) and 0 <= v <= 100, "number in [0, 100]"),
    "level": (lambda v: v in LEVELS, f"one of {LEVELS}"),
    "count": (lambda v: _int(v) and v >= 0, "integer >= 0"),
    "density_peak": (lambda v: _num(v) and v >= 0, "number >= 0"),
    "motion": (lambda v: _num(v) and v >= 0, "number >= 0"),
    "risk_rate": (_num, "number"),
    "time_to_high": (lambda v: v is None or (_num(v) and v >= 0), "number >= 0 or null"),
    "hotspot": (lambda v: v is None or (isinstance(v, dict) and all(k in v and _num(v[k]) and 0 <= v[k] <= 1 for k in ("x", "y"))),
                "null or {x, y} with both in [0, 1]"),
    "timestamp": (lambda v: isinstance(v, str) and bool(ISO.match(v)), "ISO 8601 date-time string"),
    "video_t": (lambda v: _num(v) and v >= 0, "number >= 0"),
    "heatmap": (_str, "non-empty string (snapshot path)"),
    "evidence_hash": (lambda v: isinstance(v, str) and bool(HEX64.match(v)), "64 lowercase hex characters"),
    "prev_hash": (lambda v: isinstance(v, str) and bool(HEX64.match(v)), "64 lowercase hex characters"),
    "event_hash": (lambda v: isinstance(v, str) and bool(HEX64.match(v)), "64 lowercase hex characters"),
}
ALERT_FIELDS = {
    "alert_kind": (lambda v: v in ALERT_KINDS, f"one of {ALERT_KINDS}"),
    "message": (_str, "non-empty string"),
}


def level_for(risk):
    return "HIGH" if risk >= LEVEL_HIGH else "MEDIUM" if risk >= LEVEL_MEDIUM else "LOW"


def check_event(e, where="event"):
    """Problems with one event, as a list of strings (empty = fine)."""
    if not isinstance(e, dict):
        return [f"{where}: not a JSON object"]
    errors = []
    table = dict(FIELDS)
    if e.get("type") == "crowd_alert":
        table.update(ALERT_FIELDS)
    for name, (ok, desc) in table.items():
        if name not in e:
            errors.append(f"{where}: missing field '{name}'")
        elif not ok(e[name]):
            errors.append(f"{where}: field '{name}' = {e[name]!r} is invalid (expected {desc})")
    if not errors and abs(_boundary_gap(e["risk"], e["level"])) > 0.15:
        errors.append(f"{where}: level {e['level']} does not match risk {e['risk']} "
                      f"(LOW < {LEVEL_MEDIUM} <= MEDIUM < {LEVEL_HIGH} <= HIGH)")
    return errors


def _boundary_gap(risk, level):
    """How far risk is outside the band of `level` (0 if inside). risk is rounded to 1 decimal, so allow 0.1."""
    lo, hi = {"LOW": (0, LEVEL_MEDIUM), "MEDIUM": (LEVEL_MEDIUM, LEVEL_HIGH), "HIGH": (LEVEL_HIGH, 100.01)}[level]
    return 0.0 if lo - 0.1 <= risk < hi + 0.1 else (lo - risk if risk < lo else risk - hi)


def check_run(events, snapshot_dir=None):
    """Problems across a list of events that may contain several runs. Returns a list of strings."""
    errors = []
    by_run = {}
    for i, e in enumerate(events):
        errors += check_event(e, f"event #{i + 1} (id {e.get('id', '?') if isinstance(e, dict) else '?'})")
        if isinstance(e, dict):
            by_run.setdefault(e.get("run_id"), []).append((i, e))
    for run, items in by_run.items():
        prev_hash, prev_t, drones = verify_events.GENESIS_HASH, None, set()
        for k, (i, e) in enumerate(items):
            who = f"run {run!r} event #{i + 1} (seq {e.get('seq', '?')})"
            if e.get("seq") != k:
                errors.append(f"{who}: seq should be {k} (a gap or repeat means an event was lost, duplicated or reordered)")
            drones.add(e.get("drone_id"))
            vt = e.get("video_t")
            if _num(vt) and prev_t is not None and vt < prev_t - 1e-9:
                errors.append(f"{who}: video_t {vt} goes backwards (previous {prev_t})")
            prev_t = vt if _num(vt) else prev_t
            if e.get("prev_hash") != prev_hash:
                errors.append(f"{who}: prev_hash does not link to the previous event (chain broken)")
            if isinstance(e.get("event_hash"), str):
                try:
                    if verify_events.event_digest(e) != e["event_hash"]:
                        errors.append(f"{who}: event_hash does not match the event's contents (it was changed)")
                except (TypeError, ValueError) as ex:
                    errors.append(f"{who}: cannot hash this event ({ex})")
                prev_hash = e["event_hash"]
            if snapshot_dir and _str(e.get("heatmap")):
                f = Path(snapshot_dir) / e["heatmap"]
                if not f.exists():
                    errors.append(f"{who}: snapshot {e['heatmap']} not found in {snapshot_dir}")
                elif hashlib.sha256(f.read_bytes()).hexdigest() != e.get("evidence_hash"):
                    errors.append(f"{who}: snapshot {e['heatmap']} does not match evidence_hash")
        if len(drones) > 1:
            errors.append(f"run {run!r}: more than one drone_id {sorted(map(str, drones))}")
    return errors


def _same(a, b):
    """Equal as JSON values. 64.0 and 64 count as the same number (the hash rule treats them alike)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if _num(a) and _num(b):
        return a == b
    return a == b and type(a) is type(b)


def compare(sent, got):
    """Every sent event must come back with every field unchanged (matched by id). Extra fields the backend
    adds, such as a received-at time, are fine. Returns a list of strings."""
    errors, by_id = [], {e.get("id"): e for e in got if isinstance(e, dict)}
    for e in sent:
        back = by_id.get(e["id"])
        if back is None:
            errors.append(f"event {e['id']} (seq {e['seq']}) was sent but the backend did not return it")
            continue
        for k in e:
            if k not in back:
                errors.append(f"event {e['id']}: field '{k}' is missing from what the backend returned")
            elif not _same(e[k], back[k]):
                errors.append(f"event {e['id']}: field '{k}' changed: sent {e[k]!r}, returned {back[k]!r}")
    return errors


def load_events(path):
    p = Path(path)
    if p.is_dir():
        p = p / "events.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_events_text(text):
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    if text.startswith("{") and "\n" not in text:
        return [json.loads(text)]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def fetch_events(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return parse_events_text(r.read().decode("utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="a run folder or events.jsonl")
    ap.add_argument("--fetch", help="GET events from this URL instead (JSON list or JSON lines)")
    ap.add_argument("--expect", help="a run folder or events.jsonl the fetched events must match exactly")
    ap.add_argument("--snapshots", help="folder holding the heatmaps/ images, to check evidence_hash")
    a = ap.parse_args(argv)
    if not a.source and not a.fetch:
        ap.error("give a source folder/file or --fetch URL")
    events = fetch_events(a.fetch) if a.fetch else load_events(a.source)
    errors = check_run(events, a.snapshots)
    if a.expect:
        errors += compare(load_events(a.expect), events)
    runs = len({e.get("run_id") for e in events if isinstance(e, dict)})
    if errors:
        print(f"FAILED: {len(errors)} problem(s) in {len(events)} events ({runs} run(s))")
        for m in errors[:40]:
            print("  - " + m)
        if len(errors) > 40:
            print(f"  ... and {len(errors) - 40} more")
        return 1
    print(f"PASSED: {len(events)} events in {runs} run(s): fields, types, ranges, seq, chain and hashes are all valid"
          + (", and they match what was sent" if a.expect else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
