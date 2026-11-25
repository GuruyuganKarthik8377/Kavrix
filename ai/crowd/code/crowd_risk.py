#!/usr/bin/env python3
"""
crowd_risk.py - AI: Crowd and Scene

Turns a drone video into a crowd-crush risk score (0-100) per second, with a
density heatmap, an annotated video and signed-evidence-ready JSON events.

Pipeline per frame
  1. Detect people with YOLOv8 (class 0 = person) -> boxes -> head/centre points.
  2. Density: splat the points on a grid, Gaussian-blur, scale so that the value
     at a pixel = "people within roughly one sigma of that pixel". The peak of
     this map is the local crowd density (people per window).
  3. Motion: estimate the CAMERA motion (LK features + partial affine, RANSAC)
     and subtract it from dense optical flow. What remains is the crowd's own
     movement (surge / turbulence), not the drone's movement.
  4. Risk = 100 * d * (0.7 + 0.3 * m), where d = peak_density / D_CRIT and
     m = residual_motion / MAG_CRIT, both clipped to [0, 1]. Smoothed with an EMA.

Outputs (in --out):
  annotated.mp4        video with boxes, heat overlay and risk HUD
  events.jsonl         one crowd_risk event per --event-every seconds
  heatmaps/event_*.jpg annotated snapshot for each event (evidence_hash = sha256 of it)
  summary.json         run statistics (max risk, seconds per level, ...)

Tune D_CRIT / MAG_CRIT below (or with --d-crit / --mag-crit) for your clip.
"""
import argparse
import hashlib
import json
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

import style

# ---------------------------------------------------------------- tunables --
D_CRIT = 10.0          # people within one window that count as "critical" density
MAG_CRIT = 2.0         # residual flow (px, measured at FLOW_WIDTH) that counts as "turbulent"
WINDOW_FRAC = 0.04     # density window sigma, as a fraction of the working width
W_DENSITY = 0.7        # risk weight of density alone
W_MOTION = 0.3         # extra risk weight when the crowd is also moving erratically
LEVEL_MEDIUM = 35      # risk >= this -> MEDIUM
LEVEL_HIGH = 65        # risk >= this -> HIGH
ALERT_HIGH_HOLD = 3.0  # seconds risk must stay HIGH before a sustained_high alert fires
SURGE_RATE = 8.0       # risk points per second that count as a surge
SURGE_WINDOW = 2.0     # seconds over which the surge rate is measured
ALERT_COOLDOWN = 10.0  # minimum seconds between two surge alerts
ALERT_WARMUP = 3.0     # no alerts in the first seconds: risk smoothing and motion are still settling
SCHEMA_VERSION = 1       # version of the event format (see contract/event.schema.json)
FORECAST_MIN_RATE = 3.0  # risk points/s: slower rises give no "time to HIGH" forecast (below this the
                         # rise is mostly noise: it gave false forecasts on steady and falling clips)
FORECAST_MAX_S = 60.0    # forecasts further away than this are not shown
FLOW_WIDTH = 320       # optical flow is computed at this width (speed)
DENSITY_WIDTH = 480    # density grid is computed at this width (speed)
DEFAULT_LAT, DEFAULT_LNG = 17.3850, 78.4867   # Hyderabad, placeholder incident location

LEVEL_COLORS = {k: v[::-1] for k, v in style.LEVEL.items()}   # BGR, same colours as the frontend


# ------------------------------------------------------------ core functions --
def box_centers(boxes):
    """(N,4) xyxy boxes -> (N,2) points. Uses the upper-middle of the box (head area)."""
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
    if len(boxes) == 0:
        return np.zeros((0, 2), np.float32)
    cx = (boxes[:, 0] + boxes[:, 2]) / 2
    cy = boxes[:, 1] + 0.35 * (boxes[:, 3] - boxes[:, 1])
    return np.stack([cx, cy], axis=1)


def density_map(points, frame_shape, sigma_frac=WINDOW_FRAC, work_width=DENSITY_WIDTH):
    """Local crowd density. A lone person gives a peak of ~1; k people packed
    together give a peak of ~k. Returned at working resolution (not frame size)."""
    h, w = frame_shape[:2]
    scale = work_width / w if w > work_width else 1.0
    ww, hh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    grid = np.zeros((hh, ww), np.float32)
    for x, y in points:
        ix, iy = int(x * scale), int(y * scale)
        if 0 <= ix < ww and 0 <= iy < hh:
            grid[iy, ix] += 1.0
    sigma = max(1.0, sigma_frac * ww)
    heat = cv2.GaussianBlur(grid, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    return heat * (2.0 * np.pi * sigma * sigma)


def estimate_camera_motion(prev_gray, gray, mask=None):
    """Partial-affine (translation + rotation + zoom) camera motion prev -> curr.
    Returns a 2x3 float32 matrix; identity if it cannot be estimated."""
    identity = np.array([[1, 0, 0], [0, 1, 0]], np.float32)
    pts = cv2.goodFeaturesToTrack(prev_gray, maxCorners=300, qualityLevel=0.01,
                                  minDistance=8, mask=mask)
    if pts is None or len(pts) < 8:
        return identity
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None)
    ok = status.ravel() == 1
    if ok.sum() < 8:
        return identity
    M, _ = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC,
                                       ransacReprojThreshold=2.0)
    return identity if M is None else M.astype(np.float32)


