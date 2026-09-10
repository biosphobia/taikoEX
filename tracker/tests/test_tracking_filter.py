import numpy as np

from taiko_tracker.geometry import PinholeModel
from taiko_tracker.tracking_filter import RayKalman, measurement_covariance, ray_aligned_rotation

MODEL = PinholeModel({"focal_px": 545, "cx": -1, "cy": -1, "sphere_radius_m": 0.0225}, 640, 480)
CFG = {"pixel_noise_px": 0.35, "radius_noise_px": 0.45, "process_accel_mps2": 40.0}


def test_depth_noise_dominates_lateral():
    R = measurement_covariance(MODEL, radius_px=9.0, distance_m=1.4, rotation_cam_to_world=np.eye(3), cfg=CFG)
    lateral, depth = np.sqrt(R[0, 0]), np.sqrt(R[2, 2])
    assert lateral < 0.002                      # under two millimetres sideways
    assert depth > 0.03                         # centimetres in depth
    assert depth / lateral > 20


def test_noise_shrinks_when_the_sphere_is_closer():
    far = measurement_covariance(MODEL, 6.0, 2.0, np.eye(3), CFG)
    near = measurement_covariance(MODEL, 18.0, 0.7, np.eye(3), CFG)
    assert np.sqrt(near[2, 2]) < np.sqrt(far[2, 2]) / 4


def test_ray_rotation_points_at_the_sphere():
    direction = np.array([0.3, -0.2, 1.0])
    rotation = ray_aligned_rotation(direction, np.eye(3))
    assert np.allclose(rotation[:, 2], direction / np.linalg.norm(direction))
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-9)


def test_filter_smooths_depth_far_more_than_lateral():
    rng = np.random.default_rng(3)
    kalman = RayKalman(CFG)
    R = measurement_covariance(MODEL, 9.0, 1.4, np.eye(3), CFG)
    sigma = np.sqrt(np.diag(R))
    truth, errors = np.array([0.1, 0.05, 1.4]), []
    for step in range(120):
        kalman.predict(1 / 60.0)
        kalman.update(truth + rng.normal(0, sigma), R)
        if step > 40:
            errors.append(kalman.position - truth)
    residual = np.std(errors, axis=0)
    # Depth is averaged down by about half (68 mm of measurement noise -> 37 mm)
    # while the lateral axes, which are already sub-millimetre, are left alone.
    assert residual[2] < sigma[2] * 0.6
    assert residual[0] < sigma[0] * 1.5


def test_filter_tracks_a_fast_stroke_without_lagging():
    kalman = RayKalman(CFG)
    R = measurement_covariance(MODEL, 9.0, 1.4, np.eye(3), CFG)
    dt, speed = 1 / 120.0, 2.5
    position = np.array([0.0, 0.2, 1.4])
    last_measurement = position.copy()
    for _ in range(60):
        kalman.predict(dt)
        kalman.update(position, R)
        last_measurement = position.copy()
        position = position - np.array([0.0, speed * dt, 0.0])
    # No lag against the most recent measurement, and the speed is recovered.
    assert abs(kalman.position[1] - last_measurement[1]) < 0.002
    assert abs(kalman.velocity[1] + speed) < 0.1


def test_filter_restarts_after_the_blob_jumps():
    kalman = RayKalman(dict(CFG, outlier_sigma=6.0, outlier_frames=3))
    R = measurement_covariance(MODEL, 9.0, 1.4, np.eye(3), CFG)
    here = np.array([0.0, 0.0, 1.4])
    for _ in range(30):
        kalman.predict(1 / 60.0)
        kalman.update(here, R)
    # The sphere is re-acquired 40 cm away: the filter must follow within a few frames.
    there = np.array([0.4, 0.0, 1.4])
    for _ in range(4):
        kalman.predict(1 / 60.0)
        kalman.update(there, R)
    assert np.linalg.norm(kalman.position - there) < 0.01


def test_a_single_bad_frame_is_ignored():
    kalman = RayKalman(CFG)
    R = measurement_covariance(MODEL, 9.0, 1.4, np.eye(3), CFG)
    here = np.array([0.0, 0.0, 1.4])
    for _ in range(40):
        kalman.predict(1 / 60.0)
        kalman.update(here, R)
    settled = kalman.position.copy()
    # A hand crosses in front: the sphere looks half as big, so twice as far.
    kalman.predict(1 / 60.0)
    kalman.update(np.array([0.0, 0.0, 2.8]), R)
    assert np.linalg.norm(kalman.position - settled) < 0.01
