# AI: Crowd and Scene (crowd-crush risk)

Team Haven, Track 1.

`crowd_risk.py` takes a drone video. It outputs a crowd-crush risk score from 0 to 100 every second, a density heatmap, an annotated video, and JSON events for the backend.

## How it works

1. **Detect people**: YOLOv8 finds people (class 0) in each frame.
2. **Density**: detections are turned into a heatmap. Its peak is the number of people packed into one local window. A lone person scores about 1, and 12 people shoulder to shoulder score about 12.
3. **Motion (with camera compensation)**: the drone's own movement (pan, rotation, zoom) is estimated from background features. It is subtracted from dense optical flow, so only the crowd's own movement counts.
4. **Risk** = `100 × d × (0.7 + 0.3 × m)`, where `d = density / D_CRIT` and `m = motion / MAG_CRIT`, both capped at 1. The score is smoothed over time. Levels: **LOW** < 35 ≤ **MEDIUM** < 65 ≤ **HIGH**.

Density alone at the critical level gives 70 (HIGH). Erratic crowd movement on top of that pushes the score toward 100.

## Install

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python test_risk.py        # prints "ok" (no model download needed)
```

On first run, ultralytics downloads the YOLO weights automatically.

## Run

```bash
python crowd_risk.py clip1.mp4 --out out1
```

| Option | Default | Use |
|---|---|---|
| `--model` | `yolov8s.pt` | `yolov8m.pt` or a VisDrone-trained model if people are missed |
| `--imgsz` | `1280` | `1920` for tiny people seen from high altitude |
| `--tile` | off | `480` runs YOLO on crops to find tiny people (slower, finds far more) |
| `--classes` | `0` | `0,1` for VisDrone-trained models (pedestrian, people) |
| `--d-crit` | `10` | lower if risk stays near 0, raise if it sits near 100 |
| `--mag-crit` | `2.0` | raise if a moving drone causes false motion |
| `--detect-every` | `1` | `2` or `3` for speed on CPU |
| `--max-seconds`, `--start` | full clip | cut a 20-second demo segment |
| `--api` | none | POST each event to the backend, e.g. `http://localhost:8000/events`. Sent from a background thread in order, each retried with backoff (0.5, 1, 2, 4 s), so a slow or down backend never stalls the pipeline; `summary.json` reports `api: {sent, failed, dropped, pending}` and `events.jsonl` is always complete |
| `--run-id`, `--drone-id` | `<video>-<random>`, `D-01` | identity of this stream; they are inside the hash |
| `--lat`, `--lng` | Hyderabad | incident location shown on the map |
| `--device` | auto | `mps` on Apple Silicon, `cpu`, or `0` for a GPU |
| `--realtime` | off | pace processing to the video's clock like a live feed, so events reach the dashboard one per `--event-every` s (`--speed 2` plays at 2x). `summary.json` reports `max_lag_s`, which is above 0 if the machine cannot keep up |
| `--alert-hold` | `3` | seconds risk must stay HIGH before a `sustained_high` alert |
| `--surge-rate` | `8` | risk points per second (over 2 s) that trigger a `surge` alert |
| `--alert-cooldown`, `--alert-warmup` | `10`, `3` | min seconds between surge alerts; no alerts in the first 3 s |

## Use a drone-trained model (important)

The default `yolov8s.pt` (COCO) is trained on people seen from the side. On top-down drone
footage it misses almost everyone (0 detections on a crowd of thousands in our test).
Use a VisDrone-trained model instead:

