# AI: Face and Privacy

Team Haven · CipherCop 2026 · Track 1

This worker finds faces in a drone feed. It checks each one against the lawful watchlist through the Haven backend and **pixelates every face that isn't a match**. The privacy-safe video is uploaded as signed evidence segments. A match never raises an alert by itself: it creates a review that an officer has to confirm.

```
drone feed ─▶ detect faces (InsightFace) ─▶ track ─▶ quality gate ─▶ POST /watchlist/match ─▶ backend
                                              │                       (embedding only, nothing stored on no-match)
                                              ▼
                     pixelate everything except confirmed-by-backend matches ─▶ out/blurred.mp4
                                                                            └─▶ POST /segments (signed evidence)
```

## Privacy rules (enforced in `facepriv.py` and tested in `tests/test_privacy.py`)

1. **Blurred by default.** A face is shown only after the backend says it matched the watchlist. Faces that are unknown, still being checked, not matched, or briefly lost by the detector are all pixelated. If the backend is down, everyone stays blurred.
2. **Blur faces, match strictly.** Any face the detector scores at 0.45 or more is blurred. Faces cut off by the left, right or top edge are blurred from 0.3, because half faces score low. Weaker detections in the middle of the frame (backs of heads, shirts, jeans) are left alone. A lost face stays blurred for 0.2 s, but only if it was seen in at least 2 frames, so no stale blocks drift onto bodies. Only clear faces (score 0.6 or more, at least 40 px) are sent for matching, because poor embeddings are what cause false matches.
3. **Nothing kept for non-matches.** An embedding exists only for one request. No face crops, embeddings or images are written to disk. The backend stores nothing when there's no match.
4. **Pixelation, not Gaussian blur.** Each padded face box is reduced to an 8×8 grid of blocks. A Gaussian blur can be partly reversed.
5. **Human in the loop.** A match shows as `WATCHLIST 96% - PENDING OFFICER REVIEW` and creates a pending review. An alert is raised only when an officer confirms it with `POST /reviews/{id}`.
6. **Limited querying.** Each tracked face is checked at most 3 times, 3 s apart. A matched face is never checked again, which avoids duplicate reviews.
7. **Consent-checked enrolment.** `enroll.py` rejects any photo with more than one face. Each entry needs a legal basis and a consent reference, and it expires.

## Setup

From the project root, `python3 setup_haven.py --face` (Windows: `python setup_haven.py --face`) creates `ai/face/.venv`, installs InsightFace, and writes `ai/face/.env` with the worker and commander test logins. The first run of the worker downloads the InsightFace `buffalo_sc` model (about 15 MB) to `~/.insightface`.

The backend must be running at `HAVEN_API` (default `http://localhost:8000`). See [`../../backend/README.md`](../../backend/README.md).

In the examples below, `uv run python` can be replaced by `.venv/bin/python` (Windows: `.venv\Scripts\python`) when you run them from this folder.

## Use

```bash
# 1. Enroll a consenting team member (1-5 photos, one face each)
uv run python enroll.py --label "Harish (team consent)" --category missing_person \
    --legal-basis "Demo: consent form HV-01" --consent-ref HV-01 --days 7 me1.jpg me2.jpg

# 2. Run on a drone clip, webcam (0) or rtsp:// URL
uv run python worker.py --source drone.mp4 --drone D1 --lat 17.3630 --lng 78.4725

# 3. Check the output yourself: re-detect faces on the blurred video
uv run python scripts/audit_output.py out/blurred.mp4

# 1b. Stronger template: also learn the person's look from video (angles, distance, light).
#     Their faces are found by similarity to the photos; every other face is dropped at once.
uv run python enroll.py ... me1.jpg me2.jpg --video clip1.mov --video clip2.mov --sheet out/enrolled_samples.jpg

# Identify enrolled people in still images (everyone else pixelated) -> out/identified/
uv run python identify.py photo1.jpg photo2.jpg

# No backend? Pixelate everyone (handy for making the demo video)
uv run python worker.py --source drone.mp4 --blur-only
```

