#!/usr/bin/env python3
"""
plot_results.py - turn a crowd_risk run into a slide-ready chart.

  python plot_results.py out_concert_vd40 --png charts/concert_risk.png
  python plot_results.py out_jl030 --gt-csv eval_out/gt_jl030.csv --png charts/jl030.png

Top panel:    risk over time on LOW / MEDIUM / HIGH bands, with alert markers.
Bottom panel: detected people over time, plus ground truth if --gt-csv is given
              (CSV with columns t,gt; make one with make_gt_csv.py). The chart also
              prints the mean absolute error and correlation against the ground truth.
"""
import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

BANDS = [(0, 35, "#d8f0d8", "LOW"), (35, 65, "#ffe9c2", "MEDIUM"), (65, 100, "#f9d0cf", "HIGH")]
RISK_COLOR, COUNT_COLOR, GT_COLOR, ALERT_COLOR = "#1f4e79", "#2a7f62", "#555555", "#b3261e"


def load_events(out_dir):
    events = [json.loads(line) for line in (Path(out_dir) / "events.jsonl").read_text().splitlines() if line]
    risk = [e for e in events if e.get("type") == "crowd_risk"]
    alerts = [e for e in events if e.get("type") == "crowd_alert"]
    return risk, alerts


def load_gt(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return (np.array([float(r["t"]) for r in rows]), np.array([float(r["gt"]) for r in rows]))


def compare(t_events, counts, gt_t, gt):
    """Detected counts vs ground truth at the event times.

    Returns (MAE, mean detected/true, correlation or None). The correlation is only meaningful
    when the true crowd actually changes, so it is None if the ground truth varies < 15%."""
    g = np.interp(t_events, gt_t, gt)
    counts = np.asarray(counts, float)
    mae = float(np.abs(counts - g).mean())
    ratio = float((counts[g > 0] / g[g > 0]).mean()) if (g > 0).any() else float("nan")
    varies = g.mean() > 0 and g.std() / g.mean() >= 0.15
    corr = float(np.corrcoef(counts, g)[0, 1]) if varies and counts.std() > 0 else None
    return mae, ratio, corr


def plot(out_dir, png, gt_csv=None, title=None):
    risk, alerts = load_events(out_dir)
    if not risk:
        raise SystemExit(f"no crowd_risk events in {out_dir}/events.jsonl")
    t = np.array([e["video_t"] for e in risk])
    r = np.array([e["risk"] for e in risk])
    c = np.array([e["count"] for e in risk])
    summary_path = Path(out_dir) / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    for lo, hi, color, name in BANDS:
        ax1.axhspan(lo, hi, color=color, zorder=0)
        ax1.text(t.max() * 1.005 if t.max() else 1, (lo + hi) / 2, name, va="center", fontsize=8,
                 color="#444444")
    ax1.plot(t, r, color=RISK_COLOR, lw=2.2, marker="o", ms=3.5, zorder=3, label="risk score")
    for a in alerts:
        ax1.axvline(a["video_t"], color=ALERT_COLOR, ls="--", lw=1.2, zorder=2)
        ax1.annotate(a.get("alert_kind", "alert"), (a["video_t"], 2),
                     rotation=90, va="bottom", ha="right", fontsize=8, color=ALERT_COLOR)
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("risk (0-100)")
    d_crit = summary.get("d_crit")
    ax1.set_title(title or f"Crowd risk over time ({Path(out_dir).name}"
                  + (f", D_CRIT={d_crit:g}" if d_crit else "") + ")", fontsize=11, loc="left")

    ax2.plot(t, c, color=COUNT_COLOR, lw=2.2, marker="o", ms=3.5, label="detected people")
    note = ""
    if gt_csv:
        gt_t, gt = load_gt(gt_csv)
        ax2.plot(gt_t, gt, color=GT_COLOR, lw=1.8, ls="--", label="ground truth (people tracked)")
        mae, ratio, corr = compare(t, c, gt_t, gt)
        note = f"MAE {mae:.1f} people   detected/true {ratio:.2f}"
        note += f"   correlation {corr:.3f}" if corr is not None else "   (crowd size ~constant)"
        ax2.text(0.99, 0.04, note, transform=ax2.transAxes, ha="right", va="bottom", fontsize=9,
                 bbox=dict(boxstyle="round", fc="white", ec="#bbbbbb"))
    ax2.set_xlabel("video time (s)")
    ax2.set_ylabel("people")
    ax2.set_ylim(bottom=0)
    ax2.legend(loc="best", fontsize=8, frameon=False)
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    Path(png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, dpi=160)
    plt.close(fig)
    return note


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="a crowd_risk output folder")
    ap.add_argument("--png", default=None, help="output image (default: charts/<folder>.png)")
    ap.add_argument("--gt-csv", default=None, help="ground-truth CSV with columns t,gt")
    ap.add_argument("--title", default=None)
    a = ap.parse_args(argv)
    png = a.png or f"charts/{Path(a.out_dir).name}.png"
    note = plot(a.out_dir, png, a.gt_csv, a.title)
    print(f"wrote {png}" + (f"  ({note})" if note else ""))


if __name__ == "__main__":
    main()