- Weights: [mshamrai/yolov8s-visdrone](https://huggingface.co/mshamrai/yolov8s-visdrone) `best.pt` (22.5 MB, OpenRAIL), saved to `models/yolov8s-visdrone/best.pt`.
- Classes: `0,1` (pedestrian, people).
- `--max-det` defaults to 3000; ultralytics' own default of 300 silently caps dense crowds.

Settings that gave a LOW → MEDIUM → HIGH ramp on the Pexels concert clip:

```bash
python crowd_risk.py data/pexels/concert_city_park/pexels_34004639_concert_1080p.mp4 \
  --out out_concert --model models/yolov8s-visdrone/best.pt --classes 0,1 \
  --imgsz 1920 --conf 0.15 --detect-every 2 --max-seconds 14 --d-crit 40 --device mps
```

## Accuracy (measured)

`eval_counts.py` compares detected counts with the ground-truth head annotations of the 82
annotated VisDrone-CC sequences (3 frames each = 246 frames, 1920x1080):

```bash
python eval_counts.py data/visdrone_cc/VisDrone2020-CC --model models/yolov8s-visdrone/best.pt \
  --classes 0,1 --imgsz 1920 --conf 0.10 --device mps
```

| Setting | MAE (people) | MAPE | Median detected / true | Spearman (counts) |
|---|---|---|---|---|
| `--conf 0.10` (best) | 51 | 32% | 0.89 | 0.83 |
| `--conf 0.15` | 54 | 33% | 0.70 | 0.82 |
| `--conf 0.25` | 77 | 51% | 0.47 | 0.74 |
| `--conf 0.05` | 63 | 45% | 1.20 (overcounts) | 0.83 |
| `--tile 480 --conf 0.15` | 51 | 35% | 0.80 | 0.75 |

### Can detection be improved? Experiments (no new downloads)

Same 246 frames, VisDrone model, conf 0.10 unless stated (`eval_out/experiments.csv`):

| Setting | Time (246 frames) | MAE | MAPE | Median detected/true | Spearman (counts / density) |
|---|---|---|---|---|---|
| baseline, `--imgsz 1920` | 19 s | 51.4 | 31.6% | 0.89 | 0.831 / 0.694 |
| **`--imgsz 2560`** | 36 s | **44.7** | **28.5%** | 0.89 | **0.849 / 0.737** |
| `--imgsz 2560 --conf 0.15` | 40 s | 48.0 | 30.9% | 0.70 | 0.839 / 0.749 |
| contrast boost `--clahe` | 23 s | 54.9 | 34.1% | 0.75 | 0.814 / 0.683 |
| NMS overlap `--iou 0.8` | 18 s | 81.4 | 55.7% | 1.20 | 0.771 / 0.589 |
| NMS overlap `--iou 0.9` | 19 s | 149.1 | 104.8% | 1.52 | 0.677 / 0.410 |
| `--iou 0.8 --conf 0.15` | 18 s | 64.2 | 39.8% | 0.89 | 0.757 / 0.578 |
| test-time augmentation `--augment` | 45 s | 66.4 | 47.0% | 1.15 | 0.818 / 0.689 |

- **Only a larger input size helped**, and modestly: the mean error per sequence fell by 6.6 people (95%
  bootstrap interval -10.3 to -3.3; 51 of 82 sequences improved, 30 got worse), and the density ranking,
  which the risk score depends on, rose from 0.694 to 0.737. The median detected/true stays 0.89, so it
  tightens the errors rather than removing the undercount. It is about 2x slower.
- **Higher NMS overlap and augmentation made it worse**: they add duplicate boxes, so the crowd is
  overcounted (median 1.15 to 1.52).
- **Contrast boost did nothing useful.**
- Checked on a tuning half and a held-out half of the sequences (41 each); every conclusion held on both
  halves, except the contrast boost, which was neutral on one and worse on the other.
- **The demo runs were not changed.** They use `--imgsz 1920`, and `--d-crit` was tuned at that setting; using
  2560 would need `--d-crit` re-tuned. Use `--imgsz 2560` for offline accuracy runs, not for `--realtime`.
- Not tried: a bigger model (`yolov8m-visdrone`, needs a download), fine-tuning on VisDrone-CC, a
  density-map counter such as DM-Count, and tracking across frames.

At `--conf 0.10` the detector finds about 89% of the true crowd (median) with a mean absolute error
of 51 people (32%), and it ranks scenes by crowd size well (Spearman 0.83) and by local density
less well (0.69). Per-scene error is large: individual sequences range from about 0.3x to 1.6x
the true count, and seated rows seen from above are missed most. Results are from VisDrone-CC
frames only; they do not apply to other footage. Details are in `eval_out/` (`per_frame.csv`, `summary.json`).

Use a similar confidence for your own clips (0.10 to 0.15) and check the count by eye.

## Controlled crowd-crush experiment (Jülich), special-purpose detector

Standard detectors (COCO and VisDrone YOLO) fail on overhead fisheye footage of people in caps
(found 2-27 of ~75 people). For the Jülich "crowds in front of bottlenecks" experiment
(Wuppertal 2018, overhead camera, 75 volunteers wearing orange caps pressing toward a 0.5 m gate)
we added `--detector orange`, a colour-based cap detector. It is NOT a general person detector.

```bash
python crowd_risk.py data/juelich_crowdqueue/x/030_c_56_h0.mp4 --out out_jl030 \
  --detector orange --d-crit 7 --mag-crit 1 --event-every 5
```

Result on run 030: 75 people counted at the start (75 participants), falling to 8 as the crowd
passes the gate; risk is HIGH (64-78) while packed and falls to LOW at 60 s.
Caveats: `--d-crit 7` was tuned on this clip (so the levels are illustrative, not a validation);
the clip shows a crowd clearing, not building up; it is an indoor controlled experiment with
volunteers, not drone footage; brief double counts push the count to 83 at times.

Checked against the archive's per-person trajectories (one frame per second, GT = number of
people with a track at that frame): runs 030 and 040 (75 participants each) give a correlation of
0.995 / 0.997 between detected and true counts and a mean absolute error of 4.8 people. The
detector over-counts by about 5 on average (mean detected/true 1.19-1.23). Our likely
explanation, not verified: people who have already passed the gate are still visible in the
frame wearing caps, while their trajectories have ended. Only 33-34% of seconds are within 10% of
the true count because of that offset.

