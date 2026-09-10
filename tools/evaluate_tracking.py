#!/usr/bin/env python3
"""Measure how well the tracker does, against known truth, in every room.

The simulated camera knows exactly where it put the controllers, so the
whole pipeline can be scored: how much of the position error is left, how
many strokes are detected, and how steady the timing is.

    python tools/evaluate_tracking.py                     # every scene
    python tools/evaluate_tracking.py --scene far_shelf
    python tools/evaluate_tracking.py --set fusion.process_accel_with_imu_mps2=8

Each run does what a player does: two-point distance calibration, learn the
background with the LEDs off, three-point world calibration, then play.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tracker"))

from taiko_tracker.config import Config          # noqa: E402
from taiko_tracker.simulation import SCENES      # noqa: E402
from taiko_tracker.tracker import Tracker        # noqa: E402

# Alternate face, rim, face, rim - the pattern a taiko chart mostly is.
PAD_PATTERN = ["don", "ka", "don", "ka"]


def build(scene: str, overrides: dict) -> Tracker:
    data = {
        "camera": {"backend": "simulated", "fps": 60},
        # The virtual clock steps time by exactly one frame per read, so the
        # score does not depend on how busy the machine is - and a 187 fps
        # run is really 187 frames per simulated second, not whatever the
        # CPU managed.
        "simulation": {"scene": scene, "virtual_clock": True},
        "network": {"state_port": 47840, "command_port": 47841, "preview_port": 47842, "preview_enabled": False},
        "hid": {"enabled": False},
        "debug": {"print_hits": False},
    }
    config = Config(data)
    for dotted, value in overrides.items():
        config.set(dotted, value)
    return Tracker(config)




def settle(tracker: Tracker, seconds: float) -> None:
    """Run the tracker for a stretch of *camera time*.

    Waits are in seconds rather than frames so that the evaluation asks the
    same thing of a 187 fps camera as of a 60 fps one: a player holds still
    for a moment, not for a number of frames.
    """
    until = tracker.camera.clock() + seconds
    while tracker.camera.clock() < until:
        tracker.step()


def wait_visible(tracker: Tracker, controller: int = 0, timeout: float = 3.0) -> None:
    """Wait for the whole sphere to be seen - a player presses the button when
    the preview shows it, not while the arm is sweeping across."""
    until = tracker.camera.clock() + timeout
    while tracker.camera.clock() < until:
        tracked = tracker.controllers[controller]
        if tracked.visible and tracked.recent_radii:
            return
        tracker.step()


def calibrate(tracker: Tracker) -> dict:
    """The whole calibration a player performs, in the order the docs give.

    The background comes first on purpose.  Until the room's lamps, screens and
    posters are masked away, the tracker has no reason to prefer a small sphere
    over a big bright rectangle - and everything after this step depends on it
    measuring the sphere.
    """
    sim = tracker.camera
    # 1. Learn which parts of the room look like a controller.
    sim.goto(0, [-0.2, 0.25, 0.0])
    sim.goto(1, [0.2, 0.25, 0.0])
    settle(tracker, 0.1)
    tracker.handle_command({"cmd": "learn_background"})
    while tracker.background.active:
        tracker.step()
    settle(tracker, 0.1)
    # 2. Distance scale, from two distances straight out in front of the lens.
    tracker.handle_command({"cmd": "calibrate_distance_reset"})
    reply = {}
    for distance in (0.7, 1.5):
        point = sim.virtual.position + sim.virtual.rotation_cam_to_world[:, 2] * distance
        sim.goto(0, point.tolist())
        settle(tracker, 0.5)
        wait_visible(tracker)
        reply = tracker.handle_command({"cmd": "calibrate_distance", "controller": 0, "distance_m": distance})
        assert reply.get("ok"), reply
    optics = {"focal_px": reply.get("focal_px"), "radius_offset_px": reply.get("radius_offset_px")}
    # 3. World axes.
    for name, point in (("origin", [0, 0, 0]), ("right", [0.4, 0, 0]), ("forward", [0, 0, -0.4])):
        sim.goto(0, point)
        settle(tracker, 0.5)
        wait_visible(tracker)
        reply = tracker.handle_command({"cmd": "world_capture", "point": name, "controller": 0})
        assert reply.get("ok"), reply
    sim.goto(0, [-0.2, 0.2, 0.0])
    sim.goto(1, [0.2, 0.2, 0.0])
    settle(tracker, 0.35)
    return {"optics": optics, "background": tracker.background_result}


def measure_positions(tracker: Tracker, samples: int = 8) -> list[float]:
    """Park the controller at known spots and record the error at each."""
    sim = tracker.camera
    rng = np.random.default_rng(11)
    # Park the other hand well out of the way; this measures position accuracy,
    # not what happens when two spheres line up (see the crossing test).
    sim.goto(1, [1.2, 0.6, 0.0])
    errors = []
    for _ in range(samples):
        point = np.array([rng.uniform(-0.35, 0.35), rng.uniform(-0.05, 0.30), rng.uniform(-0.15, 0.15)])
        sim.goto(0, point)
        settle(tracker, 0.75)
        tracked = tracker.controllers[0]
        if tracked.visible:
            errors.append(float(np.linalg.norm(tracked.world_pos - point)))
    return errors


def measure_hits(tracker: Tracker, strokes: int = 24, interval: float = 0.32) -> dict:
    """Schedule alternating strokes and see which are detected, and when."""
    hits: list = []
    tracker.on_hit = hits.append
    clock = tracker.camera.clock
    start = clock() + 0.6
    plan = []
    available = {pad["kind"]: pad["id"] for pad in tracker.config["pads"]}
    for index in range(strokes):
        pad = available.get(PAD_PATTERN[index % len(PAD_PATTERN)], tracker.config["pads"][0]["id"])
        controller = index % 2          # hands alternate, as they do when playing
        at = start + index * interval
        tracker.handle_command({"cmd": "sim_hit", "at": at, "pad": pad, "controller": controller})
        plan.append((at, pad))
    while clock() < start + strokes * interval + 0.5:
        tracker.step()

    latency = float(tracker.config["hits"]["latency_compensation_ms"]) / 1000.0
    matched, wrong_pad, offsets = 0, 0, []
    used = set()
    for at, pad in plan:
        best, best_gap = None, 0.12
        for index, hit in enumerate(hits):
            if index in used:
                continue
            gap = abs((hit.time + latency) - at)
            if gap < best_gap:
                best, best_gap = index, gap
        if best is None:
            continue
        used.add(best)
        matched += 1
        offsets.append(((hits[best].time + latency) - at) * 1000.0)
        if hits[best].pad_id != pad:
            wrong_pad += 1
    return {
        "scheduled": len(plan), "detected": len(hits), "matched": matched,
        "wrong_pad": wrong_pad, "extra": len(hits) - len(used),
        "offset_mean_ms": round(statistics.fmean(offsets), 1) if offsets else None,
        "offset_spread_ms": round(statistics.pstdev(offsets), 1) if len(offsets) > 1 else None,
    }


def run_scene(scene: str, overrides: dict) -> dict:
    tracker = build(scene, overrides)
    try:
        calibration = calibrate(tracker)
        errors = measure_positions(tracker)
        hits = measure_hits(tracker)
        return {
            "scene": scene,
            "description": SCENES[scene]["description"],
            "camera": tracker.camera.camera_pose_world(),
            "distance_m": round(float(np.linalg.norm(tracker.camera.virtual.position)), 2),
            "focal_px": calibration["optics"]["focal_px"],
            "radius_offset_px": calibration["optics"]["radius_offset_px"],
            "background": calibration["background"],
            "position_error_mean_mm": round(statistics.fmean(errors) * 1000, 1) if errors else None,
            "position_error_max_mm": round(max(errors) * 1000, 1) if errors else None,
            "hits": hits,
        }
    finally:
        tracker.close()


def parse_override(text: str):
    key, _, raw = text.partition("=")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    return key, value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", action="append", help="scene name (repeatable); default: all")
    parser.add_argument("--set", action="append", default=[], help="config override, e.g. fusion.enabled=false")
    parser.add_argument("--json", type=Path, help="also write the results here")
    args = parser.parse_args()

    overrides = dict(parse_override(item) for item in args.set)
    scenes = args.scene or list(SCENES)
    results = []
    header = f"{'scene':<13} {'dist':>5} {'pos err mm':>12} {'hits':>9} {'wrong':>6} {'extra':>6} {'timing ms':>16}"
    print(header)
    print("-" * len(header))
    for scene in scenes:
        result = run_scene(scene, overrides)
        results.append(result)
        hits = result["hits"]
        timing = f"{hits['offset_mean_ms']:+.0f} +/- {hits['offset_spread_ms']:.0f}" if hits["offset_spread_ms"] is not None else "-"
        print(f"{scene:<13} {result['distance_m']:>5.2f} "
              f"{result['position_error_mean_mm']:>6.0f}/{result['position_error_max_mm']:<5.0f} "
              f"{hits['matched']:>4}/{hits['scheduled']:<4} {hits['wrong_pad']:>6} {hits['extra']:>6} {timing:>16}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
