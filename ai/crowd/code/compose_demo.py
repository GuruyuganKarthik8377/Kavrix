#!/usr/bin/env python3
"""
compose_demo.py - render a "control room" demo video from a crowd_risk run, in the Kavrix site's look.

  python compose_demo.py out_concert_vd40 --theme light \
      --title "Outdoor concert crowd, drone view" --place "Tank Bund (demo location)" \
      --source "Recorded clip: Pexels 34004639, not a live feed" --out demos/concert_control_room.mp4

Left:   the drone view (the run's annotated_clean.mp4 if it exists, so there is no duplicate status card; else
        annotated.mp4) in a rounded card, with an alert banner over it when an alert fires.
Right:  a risk gauge with the level and the time-to-HIGH estimate, people / density / rise-rate tiles, and a
        risk-over-time chart that grows as the video plays, with alerts marked.
Bottom: the evidence chain (one block per event, each linked to the one before, with a CHAIN VERIFIED badge from
        verify_events) and the alert log.

Colours, radii and fonts follow the frontend (see style.py): --theme light or dark. Everything is drawn from
the run's own events.jsonl; nothing is invented. The gauge and forecast are interpolated between the one-per-
second readings so they move smoothly (the footer says so). The interface is drawn at 2x and shrunk, so edges
are smooth. Output is 1920x1080 H.264, so it plays in a browser and drops into a slide.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

import crowd_risk as cr
import style
import verify_events

W, H = 1920, 1080
S = 2                                      # the interface is drawn at 2x and shrunk
M = 32                                     # outer margin
VIDEO = (M, 88, 1216, 684)                 # card holding the drone view: x, y, w, h
GAUGE = (1272, 88, 616, 330)
TILES = (1272, 430, 192, 84)               # first tile; three of them, 20 px apart
CHART = (1272, 530, 616, 242)
CHAIN = (M, 788, 1216, 240)
LOG = (1272, 788, 616, 240)
TOAST = (VIDEO[0] + 20, VIDEO[1] + 20, VIDEO[2] - 40, 100)      # alert banner over the drone view
TOAST_SECONDS = 4.0
VIDEO_BG = (10, 10, 11)                    # letterbox colour inside the video card, in both themes


# ------------------------------------------------------------------ data helpers --
def load_run(run_dir):
    events = [json.loads(line) for line in (Path(run_dir) / "events.jsonl").read_text().splitlines() if line]
    if not events:
        raise SystemExit(f"{run_dir}/events.jsonl is empty")
    risk = [e for e in events if e["type"] == "crowd_risk"]
    alerts = [e for e in events if e["type"] == "crowd_alert"]
    return events, risk, alerts


def risk_at(risk_events, t):
    """Risk at video time t, interpolated between the two surrounding readings."""
    ts = [e["video_t"] for e in risk_events]
    return float(np.interp(t, ts, [e["risk"] for e in risk_events]))


def latest(events, t):
    """The newest event at or before time t, or the first one."""
    seen = [e for e in events if e["video_t"] <= t + 1e-6]
    return seen[-1] if seen else events[0]


def wrap_text(draw, text, fnt, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    return lines + ([line] if line else [])


def fit(frame_w, frame_h, box_w, box_h):
    s = min(box_w / frame_w, box_h / frame_h)
    w, h = int(round(frame_w * s)), int(round(frame_h * s))
    return (box_w - w) // 2, (box_h - h) // 2, w, h


def alert_bg(theme="light"):
    """The banner's fill colour (RGB) for a theme; tests use it to find the banner."""
    return style.Theme(theme).alert_bg


