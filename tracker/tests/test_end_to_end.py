"""Run the whole tracker against the simulated camera."""
import time

import numpy as np

from taiko_tracker.config import Config
from taiko_tracker.tracker import Tracker


def make_tracker(**extra):
    # The simulated scene has a magenta "poster" in the top right corner;
    # mask it out exactly like you would for a real room.
    data = {"camera": {"backend": "simulated", "fps": 120},
            "simulation": {"virtual_clock": True},
            "processing": {"mask_polygons": [[[560, 10], [640, 10], [640, 70], [560, 70]]]},
            "network": {"state_port": 47950, "command_port": 47951, "preview_port": 47952, "preview_enabled": False},
            "hid": {"enabled": False}, "debug": {"print_hits": False}}
    data.update(extra)
    return Tracker(Config(data))


def run_frames(tracker, count=40):
    """Step the tracker.  40 frames at 120 fps is a third of a second, which is
    what the position filter needs to settle after the controller is teleported
    (a real hand cannot jump, so this only matters in tests)."""
    for _ in range(count):
        tracker.step()


def run_until(tracker, when: float) -> None:
    """Step until the camera's clock passes ``when``."""
    while tracker.camera.clock() < when:
        tracker.step()


def now(tracker) -> float:
    return tracker.camera.clock()


def calibrate_distance(tracker):
    """The two-point distance calibration a player does in the Space tab: hold
    the controller at two measured distances from the lens.  Two distances are
    needed to separate the focal length from the constant glow around the
    sphere, which otherwise scales every position."""
    sim = tracker.camera
    for point in ([0.0, 0.0, 0.35], [0.0, 0.0, -0.45]):
        sim.goto(0, point)
        run_frames(tracker, 12)
        distance = float(np.linalg.norm(sim.virtual.world_to_camera(np.array(point))))
        reply = tracker.handle_command({"cmd": "calibrate_distance", "controller": 0, "distance_m": distance})
        assert reply["ok"], reply
    assert "focal_px" in reply, reply


def calibrate_world(tracker, with_distance=True):
    sim = tracker.camera
    if with_distance:
        calibrate_distance(tracker)
    for name, point in (("origin", [0, 0, 0]), ("right", [0.4, 0, 0]), ("forward", [0, 0, -0.4])):
        sim.goto(0, point)
        run_frames(tracker, 20)
        reply = tracker.handle_command({"cmd": "world_capture", "point": name, "controller": 0})
        assert reply["ok"], reply
    assert tracker.config["world"]["calibrated"]


def test_world_calibration_through_the_camera():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        for point in ([0.15, 0.1, -0.05], [-0.25, 0.02, 0.1]):
            tracker.camera.goto(0, point)
            run_frames(tracker)
            world = tracker.controllers[0].world_pos
            assert np.allclose(world, point, atol=0.04), (world, point)
    finally:
        tracker.close()


def test_simulated_strokes_produce_timed_hits():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        hits = []
        start = now(tracker)
        plan = [(start + 0.4, "don", 1), (start + 0.8, "ka", 0),
                (start + 1.2, "don", 0), (start + 1.6, "ka", 1)]
        for at, pad, ctrl in plan:
            tracker.handle_command({"cmd": "sim_hit", "at": at, "pad": pad, "controller": ctrl})
        original = tracker.on_hit
        tracker.on_hit = lambda hit: (hits.append(hit), original(hit))
        run_until(tracker, start + 2.2)
        assert [h.pad_id for h in hits] == [p for _, p, _ in plan], [h.pad_id for h in hits]
        # Every hit lands within a frame or two of the truth, and - what
        # actually matters for rhythm - they are all late by about the same
        # amount, which is what hits.latency_compensation_ms cancels.
        latency = tracker.config["hits"]["latency_compensation_ms"] / 1000.0
        offsets = [(hit.time + latency) - at for hit, (at, _, _) in zip(hits, plan)]
        assert offsets, "no hits detected at all"
        assert max(abs(o) for o in offsets) < 0.040, offsets
        assert max(offsets) - min(offsets) < 0.020, offsets
    finally:
        tracker.close()


