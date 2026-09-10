#!/usr/bin/env python3
"""Record a video of what the tracker sees while the simulated drummer plays.

Runs the real tracker pipeline (detection -> 3D -> pads -> hits) on the
simulated camera and writes the debug view to a video file:

    python tools/record_tracker_demo.py footage/tracker-view.mp4 --seconds 30
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tracker"))

from taiko_tracker.config import Config          # noqa: E402
from taiko_tracker.tracker import Tracker        # noqa: E402
from taiko_tracker.vision import draw_detections  # noqa: E402


def compose(tracker: Tracker, hits: list, fps: float) -> np.ndarray:
    """Camera view with detections on the left, colour masks on the right."""
    frame = draw_detections(tracker.last_frame, tracker.last_detections, tracker.config)
    h, w = frame.shape[:2]
    masks = [tracker.detector.last_masks.get(c.id) for c in tracker.controllers.values()]
    mask_view = np.zeros((h, w, 3), dtype=np.uint8)
    for controller, mask in zip(tracker.controllers.values(), masks):
        if mask is None:
            continue
        colour = np.array(list(reversed(controller.cfg["led"])), dtype=np.uint8)
        mask_view[mask > 0] = colour
    canvas = np.zeros((h + 130, w * 2, 3), dtype=np.uint8)
    canvas[:h, :w] = frame
    canvas[:h, w:] = mask_view
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(canvas, "camera + detections", (8, 20), font, 0.55, (255, 255, 255), 1)
    cv2.putText(canvas, "colour masks (what the tracker keeps)", (w + 8, 20), font, 0.55, (255, 255, 255), 1)
    y = h + 24
    cv2.putText(canvas, f"{fps:4.0f} fps   world calibrated: {tracker.config['world']['calibrated']}", (8, y), font, 0.55, (200, 200, 200), 1)
    for tracked in tracker.controllers.values():
        y += 22
        if tracked.visible:
            p = tracked.world_pos
            text = f"controller {tracked.id}: x {p[0]:+.2f}  y {p[1]:+.2f}  z {p[2]:+.2f} m   radius {tracked.pixel[2]:.1f} px"
        else:
            text = f"controller {tracked.id}: not visible"
        colour = tuple(int(v) for v in reversed(tracked.cfg["led"]))
        cv2.putText(canvas, text, (8, y), font, 0.55, colour, 1)
    y = h + 24
    for hit in hits[-4:]:
        cv2.putText(canvas, f"HIT {hit.pad_id:10s} hand {hit.controller_id}  {hit.speed:.1f} m/s", (w + 8, y), font, 0.55, (80, 255, 120), 1)
        y += 22
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--chart", type=Path, default=ROOT / "game/data/songs/demo/demo.tja")
    args = parser.parse_args()

    config = Config({
        "camera": {"backend": "simulated", "fps": 60},
        "processing": {"mask_polygons": [[[560, 10], [640, 10], [640, 70], [560, 70]]]},
        "hits": {"latency_compensation_ms": 0.0},
        "network": {"state_port": 47830, "command_port": 47831, "preview_port": 47832, "preview_enabled": False},
        "hid": {"enabled": False}, "debug": {"print_hits": False},
    }, ROOT / "build" / "unused_config.json")
    tracker = Tracker(config)
    hits: list = []
    tracker.on_hit = lambda hit: hits.append(hit)
    sim = tracker.camera

    # World calibration exactly like a player would do it.
    for name, point in (("origin", [0, 0, 0]), ("right", [0.4, 0, 0]), ("forward", [0, 0, -0.4])):
        sim.goto(0, point)
        for _ in range(8):
            tracker.step()
        tracker.handle_command({"cmd": "world_capture", "point": name, "controller": 0})
    sim.goto(0, [-0.2, 0.2, 0.0])
    sim.play_chart(args.chart.read_text(encoding="utf-8"), time.time() + 1.0)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = args.output.with_suffix(".raw.avi")
    writer = None
    start = time.time()
    frames = 0
    while time.time() - start < args.seconds:
        tracker.step()
        canvas = compose(tracker, hits, tracker.fps)
        if writer is None:
            writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"MJPG"), 60, (canvas.shape[1], canvas.shape[0]))
        writer.write(canvas)
        frames += 1
    writer.release()
    tracker.close()
    print(f"{frames} frames, {len(hits)} hits detected")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            print("ffmpeg not found, leaving", raw_path)
            return 0
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw_path), "-c:v", "libx264", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output)], check=True)
    raw_path.unlink()
    print("wrote", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
