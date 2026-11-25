"""Haven face & privacy worker: detect faces in a drone feed, check each against the lawful watchlist
via the backend, pixelate everyone else, and upload the privacy-safe video as signed evidence segments.

  uv run python worker.py --source samples/sample.mp4 --drone D1 --lat 17.3630 --lng 78.4725
  uv run python worker.py --source 0 --blur-only          # webcam, no backend, everyone pixelated
"""
import argparse
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import cv2

import facepriv as fp


class Segments:
    """Cuts the blurred stream into N-second files and uploads each as signed evidence, then deletes it."""

    def __init__(self, backend: fp.Backend, drone_id: str, fps: float, size: tuple[int, int], seconds: float):
        self.backend, self.drone_id, self.fps, self.size = backend, drone_id, fps, size
        self.max_frames = max(1, int(seconds * fps))
        self.dir = tempfile.mkdtemp(prefix="haven-seg-")
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.futures, self.writer, self.frames, self.count = [], None, 0, 0

    def write(self, frame) -> None:
        if self.writer is None:
            self.path = os.path.join(self.dir, f"seg{self.count:05d}.mp4")
            self.started = datetime.now(timezone.utc).isoformat()
            self.writer, self.frames = fp.open_writer(self.path, self.fps, self.size), 0
        self.writer.write(frame)
        self.frames += 1
        if self.frames >= self.max_frames:
            self.close()

    def close(self) -> None:
        if self.writer is None:
            return
        self.writer.release()
        self.writer, self.count = None, self.count + 1
        self.futures.append(self.pool.submit(self._upload, self.path, self.started))

    def _upload(self, path: str, started: str) -> dict:
        try:
            return self.backend.upload_segment(path, self.drone_id, started)
        finally:
            os.remove(path)

    def finish(self) -> tuple[list[dict], int]:
        self.close()
        self.pool.shutdown(wait=True)
        ok = [f.result() for f in self.futures if f.exception() is None]
        return ok, len(self.futures) - len(ok)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="video file, webcam index (0) or rtsp:// URL")
    ap.add_argument("--drone", default="D1", help="drone id the feed comes from")
    ap.add_argument("--lat", type=float, default=17.3616, help="drone position (sent with matches)")
    ap.add_argument("--lng", type=float, default=78.4747)
    ap.add_argument("--out", default="out/blurred.mp4", help="privacy-safe output video")
    ap.add_argument("--blur-only", action="store_true", help="no backend: pixelate every face")
    ap.add_argument("--segment-s", type=float, default=10, help="evidence segment length in seconds (0 = no upload)")
    ap.add_argument("--model", default="buffalo_sc", help="InsightFace model pack (buffalo_sc, buffalo_l)")
    ap.add_argument("--det-size", type=int, default=640)
    ap.add_argument("--blur-thresh", type=float, default=0.45, help="min detector confidence to blur (edge-cut faces: any score)")
    ap.add_argument("--min-score", type=float, default=0.6, help="min detector confidence before matching")
    ap.add_argument("--min-size", type=int, default=40, help="min face size in px before matching")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = whole source)")
    ap.add_argument("--report", help="write a JSON summary of the run (counts, matches with their video time) to this path")
    a = ap.parse_args()

    backend = None if a.blur_only else fp.Backend.from_env("WORKER")
    model = fp.load_model(a.model, a.det_size)  # detects from 0.3; fp.to_blur keeps faces
    cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open source {a.source}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if 0 < fps <= 120 else 25.0

    tracker = fp.Tracker(max_missed=max(3, int(fps / 5)))  # ~0.2 s: covers detector flicker, not camera pans
    matcher = fp.Matcher(backend, a.drone, a.lat, a.lng, a.min_score, a.min_size)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out = segments = None
    n, t0, stamped = 0, time.monotonic(), 0
    try:
        while not (a.max_frames and n >= a.max_frames):
            ok, frame = cap.read()
            if not ok:
                break
            faces = fp.to_blur(model.get(frame), frame.shape, a.blur_thresh)
            for track, i in tracker.update([f.bbox for f in faces]):
                matcher.step(track, faces[i] if i is not None else None)
            del faces  # embeddings go with the frame
            for m in matcher.matches[stamped:]:
                m["t"] = round(n / fps, 2)  # video second at which the backend's verdict arrived
            stamped = len(matcher.matches)
            fp.render(frame, tracker.tracks)
            if out is None:
                size = (frame.shape[1], frame.shape[0])
                out = fp.open_writer(a.out, fps, size)
                if backend and a.segment_s > 0:
                    segments = Segments(backend, a.drone, fps, size, a.segment_s)
            out.write(frame)
            if segments:
                segments.write(frame)
            n += 1
            if n % 50 == 0:
                print(f"  frame {n}  tracks {len(tracker.tracks)}  queries {matcher.queries}  matches {len(matcher.matches)}")
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        matcher.drain(tracker.tracks)
        if out is not None:
            out.release()
        uploaded, failed = segments.finish() if segments else ([], 0)

    secs = time.monotonic() - t0
    print(f"\nprocessed {n} frames in {secs:.1f}s ({n / secs if secs else 0:.1f} fps) -> {a.out}")
    print(f"faces tracked {tracker._next - 1} | watchlist queries {matcher.queries} | query errors {matcher.errors}")
    for m in matcher.matches:
        print(f"  MATCH track {m['track']}  confidence {m['confidence']:.0%}  -> pending review {m['review_id']}")
    if segments:
        print(f"evidence segments uploaded {len(uploaded)} | failed {failed}")
    if a.report:
        for m in matcher.matches[stamped:]:
            m["t"] = round(n / fps, 2)
        report = {
            "source": os.path.basename(a.source), "drone_id": a.drone, "lat": a.lat, "lng": a.lng, "model": a.model,
            "frames": n, "fps": round(fps, 2), "duration_s": round(n / fps, 1), "width": size[0] if out else None,
            "height": size[1] if out else None, "faces_tracked": tracker._next - 1, "watchlist_queries": matcher.queries,
            "query_errors": matcher.errors, "matches": matcher.matches,
            "faces_pixelated": tracker._next - 1 - len(matcher.matches),  # every tracked face that was not a match
            "segments_uploaded": len(uploaded), "segments_failed": failed,
        }
        os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