def residual_flow(prev_gray, gray, M):
    """Per-pixel magnitude of optical flow after removing the camera motion M."""
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = gray.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    ex = M[0, 0] * xs + M[0, 1] * ys + M[0, 2] - xs
    ey = M[1, 0] * xs + M[1, 1] * ys + M[1, 2] - ys
    return np.linalg.norm(flow - np.stack([ex, ey], axis=-1), axis=2)


def motion_metric(res_mag, heat):
    """Mean residual motion, weighted by crowd density (so empty ground is ignored)."""
    if heat is None or heat.size == 0:
        return 0.0
    weights = cv2.resize(heat, (res_mag.shape[1], res_mag.shape[0]))
    total = float(weights.sum())
    if total < 1e-3:
        return 0.0
    return float((res_mag * weights).sum() / total)


def risk_score(peak_density, motion, d_crit=D_CRIT, mag_crit=MAG_CRIT):
    d = float(np.clip(peak_density / d_crit, 0.0, 1.0))
    m = float(np.clip(motion / mag_crit, 0.0, 1.0))
    return 100.0 * d * (W_DENSITY + W_MOTION * m)


def risk_level(risk):
    if risk >= LEVEL_HIGH:
        return "HIGH"
    if risk >= LEVEL_MEDIUM:
        return "MEDIUM"
    return "LOW"


class AlertState:
    """Turns the smoothed per-frame risk into a few alerts an officer can act on.

    sustained_high: risk stayed HIGH for `hold` seconds. Fires once, and re-arms only after
                    the risk has dropped below the MEDIUM level.
    surge:          risk rose by >= `surge_rate` points/second over `window` seconds while
                    already at MEDIUM or above. At most one per `cooldown` seconds.
    update(t, risk) -> list of alert kinds; `self.rate` holds the latest rise rate (points/s)."""

    def __init__(self, hold=ALERT_HIGH_HOLD, surge_rate=SURGE_RATE, window=SURGE_WINDOW,
                 cooldown=ALERT_COOLDOWN, warmup=ALERT_WARMUP):
        self.hold, self.surge_rate, self.window, self.cooldown = hold, surge_rate, window, cooldown
        self.warmup = warmup
        self.t_first = None
        self.hist = deque()
        self.high_since = None
        self.armed = True
        self.last_surge = -1e9
        self.rate = 0.0

    def update(self, t, risk):
        self.hist.append((t, risk))
        while self.hist and t - self.hist[0][0] > self.window:
            self.hist.popleft()
        t0, r0 = self.hist[0]
        self.rate = (risk - r0) / (t - t0) if t - t0 >= 0.5 * self.window else 0.0
        if self.t_first is None:
            self.t_first = t
        if t - self.t_first < self.warmup:       # readings are still settling: stay quiet
            return []

        alerts = []
        if risk >= LEVEL_HIGH:
            if self.high_since is None:
                self.high_since = t
            if self.armed and t - self.high_since >= self.hold:
                alerts.append("sustained_high")
                self.armed = False
        else:
            self.high_since = None
            if risk < LEVEL_MEDIUM:
                self.armed = True
        if (self.rate >= self.surge_rate and risk >= LEVEL_MEDIUM
                and t - self.last_surge >= self.cooldown):
            alerts.append("surge")
            self.last_surge = t
        return alerts


def time_to_high(risk, rate, high=LEVEL_HIGH, min_rate=FORECAST_MIN_RATE, max_s=FORECAST_MAX_S):
    """Estimated seconds until risk reaches HIGH if it keeps rising at `rate` points/s (a straight-line
    extrapolation, so an estimate, not a prediction). None if already HIGH, not rising at least
    `min_rate`, or further away than `max_s`."""
    if risk >= high or rate < min_rate:
        return None
    eta = (high - risk) / rate
    return round(eta, 1) if eta <= max_s else None


def hotspot_position(heat):
    """Normalised (x, y) in 0-1 of the densest spot, or None if nobody was detected."""
    if heat is None or heat.size == 0 or float(heat.max()) < 0.5:
        return None
    y, x = np.unravel_index(int(heat.argmax()), heat.shape)
    return {"x": round(float(x) / heat.shape[1], 3), "y": round(float(y) / heat.shape[0], 3)}


