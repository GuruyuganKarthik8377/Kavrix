"""Identify enrolled watchlist people in still images. Every other face is pixelated.

Each clear face is checked once against the backend watchlist (worker login). A match becomes a
pending officer review, like in the video worker; with commander credentials in .env the label of
the matched entry is drawn too. Nothing about non-matching faces is kept.

  uv run python identify.py photos/*.jpg --out out/identified
"""
import argparse
import os
from pathlib import Path

import cv2

import facepriv as fp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="out/identified")
    ap.add_argument("--drone")
    ap.add_argument("--lat", type=float, default=17.3616)
    ap.add_argument("--lng", type=float, default=78.4747)
    ap.add_argument("--det-size", type=int, default=1920, help="detector input; large finds small faces in crowd shots")
    ap.add_argument("--blur-thresh", type=float, default=0.45, help="min detector confidence to blur (edge-cut faces: any score)")
    ap.add_argument("--min-score", type=float, default=0.6, help="only clear faces are matched (all are blurred)")
    ap.add_argument("--min-size", type=int, default=40)
    ap.add_argument("--model", default="buffalo_sc")
    a = ap.parse_args()

    model = fp.load_model(a.model, det_size=a.det_size)  # det_thresh 0.3: blur generously
    worker = fp.Backend.from_env("WORKER")
    commander = fp.Backend.from_env("COMMANDER") if os.getenv("COMMANDER_PASSWORD") else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    for path in a.images:
        img = cv2.imread(path)  # applies EXIF rotation
        if img is None:
            print(f"{path}: cannot read (HEIC? convert first: sips -s format jpeg in.heic --out in.jpg)")
            continue
        faces, matches = fp.to_blur(model.get(img), img.shape, a.blur_thresh), []
        for f in faces:
            x1, y1, x2, y2 = f.bbox
            res = {"match": False}
            if f.det_score >= a.min_score and min(x2 - x1, y2 - y1) >= a.min_size:
                res = worker.match(f.normed_embedding.astype(float).tolist(), a.lat, a.lng, a.drone)
            if not res["match"]:
                fp.pixelate(img, f.bbox)
                continue
            matches.append((f.bbox.astype(int), res))
        labels = {}
        if matches and commander:
            labels = {r["id"]: r["label"] for r in commander._req("GET", "/reviews", params={"status": "pending"})}
        lw = max(2, img.shape[1] // 800)
        for (x1, y1, x2, y2), res in matches:
            text = f"{labels.get(res['review_id'], 'WATCHLIST')} {res['confidence']:.0%} - PENDING REVIEW"
            cv2.rectangle(img, (x1, y1), (x2, y2), fp.MATCH_COLOR, lw)
            cv2.putText(img, text, (x1, max(20, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4 * lw, fp.MATCH_COLOR, lw)
        dest = out / f"{Path(path).stem}.jpg"
        cv2.imwrite(str(dest), img)
        found = ", ".join(f"{labels.get(r['review_id'], 'watchlist')} ({r['confidence']:.0%}, review {r['review_id']})"
                          for _, r in matches) or "no watchlist match"
        print(f"{path}: {len(faces)} faces, {found} -> {dest}")


if __name__ == "__main__":
    main()
