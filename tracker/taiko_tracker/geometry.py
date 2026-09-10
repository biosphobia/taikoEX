"""Turn a detected circle into a 3D position.

Camera space: x right, y down, z forward (out of the lens), metres.
World space:  x = player's right, y = up, z = away from the camera, metres.
The camera->world transform comes from the world calibration (see
``WorldCalibration``) and is stored in the config under ``world``.
"""

from __future__ import annotations

import math

import numpy as np


class PinholeModel:
    """Sphere size -> distance, pixel -> ray.  All parameters come from ``optics``."""

    def __init__(self, optics: dict, image_width: int, image_height: int):
        self.focal = float(optics["focal_px"])
        self.cx = float(optics["cx"]) if optics.get("cx", -1) >= 0 else image_width / 2.0
        self.cy = float(optics["cy"]) if optics.get("cy", -1) >= 0 else image_height / 2.0
        self.sphere_radius = float(optics["sphere_radius_m"])
        self.k1 = float(optics.get("k1", 0.0))

    def undistort(self, x: float, y: float) -> tuple[float, float]:
        """Remove simple radial distortion from normalised coordinates."""
        if self.k1 == 0.0:
            return x, y
        r2 = x * x + y * y
        factor = 1.0 + self.k1 * r2
        return x / factor, y / factor

    def distance_from_radius(self, radius_px: float) -> float:
        """Distance from the camera centre to the sphere centre.

        A sphere of radius R at distance d subtends a half angle a with
        sin(a) = R / d.  Its silhouette in the image has radius f * tan(a).
        Solving for d gives the formula below (exact for a sphere on the
        optical axis, very close elsewhere).
        """
        if radius_px <= 0:
            return float("inf")
        return self.sphere_radius * math.sqrt(1.0 + (self.focal / radius_px) ** 2)

    def pixel_to_camera(self, x_px: float, y_px: float, radius_px: float) -> np.ndarray:
        nx, ny = self.undistort((x_px - self.cx) / self.focal, (y_px - self.cy) / self.focal)
        ray = np.array([nx, ny, 1.0])
        ray /= np.linalg.norm(ray)
        return ray * self.distance_from_radius(radius_px)

    def focal_from_known_distance(self, radius_px: float, distance_m: float) -> float:
        """Inverse of ``distance_from_radius``: used by the focal length calibration."""
        ratio = (distance_m / self.sphere_radius) ** 2 - 1.0
        return radius_px * math.sqrt(max(ratio, 0.0))


class WorldTransform:
    """Rigid transform camera -> world stored as a 3x3 rotation and a translation."""

    def __init__(self, world_cfg: dict):
        self.rotation = np.array(world_cfg.get("rotation", [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]), dtype=float)
        self.translation = np.array(world_cfg.get("translation", [0, 0, 0]), dtype=float)

    def to_world(self, cam_point: np.ndarray) -> np.ndarray:
        return self.rotation @ cam_point + self.translation

    def to_camera(self, world_point: np.ndarray) -> np.ndarray:
        return self.rotation.T @ (world_point - self.translation)

    def as_config(self) -> dict:
        return {
            "calibrated": True,
            "rotation": self.rotation.round(6).tolist(),
            "translation": self.translation.round(4).tolist(),
        }


def normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


class WorldCalibration:
    """Three point calibration.

    The player holds a controller at three spots, in camera space:

    * ``origin``  - where the drums should be centred (e.g. in front of the belly)
    * ``right``   - somewhere to the player's right, same height
    * ``forward`` - somewhere in front of the origin, towards the camera

    From these we build an orthonormal basis: x points right, y up and z away
    from the camera.  Any camera position and tilt is absorbed by this.
    """

    def __init__(self):
        self.points: dict[str, np.ndarray] = {}

    def capture(self, name: str, cam_point: np.ndarray) -> None:
        self.points[name] = np.array(cam_point, dtype=float)

    def is_complete(self) -> bool:
        return all(k in self.points for k in ("origin", "right", "forward"))

    def solve(self) -> WorldTransform:
        if not self.is_complete():
            raise ValueError("origin, right and forward must all be captured first")
        origin = self.points["origin"]
        x_axis = normalise(self.points["right"] - origin)
        towards_camera = self.points["forward"] - origin
        y_axis = normalise(np.cross(x_axis, towards_camera))
        z_axis = np.cross(x_axis, y_axis)
        rotation = np.vstack([x_axis, y_axis, z_axis])   # rows = world axes in camera coords
        translation = -rotation @ origin
        return WorldTransform({"rotation": rotation.tolist(), "translation": translation.tolist()})


def default_world_transform() -> WorldTransform:
    return WorldTransform({})