Source: Adrian, Boltes, Holl, Sieben, Seyfried, Forschungszentrum Jülich Pedestrian Dynamics
Data Archive, DOI 10.34735/ped.2018.1 (free to use with attribution). Videos are not included here.

## Outputs (in `--out`)

- `annotated_clean.mp4` (with `--clean-video`): the same view without the status card, used by `compose_demo.py`
- `annotated.mp4`: boxes, heat overlay and the risk HUD. Written as H.264 (`avc1`) so a browser can play it in the dashboard's drone-feed panel; the older mp4v codec is not playable in browsers. If H.264 is unavailable the script warns and falls back, and `summary.json` records `video_codec`. The file is large (25-35 Mbit/s: about 40 MB for 14 s at 1080p)
- `events.jsonl`: one event per second
- `heatmaps/event_NNNN.jpg`: the annotated frame for each event
- `summary.json`: max risk, people counts, seconds at each level, processing speed

## Event contract (used by the backend and the frontend)

```json
{
  "id": "4b5422c02acd",
  "type": "crowd_risk",
  "schema_version": 1,
  "run_id": "concert-pexels-34004639",
  "drone_id": "D-01",
  "seq": 12,
  "lat": 17.385, "lng": 78.4867,
  "risk": 72.4,
  "level": "HIGH",
  "count": 38,
  "density_peak": 11.3,
  "motion": 0.8,
  "risk_rate": 2.5,
  "time_to_high": null,
  "hotspot": {"x": 0.62, "y": 0.41},
  "timestamp": "2026-11-20T10:15:02.123+00:00",
  "video_t": 12.0,
  "heatmap": "heatmaps/event_0012.jpg",
  "evidence_hash": "sha256 of the heatmap file",
  "prev_hash": "event_hash of the previous event (64 zeros for the first)",
  "event_hash": "sha256 of this event's own contents, including prev_hash"
}
```

The first seven fields plus `timestamp` and `evidence_hash` follow the shared contract. `level`, `count`, `density_peak`, `motion`, `video_t`, `heatmap`, `risk_rate` and `hotspot` are extra fields (additions only, nothing was renamed).

- `risk_rate`: risk points per second over the last 2 s (positive = rising).
- `schema_version`, `run_id`, `drone_id`, `seq`: which format version, which recording or stream, which drone, and the
  event's position in its run (0, 1, 2, ... with no gaps, so a lost event is visible). The hash chain restarts at an
  all-zero `prev_hash` for every `run_id`. Set them with `--run-id` and `--drone-id`.
- `time_to_high`: estimated seconds until risk reaches HIGH (65) if it keeps rising at `risk_rate`, or `null`
  (see "Time-to-HIGH forecast" below). An estimate, never a prediction.
- `hotspot`: where the densest spot is, as fractions of the frame (`x` from the left, `y` from the top, 0-1). `null` if nobody is detected. It is a position in the video frame, not a map coordinate; the map pin still uses `lat`/`lng`.

### Alert events (new `type`)

When something needs an officer, the script also writes a `crowd_alert` event, with the same fields plus:

```json
{
  "type": "crowd_alert",
  "alert_kind": "sustained_high",
  "message": "Crowd density critical: risk 73/100 held for 3+ s (73 people detected). Send officers to the hotspot.",
  "heatmap": "heatmaps/alert_0001.jpg"
}
```

