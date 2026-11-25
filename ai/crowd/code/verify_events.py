#!/usr/bin/env python3
"""
verify_events.py - check that a crowd_risk run has not been tampered with.

  python verify_events.py out_concert_vd40
  python verify_events.py out_concert_vd40 --expect-head <chain_head from a trusted place>
  python verify_events.py out_concert_vd40 --demo        # tamper with a copy and show it is caught

What it checks, per event in events.jsonl:
  1. the event's own hash matches its contents        (catches edited values)
  2. its prev_hash equals the previous event's hash   (catches deleted, inserted, reordered events)
  3. its snapshot file exists and its SHA-256 equals evidence_hash (catches edited/removed images)
and, using summary.json, that the log has not been cut short.

Limits, stated honestly: a chain proves the log is internally consistent. Someone who can
rewrite EVERY event and the summary can forge a new consistent chain. To stop that, keep the
final `chain_head` somewhere the attacker cannot edit (the backend's signed store, a printed copy)
and pass it with --expect-head. Signing the head hash is the backend's job.

Standard library only, so an auditor can run it with no installs.
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

GENESIS_HASH = "0" * 64


def canonical_json(v):
    """Same rules as crowd_risk.canonical_json and web/verifyEvents.mjs: keys sorted, no spaces,
    and a float with no fractional part prints as an integer, so Python and JavaScript agree."""
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
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v[k])
                              for k in sorted(v)) + "}"
    raise TypeError(f"cannot hash {type(v).__name__}")


def event_digest(event):
    """Same definition as crowd_risk.event_digest (a test keeps the two in step)."""
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify(out_dir, expect_head=None):
    """-> {"ok": bool, "events": n, "head": str|None, "problems": [str, ...]}"""
    out = Path(out_dir)
    problems = []
    log = out / "events.jsonl"
    if not log.exists():
        return {"ok": False, "events": 0, "head": None, "problems": [f"{log} not found"]}

    prev, n, head = GENESIS_HASH, 0, None
    for lineno, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        n += 1
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {lineno}: not valid JSON (the line was edited)")
            continue
        who = f"line {lineno} (event {e.get('id', '?')}, t={e.get('video_t', '?')})"
        if "event_hash" not in e or "prev_hash" not in e:
            problems.append(f"{who}: no chain fields (log made by an older version)")
            continue
        if event_digest(e) != e["event_hash"]:
            problems.append(f"{who}: contents do not match its hash (the event was edited)")
        if e["prev_hash"] != prev:
            problems.append(f"{who}: chain broken here (an event was deleted, inserted or reordered)")
        prev = e["event_hash"]            # continue from what the file claims, so one break is one report
        head = e["event_hash"]
        rel = e.get("heatmap")
        if rel:
            f = out / rel
            if not f.exists():
                problems.append(f"{who}: snapshot {rel} is missing")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != e.get("evidence_hash"):
                problems.append(f"{who}: snapshot {rel} was modified (hash differs from evidence_hash)")

    summary = out / "summary.json"
    if summary.exists():
        s = json.loads(summary.read_text(encoding="utf-8"))
        if "chain_events" in s and s["chain_events"] != n:
            problems.append(f"summary.json says {s['chain_events']} events but the log has {n} "
                            "(events were removed from or added to the end)")
        if "chain_head" in s and head is not None and s["chain_head"] != head:
            problems.append("final hash differs from summary.json chain_head")
    if expect_head is not None and head != expect_head:
        problems.append(f"final hash {str(head)[:16]}... does not match the expected head "
                        f"{expect_head[:16]}... (the whole log differs from the trusted one)")
    return {"ok": not problems, "events": n, "head": head, "problems": problems}


# ----------------------------------------------------------------- tamper demo --
def _lines(d):
    return (Path(d) / "events.jsonl").read_text(encoding="utf-8").splitlines()


def _write(d, lines):
    (Path(d) / "events.jsonl").write_text("\n".join(lines) + "\n")


def _edit_value(d):
    L = _lines(d)
    e = json.loads(L[3])
    e["risk"] = 5.0                          # hide a dangerous reading
    L[3] = json.dumps(e)
    _write(d, L)


def _delete_event(d):
    L = _lines(d)
    del L[4]
    _write(d, L)


def _reorder(d):
    L = _lines(d)
    L[2], L[3] = L[3], L[2]
    _write(d, L)


def _insert_forged(d):
    L = _lines(d)
    e = json.loads(L[2])
    e["risk"], e["level"], e["id"] = 99.0, "HIGH", "forged000000"
    e["event_hash"] = event_digest(e)        # the forger even recomputes the event's own hash
    L.insert(3, json.dumps(e))
    _write(d, L)


def _edit_snapshot(d):
    e = json.loads(_lines(d)[1])
    p = Path(d) / e["heatmap"]
    p.write_bytes(p.read_bytes() + b"\x00")


def _delete_snapshot(d):
    e = json.loads(_lines(d)[1])
    (Path(d) / e["heatmap"]).unlink()


def _truncate_tail(d):
    _write(d, _lines(d)[:-3])


def _rewrite_chain(d):
    """A skilled attacker edits one event, then recomputes every hash after it and the summary."""
    L = _lines(d)
    prev, out = GENESIS_HASH, []
    for i, line in enumerate(L):
        e = json.loads(line)
        if i == 3:
            e["risk"] = 5.0
        e["prev_hash"] = prev
        e["event_hash"] = event_digest(e)
        prev = e["event_hash"]
        out.append(json.dumps(e))
    _write(d, out)
    sp = Path(d) / "summary.json"
    s = json.loads(sp.read_text(encoding="utf-8"))
    s["chain_head"] = prev
    sp.write_text(json.dumps(s))


DEMOS = [
    ("edit one risk value", _edit_value, False),
    ("delete one event", _delete_event, False),
    ("swap two events", _reorder, False),
    ("insert a forged event (hash recomputed)", _insert_forged, False),
    ("modify a snapshot image", _edit_snapshot, False),
    ("delete a snapshot image", _delete_snapshot, False),
    ("cut the last events off the log", _truncate_tail, False),
    ("rewrite the whole chain + summary", _rewrite_chain, True),
]


def demo(out_dir):
    """Tamper with copies of a real run and show each change is caught."""
    original = verify(out_dir)
    if not original["ok"]:
        print("the original run does not verify, so the demo would be meaningless:")
        print("\n".join("  " + p for p in original["problems"]))
        return 1
    print(f"original run: VERIFIED, {original['events']} events, head {original['head'][:16]}...\n")
    print(f"{'tampering':<44} {'caught?':<22} what the verifier says")
    all_ok = True
    for name, fn, needs_anchor in DEMOS:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "run"
            shutil.copytree(out_dir, work)
            fn(work)
            plain = verify(work)
            anchored = verify(work, expect_head=original["head"])
            caught = (not plain["ok"]) if not needs_anchor else (not anchored["ok"])
            all_ok &= caught
            if needs_anchor:
                status = "only with --expect-head" if (plain["ok"] and not anchored["ok"]) else (
                    "yes" if not plain["ok"] else "NO")
            else:
                status = "yes" if caught else "NO"
            said = (anchored if needs_anchor else plain)["problems"]
            print(f"{name:<44} {status:<22} {said[0] if said else '-'}")
    print("\nAll tampering detected." if all_ok else "\nSOME TAMPERING WENT UNDETECTED.")
    return 0 if all_ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="a crowd_risk output folder")
    ap.add_argument("--expect-head", default=None, help="the trusted final chain hash (from a source the attacker cannot edit)")
    ap.add_argument("--demo", action="store_true", help="tamper with copies of the run and show each change is caught")
    a = ap.parse_args(argv)
    if a.demo:
        return demo(a.out_dir)
    r = verify(a.out_dir, a.expect_head)
    if r["ok"]:
        print(f"VERIFIED: {r['events']} events, every hash and snapshot matches.")
        print(f"chain head: {r['head']}")
        return 0
    print(f"NOT VERIFIED: {len(r['problems'])} problem(s) in {r['events']} events")
    for p in r["problems"]:
        print("  - " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
