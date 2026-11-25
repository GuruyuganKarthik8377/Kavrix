"""Privacy audit: re-run the face detector on a blurred output video and report any face that is
still detectable, other than the red-boxed watchlist matches and faces already pixelated (the
detector still sees a face shape in the blocks). Run:
  uv run python scripts/audit_output.py out/blurred.mp4
AUDIT_DET_SIZE=1280 looks harder for small faces (use it on 4K footage)."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import facepriv as fp  # noqa: E402

def pixelated(crop: np.ndarray) -> bool:
    """Blocks are flat inside: most pixels barely differ from their neighbours (>= 0.54 measured on
    worker output, <= 0.42 on real faces)."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.int16)
    d = np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]
    return d.size > 0 and float((d <= 2).mean()) >= 0.5


path = sys.argv[1] if len(sys.argv) > 1 else "out/blurred.mp4"
model = fp.load_model(det_size=int(os.getenv("AUDIT_DET_SIZE", 640)), det_thresh=float(os.getenv("AUDIT_THRESH", 0.3)))
cap = cv2.VideoCapture(path)
n = leaks = 0
leak_frames = []
while True:
    ok, frame = cap.read()
    if not ok:
        break
    red = (frame[:, :, 2] > 200) & (frame[:, :, 1] < 60) & (frame[:, :, 0] < 60)  # match box outline
    for f in model.get(frame):
        x1, y1, x2, y2 = np.clip(f.bbox.astype(int), 0, [frame.shape[1], frame.shape[0]] * 2)
        ring = red[max(0, y1 - 15):y2 + 15, max(0, x1 - 15):x2 + 15]
        if ring.sum() < 40 and not pixelated(frame[y1:y2, x1:x2]):  # not matched, not blurred -> exposed
            leaks += 1
            leak_frames.append(n)
    n += 1
print(f"{path}: {n} frames, {leaks} exposed faces" + (f" in frames {sorted(set(leak_frames))}" if leaks else ""))
sys.exit(1 if leaks else 0)