- `sustained_high`: risk stayed HIGH for `--alert-hold` seconds. Fires once and re-arms only after risk drops below MEDIUM.
- `surge`: risk rose at least `--surge-rate` points/s while already MEDIUM or above (at most one per 10 s).
- No alerts in the first 3 s (readings are still settling). A steady crowd raises none: DroneCrowd seq 023 gives 0 alerts.
- `crowd_alert` is a second event `type` next to `crowd_risk`; the backend in this repo accepts both. Alerts are written to `events.jsonl` and POSTed to `--api` like any other event.

### Tamper-evident log (hash chain)

Every event, alerts included, carries `prev_hash` and `event_hash`, so `events.jsonl` is a hash chain:
each event commits to the one before it, and to its snapshot image through `evidence_hash`.
`summary.json` also records `chain_events` and `chain_head` (the last hash).

```bash
python verify_events.py out_jl030                 # VERIFIED, or a list of exactly what was changed
python verify_events.py out_jl030 --demo          # tamper with copies and show every change is caught
python verify_events.py out_jl030 --expect-head <trusted chain_head>
```

`verify_events.py` uses only the Python standard library, so an auditor can run it with no installs.
It catches: an edited value, a deleted / inserted / re-ordered event, a modified or missing snapshot
image, and events cut off the end of the log (via `summary.json`). Tested, including a forged event
whose own hash was recomputed.

