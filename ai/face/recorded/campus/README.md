# Recorded campus run

The face and privacy teammate's test of the face worker on four real campus clips with one consenting volunteer.
Everything here is the teammate's own output, used exactly as delivered.

**In the public GitHub repository only `results.json` and this file are present.** `campus_demo.mp4` and `enrolled_faces.jpg` show real people, so they are git-ignored; they are in the CipherCop submission zip. Copy them into this folder to play the recording on the **Campus test** page. Without them the page shows a notice and everything else works.


| File | What |
|---|---|
| `campus_demo.mp4` | The four pixelated clips combined into one video (1920×1080, 61 s), with a title card per clip. Everyone is pixelated except the volunteer's watchlist match, which is boxed in red with its confidence. |
| `results.json` | The matches shown in the video (60%, 79%, 82%) with their time in the video, plus the team's held-out test and limits. The website's **Campus test** and **Results** pages read it. |
| `enrolled_faces.jpg` | The 62 faces of the volunteer that enrolment found in video. Checked by eye by the team: all are him. |

The raw 4K clips and the volunteer's photos are not part of the project: they show bystanders.

On the dashboard: **Campus test** page, then **Send the recorded matches to the live system**. The backend
(`POST /demo/face-replay/campus`) feeds each match through the same path as a live worker, timed to the video: a pending
review, a sealed `watchlist_match` event and, once an officer confirms it in the Officer app, a "Missing person located"
task. The recorded watchlist entry holds no face template, so it can never match a live face.

## How the team made it

With the backend running (consent reference HV-01 is the team's demo consent form):

```bash
python enroll.py --label "Campus volunteer (consented demo)" --category missing_person \
  --legal-basis "Demo: consent form HV-01" --consent-ref HV-01 --days 7 photo1.webp photo2.webp \
  --video clip1.mov --video clip2.mov --video clip3.mov --sheet out/enrolled_samples.jpg
python worker.py --source clip.mov --drone D-02 --out out/campus_clip.mp4      # once per clip
python scripts/audit_output.py out/campus_clip.mp4                                # privacy check
```

`worker.py --report file.json` writes the counts and every match with its video time.

## Read this before quoting the numbers

* Confidence is the **cosine similarity** between face embeddings shown as a percentage, not a probability. The match line is 50%.
* The video is one run. Re-running the same footage on this backend gave 2 matches at 59–64%, because matching depends on the template and on when the checks happen.
* One volunteer, one campus, four clips: a feasibility test, not an accuracy benchmark.
