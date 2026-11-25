"""Build a test clip from InsightFace's bundled group photo (no real footage needed):
samples/sample.mp4 is a slow pan/zoom across 6 faces; samples/enroll_face.jpg is one of them,
cropped, to stand in for a consented team photo. Run: uv run python scripts/make_sample_video.py"""
import os
import sys

import cv2
import numpy as np
from insightface.data import get_image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import facepriv as fp  # noqa: E402

os.makedirs("samples", exist_ok=True)
img = get_image("t1")
H, W = img.shape[:2]
out_w, out_h, fps, seconds = 960, 540, 25, 8

writer = fp.open_writer("samples/sample.mp4", fps, (out_w, out_h))
for i in range(fps * seconds):
    t = i / (fps * seconds - 1)
    zoom = 1.0 + 0.35 * np.sin(np.pi * t)  # in then out
    cw, ch = int(W / zoom), int(W / zoom * out_h / out_w)
    ch = min(ch, H)
    x = int((W - cw) * t)
    y = (H - ch) // 2
    writer.write(cv2.resize(img[y:y + ch, x:x + cw], (out_w, out_h), interpolation=cv2.INTER_LINEAR))
writer.release()

face = max(fp.load_model().get(img), key=lambda f: f.det_score)
x1, y1, x2, y2 = face.bbox.astype(int)
m = (x2 - x1) // 2
cv2.imwrite("samples/enroll_face.jpg", img[max(0, y1 - m):y2 + m, max(0, x1 - m):x2 + m])
print(f"wrote samples/sample.mp4 ({fps * seconds} frames) and samples/enroll_face.jpg (face at {x1},{y1})")
