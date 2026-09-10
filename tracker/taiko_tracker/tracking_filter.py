"""Smoothing that knows which direction is noisy.

A camera measures a sphere's *direction* very precisely and its *distance*
very poorly.  A 45 mm sphere 1.5 m from a 640x480 PS3 Eye is about 8 px
across; half a pixel of radius error is a millimetre sideways but seven
centimetres in depth.  Averaging all three axes equally would either leave
depth jittering or make the whole position lag behind a fast stroke.

So the position goes through a constant-velocity Kalman filter whose
measurement noise is an ellipsoid stretched along the camera ray:

    sigma_lateral = distance * pixel_noise / focal
    sigma_depth   = |d(distance)/d(radius)| * radius_noise

Both come straight from the pinhole model, so the filter automatically
trusts a close, large sphere more than a distant, small one.

When the controller's IMU is available its measured acceleration (rotated
into world space with gravity removed) is used as the prediction input, so
the filter follows a fast stroke instead of lagging behind it.
"""

from __future__ import annotations

import numpy as np

from .imu import cross3


class RayKalman:
    """Constant-velocity Kalman filter for one controller, in world space."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.x = np.zeros(6)             # [position, velocity]
        self.P = np.eye(6) * 1.0
        self.initialised = False
        self.outlier_run = 0
        self.last_time = 0.0

    def reset(self) -> None:
        self.initialised = False
        self.outlier_run = 0

    def predict(self, dt: float, accel_world: np.ndarray | None = None) -> None:
        F = np.eye(6)
        F[0:3, 3:6] = np.eye(3) * dt
        self.x = F @ self.x
        if accel_world is not None:
            self.x[0:3] += 0.5 * accel_world * dt * dt
            self.x[3:6] += accel_world * dt
        # Constant-acceleration process noise (white acceleration model).
        sigma_a = float(self.cfg.get("process_accel_mps2", 40.0))
        if accel_world is not None:
            sigma_a = float(self.cfg.get("process_accel_with_imu_mps2", 12.0))
        q = sigma_a * sigma_a
        dt2, dt3, dt4 = dt * dt, dt ** 3, dt ** 4
        Q = np.zeros((6, 6))
        Q[0:3, 0:3] = np.eye(3) * (dt4 / 4.0) * q
        Q[0:3, 3:6] = np.eye(3) * (dt3 / 2.0) * q
        Q[3:6, 0:3] = np.eye(3) * (dt3 / 2.0) * q
        Q[3:6, 3:6] = np.eye(3) * dt2 * q
        self.P = F @ self.P @ F.T + Q

    def update(self, measurement: np.ndarray, R: np.ndarray) -> float:
        """Fold in one measurement.  Returns how many sigma away it was.

        A measurement that sits far outside the predicted uncertainty is not
        noise.  Either something briefly hid or clipped the sphere (a hand
        crossing in front makes it look half the size and therefore twice as
        far away), or the tracker latched onto a different object.  Such a
        frame is skipped and the filter coasts on its prediction.  If they keep
        coming, the world really has moved, so the filter restarts there rather
        than crawling towards it for a third of a second.
        """
        if not self.initialised:
            self.x[0:3] = measurement
            self.x[3:6] = 0.0
            self.P = np.eye(6)
            self.P[0:3, 0:3] = R
            self.P[3:6, 3:6] = np.eye(3) * 4.0
            self.initialised = True
            self.outlier_run = 0
            return 0.0
        H = np.zeros((3, 6))
        H[0:3, 0:3] = np.eye(3)
        innovation = measurement - H @ self.x
        S = H @ self.P @ H.T + R
        S_inv = np.linalg.inv(S)
        distance = float(np.sqrt(max(0.0, innovation @ S_inv @ innovation)))
        if distance > float(self.cfg.get("outlier_sigma", 6.0)):
            self.outlier_run += 1
            if self.outlier_run >= int(self.cfg.get("outlier_frames", 3)):
                self.initialised = False
                self.update(measurement, R)
            return distance
        self.outlier_run = 0
        K = self.P @ H.T @ S_inv
        self.x = self.x + K @ innovation
        self.P = (np.eye(6) - K @ H) @ self.P
        return distance

    @property
    def position(self) -> np.ndarray:
        return self.x[0:3]

    @property
    def velocity(self) -> np.ndarray:
        return self.x[3:6]


def measurement_covariance(model, radius_px: float, distance_m: float,
                           rotation_cam_to_world: np.ndarray, cfg: dict,
                           completeness: float = 1.0, trust_size: bool = True) -> np.ndarray:
    """Noise ellipsoid of one detection, expressed in world axes.

    ``rotation_cam_to_world`` is the world calibration's rotation matrix, so a
    tilted or sideways camera automatically puts its noisy axis where it
    really points.  ``completeness`` (0..1) comes from the blob measurement.
    """
    pixel_noise = float(cfg.get("pixel_noise_px", 0.35))
    radius_noise = float(cfg.get("radius_noise_px", 0.45))
    # A partly hidden sphere still gives a good *direction* but its size, and
    # therefore its distance, is not to be trusted.
    intact = float(cfg.get("completeness_full", 0.8))
    if completeness < intact:
        radius_noise *= min(float(cfg.get("occluded_noise_max", 8.0)), intact / max(completeness, 0.05))
    sigma_lateral = max(1e-4, distance_m * pixel_noise / model.focal)
    # d(distance)/d(radius) for distance = R * sqrt(1 + (f/r)^2)
    if radius_px > 0.5:
        derivative = model.sphere_radius * (model.focal ** 2) / (radius_px ** 3 * np.sqrt(1.0 + (model.focal / radius_px) ** 2))
    else:
        derivative = 10.0
    sigma_depth = max(sigma_lateral, abs(derivative) * radius_noise)
    if not trust_size:
        sigma_depth = float(cfg.get("unmeasured_depth_m", 5.0))
    R_cam = np.diag([sigma_lateral ** 2, sigma_lateral ** 2, sigma_depth ** 2])
    return rotation_cam_to_world @ R_cam @ rotation_cam_to_world.T


def radius_is_plausible(previous_radius: float, radius: float, distance_m: float,
                        dt: float, cfg: dict) -> bool:
    """Could the sphere's apparent size really have changed this much?

    Apparent radius and distance are inversely proportional, so a hand moving
    at ``max_speed_mps`` changes the radius by at most ``r * v * dt / D`` per
    frame - about half a pixel at arm's length and sixty frames a second.  A
    bigger jump than that did not come from the sphere moving: something
    crossed in front of it, the other controller passed over it, or the colour
    threshold clipped part of it.  The direction is still good, the size is
    not, so the caller stops trusting the distance for that frame.
    """
    if previous_radius <= 0 or radius <= 0 or dt <= 0:
        return True
    speed = float(cfg.get("max_speed_mps", 6.0))
    allowance = float(cfg.get("radius_noise_px", 0.45)) * 3.0
    limit = previous_radius * speed * dt / max(distance_m, 1e-3) + allowance
    return abs(radius - previous_radius) <= limit


def ray_aligned_rotation(direction_cam: np.ndarray, world_rotation: np.ndarray) -> np.ndarray:
    """Rotation whose third column is the camera ray, expressed in world axes.

    The noise ellipsoid is aligned with the *ray to the sphere*, not with the
    camera's optical axis - the two differ noticeably at the edge of a wide
    frame.
    """
    z = direction_cam / max(np.linalg.norm(direction_cam), 1e-9)
    helper = np.array([0.0, 1.0, 0.0]) if abs(z[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    x = cross3(helper, z)
    x /= max(np.linalg.norm(x), 1e-9)
    y = cross3(z, x)
    return world_rotation @ np.column_stack([x, y, z])