# ------------------------------------------------------------------------ canvas --
class Canvas:
    """Draws in 1x units onto an image that is S times larger (supersampling)."""

    def __init__(self, img, s=S):
        self.img, self.s = img, s
        self.d = ImageDraw.Draw(img, "RGBA")

    def _b(self, box):
        return tuple(v * self.s for v in box)

    def rrect(self, box, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._b(box), radius=r * self.s, fill=fill, outline=outline,
                                 width=max(1, round(width * self.s)))

    def rect(self, box, fill):
        self.d.rectangle(self._b(box), fill=fill)

    def line(self, pts, fill, width=1):
        self.d.line([(x * self.s, y * self.s) for x, y in pts], fill=fill, width=max(1, round(width * self.s)),
                    joint="curve")

    def arc(self, box, a0, a1, fill, width):
        self.d.arc(self._b(box), a0, a1, fill=fill, width=max(1, round(width * self.s)))

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.d.ellipse(self._b(box), fill=fill, outline=outline, width=max(1, round(width * self.s)))

    def font(self, role, size):
        return style.font(role, int(round(size * self.s)))

    def text(self, xy, s, role, size, fill, anchor="la"):
        self.d.text((xy[0] * self.s, xy[1] * self.s), s, font=self.font(role, size), fill=fill, anchor=anchor)

    def width(self, s, role, size):
        return self.d.textlength(s, font=self.font(role, size)) / self.s

    def wrap(self, text, role, size, max_w):
        return wrap_text(self.d, text, self.font(role, size), max_w * self.s)

    def pill(self, xy, text, role, size, fg, bg, pad=12, outline=None, dot=None):
        """A rounded chip with optional leading dot. Returns its width."""
        h = round(size * 2.0)
        w = self.width(text, role, size) + 2 * pad + (size if dot else 0)
        x, y = xy
        self.rrect((x, y, x + w, y + h), h / 2, fill=bg, outline=outline)
        tx = x + pad
        if dot:
            self.ellipse((tx, y + h / 2 - 4, tx + 8, y + h / 2 + 4), fill=dot)
            tx += size
        self.text((tx, y + h / 2), text, role, size, fg, anchor="lm")
        return w

    def dashed_v(self, x, y0, y1, fill, width=1.5, dash=6, gap=5):
        y = y0
        while y < y1:
            self.line([(x, y), (x, min(y + dash, y1))], fill, width)
            y += dash + gap