def test_osu_mode_taps_keys():
    tracker = make_tracker(osu={"enabled": True})
    try:
        calibrate_world(tracker)
        tapped = []
        tracker.keys.tap = tapped.append
        tracker.handle_command({"cmd": "sim_hit", "at": now(tracker) + 0.4, "pad": "ka", "controller": 1})
        run_until(tracker, now(tracker) + 1.0)
        assert tapped == ["k"]   # right hand on the rim
    finally:
        tracker.close()


def test_place_pad_and_layout_commands():
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        tracker.camera.goto(1, [0.3, 0.05, -0.1])
        run_frames(tracker)
        reply = tracker.handle_command({"cmd": "place_pad", "pad": "don", "controller": 1})
        # A single depth reading at this distance is good to a few centimetres,
        # which is well inside a pad's radius - that is what it has to be good
        # enough for.
        assert reply["ok"] and np.allclose(reply["pad"]["center"], [0.3, 0.05, -0.1], atol=0.06)
        reply = tracker.handle_command({"cmd": "pad_layout_taiko", "center": [0, 0.1, 0]})
        assert reply["ok"] and reply["pads"][0]["center"] == [0.0, 0.1, 0.0]
        reply = tracker.handle_command({"cmd": "pad_layout_taiko", "center": [0, 0.1, 0], "style": "four_pads"})
        assert reply["ok"] and len(reply["pads"]) == 4
        reply = tracker.handle_command({"cmd": "nope"})
        assert not reply["ok"]
    finally:
        tracker.close()


def test_hits_are_detected_from_the_accelerometer_alone():
    """The controller's own accelerometer feels the stroke stop.  That path has
    to work without leaning on the camera's distance estimate, which is the
    measurement that degrades when the camera is far away or off to one side."""
    tracker = make_tracker(fusion={"use_imu_accel": False},
                           hits={"use_accelerometer": True, "min_speed_mps": 0.1})
    try:
        calibrate_world(tracker)
        hits = []
        tracker.on_hit = hits.append
        start = now(tracker)
        plan = [("don", 1), ("ka", 0), ("don", 0), ("ka", 1)]
        for index, (pad, controller) in enumerate(plan):
            tracker.handle_command({"cmd": "sim_hit", "at": start + 0.6 + 0.4 * index,
                                    "pad": pad, "controller": controller})
        run_until(tracker, start + 2.6)
        # Every stroke is felt, in order, with nothing spurious in between.
        assert [h.pad_id for h in hits] == [pad for pad, _ in plan], [h.pad_id for h in hits]
        assert all(h.strength > 0 for h in hits)
    finally:
        tracker.close()


def test_occluded_sphere_does_not_teleport():
    """A hand crossing in front makes the blob look small, which reads as far
    away.  The filter must ignore that frame rather than lurch backwards."""
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        tracker.camera.goto(0, [0.0, 0.1, 0.0])
        run_frames(tracker)
        before = tracker.controllers[0].world_pos.copy()
        tracked = tracker.controllers[0]
        covariance = np.eye(3) * 1e-4
        tracked.measure(tracker.last_detections[0], np.array([0.0, 0.0, 2.8]),
                        np.array([0.0, 0.1, 1.2]), covariance, tracked.last_seen + 1 / 120.0, None)
        assert np.linalg.norm(tracked.world_pos - before) < 0.05
    finally:
        tracker.close()


def test_one_sphere_passing_behind_the_other_does_not_throw_the_position():
    """The hands cross: for a few frames the magenta sphere is mostly hidden
    behind the cyan one and looks tiny, which naively reads as far away."""
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        sim = tracker.camera
        sim.goto(1, [0.0, 0.15, 0.0])
        sim.goto(0, [-0.35, 0.15, 0.0])
        run_frames(tracker)
        positions = []
        for step in range(60):
            # Sweep the left hand across the right one and out the other side.
            x = -0.35 + 0.7 * step / 59.0
            sim.goto(0, [x, 0.15, 0.02])
            tracker.step()
            positions.append(tracker.controllers[0].world_pos.copy())
        depth = np.array([p[2] for p in positions])
        # The sweep is at a constant depth, so nothing should lurch away.
        assert depth.max() - depth.min() < 0.15, (depth.min(), depth.max())
        assert abs(positions[-1][0] - 0.35) < 0.06, positions[-1]
    finally:
        tracker.close()