# ----------------------------------------------------------------- detector --
def tile_origins(width, height, tile, overlap=0.2):
    """Top-left corners of square tiles that cover the whole frame, with overlap."""
    tile = min(tile, width, height)
    step = max(1, int(tile * (1 - overlap)))
    xs = list(range(0, width - tile + 1, step))
    ys = list(range(0, height - tile + 1, step))
    if xs[-1] != width - tile:
        xs.append(width - tile)
    if ys[-1] != height - tile:
        ys.append(height - tile)
    return [(x, y) for y in ys for x in xs], tile


def merge_boxes(boxes, scores, iou=0.5):
    """Non-max suppression so people seen in two overlapping tiles count once."""
    boxes = np.asarray(boxes, np.float32).reshape(-1, 4)
    if len(boxes) == 0:
        return boxes
    xywh = np.stack([boxes[:, 0], boxes[:, 1], boxes[:, 2] - boxes[:, 0],
                     boxes[:, 3] - boxes[:, 1]], axis=1)
    keep = cv2.dnn.NMSBoxes(xywh.tolist(), np.asarray(scores, np.float32).tolist(), 0.0, iou)
    return boxes[np.asarray(keep, int).reshape(-1)]


def apply_clahe(img, clip=2.0, grid=8):
    """Local contrast boost on the lightness channel (helps shadowed, low-contrast areas)."""
    l, a, b = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2LAB))
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


class YoloDetector:
    """Wraps ultralytics YOLO. detect(frame, idx) -> (N,4) xyxy float array.

    tile > 0 runs YOLO on tile x tile pixel crops (each upscaled to imgsz) and merges
    the results. This finds far more people when they are tiny (high altitude)."""

    def __init__(self, model="yolov8s.pt", imgsz=1280, conf=0.25, classes=(0,), device=None,
                 tile=0, tile_overlap=0.2, max_det=3000, iou=None, augment=False, clahe=False):
        from ultralytics import YOLO  # imported here so tests need no torch
        self.model = YOLO(model)
        self.iou, self.augment, self.clahe = iou, augment, clahe
        self.imgsz, self.conf, self.device = imgsz, conf, device
        self.max_det = max_det  # ultralytics defaults to 300, which silently caps dense crowds
        self.classes = list(classes) if classes else None
        self.tile, self.tile_overlap = tile, tile_overlap

    def _predict(self, img):
        if self.clahe:
            img = apply_clahe(img)
        extra = {}
        if self.iou is not None:
            extra["iou"] = self.iou           # NMS overlap: higher keeps more touching neighbours
        if self.augment:
            extra["augment"] = True           # test-time augmentation (flips and scales), slower
        res = self.model.predict(img, imgsz=self.imgsz, conf=self.conf, classes=self.classes,
                                 device=self.device, max_det=self.max_det, verbose=False, **extra)[0]
        if res.boxes is None or len(res.boxes) == 0:
            return np.zeros((0, 4), np.float32), np.zeros(0, np.float32)
        return (res.boxes.xyxy.cpu().numpy().astype(np.float32),
                res.boxes.conf.cpu().numpy().astype(np.float32))

    def detect(self, frame, idx=0):
        if not self.tile:
            return self._predict(frame)[0]
        h, w = frame.shape[:2]
        origins, t = tile_origins(w, h, self.tile, self.tile_overlap)
        all_b, all_s = [], []
        for x0, y0 in origins:
            b, s = self._predict(frame[y0:y0 + t, x0:x0 + t])
            if len(b):
                b[:, [0, 2]] += x0
                b[:, [1, 3]] += y0
                all_b.append(b)
                all_s.append(s)
        if not all_b:
            return np.zeros((0, 4), np.float32)
        return merge_boxes(np.concatenate(all_b), np.concatenate(all_s))


class ColorBlobDetector:
    """Special-purpose detector for footage where people wear one bright colour of cap or
    helmet (e.g. the Jülich entrance experiments, orange caps, filmed from above).

    It thresholds the colour in HSV, then splits touching caps with a distance-transform peak
    search. It is NOT a general person detector; use YoloDetector for ordinary footage.
    detect(frame, idx) -> (N,4) xyxy boxes, built so that box_centers() returns each cap centre."""

    def __init__(self, hsv_low=(3, 150, 140), hsv_high=(18, 255, 255), min_radius_frac=0.0031):
        self.lo = np.array(hsv_low, np.uint8)
        self.hi = np.array(hsv_high, np.uint8)
        self.min_radius_frac = min_radius_frac

    def detect(self, frame, idx=0):
        h, w = frame.shape[:2]
        min_r = max(2, int(round(self.min_radius_frac * w)))
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lo, self.hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        k = 2 * min_r + 1
        peaks = (dist >= cv2.dilate(dist, np.ones((k, k), np.uint8))) & (dist >= min_r)
        n, _, _, cent = cv2.connectedComponentsWithStats(peaks.astype(np.uint8))
        boxes = []
        for cx, cy in cent[1:n]:
            r = max(float(dist[int(round(cy)), int(round(cx))]), min_r)
            bh = 2 * r
            boxes.append([cx - r, cy - 0.35 * bh, cx + r, cy + 0.65 * bh])
        return np.asarray(boxes, np.float32).reshape(-1, 4)


