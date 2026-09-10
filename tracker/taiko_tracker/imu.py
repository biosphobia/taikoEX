"""Orientation of a PS Move from its accelerometer and gyroscope.

A small complementary ("Mahony style") filter: the gyro is integrated into
a quaternion and the accelerometer slowly pulls it back so that "down" in
the controller frame matches gravity.  Yaw (heading) cannot be observed by
those two sensors, so it drifts slowly; pressing the MOVE button - or the
``recenter_orientation`` command - snaps the heading to "facing forward".

Frames
------
sensor : the controller's own axes as reported by the IMU
world  : the calibrated playing space (x right, y up, z away from the camera)

``handle_axis`` is the sensor-frame direction that points from the handle
towards the sphere.  Hold the controller upright and run
``imu_calibrate_upright`` to measure it instead of guessing.
"""

from __future__ import annotations

import math

import numpy as np

WORLD_UP = np.array([0.0, 1.0, 0.0])
WORLD_FORWARD = np.array([0.0, 0.0, -1.0])   # towards the camera


def quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_conjugate(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate vector v by quaternion q (w, x, y, z)."""
    qv = np.array([0.0, v[0], v[1], v[2]])
    return quat_multiply(quat_multiply(q, qv), quat_conjugate(q))[1:]


def quat_from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    norm = np.linalg.norm(axis)
    if norm < 1e-9 or abs(angle) < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    half = angle / 2.0
    return np.concatenate([[math.cos(half)], axis / norm * math.sin(half)])


def quat_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Shortest rotation taking unit vector a onto unit vector b."""
    dot = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if dot > 0.999999:
        return np.array([1.0, 0.0, 0.0, 0.0])
    if dot < -0.999999:
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 0.0, 1.0])
        return quat_from_axis_angle(axis, math.pi)
    axis = np.cross(a, b)
    return quat_from_axis_angle(axis, math.acos(dot))


class OrientationFilter:
    """Tracks one controller's orientation.  Feed it ``update(accel_g, gyro_rad_s, dt)``."""

    def __init__(self, imu_cfg: dict):
        self.cfg = imu_cfg
        self.handle_axis = np.array(imu_cfg.get("handle_axis", [0, 1, 0]), dtype=float)
        self.handle_axis /= max(np.linalg.norm(self.handle_axis), 1e-9)
        self.q = np.array([1.0, 0.0, 0.0, 0.0])      # sensor -> world
        self.gyro_bias = np.zeros(3)
        self.last_accel_g = np.array([0.0, 1.0, 0.0])
        self._still_samples: list[np.ndarray] = []
        self.initialised = False

    # ---- bias -------------------------------------------------------------
    def _update_bias(self, accel_g: np.ndarray, gyro: np.ndarray) -> None:
        """Average the gyro while the controller lies still to learn its bias."""
        still = abs(np.linalg.norm(accel_g) - 1.0) < 0.05 and np.linalg.norm(gyro - self.gyro_bias) < float(self.cfg.get("still_gyro_rad_s", 0.15))
        if still:
            self._still_samples.append(gyro.copy())
            if len(self._still_samples) >= int(self.cfg.get("bias_samples", 120)):
                self.gyro_bias = np.mean(self._still_samples, axis=0)
                self._still_samples.clear()
        else:
            self._still_samples.clear()

    # ---- main update ------------------------------------------------------
    def update(self, accel_g, gyro_rad_s, dt: float) -> np.ndarray:
        accel = np.asarray(accel_g, dtype=float)
        gyro = np.asarray(gyro_rad_s, dtype=float)
        self.last_accel_g = accel
        if bool(self.cfg.get("auto_bias", True)):
            self._update_bias(accel, gyro)
        gyro = gyro - self.gyro_bias
        norm = np.linalg.norm(accel)

        if not self.initialised and norm > 0.5:
            # First sample: align gravity, heading = forward.
            down_sensor = -accel / norm
            self.q = quat_between(down_sensor, -WORLD_UP)
            self.initialised = True
            self.recenter_yaw()
            return self.q

        # Accelerometer correction: only trust it near 1 g (no big linear acceleration).
        kp = float(self.cfg.get("accel_gain", 1.0))
        correction = np.zeros(3)
        if 0.75 < norm < 1.25 and kp > 0:
            measured_down = -accel / norm
            predicted_down = quat_rotate(quat_conjugate(self.q), -WORLD_UP)
            correction = np.cross(measured_down, predicted_down) * kp
        omega = gyro + correction
        dq = quat_from_axis_angle(omega, float(np.linalg.norm(omega)) * dt)
        self.q = quat_multiply(self.q, dq)
        self.q /= np.linalg.norm(self.q)
        return self.q

    # ---- helpers ----------------------------------------------------------
    def recenter_yaw(self) -> None:
        """Rotate about world up so the handle's tilt direction faces the camera."""
        handle_world = quat_rotate(self.q, self.handle_axis)
        flat = handle_world - WORLD_UP * np.dot(handle_world, WORLD_UP)
        if np.linalg.norm(flat) < 0.05:
            # Handle is vertical: use the sensor's z axis as "facing" instead.
            facing = quat_rotate(self.q, np.array([0.0, 0.0, 1.0]))
            flat = facing - WORLD_UP * np.dot(facing, WORLD_UP)
            if np.linalg.norm(flat) < 0.05:
                return
        flat /= np.linalg.norm(flat)
        angle = math.atan2(np.cross(flat, WORLD_FORWARD)[1], np.dot(flat, WORLD_FORWARD))
        self.q = quat_multiply(quat_from_axis_angle(WORLD_UP, angle), self.q)
        self.q /= np.linalg.norm(self.q)

    def handle_direction_world(self) -> np.ndarray:
        return quat_rotate(self.q, self.handle_axis)

    def as_list(self) -> list[float]:
        return [round(float(v), 5) for v in self.q]