def test_fastest_camera_mode_keeps_tracking_and_timing():
    """The whole pipeline at the PS3 Eye's fastest mode, 320x240 at 187 fps.

    Switching modes keeps the calibration roughly right - every pixel setting
    is written for the reference width and scaled with the frame - and the
    documented order (fastest mode first, then the Space tab) makes it exact.
    """
    tracker = make_tracker()
    try:
        calibrate_world(tracker)
        focal_before = tracker.config["optics"]["focal_px"]
        reply = tracker.handle_command({"cmd": "camera_fastest", "seconds": 0.2})
        assert reply["ok"], reply
        assert reply["chosen"]["width"] == 320 and reply["chosen"]["fps_requested"] == 187, reply["chosen"]
        assert tracker.frame_size == (320, 240)
        assert tracker.config["optics"]["focal_px"] == focal_before      # stored at the reference width
        assert abs(tracker.model.focal - focal_before / 2) < 1e-6
        # The probe measures real time; the test goes back to the virtual clock.
        tracker.camera.virtual_clock = True
        state = tracker.build_state(now(tracker), [])
        assert state["fps_requested"] == 187 and state["fps_camera"] == 187 and state["frame"] == [320, 240]

        # Carried over from 640x480: right to well inside the drum face.
        tracker.camera.goto(0, [0.15, 0.1, -0.05])
        run_frames(tracker, 60)
        assert np.allclose(tracker.controllers[0].world_pos, [0.15, 0.1, -0.05], atol=0.2)

        # The Space tab redone at this resolution: as accurate as the big frame was.
        tracker.handle_command({"cmd": "world_reset"})
        calibrate_world(tracker)
        for point in ([0.15, 0.1, -0.05], [-0.25, 0.02, 0.1]):
            tracker.camera.goto(0, point)
            run_frames(tracker, 60)
            world = tracker.controllers[0].world_pos
            assert np.allclose(world, point, atol=0.05), (world, point)

        hits = []
        start = now(tracker)
        plan = [(start + 0.4, "don", 1), (start + 0.8, "ka", 0), (start + 1.2, "don", 0), (start + 1.6, "ka", 1)]
        for at, pad, ctrl in plan:
            tracker.handle_command({"cmd": "sim_hit", "at": at, "pad": pad, "controller": ctrl})
        original = tracker.on_hit
        tracker.on_hit = lambda hit: (hits.append(hit), original(hit))
        run_until(tracker, start + 2.2)
        assert [h.pad_id for h in hits] == [p for _, p, _ in plan], [h.pad_id for h in hits]
        latency = tracker.config["hits"]["latency_compensation_ms"] / 1000.0
        offsets = [(hit.time + latency) - at for hit, (at, _, _) in zip(hits, plan)]
        assert max(abs(o) for o in offsets) < 0.040, offsets
        assert max(offsets) - min(offsets) < 0.020, offsets
    finally:
        tracker.close()


def test_reset_config_keeps_the_controllers_and_pads():
    """Reset to defaults must leave a config that can track: two controllers,
    the drum, and every live object rebuilt from them."""
    tracker = make_tracker()
    try:
        tracker.handle_command({"cmd": "set_config", "patch": {"controllers": [{"id": 0, "name": "Only one", "led": [0, 255, 0],
                                                                                 "hsv_min": [45, 80, 100], "hsv_max": [80, 255, 255]}]}})
        assert len(tracker.controllers) == 1
        reply = tracker.handle_command({"cmd": "reset_config"})
        assert reply["ok"], reply
        assert [c["id"] for c in reply["config"]["controllers"]] == [0, 1]
        assert [p["id"] for p in reply["config"]["pads"]] == ["don", "ka"]
        assert sorted(tracker.controllers) == [0, 1]
        run_frames(tracker, 5)
        state = tracker.build_state(now(tracker), [])
        assert len(state["controllers"]) == 2
    finally:
        tracker.close()


def test_empty_controller_list_in_a_saved_config_falls_back_to_defaults():
    from taiko_tracker.config import Config

    config = Config({"controllers": [], "pads": []})
    assert [c["id"] for c in config["controllers"]] == [0, 1]
    assert [p["id"] for p in config["pads"]] == ["don", "ka"]
