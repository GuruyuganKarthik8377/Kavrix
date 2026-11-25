"""Enroll a consenting person into the watchlist from 1-5 photos (needs commander credentials).

Each photo must contain exactly one face. Optional --video files are the "training" step: the
person is found in them by similarity to the photos, and those extra views (angles, distance,
light) are averaged into the template. Every other face in the videos is compared once and its
embedding dropped on the spot. Only the averaged embedding is sent to the backend, which encrypts
it before storing.

  uv run python enroll.py --label "Harish (team consent)" --category missing_person \
      --legal-basis "Demo: consent form HV-01" --consent-ref HV-01 photo1.jpg photo2.jpg \
      --video clip1.mov --video clip2.mov --sheet out/enrolled_samples.jpg
"""
import argparse
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

import facepriv as fp


def photo_embeddings(model, photos: list[str]) -> list[np.ndarray]:
    embs = []
    for path in photos:
        img = cv2.imread(path)
        if img is None:
            raise SystemExit(f"cannot read {path}")
        faces = model.get(img)
        if len(faces) != 1:
            raise SystemExit(f"{path}: found {len(faces)} faces; use a photo with exactly one face")
        if faces[0].det_score < 0.6:
            raise SystemExit(f"{path}: face too unclear (score {faces[0].det_score:.2f})")
        embs.append(faces[0].normed_embedding.astype(float))
    return embs


def unit(v) -> np.ndarray:
    v = np.mean(v, axis=0)
    return v / np.linalg.norm(v)


def mine(model, ref: np.ndarray, videos: list[str], thresh: float = 0.45, per_s: float = 4,
         min_score: float = 0.6, min_size: int = 40) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Faces in `videos` that look like `ref` -> (embeddings, 112 px crops). Nothing else is kept."""
    embs, crops = [], []
    for path in videos:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise SystemExit(f"cannot open {path}")
        step, i = max(1, round((cap.get(cv2.CAP_PROP_FPS) or 30) / per_s)), 0
        while cap.grab():
            if i % step == 0:
                frame = cap.retrieve()[1]
                for f in model.get(frame):
                    x1, y1, x2, y2 = f.bbox.astype(int)
                    if f.det_score < min_score or min(x2 - x1, y2 - y1) < min_size:
                        continue
                    if float(f.normed_embedding @ ref) >= thresh:
                        embs.append(f.normed_embedding.astype(float))
                        crop = frame[max(0, y1):y2, max(0, x1):x2]
                        crops.append(cv2.resize(crop, (112, 112)))
            i += 1
        cap.release()
    return embs, crops


def contact_sheet(crops: list[np.ndarray], path: str, cols: int = 10) -> None:
    rows = [crops[i:i + cols] for i in range(0, len(crops), cols)]
    rows[-1] = rows[-1] + [np.zeros_like(crops[0])] * (cols - len(rows[-1]))
    cv2.imwrite(path, np.vstack([np.hstack(r) for r in rows]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photos", nargs="+")
    ap.add_argument("--label", required=True)
    ap.add_argument("--category", required=True, choices=["missing_person", "wanted_offender"])
    ap.add_argument("--legal-basis", required=True, help="FIR / court order / missing-person report / consent form")
    ap.add_argument("--consent-ref", required=True, help="reference to the signed consent (demo enrolments)")
    ap.add_argument("--days", type=int, default=7, help="entry expires after this many days")
    ap.add_argument("--model", default="buffalo_sc")
    ap.add_argument("--video", action="append", default=[], help="extra footage of the same person (repeatable)")
    ap.add_argument("--mine-thresh", type=float, default=0.45, help="similarity to the photos to accept a video face")
    ap.add_argument("--sheet", help="save the accepted video faces here, to check by eye before trusting them")
    ap.add_argument("--dry-run", action="store_true", help="build the template but don't enroll")
    a = ap.parse_args()
    if len(a.photos) > 5:
        raise SystemExit("use at most 5 photos")
    if not 1 <= a.days <= 90:
        raise SystemExit("--days must be between 1 and 90")

    model = fp.load_model(a.model, det_thresh=0.5)  # enrolment wants only clear faces
    embs = photo_embeddings(model, a.photos)
    mean = unit(embs)
    worst = min(float(e @ mean) for e in embs)
    if len(embs) > 1 and worst < 0.5:
        raise SystemExit(f"photos don't look like the same person (lowest similarity {worst:.2f})")

    if a.video:
        video_model = fp.load_model(a.model, det_size=1920, det_thresh=0.5)  # small faces in 4K crowd footage
        mined, crops = mine(video_model, mean, a.video, a.mine_thresh)
        print(f"found the person {len(mined)} times in {len(a.video)} video(s)")
        if crops and a.sheet:
            contact_sheet(crops, a.sheet)
            print(f"accepted faces saved to {a.sheet}; check they are all the same person")
        mean = unit(embs + mined)
    if a.dry_run:
        print("dry run: template built, nothing enrolled")
        return

    r = fp.Backend.from_env("COMMANDER").add_watchlist(
        label=a.label, category=a.category, legal_basis=a.legal_basis, consent_ref=a.consent_ref,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=a.days)).isoformat(), embedding=mean.tolist(),
    )
    print(f"enrolled {r['label']!r} as {r['id']} ({r['category']}), expires {r['expires_at']}")


if __name__ == "__main__":
    main()
