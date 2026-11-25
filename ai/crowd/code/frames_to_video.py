#!/usr/bin/env python3
"""
frames_to_video.py - turn DroneCrowd / VisDrone-CC frame sequences into MP4 files
(no ffmpeg needed, only OpenCV).

  python frames_to_video.py data/visdrone_cc/VisDrone2020-CC/sequences/00100 clips/seq100.mp4
  python frames_to_video.py DATASET/sequences clips --seqs 00100 00054 00046 --fps 5 --loops 2

First argument: one sequence folder, or the `sequences` folder together with --seqs.
Second argument: an output .mp4 file (one sequence) or an output folder (several).

The VisDrone-CC frames are about every 10th frame of the original video, so
motion between frames is large. --fps 5 plays 30 frames in 6 s; use --loops to
repeat a sequence for a longer demo (the jump back to frame 1 is a visible cut,
so keep loops small and say so in your pitch).
"""
import argparse
import sys
from pathlib import Path

import cv2


def convert(seq_dir, out_path, fps=5.0, loops=1, width=0):
    frames = sorted(p for p in Path(seq_dir).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not frames:
        raise SystemExit(f"no frames in {seq_dir}")
    first = cv2.imread(str(frames[0]))
    h, w = first.shape[:2]
    if width and width < w:
        h, w = int(round(h * width / w)), width
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = 0
    for _ in range(max(1, loops)):
        for p in frames:
            img = cv2.imread(str(p))
            if img.shape[1] != w:
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            writer.write(img)
            n += 1
    writer.release()
    return n, w, h


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a sequence folder, or the 'sequences' folder when using --seqs")
    ap.add_argument("out", help="output .mp4 (one sequence) or output folder (with --seqs)")
    ap.add_argument("--seqs", nargs="*", help="sequence ids, e.g. 00100 00054")
    ap.add_argument("--fps", type=float, default=5.0, help="playback fps (default 5)")
    ap.add_argument("--loops", type=int, default=1, help="repeat the sequence N times")
    ap.add_argument("--width", type=int, default=0, help="downscale to this width (0 = keep 1920)")
    a = ap.parse_args(argv)

    if a.seqs:
        for s in a.seqs:
            out = Path(a.out) / f"seq{s}.mp4"
            n, w, h = convert(Path(a.source) / s, out, a.fps, a.loops, a.width)
            print(f"{out}: {n} frames, {w}x{h}, {n / a.fps:.1f}s")
    else:
        out = Path(a.out)
        if out.suffix.lower() != ".mp4":
            sys.exit("when no --seqs is given, 'out' must be a .mp4 file")
        n, w, h = convert(a.source, out, a.fps, a.loops, a.width)
        print(f"{out}: {n} frames, {w}x{h}, {n / a.fps:.1f}s")


if __name__ == "__main__":
    main()
