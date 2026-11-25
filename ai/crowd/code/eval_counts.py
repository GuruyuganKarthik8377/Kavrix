#!/usr/bin/env python3
"""
eval_counts.py - how accurate are crowd_risk's people counts?

Compares the detector's counts (and local peak density) with the ground-truth head
annotations of VisDrone2020-CC / DroneCrowd (82 annotated sequences, 30 frames each).

  python eval_counts.py data/visdrone_cc/VisDrone2020-CC \
      --model models/yolov8s-visdrone/best.pt --classes 0,1 --imgsz 1920 --conf 0.15 --device mps

Outputs (in --out, default eval_out/): per_frame.csv and summary.json, plus a printed table
and a one-line sentence you can paste into the deck.

Ground truth format: annotations/<seq>.txt, one head per line: frame,x,y (frame is 1-based
and matches sequences/<seq>/<frame:05d>.jpg).
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

import crowd_risk as cr


def load_annotations(path):
    """-> {frame_number: (N,2) float array of head points}"""
    frames = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3:
            frames[int(parts[0])].append((float(parts[1]), float(parts[2])))
    return {k: np.asarray(v, np.float32) for k, v in frames.items()}


def pick_frames(available, n):
    """n frames spread evenly over the available frame numbers."""
    available = sorted(available)
    if n >= len(available):
        return available
    idx = np.linspace(0, len(available) - 1, n).round().astype(int)
    return [available[i] for i in idx]


def ranks(a):
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a), float)
    r[order] = np.arange(len(a))
    for v in np.unique(a):               # average the ranks of ties
        m = a == v
        r[m] = r[m].mean()
    return r


def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a, b):
    return pearson(ranks(np.asarray(a, float)), ranks(np.asarray(b, float)))


def summarize(gt, det, gt_peak, det_peak):
    gt, det = np.asarray(gt, float), np.asarray(det, float)
    ok = gt > 0
    ratio = det[ok] / gt[ok]
    out = {
        "frames": int(len(gt)),
        "mae": round(float(np.abs(det - gt).mean()), 1),
        "mape_pct": round(float(100 * (np.abs(det[ok] - gt[ok]) / gt[ok]).mean()), 1),
        "mean_detected_over_true": round(float(ratio.mean()), 2),
        "median_detected_over_true": round(float(np.median(ratio)), 2),
        "pearson_count": round(pearson(gt, det), 3),
        "spearman_count": round(spearman(gt, det), 3),
        "spearman_peak_density": round(spearman(gt_peak, det_peak), 3),
    }
    bins = {"gt<100": gt < 100, "gt 100-250": (gt >= 100) & (gt < 250), "gt>=250": gt >= 250}
    out["by_density"] = {
        name: {"frames": int(m.sum()),
               "mean_detected_over_true": round(float((det[m & ok] / gt[m & ok]).mean()), 2)}
        for name, m in bins.items() if (m & ok).any()
    }
    return out


def evaluate(root, detector, seqs=None, frames_per_seq=3):
    root = Path(root)
    rows = []
    ann_files = sorted((root / "annotations").glob("*.txt"))
    for ann in ann_files:
        seq = ann.stem
        if seqs and seq not in seqs:
            continue
        gt = load_annotations(ann)
        for fn in pick_frames(gt.keys(), frames_per_seq):
            img = cv2.imread(str(root / "sequences" / seq / f"{fn:05d}.jpg"))
            if img is None:
                continue
            boxes = np.asarray(detector.detect(img, fn), np.float32).reshape(-1, 4)
            pts = cr.box_centers(boxes)
            rows.append({
                "seq": seq, "frame": fn, "gt_count": len(gt[fn]), "det_count": len(boxes),
                "gt_peak": round(float(cr.density_map(gt[fn], img.shape).max()), 2),
                "det_peak": round(float(cr.density_map(pts, img.shape).max()), 2),
            })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="dataset folder containing annotations/ and sequences/")
    ap.add_argument("--out", default="eval_out")
    ap.add_argument("--frames-per-seq", type=int, default=3)
    ap.add_argument("--seqs", nargs="*", help="only these sequence ids, e.g. 00054 00046")
    ap.add_argument("--model", default="yolov8s.pt")
    ap.add_argument("--classes", default="0")
    ap.add_argument("--imgsz", type=int, default=1920)
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--tile", type=int, default=0)
    ap.add_argument("--max-det", type=int, default=3000)
    ap.add_argument("--iou", type=float, default=None)
    ap.add_argument("--augment", action="store_true")
    ap.add_argument("--clahe", action="store_true")
    ap.add_argument("--device", default=None)
    a = ap.parse_args(argv)

    classes = [int(c) for c in a.classes.split(",") if c.strip()]
    det = cr.YoloDetector(a.model, a.imgsz, a.conf, classes, a.device, a.tile, 0.2, a.max_det,
                          a.iou, a.augment, a.clahe)
    rows = evaluate(a.root, det, set(a.seqs) if a.seqs else None, a.frames_per_seq)
    if not rows:
        raise SystemExit("no frames evaluated: check the dataset path")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "per_frame.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    s = summarize([r["gt_count"] for r in rows], [r["det_count"] for r in rows],
                  [r["gt_peak"] for r in rows], [r["det_peak"] for r in rows])
    s["settings"] = {"model": a.model, "classes": classes, "imgsz": a.imgsz, "conf": a.conf,
                     "tile": a.tile, "frames_per_seq": a.frames_per_seq, "iou": a.iou,
                     "augment": a.augment, "clahe": a.clahe}
    (out / "summary.json").write_text(json.dumps(s, indent=2))
    print(json.dumps(s, indent=2))
    print(f"\nPaste-ready: on {s['frames']} frames from VisDrone-CC the detector finds a median of "
          f"{100 * s['median_detected_over_true']:.0f}% of the true crowd; its counts rank scenes "
          f"correctly (Spearman {s['spearman_count']:.2f}) and its density ranking agrees with the "
          f"ground truth (Spearman {s['spearman_peak_density']:.2f}).")
    print(f"Wrote {out}/per_frame.csv and summary.json")


if __name__ == "__main__":
    main()