No footage yet? `uv run python scripts/make_sample_video.py` builds `samples/sample.mp4`, an 8 s pan across InsightFace's bundled 6-person group photo. It also writes `samples/enroll_face.jpg`, one face from that photo, to stand in for a consented photo.

### `worker.py` options

| Flag | Default | What it does |
|---|---|---|
| `--source` | (required) | Video file, webcam index or RTSP URL |
| `--drone`, `--lat`, `--lng` | D1, Charminar | Drone id and position sent with each match |
| `--out` | `out/blurred.mp4` | Privacy-safe output (H.264, plays in browsers) |
| `--segment-s` | 10 | Length of each uploaded evidence segment (0 = no upload) |
| `--blur-thresh` | 0.45 | Detector confidence at which a face is blurred. Faces cut off by the left, right or top edge are blurred from 0.3, since half faces score low. |
| `--min-score`, `--min-size` | 0.6, 40 | Quality needed before a face is sent for matching |
| `--model` | `buffalo_sc` | `buffalo_l` is more accurate but about 20× larger and slower |
| `--blur-only` | off | Skip the backend and pixelate every face |

## Tested results

- `uv run python tests/test_privacy.py` (offline): pixelation, tracking, the fail-safe for briefly lost faces, the quality gate, the query cap, behaviour when the backend is down, blur-only mode, and that tracks never keep an embedding.
- End to end against the live backend on the sample clip (200 frames, 6 people, CPU at 16 fps):
  - The enrolled face was matched at 96% confidence and became 1 pending review.
  - The other 5 people were pixelated.
  - 2 signed evidence segments were uploaded and both verified.
  - The evidence chain verified.
- Enrolment rejected the 6-person group photo.
- **Learning from video, on four 4K campus clips of a consenting volunteer (2 photos):**
  - **Held-out test:** trained on 2 clips, tested on a third. With the photos alone, recall at the 0.5 threshold was 84% and his mean similarity 0.63. With the 46 faces taken from video, recall was 100% and mean similarity 0.73.
  - **Bystanders:** the closest of about 215 bystander faces scored 0.29. The fourth clip doesn't show him, and nobody in it matched.
  - **Contact sheet:** all 62 faces accepted from video, checked by eye, were him.
  - **`identify.py` on a crowd frame:** he was identified at 85% and sent for review, and the other 29 faces were pixelated.
- The audit at 0.3 flagged no real faces, only a shirt and a hand.

## Campus test (recorded)

The video-learning enrolment and the worker were run on four real campus clips with a consenting volunteer. The
combined recording, the numbers and how it was made are in
[`recorded/campus/`](recorded/campus/README.md). The dashboard's **Campus test** page shows it, and the backend can replay its
matches into the live review flow (`POST /demo/face-replay/campus`). Extra tools:

| Tool | What |
|---|---|
| `worker.py --report out.json` | Writes counts and every match with its video time and box. |
| `scripts/audit_output.py` | Re-detects faces in a pixelated video and flags any that are left (`AUDIT_DET_SIZE=1280` looks harder for small faces). |

## Known limits

- **Video timing.** Matches are sent as soon as a face is seen, so a review isn't linked to the evidence segment that contains it. Find the segment by drone and time. Linking them needs a small backend change.
- **Edge slivers.** A face that is mostly outside the frame, such as a chin at the top edge, can't be detected, so that sliver isn't blurred. It isn't identifiable.
- **Threshold tuning.** `MATCH_THRESHOLD` (0.5 cosine, set in the backend) is tuned for `buffalo_sc`. Check it on your own team photos and drone footage before the demo.
- **Model licence.** InsightFace's pretrained models are licensed for non-commercial research only. That's fine for a hackathon, but production needs a licensed model.
- **No live preview window**, because headless OpenCV has none. Watch `out/blurred.mp4`, or play the segments on the dashboard.
