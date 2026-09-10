import numpy as np

from taiko_tracker.pads import HitDetector, Pad, single_drum_layout, taiko_layout


def make_detector(**overrides):
    cfg = {"mode": "plane", "min_speed_mps": 0.5, "rearm_height_m": 0.04, "cooldown_s": 0.05,
           "latency_compensation_ms": 0.0, "max_frame_gap_s": 0.25,
           "height_window_m": 0.15}
    cfg.update(overrides)
    return HitDetector([Pad.from_config(p) for p in taiko_layout()], cfg)


def stroke_down_and_back(detector, x, t0, dt=1 / 120, controller=0, lift=True):
    """Drive a controller down onto a pad and let it come back up, the way a
    hand does over an imaginary drum.  With ``lift=False`` it stays low
    afterwards, so the pad is not re-armed."""
    heights = [0.16, 0.10, 0.04, -0.01, -0.02, 0.02, 0.08] if lift else [0.16, 0.10, 0.04, -0.01, -0.02, 0.0, 0.02]
    hits = []
    previous = None
    for index, height in enumerate(heights):
        position = np.array([x, height, 0.0])
        velocity = None
        if previous is not None:
            velocity = (position - previous) / dt
        hits += detector.update(controller, position, t0 + index * dt, velocity)
        previous = position
    return hits


def stroke(detector, x, start_h, end_h, t0, dt=1 / 60):
    hits = []
    hits += detector.update(0, np.array([x, start_h, 0.0]), t0)
    hits += detector.update(0, np.array([x, end_h, 0.0]), t0 + dt)
    return hits


def test_downward_crossing_hits_nearest_pad():
    det = make_detector()
    hits = stroke(det, 0.12, 0.06, -0.02, 10.0)
    assert len(hits) == 1
    assert hits[0].pad_id == "right_don"
    assert hits[0].kind == "don" and hits[0].side == "right"


def test_crossing_time_is_interpolated():
    det = make_detector()
    hits = stroke(det, -0.30, 0.03, -0.01, 5.0, dt=0.02)
    assert len(hits) == 1
    assert abs(hits[0].time - (5.0 + 0.02 * 0.75)) < 1e-9
    assert hits[0].pad_id == "left_ka"


def test_slow_movement_does_not_hit():
    det = make_detector()
    hits = stroke(det, 0.10, 0.004, -0.001, 1.0)   # 0.3 m/s
    assert hits == []


def test_needs_rearm_before_second_hit():
    det = make_detector()
    assert len(stroke(det, 0.10, 0.06, -0.02, 1.0)) == 1
    # Wobble around the plane without rising above the rearm height -> nothing.
    assert det.update(0, np.array([0.10, 0.01, 0.0]), 1.10) == []
    assert det.update(0, np.array([0.10, -0.02, 0.0]), 1.12) == []
    # Rise above the rearm height, then strike again -> hit.
    det.update(0, np.array([0.10, 0.08, 0.0]), 1.30)
    assert len(det.update(0, np.array([0.10, -0.02, 0.0]), 1.32)) == 1


def test_missing_pad_area_is_a_miss():
    det = make_detector()
    assert stroke(det, 0.9, 0.06, -0.02, 1.0) == []


def test_upward_crossing_is_ignored():
    det = make_detector()
    det.update(0, np.array([0.10, -0.05, 0.0]), 1.0)
    assert det.update(0, np.array([0.10, 0.05, 0.0]), 1.02) == []



# --- stroke mode: the default, and the one that survives a noisy depth ------
def test_stroke_mode_fires_at_the_turning_point():
    det = make_detector(mode="stroke")
    hits = stroke_down_and_back(det, 0.10, 10.0)
    assert len(hits) == 1
    assert hits[0].pad_id == "right_don"
    # The bottom of the stroke is the fourth or fifth sample, around 30-40 ms in.
    assert 10.02 < hits[0].time < 10.05, hits[0].time


def test_stroke_mode_tolerates_a_wrong_height():
    """The whole point: the camera's distance estimate can be badly off and the
    stroke is still detected, because only the turn-around matters."""
    det = make_detector(mode="stroke")
    hits = []
    heights = [0.36, 0.30, 0.24, 0.19, 0.18, 0.22, 0.28]   # the same stroke, read 20 cm too high
    previous = None
    for index, height in enumerate(heights):
        position = np.array([0.10, height, 0.0])
        velocity = (position - previous) * 120 if previous is not None else None
        hits += det.update(0, position, 10.0 + index / 120.0, velocity)
        previous = position
    assert hits == []          # 20 cm out is outside the height window
    det = make_detector(mode="stroke", height_window_m=0.25)
    hits = []
    previous = None
    for index, height in enumerate(heights):
        position = np.array([0.10, height, 0.0])
        velocity = (position - previous) * 120 if previous is not None else None
        hits += det.update(0, position, 20.0 + index / 120.0, velocity)
        previous = position
    assert len(hits) == 1 and hits[0].pad_id == "right_don"


def test_stroke_mode_ignores_a_slow_wave():
    det = make_detector(mode="stroke")
    hits = stroke_down_and_back(det, 0.10, 30.0, dt=1 / 4)     # the same path, very slowly
    assert hits == []


def test_stroke_mode_needs_rearm():
    det = make_detector(mode="stroke")
    assert len(stroke_down_and_back(det, 0.10, 40.0, lift=False)) == 1
    # Wobbling at the bottom without lifting away must not fire again.
    previous = None
    extra = []
    for index, height in enumerate([-0.01, -0.02, -0.01, -0.02, -0.01]):
        position = np.array([0.10, height, 0.0])
        velocity = (position - previous) * 120 if previous is not None else None
        extra += det.update(0, position, 40.2 + index / 120.0, velocity)
        previous = position
    assert extra == []