**What it does not prove.** A chain shows the log is internally consistent. Someone who can rewrite
every event and `summary.json` can build a new consistent chain; the demo shows this case is caught
only when you pass `--expect-head` with the real final hash. So the final `chain_head` must be stored
where an attacker cannot edit it (the backend's signed store; this repo's backend signs every run's chain head with Ed25519). This script hashes
and chains; it does not sign. Signing the head is the backend's job.

For the backend and the frontend: the new fields are additions only. `event_hash` is
`sha256(canonical_json(event_without_event_hash))`. Canonical JSON means keys sorted, no spaces, and a
float with no fractional part prints as an integer (Python's `0.0` is JavaScript's `0`), so Python and
JavaScript give the same hash. Use `crowd_risk.canonical_json` (Python) or `web/verifyEvents.mjs`
(browser or Node), both tested against each other. Plain `json.dumps` does NOT match JavaScript.
The snapshot check is `sha256(file) == evidence_hash` as before. Numbers must stay between 1e-6 and 1e15.

The demo runs use `--lat 17.4239 --lng 78.4738` (Tank Bund) so the dashboard map names the place.

## Integrating with a backend

The backend in this repo (`backend/app.py`) already implements this contract. The tools, all standard-library Python except
`crowd_risk.py`, so they run on any machine without a GPU or model:

- `contract/event.schema.json`, `contract/example_*.json`, `contract/hash_test_vectors.json`: the exact event format
  and test vectors for the hash rule.
- `post_events.py <run> --api URL`: replay a recorded run into the backend, one event per second, printing the HTTP
  status of each. `--from-seq` resumes or back-fills.
- `contract_test.py <run>` or `--fetch URL [--expect <run>]`: validate events (fields, types, ranges, seq, chain,
  hashes) and check the backend returns them unchanged.
- `verify_events.py <run>`: the tamper check; `web/verifyEvents.mjs` is the same check for the browser.

## Time-to-HIGH forecast

While risk is below HIGH and rising at 3 points/s or faster, each event carries `time_to_high` =
`(65 - risk) / risk_rate`: a straight-line extrapolation. It appears in the event, in the `surge` alert text
("HIGH in about 2 s at this rate (estimate)"), on the annotated video and in the control-room video. It is
`null` when risk is already HIGH, falling or flat, further than 60 s away, or during the first 3 s.

**How good is it (measured, small sample).** Across all nine demo runs it made 10 forecasts; 9 came true (HIGH
was reached later) with a mean error of 1.2 s (worst 4 s), and 1 never came true (a falling-risk moment at the
end of the Jülich clip). Nearly all of those are from one clip, the concert ramp, so treat this as
illustrative, not validated. Slow rises were the problem: with a 1 point/s minimum it made false forecasts on
steady and falling clips, so the minimum is 3 points/s. Forecasts made while HIGH is 10 s or more away
overshoot (11.0 s predicted, 7.0 s actual). It assumes the rise continues; a crowd that stops growing
makes it wrong.

## Control-room demo video

```bash
python crowd_risk.py <clip> --out out_concert_vd40 ... --clean-video      # also writes annotated_clean.mp4
python compose_demo.py out_concert_vd40 --theme light --out demos/concert_control_room.mp4 \
  --title "Outdoor concert crowd, drone view" --place "Tank Bund (demo location)" \
  --source "Recorded clip: Pexels 34004639, not a live feed"
```

Renders one 1920x1080 H.264 video from a run, in the website's look: `--theme light` or `dark` use the frontend's
own colour tokens (`src/index.css`), 20 px cards, its drone icon and `RECORDED` pill. The drone view is on the left
in a rounded card (it uses `annotated_clean.mp4` when the run has one, so there is no second status card, and falls
back to `annotated.mp4`); an alert banner appears over it for 4 s when an alert fires. On the right: a risk gauge with
the level and the time-to-HIGH estimate, people / peak density / rise-rate tiles, and a risk-over-time chart that
grows as the video plays with the alerts marked. Along the bottom: the evidence chain (one block per event with its
hash and the previous hash, and a CHAIN VERIFIED badge from `verify_events.py`) and the alert log. Everything is
drawn from the run's own `events.jsonl`, at 2x and shrunk so edges are smooth. The gauge and forecast are interpolated
between the one-per-second readings so they move smoothly (the footer says so). If the run's chain does not verify,
the badge says NOT VERIFIED. Options: `--start`, `--seconds` to cut an excerpt, `--note` for the footer, `--video`
for another drone video. It renders about 14 s of video in 10 s. Made in `demos/`: `concert_control_room.mp4` (light,
14 s, 44 MB), `concert_control_room_dark.mp4` (dark, 44 MB) and `jl030_control_room.mp4` (first 40 s of the Jülich
experiment, 120 MB).

**The drone view itself** (`annotated.mp4` and the snapshots) was restyled to match: a status card with the risk
number, a level pill, a bar and the time-to-HIGH line; thin light person outlines; a `HOTSPOT` label; and a heat
overlay that fades from transparent through green and amber to red, the same level colours the dashboard uses (it
replaces the old rainbow colormap). `--clean-video` writes the same video without the status card.

**Fonts.** The site's four typefaces are bundled in `fonts/`, and `style.py` picks them up automatically: Inter
(text), Inter Tight (headings, title, tile numbers), JetBrains Mono (hashes and labels) and VT323 (the big risk
number). All four are free under the SIL Open Font License; the licence texts are alongside them
(`fonts/OFL-*.txt`), sourced from the Google Fonts repository (about 1.8 MB). If the folder is removed the code falls
back to SF Pro and SF Mono, then Helvetica, Arial or DejaVu, and the big number uses bold sans.

## Charts for the deck

```bash
python plot_results.py out_jl030 --gt-csv eval_out/gt_jl030.csv --png charts/jl030_count_vs_truth.png
python plot_results.py out_concert_vd40 --png charts/concert_risk.png
```

`plot_results.py` draws risk over time on LOW / MEDIUM / HIGH bands with alert markers, and detected vs true people if you pass a `t,gt` CSV (make one with `make_gt_csv.py`). The correlation is only printed when the true crowd changes by 15% or more; for a steady crowd it prints the detected/true ratio instead. Charts already made: `charts/concert_risk.png`, `charts/dc023_count_vs_truth.png` (detected/true 0.71), `charts/jl030_count_vs_truth.png` (correlation 0.995, MAE 4.8).

## Live demo mode

```bash
python crowd_risk.py clip.mp4 --out out_live --realtime --event-every 1 --api http://localhost:8000/events
```

With `--realtime` the events arrive at the dashboard once per second of video, like a live drone feed, instead of in one burst. Alerts arrive the moment they fire.

## Tuning for a demo clip

1. Run on the clip, then open `annotated.mp4` and check that the people count looks right.
2. Read `summary.json`. If max risk is low, lower `--d-crit` (for example to 6). If the whole clip is HIGH, raise it.
3. Aim for a clip that goes LOW → MEDIUM → HIGH. Report the `--d-crit` you used.

## Limits

- None of the datasets contains a real stampede. The demo shows risk rising as density builds; it does not detect an actual crush.
- People seen from high altitude are tiny, so counts can be low. Use `--imgsz 1920` or a VisDrone-trained model.
- `D_CRIT` depends on altitude and camera, so it needs tuning per site. It is not calibrated to people per m².

## Dataset

<!-- Fill in the clips you used -->
Clips from **DroneCrowd** (VisDrone team): https://github.com/VisDrone/DroneCrowd.
Optional: **MovingDroneCrowd**: https://huggingface.co/datasets/fyw1999/MovingDroneCrowd.
Datasets are not included in this folder.
