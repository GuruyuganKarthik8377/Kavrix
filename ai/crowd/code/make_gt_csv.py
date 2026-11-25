#!/usr/bin/env python3
"""
make_gt_csv.py - turn ground-truth files into a t,gt CSV for plot_results.py.

  # Jülich trajectories (people with a track at each frame):
  python make_gt_csv.py juelich data/juelich_crowdqueue/traj/x/030_c_56_h0.txt eval_out/gt_jl030.csv

  # DroneCrowd full version (GT_img<seq><frame>.mat files; needs scipy):
  python make_gt_csv.py dronecrowd data/dronecrowd_full/test/gt/test_data/ground_truth 023 eval_out/gt_dc023.csv
"""
import argparse
import csv
import re
from collections import Counter
from pathlib import Path


def write_csv(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "gt"])
        w.writerows(rows)


def juelich(txt, fps):
    """PeTrack trajectory file: '# ...' header, then 'id frame x y z'."""
    per_frame = Counter()
    for line in Path(txt).read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        per_frame[int(parts[1])] += 1
    return [(round(f / fps, 3), per_frame[f]) for f in sorted(per_frame)]


def dronecrowd(gt_dir, seq, fps):
    import scipy.io as io  # imported here: only needed for this mode
    rows = []
    for f in sorted(Path(gt_dir).glob(f"GT_img{seq}*.mat")):
        frame = int(re.search(r"img\d{3}(\d{3})\.mat", f.name).group(1))
        info = io.loadmat(f, struct_as_record=False, squeeze_me=True)["image_info"]
        rows.append((round((frame - 1) / fps, 3), int(info.number)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="kind", required=True)
    j = sub.add_parser("juelich")
    j.add_argument("txt")
    j.add_argument("out")
    j.add_argument("--fps", type=float, default=25.0)
    d = sub.add_parser("dronecrowd")
    d.add_argument("gt_dir")
    d.add_argument("seq", help="3-digit sequence id, e.g. 023")
    d.add_argument("out")
    d.add_argument("--fps", type=float, default=25.0, help="assumed frame rate (not stated by the dataset)")
    a = ap.parse_args(argv)
    rows = juelich(a.txt, a.fps) if a.kind == "juelich" else dronecrowd(a.gt_dir, a.seq, a.fps)
    write_csv(a.out, rows)
    print(f"wrote {a.out}: {len(rows)} rows")


if __name__ == "__main__":
    main()