# ------------------------------------------------------------- drawing/events --
_HEAT_BGR, _HEAT_ALPHA = style.heat_lut()


def _draw_hotspot(out, hotspot, level):
    """Ring, cross and a small HOTSPOT label in the level colour, drawn anti-aliased."""
    h, w = out.shape[:2]
    u = max(0.6, w / 1920)
    hx, hy = int(hotspot["x"] * w), int(hotspot["y"] * h)
    r = int(30 * u)
    box = (hx - r - 14, hy - r - 14, 2 * r + 28 + int(150 * u), 2 * r + 28)
    color = style.LEVEL[level]

    def painter(d, ss, off):
        k = ss * u
        cx, cy = (off[0] + r + 14) * ss, (off[1] + r + 14) * ss
        d.ellipse((cx - r * ss, cy - r * ss, cx + r * ss, cy + r * ss), outline=color + (255,), width=max(2, int(3 * k)))
        for dx, dy in ((1, 0), (0, 1)):
            d.line((cx - (r + 10 * u) * ss * dx - 0, cy - (r + 10 * u) * ss * dy,
                    cx + (r + 10 * u) * ss * dx, cy + (r + 10 * u) * ss * dy), fill=color + (255,), width=max(1, int(2 * k)))
        d.ellipse((cx - 4 * k, cy - 4 * k, cx + 4 * k, cy + 4 * k), fill=color + (255,))
        f = style.font("mono-bold", int(13 * k))
        tw = d.textlength("HOTSPOT", font=f)
        x0, y0 = cx + (r + 8 * u) * ss, cy - 13 * k
        d.rounded_rectangle((x0, y0, x0 + tw + 30 * k, y0 + 26 * k), radius=13 * k, fill=(14, 14, 15, 225))
        d.ellipse((x0 + 9 * k, y0 + 9 * k, x0 + 17 * k, y0 + 17 * k), fill=color + (255,))
        d.text((x0 + 23 * k, y0 + 5 * k), "HOTSPOT", font=f, fill=(241, 240, 236, 255))

    style.paint_rgba(out, box, painter)


def draw_overlay(frame, boxes, heat, d_crit, level, hotspot=None):
    """The drone view with its overlays but no status panel: the density colours (green, amber, red: the
    level colours), thin person outlines and the hotspot marker."""
    out = frame.copy()
    h, w = out.shape[:2]
    if heat is not None and heat.size:
        idx = (np.clip(cv2.resize(heat / d_crit, (w, h)), 0.0, 1.0) * 255).astype(np.uint8)
        a = _HEAT_ALPHA[idx][..., None]
        out = (out * (1 - a) + _HEAT_BGR[idx] * a).astype(np.uint8)
    pts = np.asarray(boxes).reshape(-1, 4).astype(int)
    if len(pts):
        layer = out.copy()
        for x1, y1, x2, y2 in pts:
            cv2.rectangle(layer, (x1, y1), (x2, y2), (240, 240, 236), 1, cv2.LINE_AA)
        out = cv2.addWeighted(layer, 0.65, out, 0.35, 0)
    if hotspot and level != "LOW":
        _draw_hotspot(out, hotspot, level)
    return out


def draw_hud(frame, risk, level, count, peak, motion, d_crit, t, eta=None):
    """A small status card in the top-left corner (drawn in place): risk number, level pill, bar,
    readings and, when there is one, the time-to-HIGH estimate."""
    h, w = frame.shape[:2]
    u = max(0.6, w / 1920)
    pad, cw_u, ch_u = int(24 * u), 440, (150 if eta is None else 184)
    color = style.LEVEL[level]
    white, muted = (241, 240, 236, 255), (176, 176, 170, 255)

    def painter(d, ss, off):
        k = ss * u
        ox, oy = off[0] * ss, off[1] * ss

        def P(x, y):
            return (ox + x * k, oy + y * k)

        d.rounded_rectangle((*P(0, 0), *P(cw_u, ch_u)), radius=18 * k, fill=(14, 14, 15, 224),
                            outline=(255, 255, 255, 34), width=max(1, int(k)))
        d.text(P(22, 18), "CROWD RISK", font=style.font("mono-bold", int(12 * k)), fill=muted)
        num_font = style.font("sans-bold", int(58 * k))
        d.text(P(22, 34), f"{risk:.0f}", font=num_font, fill=white)
        nw = d.textlength(f"{risk:.0f}", font=num_font)
        pf = style.font("sans-bold", int(15 * k))
        pw = d.textlength(level, font=pf)
        px0 = ox + 22 * k + nw + 16 * k
        d.rounded_rectangle((px0, oy + 56 * k, px0 + pw + 26 * k, oy + 84 * k), radius=14 * k, fill=color + (255,))
        d.text((px0 + 13 * k, oy + 60 * k), level, font=pf, fill=(18, 18, 19, 255))
        d.rounded_rectangle((*P(22, 104), *P(cw_u - 22, 112)), radius=4 * k, fill=(255, 255, 255, 44))
        d.rounded_rectangle((*P(22, 104), *P(22 + (cw_u - 44) * min(1.0, max(0.0, risk / 100)) + 0.1, 112)),
                            radius=4 * k, fill=color + (255,))
        d.text(P(22, 124), f"people {count}   peak {peak:.1f}/{d_crit:g}   motion {motion:.1f}   t {t:.1f}s",
               font=style.font("mono", int(12.5 * k)), fill=muted)
        if eta is not None:
            d.text(P(22, 152), f"HIGH in about {eta:.0f} s at this rate (estimate)",
                   font=style.font("sans-medium", int(14 * k)), fill=style.LEVEL["MEDIUM"] + (255,))

    style.paint_rgba(frame, (pad, pad, int(cw_u * u), int(ch_u * u)), painter)
    return frame


