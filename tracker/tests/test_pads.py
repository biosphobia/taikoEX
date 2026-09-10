import numpy as np

from taiko_tracker.pads import HitDetector, Pad, taiko_layout


def make_detector(**overrides):
    cfg = {"min_speed_mps": 0.5, "rearm_height_m": 0.04, "cooldown_s": 0.05,
           "latency_compensation_ms": 0.0, "max_frame_gap_s": 0.25}
    cfg.update(overrides)
    return HitDetector([Pad.from_config(p) for p in taiko_layout()], cfg)


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
