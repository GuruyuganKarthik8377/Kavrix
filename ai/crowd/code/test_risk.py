#!/usr/bin/env python3
"""Tests for crowd_risk.py. No YOLO/torch needed. Run: python test_risk.py  (prints "ok")."""
import hashlib
import http.server
import json
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np

import crowd_risk as cr


def _texture(h=240, w=320, seed=0):
    rng = np.random.default_rng(seed)
    noise = rng.random((h, w)).astype(np.float32)
    tex = cv2.GaussianBlur(noise, (0, 0), 2.0)
    tex = (tex - tex.min()) / (tex.max() - tex.min())
    return (tex * 255).astype(np.uint8)


def test_density_single_and_cluster():
    shape = (1080, 1920)
    assert cr.density_map(np.zeros((0, 2)), shape).max() == 0
    one = cr.density_map(np.array([[960, 540]]), shape).max()
    assert 0.8 < one < 1.2, one
    packed = cr.density_map(np.full((12, 2), [960, 540]), shape).max()
    assert 10 < packed < 14, packed
    rng = np.random.default_rng(1)
    spread = cr.density_map(rng.random((12, 2)) * [1920, 1080], shape).max()
    assert spread < packed / 3, (spread, packed)


def test_box_centers_head_area():
    c = cr.box_centers([[0, 0, 10, 100]])
    assert np.allclose(c, [[5, 35]])
    assert cr.box_centers([]).shape == (0, 2)


def test_risk_score_and_levels():
    assert cr.risk_score(0, 5) == 0
    vals = [cr.risk_score(d, 0) for d in (0, 2, 5, 8, 10, 20)]
    assert vals == sorted(vals)
    assert abs(cr.risk_score(cr.D_CRIT, 0) - 100 * cr.W_DENSITY) < 1e-6
    assert abs(cr.risk_score(cr.D_CRIT, cr.MAG_CRIT) - 100) < 1e-6
    assert cr.risk_score(5, cr.MAG_CRIT) > cr.risk_score(5, 0)
    assert cr.risk_level(10) == "LOW"
    assert cr.risk_level(cr.LEVEL_MEDIUM) == "MEDIUM"
    assert cr.risk_level(cr.LEVEL_HIGH) == "HIGH"


def test_camera_motion_is_compensated():
    prev = _texture()
    M_true = np.float32([[1, 0, 4], [0, 1, 2]])
    curr = cv2.warpAffine(prev, M_true, (prev.shape[1], prev.shape[0]), borderMode=cv2.BORDER_REFLECT)
    M = cr.estimate_camera_motion(prev, curr)
    assert abs(M[0, 2] - 4) < 0.5 and abs(M[1, 2] - 2) < 0.5, M
    inner = (slice(20, -20), slice(20, -20))
    compensated = cr.residual_flow(prev, curr, M)[inner].mean()
    raw = cr.residual_flow(prev, curr, np.float32([[1, 0, 0], [0, 1, 0]]))[inner].mean()
    assert compensated < 0.6 and raw > 3, (compensated, raw)


def test_crowd_motion_survives_compensation():
    prev = _texture(seed=2)
    curr = cv2.warpAffine(prev, np.float32([[1, 0, 3], [0, 1, 0]]),
                          (prev.shape[1], prev.shape[0]), borderMode=cv2.BORDER_REFLECT)
    patch = prev[100:140, 140:180]
    curr[100:140, 140:180] = np.roll(patch, -5, axis=0)   # the "crowd" moves on its own
    M = cr.estimate_camera_motion(prev, curr)
    mag = cr.residual_flow(prev, curr, M)
    assert mag[105:135, 145:175].mean() > 2 * mag[20:60, 20:60].mean()


def test_clahe_raises_local_contrast_and_keeps_shape():
    rng = np.random.default_rng(0)
    flat = (100 + rng.integers(-6, 7, (240, 320, 3))).astype(np.uint8)     # low-contrast image
    out = cr.apply_clahe(flat)
    assert out.shape == flat.shape and out.dtype == np.uint8
    assert out.astype(float).std() > flat.astype(float).std() * 1.5


def test_tiles_cover_frame_and_merge_dedups():
    origins, t = cr.tile_origins(1920, 1080, 480, 0.2)
    assert t == 480
    covered = np.zeros((1080, 1920), bool)
    for x, y in origins:
        assert 0 <= x <= 1920 - t and 0 <= y <= 1080 - t
        covered[y:y + t, x:x + t] = True
    assert covered.all()
    # the same person seen by two overlapping tiles is counted once; a distant one stays
    boxes = np.array([[100, 100, 120, 140], [101, 101, 121, 141], [500, 500, 520, 540]], np.float32)
    kept = cr.merge_boxes(boxes, [0.9, 0.5, 0.7])
    assert len(kept) == 2
    assert cr.merge_boxes([], []).shape == (0, 4)