def annotate(frame, boxes, heat, risk, level, count, peak, motion, d_crit, t, hotspot=None, eta=None, hud=True):
    out = draw_overlay(frame, boxes, heat, d_crit, level, hotspot)
    return draw_hud(out, risk, level, count, peak, motion, d_crit, t, eta) if hud else out


def make_event(*, risk, level, count, peak, motion, video_t, lat, lng, heatmap, evidence_hash,
               hotspot=None, risk_rate=0.0, event_type="crowd_risk", extra=None, eta=None):
    event = {
        "id": uuid.uuid4().hex[:12],
        "type": event_type,
        "lat": lat,
        "lng": lng,
        "risk": round(float(risk), 1),
        "level": level,
        "count": int(count),
        "density_peak": round(float(peak), 2),
        "motion": round(float(motion), 2),
        "risk_rate": round(float(risk_rate), 1),
        "time_to_high": eta,
        "hotspot": hotspot,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "video_t": round(float(video_t), 2),
        "heatmap": heatmap,
        "evidence_hash": evidence_hash,
    }
    if extra:
        event.update(extra)
    return event


GENESIS_HASH = "0" * 64   # prev_hash of the first event in a run


def canonical_json(v):
    """Canonical JSON that prints the same in Python and JavaScript, so a browser or any
    other backend can recompute event_hash: keys sorted, no spaces, and a float with no
    fractional part prints as an integer (Python's 0.0 is JavaScript's 0). Numbers must
    lie between 1e-6 and 1e15 in magnitude (ours are rounded to 1-3 decimals).
    verify_events.py and web/verifyEvents.mjs implement the same rules."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("NaN/Infinity cannot be hashed")
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(canonical_json(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v[k])
                              for k in sorted(v)) + "}"
    raise TypeError(f"cannot hash {type(v).__name__}")


def event_digest(event):
    """SHA-256 of an event's canonical JSON, excluding its own event_hash field.
    verify_events.py re-implements this on purpose, so an auditor needs no other code."""
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def chain_event(event, prev_hash):
    """Link an event to the one before it: edits, deletions, insertions and re-ordering
    anywhere in events.jsonl then break the chain."""
    event["prev_hash"] = prev_hash
    event["event_hash"] = event_digest(event)
    return event


def alert_message(kind, risk, count, rate, hold, eta=None):
    if kind == "surge":
        msg = (f"Rapid crowd build-up: risk rising {rate:.0f} points/s, now {risk:.0f}/100 "
               f"({count} people detected).")
        if eta is not None:
            msg += f" HIGH in about {eta:.0f} s at this rate (estimate)."
        return msg + " Check the hotspot."
    return (f"Crowd density critical: risk {risk:.0f}/100 held for {hold:.0f}+ s "
            f"({count} people detected). Send officers to the hotspot.")


def post_event(url, event, timeout=2.0):
    """POST an event as JSON. Returns True on success; never raises."""
    req = urllib.request.Request(url, data=json.dumps(event).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class EventSender:
    """Sends events to the backend from a background thread, so a slow or down backend never stalls the
    pipeline. Events go out in order, one at a time; each is retried with exponential backoff (0.5, 1, 2, 4 s)
    before it is given up on. If the queue is full (backend down for a long time) new events are dropped and
    counted. events.jsonl is always complete, so gaps can be back-filled with post_events.py; a gap shows up as a
    broken prev_hash link or a missing seq number. close() returns {sent, failed, dropped, pending}."""

    def __init__(self, url, timeout=2.0, retries=4, backoff=0.5, maxsize=2000):
        self.url, self.timeout, self.retries, self.backoff = url, timeout, retries, backoff
        self.q = queue.Queue(maxsize)
        self.sent = self.failed = self.dropped = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send(self, event):
        try:
            self.q.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while True:
            event = self.q.get()
            if event is None:
                return
            ok = False
            for attempt in range(self.retries + 1):
                if post_event(self.url, event, self.timeout):
                    ok = True
                    break
                if attempt < self.retries:
                    time.sleep(min(4.0, self.backoff * (2 ** attempt)))
            if ok:
                self.sent += 1
            else:
                self.failed += 1

    def close(self, timeout=10.0):
        """Wait up to `timeout` s for queued events to go out, then stop. Returns the counters."""
        try:
            self.q.put(None, timeout=1.0)
        except queue.Full:
            pass
        self._thread.join(timeout)
        pending = self.q.qsize() if self._thread.is_alive() else 0      # still queued after the wait
        return {"sent": self.sent, "failed": self.failed, "dropped": self.dropped, "pending": pending}


def open_video_writer(path, fps, size):
    """Open the output video as H.264 ('avc1') so a browser can play annotated.mp4 in the
    dashboard; fall back to mp4v (which browsers cannot play) if H.264 is unavailable.
    Returns (writer, codec_tag)."""
    for tag in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*tag), fps, size)
        if writer.isOpened():
            return writer, tag
        writer.release()
    raise SystemExit(f"cannot open a video writer for {path}")


# ------------------------------------------------------------------- pipeline --
def process_video(video, out_dir, detector, *, lat=DEFAULT_LAT, lng=DEFAULT_LNG,
                  d_crit=D_CRIT, mag_crit=MAG_CRIT, detect_every=1, event_every=1.0,
                  api=None, start=0.0, max_seconds=None, alpha=0.25, quiet=False,
                  realtime=False, speed=1.0, alert_hold=ALERT_HIGH_HOLD, surge_rate=SURGE_RATE,
                  alert_cooldown=ALERT_COOLDOWN, alert_warmup=ALERT_WARMUP, clean_video=False,
                  run_id=None, drone_id="D-01"):
    run_id = run_id or f"{re.sub(r'[^A-Za-z0-9_.-]', '-', Path(str(video)).stem)}-{uuid.uuid4().hex[:6]}"
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    idx = int(start * fps)
    if idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)

    out_dir = Path(out_dir)
    (out_dir / "heatmaps").mkdir(parents=True, exist_ok=True)
    writer, video_codec = open_video_writer(out_dir / "annotated.mp4", fps, (W, H))
    # optional second video without the status card, for compose_demo.py (it draws its own panel)
    clean_writer = open_video_writer(out_dir / "annotated_clean.mp4", fps, (W, H))[0] if clean_video else None
    if video_codec != "avc1" and not quiet:
        print("warning: H.264 is unavailable here, so annotated.mp4 uses mp4v and browsers cannot play it; "
              "re-encode it to H.264 before using it in the dashboard", file=sys.stderr)
    events_f = open(out_dir / "events.jsonl", "w")

    boxes = np.zeros((0, 4), np.float32)
    prev_gray, risk_s = None, None
    next_event_t = start
    n = n_events = n_alerts = 0
    counts, level_secs = [], {"LOW": 0.0, "MEDIUM": 0.0, "HIGH": 0.0}
    max_risk, t0 = 0.0, time.time()
    flow_scale = FLOW_WIDTH / W
    alerts = AlertState(alert_hold, surge_rate, SURGE_WINDOW, alert_cooldown, alert_warmup)
    max_lag = 0.0
    sender = EventSender(api) if api else None

    last_hash, chain_len = GENESIS_HASH, 0

    def emit(event):
        """Chain one event to the previous one, write it to events.jsonl and POST it if --api is set."""
        nonlocal last_hash, chain_len
        event["schema_version"] = SCHEMA_VERSION      # identity fields are part of the hashed event
        event["run_id"], event["drone_id"], event["seq"] = run_id, drone_id, chain_len
        chain_event(event, last_hash)
        last_hash = event["event_hash"]
        chain_len += 1
        events_f.write(json.dumps(event) + "\n")
        events_f.flush()
        if sender:
            sender.send(event)                        # non-blocking: a slow backend cannot stall the pipeline

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        if max_seconds is not None and t >= start + max_seconds:
            break

        if n % max(1, detect_every) == 0:
            boxes = np.asarray(detector.detect(frame, idx), np.float32).reshape(-1, 4)
        heat = density_map(box_centers(boxes), frame.shape)
        peak, count = float(heat.max()), len(boxes)

        gray = cv2.cvtColor(cv2.resize(frame, (FLOW_WIDTH, int(H * flow_scale))), cv2.COLOR_BGR2GRAY)
        motion = 0.0
        if prev_gray is not None:
            mask = np.full(gray.shape, 255, np.uint8)       # keep features off people
            for x1, y1, x2, y2 in (boxes * flow_scale).astype(int):
                mask[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = 0
            M = estimate_camera_motion(prev_gray, gray, mask)
            motion = motion_metric(residual_flow(prev_gray, gray, M), heat)
        prev_gray = gray

        raw = risk_score(peak, motion, d_crit, mag_crit)
        risk_s = raw if risk_s is None else (1 - alpha) * risk_s + alpha * raw
        level = risk_level(risk_s)
        max_risk = max(max_risk, risk_s)
        counts.append(count)
        level_secs[level] += 1.0 / fps
        fired = alerts.update(t, risk_s)
        hotspot = hotspot_position(heat)
        # no forecast during the warm-up either: the smoothed risk is still settling there
        settled = alerts.t_first is not None and t - alerts.t_first >= alert_warmup
        eta = time_to_high(risk_s, alerts.rate) if settled else None

        overlay = draw_overlay(frame, boxes, heat, d_crit, level, hotspot)
        shown = draw_hud(overlay.copy() if clean_writer else overlay, risk_s, level, count, peak, motion,
                         d_crit, t, eta)
        writer.write(shown)
        if clean_writer is not None:
            clean_writer.write(overlay)

        if t >= next_event_t:
            while next_event_t <= t:
                next_event_t += event_every
            jpg = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            rel = f"heatmaps/event_{n_events:04d}.jpg"
            (out_dir / rel).write_bytes(jpg)
            emit(make_event(risk=risk_s, level=level, count=count, peak=peak, motion=motion,
                            video_t=t, lat=lat, lng=lng, heatmap=rel, hotspot=hotspot,
                            risk_rate=alerts.rate, eta=eta,
                            evidence_hash=hashlib.sha256(jpg).hexdigest()))
            n_events += 1

        for kind in fired:
            jpg = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            rel = f"heatmaps/alert_{n_alerts:04d}.jpg"
            (out_dir / rel).write_bytes(jpg)
            emit(make_event(risk=risk_s, level=level, count=count, peak=peak, motion=motion,
                            video_t=t, lat=lat, lng=lng, heatmap=rel, hotspot=hotspot,
                            risk_rate=alerts.rate, eta=eta, evidence_hash=hashlib.sha256(jpg).hexdigest(),
                            event_type="crowd_alert",
                            extra={"alert_kind": kind,
                                   "message": alert_message(kind, risk_s, count, alerts.rate,
                                                            alert_hold, eta)}))
            n_alerts += 1
            if not quiet:
                print(f"  ALERT [{kind}] t={t:.1f}s risk={risk_s:.0f} people={count}")

        if realtime:                       # pace the loop like a live feed
            lag = time.time() - (t0 + (t - start) / speed)
            if lag < 0:
                time.sleep(-lag)
            else:
                max_lag = max(max_lag, lag)

        n += 1
        idx += 1
        if not quiet and n % 50 == 0:
            print(f"  t={t:6.1f}s  people={count:3d}  peak={peak:5.1f}  risk={risk_s:5.1f} {level}")

    cap.release()
    writer.release()
    if clean_writer is not None:
        clean_writer.release()
    events_f.close()
    api_stats = sender.close() if sender else {"sent": 0, "failed": 0, "dropped": 0, "pending": 0}
    if sender and not quiet and (api_stats["failed"] or api_stats["dropped"] or api_stats["pending"]):
        print(f"warning: {api}: sent {api_stats['sent']}, failed {api_stats['failed']}, dropped "
              f"{api_stats['dropped']}, still queued {api_stats['pending']}. events.jsonl is complete; "
              "back-fill with post_events.py", file=sys.stderr)

    elapsed = max(time.time() - t0, 1e-6)
    summary = {
        "video": str(video), "frames": n, "duration_s": round(n / fps, 1),
        "events": n_events, "alerts": n_alerts, "max_risk": round(max_risk, 1),
        "realtime": realtime, "max_lag_s": round(max_lag, 2),
        "chain_events": chain_len, "chain_head": last_hash, "video_codec": video_codec,
        "clean_video": clean_video,
        "mean_count": round(float(np.mean(counts)), 1) if counts else 0.0,
        "max_count": int(max(counts)) if counts else 0,
        "seconds_by_level": {k: round(v, 1) for k, v in level_secs.items()},
        "processing_fps": round(n / elapsed, 2),
        "d_crit": d_crit, "mag_crit": mag_crit, "api": api_stats,
        "api_failures": api_stats["failed"] + api_stats["dropped"] + api_stats["pending"],
        "run_id": run_id, "drone_id": drone_id, "schema_version": SCHEMA_VERSION,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Crowd-crush risk from drone video.")
    p.add_argument("video", help="input video file")
    p.add_argument("--out", default="out", help="output folder (default: out)")
    p.add_argument("--detector", choices=["yolo", "orange"], default="yolo",
                   help="yolo = general person detector (default); orange = special-purpose orange-cap "
                        "detector for the Jülich entrance experiments (not a general detector)")
    p.add_argument("--hsv-low", default="3,150,140", help="orange detector: HSV lower bound H,S,V")
    p.add_argument("--hsv-high", default="18,255,255", help="orange detector: HSV upper bound H,S,V")
    p.add_argument("--model", default="yolov8s.pt",
                   help="YOLO weights (default yolov8s.pt; try yolov8m.pt or a VisDrone model)")
    p.add_argument("--imgsz", type=int, default=1280, help="YOLO inference size (try 1920 for tiny people)")
    p.add_argument("--conf", type=float, default=0.25, help="detection confidence threshold")
    p.add_argument("--classes", default="0", help="comma-separated class ids counted as people "
                   "(COCO models: 0; VisDrone models: 0,1 = pedestrian,people)")
    p.add_argument("--tile", type=int, default=0,
                   help="run YOLO on tile x tile pixel crops (e.g. 480) to find tiny people; 0 = off")
    p.add_argument("--tile-overlap", type=float, default=0.2, help="tile overlap fraction")
    p.add_argument("--max-det", type=int, default=3000, help="max detections per image/tile")
    p.add_argument("--iou", type=float, default=None, help="YOLO NMS overlap threshold (default: model's 0.7)")
    p.add_argument("--augment", action="store_true", help="test-time augmentation (slower)")
    p.add_argument("--clahe", action="store_true", help="boost local contrast before detecting")
    p.add_argument("--device", default=None, help="e.g. cpu, mps, 0")
    p.add_argument("--detect-every", type=int, default=1, help="run YOLO every N frames (speed)")
    p.add_argument("--d-crit", type=float, default=D_CRIT, help="critical density (lower = more sensitive)")
    p.add_argument("--mag-crit", type=float, default=MAG_CRIT, help="critical residual motion")
    p.add_argument("--event-every", type=float, default=1.0, help="seconds between events")
    p.add_argument("--lat", type=float, default=DEFAULT_LAT)
    p.add_argument("--lng", type=float, default=DEFAULT_LNG)
    p.add_argument("--api", default=None, help="POST each event to this URL (the Haven backend)")
    p.add_argument("--run-id", default=None, help="id of this run/stream (default: <video name>-<random>)")
    p.add_argument("--drone-id", default="D-01", help="id of the drone that filmed it (default D-01)")
    p.add_argument("--start", type=float, default=0.0, help="start at this many seconds")
    p.add_argument("--max-seconds", type=float, default=None, help="process only this many seconds")
    p.add_argument("--realtime", action="store_true",
                   help="pace processing to the video's own clock, like a live drone feed "
                        "(events then reach the dashboard one per --event-every seconds)")
    p.add_argument("--speed", type=float, default=1.0, help="with --realtime: playback speed multiplier")
    p.add_argument("--alert-hold", type=float, default=ALERT_HIGH_HOLD,
                   help="seconds risk must stay HIGH before a sustained_high alert")
    p.add_argument("--surge-rate", type=float, default=SURGE_RATE,
                   help="risk points per second that trigger a surge alert")
    p.add_argument("--alert-cooldown", type=float, default=ALERT_COOLDOWN,
                   help="minimum seconds between surge alerts")
    p.add_argument("--clean-video", action="store_true",
                   help="also write annotated_clean.mp4 without the status card (for compose_demo.py)")
    p.add_argument("--alert-warmup", type=float, default=ALERT_WARMUP,
                   help="no alerts during the first N seconds while readings settle")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    classes = [int(c) for c in a.classes.split(",") if c.strip() != ""]
    if a.detector == "orange":
        det = ColorBlobDetector(tuple(int(v) for v in a.hsv_low.split(",")),
                                tuple(int(v) for v in a.hsv_high.split(",")))
    else:
        det = YoloDetector(a.model, a.imgsz, a.conf, classes, a.device, a.tile,
                           a.tile_overlap, a.max_det, a.iou, a.augment, a.clahe)
    s = process_video(a.video, a.out, det, lat=a.lat, lng=a.lng, d_crit=a.d_crit,
                      mag_crit=a.mag_crit, detect_every=a.detect_every,
                      event_every=a.event_every, api=a.api, start=a.start,
                      max_seconds=a.max_seconds, realtime=a.realtime, speed=a.speed,
                      alert_hold=a.alert_hold, surge_rate=a.surge_rate,
                      alert_cooldown=a.alert_cooldown, alert_warmup=a.alert_warmup,
                      clean_video=a.clean_video, run_id=a.run_id, drone_id=a.drone_id)
    print(json.dumps(s, indent=2))
    print(f"\nWrote {a.out}/annotated.mp4, events.jsonl, heatmaps/, summary.json")


if __name__ == "__main__":
    main()