# -------------------------------------------------------------------- renderer --
class Renderer:
    def __init__(self, theme_name, title, place, source, note, verified, duration, t0):
        self.th = style.Theme(theme_name)
        self.title, self.place, self.source, self.note = title, place, source, note
        self.verified, self.duration, self.t0 = verified, max(duration, 1.0), t0
        self.base = self._build_base()
        vx, vy, vw, vh = VIDEO
        mask = Image.new("L", (vw * S, vh * S), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, vw * S - 1, vh * S - 1), radius=style.RADIUS * S, fill=255)
        self.mask = mask.resize((vw, vh), Image.LANCZOS)

    # ---- static parts, drawn once
    def _build_base(self):
        th = self.th
        img = Image.new("RGB", (W * S, H * S), th.bg)
        c = Canvas(img)
        # top bar: brand pill, title, status chips
        c.rrect((M, 16, M + 178, 58), 21, fill=th.surface, outline=th.line)
        style.draw_drone_icon(c.d, M + 16, 29, 26, th.ink, k=S)
        c.text((M + 52, 37), "Kavrix", "sans-bold", 18, th.ink, anchor="lm")
        c.text((M + 52 + c.width("Kavrix", "sans-bold", 18) + 6, 37), "/D-01", "sans", 16, th.muted, anchor="lm")
        c.text((W / 2, 37), self.title, "display", 26, th.ink, anchor="mm")
        pw = c.width(self.place, "mono", 13) + 28
        c.rrect((W - M - pw, 18, W - M, 56), 19, fill=th.surface, outline=th.line)
        c.text((W - M - pw / 2, 37), self.place, "mono", 13, th.muted, anchor="mm")
        rw = c.width("RECORDED", "mono-bold", 12) + 52
        rx = W - M - pw - 14 - rw
        c.rrect((rx, 18, rx + rw, 56), 19, fill=th.surface, outline=th.line)
        c.ellipse((rx + 16, 33, rx + 24, 41), fill=style.LEVEL["MEDIUM"])
        c.text((rx + 34, 37), "RECORDED", "mono-bold", 12, th.ink, anchor="lm")
        # cards
        for box in (GAUGE, CHART, CHAIN, LOG):
            c.rrect((box[0], box[1], box[0] + box[2], box[1] + box[3]), style.RADIUS, fill=th.panel, outline=th.line)
        vx, vy, vw, vh = VIDEO
        c.rrect((vx, vy, vx + vw, vy + vh), style.RADIUS, fill=VIDEO_BG)
        # gauge track
        gx, gy, gw, gh = GAUGE
        c.text((gx + 24, gy + 22), "CROWD RISK", "mono-bold", 12, th.muted, anchor="lm")
        cx, cy, r = gx + gw / 2, gy + 180, 124
        for lo, hi, lvl in ((0, 35, "LOW"), (35, 65, "MEDIUM"), (65, 100, "HIGH")):
            c.arc((cx - r, cy - r, cx + r, cy + r), 180 + 1.8 * lo, 180 + 1.8 * hi,
                  style.mix(style.LEVEL[lvl], th.panel, 0.72), 22)
        # stat tiles
        for i, label in enumerate(("PEOPLE DETECTED", "PEAK DENSITY", "RISK RATE")):
            x, y, w, h = TILES[0] + i * (TILES[2] + 20), TILES[1], TILES[2], TILES[3]
            c.rrect((x, y, x + w, y + h), 16, fill=th.panel, outline=th.line)
            c.text((x + 16, y + 20), label, "mono-bold", 11, th.muted, anchor="lm")
        # chart frame: header, level bands, threshold lines, time ticks
        cx0, cy0, cw, ch = CHART
        c.text((cx0 + 24, cy0 + 22), "RISK OVER TIME", "mono-bold", 12, th.muted, anchor="lm")
        px0, py0, px1, py1 = self.plot_rect()
        for lo, hi, lvl in ((0, 35, "LOW"), (35, 65, "MEDIUM"), (65, 100, "HIGH")):
            ya, yb = py1 - (py1 - py0) * hi / 100, py1 - (py1 - py0) * lo / 100
            c.rect((px0, ya, px1, yb), style.mix(th.panel, style.LEVEL[lvl], 0.13))
        for v in (35, 65):
            y = py1 - (py1 - py0) * v / 100
            c.line([(px0, y), (px1, y)], style.mix(th.panel, th.muted, 0.35), 1)
            c.text((px1 - 4, y - 7), str(v), "mono", 10, th.muted, anchor="rm")
        step = max(1, round(self.duration / 6))
        for secs in range(0, int(self.duration) + 1, step):
            x = px0 + (px1 - px0) * secs / self.duration
            c.text((x, py1 + 12), f"{secs}s", "mono", 11, th.muted, anchor="mm")
        # chain and log frames
        c.text((CHAIN[0] + 24, CHAIN[1] + 24), "EVIDENCE CHAIN", "mono-bold", 12, th.muted, anchor="lm")
        badge, col = (("CHAIN VERIFIED", style.LEVEL["LOW"]) if self.verified else ("NOT VERIFIED", style.LEVEL["HIGH"]))
        c.pill((CHAIN[0] + 168, CHAIN[1] + 11), badge, "mono-bold", 11, (18, 18, 19), col, pad=12)
        c.text((CHAIN[0] + 24, CHAIN[1] + CHAIN[3] - 26),
               "Each block commits to the one before it (SHA-256). Editing any event breaks every block after it.",
               "sans", 13, th.muted, anchor="lm")
        c.text((LOG[0] + 24, LOG[1] + 24), "ALERT LOG", "mono-bold", 12, th.muted, anchor="lm")
        # footer
        c.text((M, H - 24), self.note, "sans", 13, th.muted, anchor="lm")
        c.text((W - M, H - 24), self.source, "sans", 13, th.muted, anchor="rm")
        return img

    def plot_rect(self):
        x, y, w, h = CHART
        return x + 24, y + 54, x + w - 24, y + h - 38

    # ---- per frame
    def render(self, video_bgr, t, events, risk_events, alerts):
        th = self.th
        ui = self.base.copy()
        c = Canvas(ui)
        cur = latest(events, t)
        risk = risk_at(risk_events, t)
        lvl = cr.risk_level(risk)
        col = style.LEVEL[lvl]
        text_col = {"LOW": th.text_low, "MEDIUM": th.text_med, "HIGH": th.text_high}

        # gauge: coloured fill, needle, number, level, forecast
        gx, gy, gw, gh = GAUGE
        cx, cy, r = gx + gw / 2, gy + 180, 124
        for lo, hi, name in ((0, 35, "LOW"), (35, 65, "MEDIUM"), (65, 100, "HIGH")):
            if risk > lo:
                c.arc((cx - r, cy - r, cx + r, cy + r), 180 + 1.8 * lo, 180 + 1.8 * min(hi, risk),
                      style.LEVEL[name], 22)
        ang = np.radians(180 + 1.8 * risk)
        c.line([(cx, cy), (cx + (r - 34) * np.cos(ang), cy + (r - 34) * np.sin(ang))], th.ink, 4)
        c.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=th.ink)
        c.ellipse((cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5), fill=th.panel)
        big = ("pixel", 92) if style.has_pixel_font() else ("display", 64)
        c.text((cx, cy + 46), f"{risk:.0f}", big[0], big[1], th.ink, anchor="mm")
        pw = c.width(lvl, "sans-bold", 15) + 30
        c.rrect((cx - pw / 2, cy + 82, cx + pw / 2, cy + 108), 13, fill=col)
        c.text((cx, cy + 95), lvl, "sans-bold", 15, (18, 18, 19), anchor="mm")
        eta = cur.get("time_to_high")           # counts down between readings so it agrees with the banner
        eta = None if eta is None else eta - (t - cur["video_t"])
        if eta is not None and eta >= 0.5 and risk < cr.LEVEL_HIGH:
            c.text((cx, cy + 128), f"HIGH in about {eta:.0f} s at this rate (estimate)", "sans-bold", 15,
                   th.text_med, anchor="mm")

        # tiles
        values = (f"{cur['count']}", f"{cur['density_peak']:.1f}", f"{cur['risk_rate']:+.1f}/s")
        for i, v in enumerate(values):
            x, y = TILES[0] + i * (TILES[2] + 20), TILES[1]
            c.text((x + 16, y + 52), v, "display", 29, th.ink, anchor="lm")

        # chart: the line grows with the video
        px0, py0, px1, py1 = self.plot_rect()
        t0 = risk_events[0]["video_t"]

        def pt(ts, rv):
            return px0 + (px1 - px0) * (ts - t0) / self.duration, py1 - (py1 - py0) * rv / 100

        tc = min(t, risk_events[-1]["video_t"])          # after the last reading the line stops at the plot edge
        seen = [e for e in risk_events if e["video_t"] <= t + 1e-6]
        pts = [pt(e["video_t"], e["risk"]) for e in seen] + [pt(tc, risk)]
        if len(pts) > 1:
            c.line(pts, th.ink, 2.5)
        for a in alerts:
            if a["video_t"] <= t + 1e-6:
                ax, _ = pt(a["video_t"], 0)
                c.dashed_v(ax, py0, py1, th.text_high, 1.5)
                c.text((ax + 5, py0 + 9), a["alert_kind"].replace("_", " "), "mono", 10, th.text_high, anchor="lm")
        px, py = pt(tc, risk)
        c.ellipse((px - 6, py - 6, px + 6, py + 6), fill=col, outline=th.ink, width=2)

        # evidence chain: the last seven events up to now
        seen_all = [e for e in events if e["video_t"] <= t + 1e-6] or [events[0]]
        last = seen_all[-7:]
        bw, gap = 150, 16
        for i, e in enumerate(last):
            x, y = CHAIN[0] + 24 + i * (bw + gap), CHAIN[1] + 52
            newest = e is last[-1]
            is_alert = e["type"] == "crowd_alert"
            c.rrect((x, y, x + bw, y + 130), 14, fill=th.panel_hi, outline=th.ink if newest else th.line,
                    width=2.5 if newest else 1)
            c.ellipse((x + 14, y + 14, x + 24, y + 24), fill=style.LEVEL[e["level"]])
            tag = ("ALERT  " if is_alert else "") + f"t {e['video_t']:.1f}s"
            c.text((x + 32, y + 19), tag, "sans-bold", 14, th.text_high if is_alert else th.ink, anchor="lm")
            c.text((x + 14, y + 46), f"risk {e['risk']:.0f}  {e['level'].title()}", "sans", 14, th.ink, anchor="lm")
            c.text((x + 14, y + 72), "hash", "mono", 11, th.muted, anchor="lm")
            c.text((x + 14, y + 90), e["event_hash"][:10], "mono-bold", 14, th.ink, anchor="lm")
            c.text((x + 14, y + 112), "prev " + e["prev_hash"][:6], "mono", 11, th.muted, anchor="lm")
            if i < len(last) - 1:
                c.line([(x + bw + 2, y + 65), (x + bw + gap - 2, y + 65)], th.muted, 2)

        # alert log: the latest two so far
        log = [a for a in alerts if a["video_t"] <= t + 1e-6][-2:]
        lx, ly = LOG[0] + 24, LOG[1] + 56
        if not log:
            c.text((lx, ly + 8), "No alerts yet.", "sans", 15, th.muted, anchor="lm")
        for a in reversed(log):
            c.text((lx, ly), f"{a['video_t']:.1f}s   {a['alert_kind'].replace('_', ' ')}", "sans-bold", 15,
                   th.text_high, anchor="lm")
            for line in c.wrap(a["message"], "sans", 14, LOG[2] - 48)[:3]:
                ly += 20
                c.text((lx, ly), line, "sans", 14, th.ink, anchor="lm")
            ly += 34

        frame = ui.reduce(S)                                           # 2x -> 1x: smooth edges

        # drone view with rounded corners, then its chips and the alert banner (drawn at 2x, shrunk)
        vx, vy, vw, vh = VIDEO
        fx, fy, fw, fh = fit(video_bgr.shape[1], video_bgr.shape[0], vw, vh)
        small = cv2.resize(video_bgr, (fw, fh), interpolation=cv2.INTER_AREA)
        tile = Image.new("RGB", (vw, vh), VIDEO_BG)
        tile.paste(Image.fromarray(small[:, :, ::-1]), (fx, fy))
        frame.paste(tile, (vx, vy), self.mask)

        layer = Image.new("RGBA", (vw * S, vh * S), (0, 0, 0, 0))
        lc = Canvas(layer)
        chip_bg, chip_fg = (14, 14, 15, 215), (241, 240, 236, 255)
        tw = lc.width("RECORDED CLIP", "mono-bold", 12) + 24 + 12
        lc.pill((fx + fw - tw - 16, fy + 16), "RECORDED CLIP", "mono-bold", 12, chip_fg, chip_bg, pad=12,
                dot=style.LEVEL["MEDIUM"] + (255,))
        lc.pill((fx + 16, fy + fh - 16 - 24), f"t {t:.1f} s", "mono-bold", 12, chip_fg, chip_bg, pad=12)
        active = [a for a in alerts if 0 <= t - a["video_t"] < TOAST_SECONDS]
        if active:
            a = active[-1]
            bx, by, bw_, bh_ = 20, 20, vw - 40, TOAST[3]
            lc.rrect((bx, by, bx + bw_, by + bh_), 16, fill=th.alert_bg + (248,), outline=style.LEVEL["HIGH"], width=2)
            lc.text((bx + 22, by + 22), "ALERT  " + a["alert_kind"].replace("_", " ").upper(), "sans-bold", 16,
                    th.text_high, anchor="lm")
            for i, line in enumerate(lc.wrap(a["message"], "sans", 18, bw_ - 44)[:2]):
                lc.text((bx + 22, by + 52 + i * 26), line, "sans", 18, th.ink, anchor="lm")
        layer = layer.reduce(S)
        region = frame.crop((vx, vy, vx + vw, vy + vh)).convert("RGBA")
        frame.paste(Image.alpha_composite(region, layer).convert("RGB"), (vx, vy), self.mask)
        return np.asarray(frame)[:, :, ::-1]


