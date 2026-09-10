"""Run the whole tracker against the simulated camera."""
import time

import numpy as np

from taiko_tracker.config import Config
from taiko_tracker.tracker import Tracker


def make_tracker(**extra):
    # The simulated scene has a magenta "poster" in the top right corner;
    # mask it out exactly like you would for a real room.
    data = {"camera": {"backend": "simulated", "fps": 120},
            "processing": {"mask_polygons": [[[560, 10], [640, 10], [640, 70], [560, 70]]]},
            "network": {"state_port": 47950, "command_port": 47951, "preview_port": 47952, "preview_enabled": False},
            "hid": {"enabled": False}, "debug": {"print_hits": False}}
    data.update(extra)
    return Tracker(Config(data))


def run_frames(tracker, count):
    for _ in range(count):
        tracker.step()


def calibrate_world(tracker):
    sim = tracker.camera
    for name, point in (("origin", [0, 0, 0]), ("right", [0.4, 0, 0]), ("forward", [0, 0, -0.4])):
        sim.goto(0, point)
        run_frames(tracker, 4)
        reply = tracker.handle_command({"cmd": "world_capture", "point": name, "controller": 0})
        assert reply["ok"], reply
    assert tracker.config["world"]["calibrated"]


def test_world_calibration_through_the_camera():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        for point in ([0.15, 0.1, -0.05], [-0.25, 0.02, 0.1]):
            tracker.camera.goto(0, point)
            run_frames(tracker, 6)
            world = tracker.controllers[0].world_pos
            assert np.allclose(world, point, atol=0.04), (world, point)
    finally:
        tracker.close()


def test_simulated_strokes_produce_timed_hits():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        hits = []
        now = time.time()
        plan = [(now + 0.4, "right_don", 1), (now + 0.7, "left_ka", 0), (now + 1.0, "left_don", 0), (now + 1.3, "right_ka", 1)]
        for at, pad, ctrl in plan:
            tracker.handle_command({"cmd": "sim_hit", "at": at, "pad": pad, "controller": ctrl})
        original = tracker.on_hit
        tracker.on_hit = lambda hit: (hits.append(hit), original(hit))
        while time.time() < now + 1.8:
            tracker.step()
        assert [h.pad_id for h in hits] == [p for _, p, _ in plan]
        latency = tracker.config["hits"]["latency_compensation_ms"] / 1000.0
        for hit, (at, _, _) in zip(hits, plan):
            assert abs((hit.time + latency) - at) < 0.012, (hit.time + latency - at)
    finally:
        tracker.close()


def test_osu_mode_taps_keys():
    tracker = make_tracker(osu={"enabled": True})
    try:
        calibrate_world(tracker)
        tapped = []
        tracker.keys.tap = tapped.append
        tracker.handle_command({"cmd": "sim_hit", "at": time.time() + 0.3, "pad": "right_ka", "controller": 1})
        end = time.time() + 0.8
        while time.time() < end:
            tracker.step()
        assert tapped == ["k"]
    finally:
        tracker.close()


def test_place_pad_and_layout_commands():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        tracker.camera.goto(1, [0.3, 0.05, -0.1])
        run_frames(tracker, 6)
        reply = tracker.handle_command({"cmd": "place_pad", "pad": "right_don", "controller": 1})
        assert reply["ok"] and np.allclose(reply["pad"]["center"], [0.3, 0.05, -0.1], atol=0.04)
        reply = tracker.handle_command({"cmd": "pad_layout_taiko", "center": [0, 0.1, 0]})
        assert reply["ok"] and reply["pads"][1]["center"] == [-0.1, 0.1, 0.0]
        reply = tracker.handle_command({"cmd": "nope"})
        assert not reply["ok"]
    finally:
        tracker.close()
