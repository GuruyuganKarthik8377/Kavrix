#!/usr/bin/env python3
"""
post_events.py - replay a recorded run into a backend. No GPU, model or video needed (Python standard library only).

  python post_events.py sample_data/concert --api http://localhost:8000/events            # 1 event per second
  python post_events.py sample_data/jl030/events.jsonl --api http://localhost:8000/events --rate 0   # as fast as possible
  python post_events.py sample_data/concert --api http://localhost:8000/events --dry-run  # just show what would be sent

It POSTs each event of events.jsonl, in order, as application/json, exactly as `crowd_risk.py --api` does, and
prints the HTTP status of every one. A 2xx counts as accepted. Exit code 0 if every event was accepted, else 1.
Use --from-seq to resume or back-fill after an outage: events.jsonl is always complete, and every event carries a
`seq` number and a `prev_hash` link, so a gap is easy to see in the backend.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def load_events(path):
    p = Path(path)
    if p.is_dir():
        p = p / "events.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def post_one(url, event, timeout=2.0):
    """POST one event. Returns (HTTP status or None if the request never completed, short detail text)."""
    req = urllib.request.Request(url, data=json.dumps(event).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(e)


def replay(events, url, rate=1.0, timeout=2.0, from_seq=0, stop_on_error=False, dry_run=False,
           out=print, sleep=time.sleep):
    """Send the events in order, `rate` per second (0 = no pause). Returns {sent, failed, skipped, statuses}."""
    stats = {"sent": 0, "failed": 0, "skipped": 0, "statuses": {}}
    for e in events:
        if e.get("seq", 0) < from_seq:
            stats["skipped"] += 1
            continue
        label = f"seq {e.get('seq', '?'):>3}  {e['type']:<12}  risk {e['risk']:5.1f}  {e['level']:<6}"
        if dry_run:
            out(f"{label}  (dry run, not sent)")
            stats["sent"] += 1
        else:
            status, detail = post_one(url, e, timeout)
            ok = status is not None and 200 <= status < 300
            stats["sent" if ok else "failed"] += 1
            key = str(status)
            stats["statuses"][key] = stats["statuses"].get(key, 0) + 1
            out(f"{label}  -> {status if status is not None else 'no response'}"
                + ("" if ok else f"  {detail.strip()[:120]}"))
            if not ok and stop_on_error:
                break
        if rate and rate > 0:
            sleep(1.0 / rate)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a run folder or an events.jsonl file")
    ap.add_argument("--api", required=False, help="URL to POST each event to")
    ap.add_argument("--rate", type=float, default=1.0, help="events per second (default 1, like a live feed; 0 = fast)")
    ap.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for each response")
    ap.add_argument("--from-seq", type=int, default=0, help="skip events with a smaller seq (resume / back-fill)")
    ap.add_argument("--stop-on-error", action="store_true", help="stop at the first rejected event")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    a = ap.parse_args(argv)
    if not a.api and not a.dry_run:
        ap.error("--api is required unless --dry-run")
    events = load_events(a.source)
    stats = replay(events, a.api, a.rate, a.timeout, a.from_seq, a.stop_on_error, a.dry_run)
    print(f"\n{stats['sent']} accepted, {stats['failed']} failed, {stats['skipped']} skipped, "
          f"of {len(events)} events. HTTP statuses: {stats['statuses'] or 'n/a'}")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