def test_eval_counts_parsing_and_metrics():
    import eval_counts as ev
    assert abs(ev.spearman([1, 2, 3, 4], [10, 20, 35, 90]) - 1) < 1e-9
    assert ev.spearman([1, 2, 3, 4], [4, 3, 2, 1]) < -0.99
    assert ev.pick_frames(range(1, 31), 3) == [1, 15, 30]
    s = ev.summarize([100, 200, 400], [50, 100, 200], [5, 9, 20], [2, 4, 9])
    assert s["median_detected_over_true"] == 0.5 and s["spearman_count"] > 0.99

    class PointDetector:          # "detects" a box on every ground-truth head
        def __init__(self, points):
            self.points = points

        def detect(self, frame, idx=0):
            p = self.points[idx]
            return np.concatenate([p - 5, p + 5], axis=1)

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "annotations").mkdir()
        (root / "sequences/00001").mkdir(parents=True)
        pts, lines = {}, []
        rng = np.random.default_rng(0)
        for fn, n in [(1, 10), (2, 20), (3, 40)]:
            p = rng.random((n, 2)) * [300, 200] + 20
            pts[fn] = p.astype(np.float32)
            lines += [f"{fn},{int(x)},{int(y)}" for x, y in p]
            cv2.imwrite(str(root / f"sequences/00001/{fn:05d}.jpg"), np.zeros((240, 340, 3), np.uint8))
        (root / "annotations/00001.txt").write_text("\n".join(lines))
        assert {k: len(v) for k, v in ev.load_annotations(root / "annotations/00001.txt").items()} == \
            {1: 10, 2: 20, 3: 40}
        rows = ev.evaluate(root, PointDetector(pts), frames_per_seq=3)
        assert [(r["gt_count"], r["det_count"]) for r in rows] == [(10, 10), (20, 20), (40, 40)]


def test_color_blob_detector_counts_caps_and_ignores_other_colours():
    frame = np.full((720, 1280, 3), 60, np.uint8)
    centres = [(200, 200), (400, 220), (600, 400), (900, 300), (1000, 600), (300, 560)]
    for x, y in centres:                      # orange discs (BGR orange)
        cv2.circle(frame, (x, y), 22, (0, 120, 255), -1)
    cv2.circle(frame, (700, 100), 22, (255, 0, 0), -1)      # blue disc, must be ignored
    cv2.circle(frame, (800, 650), 22, (0, 200, 0), -1)      # green disc, must be ignored
    det = cr.ColorBlobDetector()
    boxes = det.detect(frame)
    assert len(boxes) == len(centres), len(boxes)
    pts = cr.box_centers(boxes)               # centres must land on the caps
    for cx, cy in centres:
        assert np.min(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)) < 4
    assert det.detect(np.full((720, 1280, 3), 60, np.uint8)).shape == (0, 4)


class FakeDetector:
    """Returns a cluster of people that grows from 0 to 40 over the clip."""

    def __init__(self, n_frames, w, h):
        rng = np.random.default_rng(3)
        self.pts = rng.normal([w * 0.5, h * 0.5], [w * 0.04, h * 0.05], (40, 2))
        self.n_frames = n_frames

    def detect(self, frame, idx=0):
        k = int(40 * idx / max(1, self.n_frames - 1))
        p = self.pts[:k]
        return np.concatenate([p - [4, 8], p + [4, 8]], axis=1).astype(np.float32)


def _synthetic_video(path, w=640, h=360, fps=10, seconds=4):
    n = fps * seconds
    tex = cv2.cvtColor(cv2.resize(_texture(400, 800, seed=4), (w + 2 * n + 10, h)), cv2.COLOR_GRAY2BGR)
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(n):
        wr.write(np.ascontiguousarray(tex[:, i * 2: i * 2 + w]))   # drone pans 2 px/frame
    wr.release()
    return n, w, h


