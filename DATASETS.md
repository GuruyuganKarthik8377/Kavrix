# Datasets used by Team Haven

Large datasets are not uploaded. Links are below; the small recorded outputs used in the demo are included.

| Data | Used for | Where | Licence / terms |
|---|---|---|---|
| VisDrone2020-CC / DroneCrowd | Crowd counting accuracy evaluation (82 sequences, 246 frames) and DroneCrowd seq 023 | https://github.com/VisDrone/DroneCrowd | Research use; licence not stated in the repository |
| MovingDroneCrowd (optional) | Extra moving-drone crowd clips | https://huggingface.co/datasets/fyw1999/MovingDroneCrowd | See dataset card |
| Pexels clip 34004639 (outdoor concert, drone view) | Concert demo run (LOW → HIGH ramp) | Pexels, video id 34004639 | Pexels licence (free to use) |
| Forschungszentrum Jülich, "Crowds in front of bottlenecks" (Wuppertal 2018), runs 030 / 040 | Controlled crowd-crush experiment, count validated against trajectories | https://doi.org/10.34735/ped.2018.1 | Free to use with attribution (Adrian, Boltes, Holl, Sieben, Seyfried) |
| YOLOv8s VisDrone weights (`mshamrai/yolov8s-visdrone`) | Person detector for drone views | https://huggingface.co/mshamrai/yolov8s-visdrone | OpenRAIL |
| InsightFace `buffalo_sc` (downloaded on first run) | Face detection and embeddings | https://github.com/deepinsight/insightface | Non-commercial research only |
| InsightFace bundled group photo | Face-privacy demo clip and enrolment stand-in, generated locally by `ai/face/scripts/make_sample_video.py` (not stored in this repository) | Shipped inside the `insightface` package | As above |
| Team campus clips (four 4K clips, one consenting volunteer) | Real-footage face test: learning the volunteer from video, matching, pixelating everyone else | Recorded by the team on a college campus with the volunteer's consent | Team-owned. **Raw clips and volunteer photos are not included** (they show bystanders), and the pixelated recording and face sheet are not in this public repository either; only the numbers are, in `ai/face/recorded/campus/results.json` |

## Included recorded outputs (small)

- `ai/crowd/sample_data/concert`: 15 hash-chained events and annotated snapshots
- `ai/crowd/sample_data/jl030`: 67 events and 2 alerts
- `ai/crowd/results/`: accuracy tables (`per_frame.csv`, `summary.json`, `experiments.csv`) and charts
- `ai/face/recorded/campus/results.json`: the face team's campus test numbers (the 61 s recording and the volunteer's face sheet are not published)