# ------------------------------------------------------------------------ main --
def compose(run_dir, out_path, title, place, source, note, start=0.0, seconds=None, video=None, theme="light"):
    run_dir = Path(run_dir)
    events, risk_events, alerts = load_run(run_dir)
    if not risk_events:
        raise SystemExit("the run has no crowd_risk events")
    t0 = events[0]["video_t"]
    duration = max(risk_events[-1]["video_t"] - t0, 1.0)
    verified = verify_events.verify(run_dir)["ok"]

    path = Path(video) if video else (run_dir / "annotated_clean.mp4"
                                      if (run_dir / "annotated_clean.mp4").exists() else run_dir / "annotated.mp4")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    first = int(start * fps)
    if first:
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    limit = int(seconds * fps) if seconds else None

    renderer = Renderer(theme, title, place, source, note, verified, duration, t0)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    writer, codec = cr.open_video_writer(out_path, fps, (W, H))
    n = 0
    while limit is None or n < limit:
        ok, frame = cap.read()
        if not ok:
            break
        writer.write(np.ascontiguousarray(renderer.render(frame, t0 + (first + n) / fps, events, risk_events, alerts)))
        n += 1
    cap.release()
    writer.release()
    return {"frames": n, "seconds": round(n / fps, 1), "fps": fps, "codec": codec, "verified": verified,
            "size": f"{W}x{H}", "theme": theme, "drone_video": path.name, "out": str(out_path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="a crowd_risk output folder (events.jsonl, annotated_clean.mp4 or annotated.mp4)")
    ap.add_argument("--out", default=None, help="output .mp4 (default: <run_dir>/control_room.mp4)")
    ap.add_argument("--theme", choices=["light", "dark"], default="light", help="the site's light or dark theme")
    ap.add_argument("--title", default="Crowd-risk demo")
    ap.add_argument("--place", default="Demo location")
    ap.add_argument("--source", default="Recorded clip, not a live feed")
    ap.add_argument("--note", default="Risk is relative to the people detected (about 89% of the true crowd on "
                    "VisDrone); levels are illustrative. Gauge and forecast are interpolated between readings.")
    ap.add_argument("--start", type=float, default=0.0, help="start this many seconds into the run")
    ap.add_argument("--seconds", type=float, default=None, help="render only this many seconds")
    ap.add_argument("--video", default=None, help="drone video to show (default: annotated_clean.mp4 or annotated.mp4)")
    a = ap.parse_args(argv)
    out = a.out or str(Path(a.run_dir) / "control_room.mp4")
    result = compose(a.run_dir, out, a.title, a.place, a.source, a.note, a.start, a.seconds, a.video, a.theme)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