def test_end_to_end_with_fake_detector():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n, w, h = _synthetic_video(d / "clip.mp4")
        s = cr.process_video(d / "clip.mp4", d / "out", FakeDetector(n, w, h),
                             d_crit=10, quiet=True, alpha=0.5, alert_hold=1.0, alert_warmup=0.5)
        assert s["frames"] == n and s["events"] == 4, s
        assert (d / "out/annotated.mp4").stat().st_size > 0
        # browsers only play H.264; the output must be avc1 whenever this machine can encode it
        assert s["video_codec"] in ("avc1", "mp4v")
        head = (d / "out/annotated.mp4").read_bytes()[:4096]
        assert (b"avc1" in head) == (s["video_codec"] == "avc1"), s["video_codec"]
        everything = [json.loads(l) for l in (d / "out/events.jsonl").read_text().splitlines()]
        events = [e for e in everything if e["type"] == "crowd_risk"]
        alerts = [e for e in everything if e["type"] == "crowd_alert"]
        need = {"id", "type", "lat", "lng", "risk", "level", "count", "timestamp",
                "evidence_hash", "heatmap", "video_t", "risk_rate", "hotspot", "time_to_high"}
        for e in everything:
            assert need <= e.keys(), e.keys()
            assert 0 <= e["risk"] <= 100
            data = (d / "out" / e["heatmap"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == e["evidence_hash"]
        assert events[0]["level"] == "LOW" and events[-1]["level"] == "HIGH", \
            [(e["risk"], e["level"]) for e in events]
        assert events[-1]["risk"] > events[0]["risk"]
        # the crowd sits in the middle of the frame, so the hotspot should too
        hs = events[-1]["hotspot"]
        assert abs(hs["x"] - 0.5) < 0.12 and abs(hs["y"] - 0.5) < 0.15, hs
        # a growing crowd must raise at least one alert, with an officer-readable message
        assert alerts and s["alerts"] == len(alerts)
        assert all(a["alert_kind"] in ("sustained_high", "surge") and a["message"] for a in alerts)


def test_alert_state_sustained_high_fires_once_and_rearms():
    a = cr.AlertState(hold=3.0, surge_rate=1e9, warmup=0.0)   # surge and warm-up disabled here
    fired = []
    for i in range(0, 200):                               # 10 fps: 2 s low, 6 s high, 2 s low, 6 s high
        t = i / 10
        risk = 80 if (20 <= i < 80 or i >= 100) else 10
        fired += [(round(t, 1), k) for k in a.update(t, risk)]
    assert [k for _, k in fired] == ["sustained_high", "sustained_high"], fired
    assert abs(fired[0][0] - 5.0) < 0.2 and abs(fired[1][0] - 13.0) < 0.2, fired
    b = cr.AlertState(hold=3.0, surge_rate=1e9, warmup=0.0)   # flickering just below HIGH never alerts
    assert not any(b.update(i / 10, 60 + 10 * (i % 2)) for i in range(100))


def test_alert_state_surge_needs_fast_rise_and_respects_cooldown():
    slow = cr.AlertState(surge_rate=8.0, window=2.0, cooldown=10.0)
    assert not any(slow.update(i / 10, 20 + 0.1 * i) for i in range(300))     # 1 point/s
    fast = cr.AlertState(surge_rate=8.0, window=2.0, cooldown=10.0)
    hits = [i / 10 for i in range(60) if "surge" in fast.update(i / 10, 20 + 2 * i)]  # 20 points/s
    assert len(hits) == 1, hits                                                 # one alert, then cooldown
    low = cr.AlertState(surge_rate=8.0, window=2.0, cooldown=10.0)
    assert not any(low.update(i / 10, 2 * i / 10 * 5) for i in range(20))      # fast but still LOW risk
    assert fast.rate > 8.0


def test_alert_state_is_silent_during_warmup():
    quiet = cr.AlertState(hold=0.5, surge_rate=8.0, warmup=3.0)
    early = [k for i in range(30) for k in quiet.update(i / 10, 10 + 30 * i / 10)]   # rises 10 -> 100
    assert early == [], early                       # a start-up jump inside 3 s must not alert
    loud = cr.AlertState(hold=0.5, surge_rate=8.0, warmup=0.0)
    assert [k for i in range(30) for k in loud.update(i / 10, 10 + 30 * i / 10)], "no alert without warm-up"


def test_time_to_high_forecast():
    assert cr.time_to_high(45.0, 10.0) == 2.0                  # (65 - 45) / 10 points per second
    assert cr.time_to_high(47.3, 6.2) == 2.9
    assert cr.time_to_high(70.0, 10.0) is None                 # already HIGH
    assert cr.time_to_high(40.0, 2.9) is None                  # below the 3 points/s minimum: mostly noise
    assert cr.time_to_high(40.0, 3.0) == 8.3
    assert cr.time_to_high(40.0, -5.0) is None                 # falling
    assert cr.time_to_high(5.0, 1.0, min_rate=0.5) == 60.0     # exactly the 60 s cap is allowed
    assert cr.time_to_high(4.0, 1.0, min_rate=0.5) is None     # 61 s is beyond it
    assert cr.time_to_high(1.0, 1.0, min_rate=0.5, max_s=100) == 64.0   # unless the cap is raised
    surge = cr.alert_message("surge", 47.0, 498, 6.2, 3.0, eta=2.9)
    assert "HIGH in about 3 s" in surge and "(estimate)" in surge
    assert "HIGH in" not in cr.alert_message("surge", 47.0, 498, 6.2, 3.0)         # no forecast, no sentence
    assert "HIGH in" not in cr.alert_message("sustained_high", 80.0, 100, 0.0, 3.0, eta=2.0)


def test_compose_demo_helpers_and_render():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return                                            # the control-room video is optional
    import shutil
    import compose_demo as cd
    from PIL import Image, ImageDraw

    risk = [{"video_t": 0.0, "risk": 10.0}, {"video_t": 2.0, "risk": 50.0}]
    assert cd.risk_at(risk, 1.0) == 30.0 and cd.risk_at(risk, -5) == 10.0 and cd.risk_at(risk, 9) == 50.0
    assert cd.fit(1920, 1080, 1280, 720) == (0, 0, 1280, 720)             # 16:9 fills the box
    assert cd.fit(1920, 1440, 1280, 720) == (160, 0, 960, 720)            # 4:3 is centred with side bars
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    import style
    lines = cd.wrap_text(d, "one two three four five six seven eight", style.font("sans", 20), 150)
    assert len(lines) > 1 and " ".join(lines) == "one two three four five six seven eight"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        n, w, h = _synthetic_video(tmp / "clip.mp4", seconds=6)
        cr.process_video(tmp / "clip.mp4", tmp / "run", FakeDetector(n, w, h), d_crit=10, quiet=True,
                         alpha=0.5, alert_hold=1.0, alert_warmup=0.5, event_every=0.5)
        events, risk_ev, alerts = cd.load_run(tmp / "run")
        assert alerts, "the growing crowd must raise an alert for the banner to show"
        out = tmp / "demo.mp4"
        res = cd.compose(tmp / "run", out, "Test", "Nowhere", "src", "note")
        assert res["verified"] is True and res["size"] == "1920x1080"
        cap = cv2.VideoCapture(str(out))
        assert (int(cap.get(3)), int(cap.get(4))) == (1920, 1080) and int(cap.get(7)) == n
        fps = cap.get(5)

        def frame_at(sec):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(sec * fps))
            ok, f = cap.read()
            assert ok
            return f
        x, y, bw, bh = cd.TOAST
        t_alert = alerts[0]["video_t"] - events[0]["video_t"]
        def banner_share(frame):       # fraction of the banner area painted in the banner's dark red
            region = frame[y:y + bh, x:x + bw].astype(int)
            target = np.array(cd.alert_bg("light")[::-1])            # frames are BGR
            return float((np.abs(region - target).max(axis=2) <= 14).mean())
        assert banner_share(frame_at(0.1)) < 0.05, "no banner before the alert"
        assert banner_share(frame_at(min(t_alert + 0.4, 5.5))) > 0.4, "the alert banner must cover that area"
        t_last = max(a["video_t"] for a in alerts) - events[0]["video_t"]
        if t_last + cd.TOAST_SECONDS + 0.3 < 5.9:            # the banner leaves once the last alert expires
            assert banner_share(frame_at(t_last + cd.TOAST_SECONDS + 0.3)) < 0.05
        assert not np.array_equal(frame_at(0.5)[600:1000, 1300:1900], frame_at(5.0)[600:1000, 1300:1900])

        # a tampered run is flagged in the video itself
        bad = tmp / "bad"
        shutil.copytree(tmp / "run", bad)
        lines = (bad / "events.jsonl").read_text().splitlines()
        e = json.loads(lines[3])
        e["risk"] = 1.0
        lines[3] = json.dumps(e)
        (bad / "events.jsonl").write_text("\n".join(lines) + "\n")
        assert cd.compose(bad, tmp / "bad.mp4", "T", "P", "s", "n", seconds=1)["verified"] is False


def test_style_themes_fonts_and_heat_ramp():
    import style
    assert style.Theme("light").bg == (241, 240, 236) and style.Theme("dark").bg == (14, 14, 15)   # frontend tokens
    assert style.Theme("dark").text_high == style.LEVEL["HIGH"] == (229, 72, 77)
    assert style.Theme("light").text_high != style.LEVEL["HIGH"]       # a darker red, readable on the light cards
    try:
        style.Theme("sepia")
        raise AssertionError("an unknown theme must be rejected")
    except ValueError:
        pass
    for role in ("sans", "sans-medium", "sans-bold", "display", "mono", "mono-bold", "pixel"):
        f = style.font(role, 20)                                   # falls back gracefully, never raises
        assert f.getlength("Kavrix 47") > 20
    bgr, alpha = style.heat_lut()
    assert bgr.shape == (256, 3) and alpha.shape == (256,)
    assert alpha[0] == 0 and alpha[2] < 0.01 and alpha[255] == max(alpha)              # transparent when empty
    assert np.all(np.diff(alpha) >= -1e-9)                                              # never fades back out
    assert tuple(bgr[128][::-1].round()) == style.LEVEL["MEDIUM"]                       # amber at half of D_CRIT
    assert tuple(bgr[255][::-1].round()) == style.LEVEL["HIGH"] and tuple(bgr[0][::-1].round()) == style.LEVEL["LOW"]
    frame = np.full((200, 300, 3), 77, np.uint8)
    before = frame.copy()
    style.paint_rgba(frame, (50, 40, 100, 60), lambda d, ss, off: d.rectangle(
        (off[0] * ss, off[1] * ss, (off[0] + 100) * ss, (off[1] + 60) * ss), fill=(255, 0, 0, 255)))
    assert not np.array_equal(frame[40:100, 50:150], before[40:100, 50:150])           # the box was painted
    mask = np.ones(frame.shape[:2], bool)
    mask[40:100, 50:150] = False
    assert np.array_equal(frame[mask], before[mask])                                    # nothing else was touched


def test_drone_panel_hud_and_clean_video():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        n, w, h = _synthetic_video(tmp / "clip.mp4", w=960, h=540, seconds=3)
        s = cr.process_video(tmp / "clip.mp4", tmp / "run", FakeDetector(n, w, h), d_crit=10, quiet=True,
                             alpha=0.5, clean_video=True)
        assert s["clean_video"] is True and (tmp / "run/annotated_clean.mp4").stat().st_size > 0

        def last_frame(name):
            cap = cv2.VideoCapture(str(tmp / "run" / name))
            cap.set(cv2.CAP_PROP_POS_FRAMES, cap.get(cv2.CAP_PROP_FRAME_COUNT) - 3)
            ok, f = cap.read()
            assert ok
            return f.astype(int)
        with_hud, clean = last_frame("annotated.mp4"), last_frame("annotated_clean.mp4")
        hud = (slice(8, 100), slice(8, 260))                      # where the status card sits at this size
        assert np.abs(with_hud[hud] - clean[hud]).mean() > 25, "the card must show on annotated.mp4 only"
        rest = np.ones(with_hud.shape[:2], bool)
        rest[:110, :270] = False
        assert np.abs(with_hud[rest] - clean[rest]).mean() < 4, "nothing else may differ"
    # the same overlay helpers work directly on a frame
    frame = np.full((540, 960, 3), 90, np.uint8)
    heat = np.zeros((54, 96), np.float32)
    heat[27, 48] = 12.0
    over = cr.draw_overlay(frame, np.array([[100, 100, 130, 160]], np.float32), heat, 10.0, "HIGH",
                           {"x": 0.5, "y": 0.5})
    assert over.shape == frame.shape and not np.array_equal(over, frame)
    assert np.array_equal(frame, np.full((540, 960, 3), 90, np.uint8)), "draw_overlay must not modify its input"
    shown = cr.draw_hud(over.copy(), 72.0, "HIGH", 40, 12.0, 0.2, 10.0, 3.0, eta=None)
    assert not np.array_equal(shown[10:100, 10:260], over[10:100, 10:260])


def test_compose_themes_and_clean_video_source():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return
    import compose_demo as cd
    import style
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        n, w, h = _synthetic_video(tmp / "clip.mp4", seconds=3)
        cr.process_video(tmp / "clip.mp4", tmp / "run", FakeDetector(n, w, h), d_crit=10, quiet=True,
                         clean_video=True, event_every=0.5)
        corner = {}
        for theme in ("light", "dark"):
            res = cd.compose(tmp / "run", tmp / f"{theme}.mp4", "T", "P", "s", "n", seconds=1, theme=theme)
            assert res["theme"] == theme and res["drone_video"] == "annotated_clean.mp4"   # no duplicate card
            cap = cv2.VideoCapture(str(tmp / f"{theme}.mp4"))
            ok, f = cap.read()
            assert ok
            corner[theme] = tuple(int(v) for v in f[4, 4][::-1])
            assert max(abs(a - b) for a, b in zip(corner[theme], style.Theme(theme).bg)) <= 6, (theme, corner[theme])
        assert corner["light"] != corner["dark"]
        (tmp / "run/annotated_clean.mp4").unlink()                       # without it, fall back to annotated.mp4
        assert cd.compose(tmp / "run", tmp / "x.mp4", "T", "P", "s", "n", seconds=1)["drone_video"] == "annotated.mp4"


def test_hotspot_position():
    assert cr.hotspot_position(np.zeros((40, 60), np.float32)) is None
    heat = np.zeros((40, 60), np.float32)
    heat[10, 45] = 5.0
    assert cr.hotspot_position(heat) == {"x": 0.75, "y": 0.25}


def test_realtime_pacing_matches_video_clock():
    import time
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n, w, h = _synthetic_video(d / "clip.mp4", fps=10, seconds=2)
        t0 = time.time()
        cr.process_video(d / "clip.mp4", d / "fast", FakeDetector(n, w, h), quiet=True)
        fast = time.time() - t0
        t0 = time.time()
        s = cr.process_video(d / "clip.mp4", d / "live", FakeDetector(n, w, h), quiet=True, realtime=True)
        live = time.time() - t0
        assert live >= 1.8 and live < 3.0, live          # a 2 s clip takes about 2 s
        assert fast < live and s["realtime"] is True and s["max_lag_s"] < 0.5


def test_plot_results_and_gt_csv():
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return                                            # charts are optional
    import make_gt_csv
    import plot_results
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n, w, h = _synthetic_video(d / "clip.mp4")
        cr.process_video(d / "clip.mp4", d / "out", FakeDetector(n, w, h), d_crit=10, quiet=True,
                         alert_hold=1.0)
        traj = d / "traj.txt"
        traj.write_text("# header\n" + "\n".join(f"{p} {f} 1.0 1.0 1.7" for f in range(0, 40)
                                                    for p in range(1, 1 + f // 2 + 1)))
        assert make_gt_csv.juelich(traj, 10.0)[10] == (1.0, 6)
        make_gt_csv.write_csv(d / "gt.csv", make_gt_csv.juelich(traj, 10.0))
        note = plot_results.plot(d / "out", d / "chart.png", d / "gt.csv")
        assert (d / "chart.png").stat().st_size > 5000 and "MAE" in note
        plot_results.plot(d / "out", d / "chart2.png")      # works without ground truth too


def test_hash_chain_and_tamper_detection():
    import contextlib
    import io
    import verify_events as ve
    sample = {"id": "a", "type": "crowd_risk", "risk": 12.5, "hotspot": {"x": 0.1, "y": 0.2}, "name": "é"}
    assert cr.event_digest(sample) == ve.event_digest(sample)            # producer and auditor agree
    assert cr.event_digest(sample) == cr.event_digest({**sample, "event_hash": "ignored"})

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        n, w, h = _synthetic_video(d / "clip.mp4", seconds=6)
        s = cr.process_video(d / "clip.mp4", d / "out", FakeDetector(n, w, h), d_crit=10, quiet=True,
                             alpha=0.5, alert_hold=1.0, alert_warmup=0.5, event_every=0.5)
        lines = [json.loads(x) for x in (d / "out/events.jsonl").read_text().splitlines()]
        assert len(lines) >= 8 and s["chain_events"] == len(lines) and s["chain_head"] == lines[-1]["event_hash"]
        assert lines[0]["prev_hash"] == cr.GENESIS_HASH
        assert all(b["prev_hash"] == a["event_hash"] for a, b in zip(lines, lines[1:]))

        ok = ve.verify(d / "out")
        assert ok["ok"] and ok["events"] == len(lines) and ok["head"] == s["chain_head"], ok
        assert not ve.verify(d / "out", expect_head="f" * 64)["ok"]       # wrong trusted head
        assert ve.verify(d / "out", expect_head=s["chain_head"])["ok"]

        # every tampering in the demo list must be caught, and the demo must say so
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = ve.demo(d / "out")
        assert code == 0 and "All tampering detected" in buf.getvalue(), buf.getvalue()
        assert ve.verify(d / "out")["ok"]                                 # the original is untouched

        # and individually: each plain tampering (not the anchored one) fails verification on its own
        import shutil
        for name, fn, needs_anchor in ve.DEMOS:
            work = d / "work"
            shutil.copytree(d / "out", work)
            fn(work)
            plain = ve.verify(work)
            assert plain["ok"] == needs_anchor, (name, plain)             # only the full rewrite passes unanchored
            assert not ve.verify(work, expect_head=s["chain_head"])["ok"], name
            shutil.rmtree(work)


def test_canonical_json_is_language_independent():
    import shutil
    import subprocess
    import verify_events as ve
    sample = {"id": "abc", "type": "crowd_risk", "risk": 64.0, "motion": 0.0, "density_peak": 6.41,
              "count": 75, "hotspot": {"x": 0.21, "y": 0.558}, "lat": 17.4239, "heatmap": None,
              "ok": True, "list": [1.0, 2.5, "é"]}
    expected = ('{"count":75,"density_peak":6.41,"heatmap":null,"hotspot":{"x":0.21,"y":0.558},"id":"abc",'
                '"lat":17.4239,"list":[1,2.5,"é"],"motion":0,"ok":true,"risk":64,"type":"crowd_risk"}')
    assert cr.canonical_json(sample) == expected          # 64.0 and 0.0 print as 64 and 0, like JavaScript
    assert ve.canonical_json(sample) == expected
    digest = "d4476d4c4a29d6d02fdb1d4355d2163253af5b026fde56c0b4021a9461be2cd7"
    assert cr.event_digest(sample) == ve.event_digest(sample) == digest
    for bad in (float("nan"), float("inf")):
        try:
            cr.canonical_json({"x": bad})
            raise AssertionError("NaN/inf must be rejected")
        except ValueError:
            pass
    node = shutil.which("node")
    if node:                                             # the real cross-language check
        js = ("const c=require('crypto');"
              "const k=v=>Array.isArray(v)?'['+v.map(k).join(',')+']':v&&typeof v==='object'?"
              "'{'+Object.keys(v).sort().map(x=>JSON.stringify(x)+':'+k(v[x])).join(',')+'}':JSON.stringify(v);"
              "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const t=k(JSON.parse(s));"
              "console.log(t);console.log(c.createHash('sha256').update(t).digest('hex'))})")
        out = subprocess.run([node, "-e", js], input=json.dumps(sample), capture_output=True,
                             text=True, timeout=30).stdout.splitlines()
        assert out == [expected, digest], out


def _api_server(behaviour):
    """A local HTTP endpoint. behaviour(n) -> (status, delay_s) for the n-th request. Returns (url, got, stop)."""
    got, count = [], [0]

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            count[0] += 1
            status, delay = behaviour(count[0])
            import time as _t
            _t.sleep(delay)
            if status < 300:
                got.append(body)
            self.send_response(status)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/events", got, srv.shutdown


def test_event_sender_retries_keeps_order_and_never_blocks():
    import time
    # 1. a flaky backend: the first two requests fail, retries get every event through, in order
    url, got, stop = _api_server(lambda n: (500, 0) if n <= 2 else (201, 0))
    s = cr.EventSender(url, timeout=1.0, retries=3, backoff=0.01)
    for i in range(5):
        s.send({"seq": i})
    stats = s.close(timeout=5)
    stop()
    assert stats == {"sent": 5, "failed": 0, "dropped": 0, "pending": 0}, stats
    assert [e["seq"] for e in got] == [0, 1, 2, 3, 4], got

    # 2. a slow backend must not block the caller
    url, got, stop = _api_server(lambda n: (201, 0.4))
    s = cr.EventSender(url, timeout=2.0, retries=0)
    t0 = time.time()
    for i in range(5):
        s.send({"seq": i})
    assert time.time() - t0 < 0.1, "send() must return immediately"
    assert s.close(timeout=10)["sent"] == 5
    stop()

    # 3. a backend that is down: events are counted as failed, not lost silently, and close() returns
    url, got, stop = _api_server(lambda n: (503, 0))
    s = cr.EventSender(url, timeout=1.0, retries=1, backoff=0.01)
    for i in range(3):
        s.send({"seq": i})
    stats = s.close(timeout=5)
    stop()
    assert stats["sent"] == 0 and stats["failed"] == 3 and stats["dropped"] == 0, stats

    # 4. a full queue drops (and counts) instead of growing without limit
    url, got, stop = _api_server(lambda n: (201, 0.3))
    s = cr.EventSender(url, timeout=2.0, retries=0, maxsize=1)
    for i in range(6):
        s.send({"seq": i})
    stats = s.close(timeout=10)
    stop()
    assert stats["dropped"] >= 3 and stats["sent"] + stats["dropped"] == 6, stats


def test_events_carry_identity_and_sequence_and_reach_the_api():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        n, w, h = _synthetic_video(tmp / "clip.mp4", seconds=4)
        url, got, stop = _api_server(lambda k: (201, 0))
        s = cr.process_video(tmp / "clip.mp4", tmp / "run", FakeDetector(n, w, h), d_crit=10, quiet=True,
                             alpha=0.5, alert_hold=1.0, alert_warmup=0.5, event_every=0.5, api=url,
                             run_id="run-test-1", drone_id="D-07")
        stop()
        events = [json.loads(x) for x in (tmp / "run/events.jsonl").read_text().splitlines()]
        assert [e["seq"] for e in events] == list(range(len(events)))                     # 0, 1, 2, ...
        assert {e["run_id"] for e in events} == {"run-test-1"} and {e["drone_id"] for e in events} == {"D-07"}
        assert {e["schema_version"] for e in events} == {cr.SCHEMA_VERSION}
        assert s["run_id"] == "run-test-1" and s["drone_id"] == "D-07"
        assert s["api"]["sent"] == len(events) and s["api"]["failed"] == 0 and s["api_failures"] == 0
        assert got == events, "the backend must receive exactly what is in events.jsonl, in order"
        import verify_events as ve
        assert ve.verify(tmp / "run")["ok"]                  # the identity fields are inside the hash chain
        lines = (tmp / "run/events.jsonl").read_text().splitlines()
        e = json.loads(lines[2])
        e["run_id"] = "someone-elses-run"                    # moving an event to another run must be detected
        lines[2] = json.dumps(e)
        (tmp / "run/events.jsonl").write_text("\n".join(lines) + "\n")
        assert not ve.verify(tmp / "run")["ok"]
        # an auto-generated run id is readable and unique
        r1 = cr.process_video(tmp / "clip.mp4", tmp / "r1", FakeDetector(n, w, h), quiet=True, max_seconds=1)["run_id"]
        r2 = cr.process_video(tmp / "clip.mp4", tmp / "r2", FakeDetector(n, w, h), quiet=True, max_seconds=1)["run_id"]
        assert r1.startswith("clip-") and r1 != r2


def _small_run(tmp, seconds=4):
    n, w, h = _synthetic_video(tmp / "clip.mp4", seconds=seconds)
    cr.process_video(tmp / "clip.mp4", tmp / "run", FakeDetector(n, w, h), d_crit=10, quiet=True, alpha=0.5,
                     alert_hold=1.0, alert_warmup=0.5, event_every=0.5, run_id="run-t", drone_id="D-01")
    return tmp / "run"


def test_post_events_replays_in_order_resumes_and_reports_failures():
    import post_events as pe
    with tempfile.TemporaryDirectory() as tmp:
        run = _small_run(Path(tmp))
        events = pe.load_events(run)
        assert events == pe.load_events(run / "events.jsonl") and len(events) >= 8
        url, got, stop = _api_server(lambda n: (201, 0))
        lines = []
        stats = pe.replay(events, url, rate=0, out=lines.append)
        assert stats["sent"] == len(events) and stats["failed"] == 0 and stats["statuses"] == {"201": len(events)}
        assert got == events, "the backend must receive the events unchanged and in order"
        assert all("-> 201" in x for x in lines)
        got.clear()
        stats = pe.replay(events, url, rate=0, from_seq=5, out=lambda s: None)       # resume / back-fill
        assert stats["skipped"] == 5 and [e["seq"] for e in got] == list(range(5, len(events)))
        stop()
        # a backend that rejects one event: reported, others still sent, exit code 1
        url, got, stop = _api_server(lambda n: (422, 0) if n == 3 else (201, 0))
        lines = []
        stats = pe.replay(events, url, rate=0, out=lines.append)
        assert stats["failed"] == 1 and stats["sent"] == len(events) - 1 and stats["statuses"]["422"] == 1
        assert any("422" in x for x in lines)
        assert pe.main([str(run), "--api", url, "--rate", "0"]) in (0, 1)
        stop()
        # nothing listening: every event fails, nothing raises
        stats = pe.replay(events[:2], "http://127.0.0.1:9/x", rate=0, timeout=0.3, out=lambda s: None)
        assert stats["failed"] == 2 and "None" in stats["statuses"]
        # a dry run sends nothing; the replay is paced
        stats = pe.replay(events[:3], "http://unused", rate=2, dry_run=True, out=lambda s: None, sleep=lambda s: lines.append(("sleep", s)))
        assert stats["sent"] == 3 and ("sleep", 0.5) in lines
        assert pe.main([str(run), "--dry-run", "--rate", "0"]) == 0


def test_contract_test_passes_a_real_run_and_catches_each_kind_of_problem():
    import contract_test as ct
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        run = _small_run(tmp)
        events = ct.load_events(run)
        assert any(e["type"] == "crowd_alert" for e in events)
        assert ct.check_run(events, run) == [] and ct.main([str(run), "--snapshots", str(run)]) == 0

        def problems(mutate):
            ev = json.loads(json.dumps(events))
            mutate(ev)
            return ct.check_run(ev)
        def missing(ev): del ev[2]["motion"]
        def bad_level(ev): ev[2]["level"] = "HIGH" if ev[2]["risk"] < 30 else "LOW"
        def bad_enum(ev): ev[2]["level"] = "SEVERE"
        def bad_type(ev): ev[2]["count"] = "37"
        def out_of_range(ev): ev[2]["risk"] = 140
        def bad_hotspot(ev): ev[3]["hotspot"] = {"x": 2, "y": 0.5}
        def no_message(ev): next(e for e in ev if e["type"] == "crowd_alert").pop("message")
        def edited(ev): ev[3]["count"] += 1
        def gap(ev): del ev[4]
        def swapped(ev): ev[2], ev[3] = ev[3], ev[2]
        def other_run(ev): ev[3]["run_id"] = "someone-else"
        for name, fn, needle in [("missing field", missing, "missing field 'motion'"), ("level vs risk", bad_level, "does not match risk"),
                                 ("bad enum", bad_enum, "'level'"), ("wrong type", bad_type, "'count'"),
                                 ("out of range", out_of_range, "'risk'"), ("bad hotspot", bad_hotspot, "'hotspot'"),
                                 ("alert without message", no_message, "'message'"), ("edited value", edited, "event_hash does not match"),
                                 ("lost event", gap, "seq should be"), ("reordered", swapped, "seq should be"),
                                 ("event moved to another run", other_run, "seq should be")]:
            msgs = problems(fn)
            assert any(needle in m for m in msgs), (name, msgs[:3])
        # a snapshot that was changed is caught with --snapshots
        snap = run / events[1]["heatmap"]
        snap.write_bytes(snap.read_bytes() + b"\0")
        assert any("does not match evidence_hash" in m for m in ct.check_run(events, run))
        assert ct.main([str(run), "--snapshots", str(run)]) == 1

        # round trip through a backend: unchanged passes; rounding a value, or dropping an event, is caught
        assert ct.compare(events, json.loads(json.dumps(events))) == []
        assert ct.compare(events, [dict(e, received_at="2026-11-20T10:00:00Z") for e in events]) == []   # extras are fine
        rounded = json.loads(json.dumps(events))
        rounded[2]["risk"] = round(rounded[2]["risk"])
        assert any("'risk' changed" in m for m in ct.compare(events, rounded)) or rounded[2]["risk"] == events[2]["risk"]
        assert any("did not return" in m for m in ct.compare(events, events[:-1]))
        as_int = json.loads(json.dumps(events).replace('"motion": 0.0', '"motion": 0'))
        assert ct.compare(events, as_int) == []                        # 0.0 vs 0 is the same number
        # --fetch reads JSON lines and JSON lists
        for body in ("\n".join(json.dumps(e) for e in events), json.dumps(events)):
            assert ct.parse_events_text(body) == events


def test_schema_file_agrees_with_contract_test():
    import contract_test as ct
    here = Path(__file__).resolve().parent
    path = next(p for p in (here / "contract" / "event.schema.json", here.parent / "contract" / "event.schema.json")
                if p.exists())                       # next to the code, or one level up
    schema = json.loads(path.read_text())
    assert set(schema["required"]) == set(ct.FIELDS), set(schema["required"]) ^ set(ct.FIELDS)
    props = schema["properties"]
    assert set(ct.FIELDS) | set(ct.ALERT_FIELDS) <= set(props)
    assert props["type"]["enum"] == list(ct.TYPES) and props["level"]["enum"] == list(ct.LEVELS)
    assert props["alert_kind"]["enum"] == list(ct.ALERT_KINDS) and props["schema_version"]["const"] == ct.SCHEMA_VERSION
    assert schema["allOf"][0]["then"]["required"] == ["alert_kind", "message"]
    assert schema["additionalProperties"] is True                      # new fields must not break old readers


def test_post_event_to_local_api():
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/events"
    assert cr.post_event(url, {"type": "crowd_risk", "risk": 50})
    srv.server_close()
    assert got == [{"type": "crowd_risk", "risk": 50}]
    assert cr.post_event("http://127.0.0.1:9/nothing", {}, timeout=0.5) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
