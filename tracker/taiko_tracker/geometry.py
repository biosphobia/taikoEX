"""Turn a detected circle into a 3D position.

Camera space: x right, y down, z forward (out of the lens), metres.
World space:  x = player's right, y = up, z = away from the camera, metres.
The camera->world transform comes from the world calibration (see
``WorldCalibration``) and is stored in the config under ``world``.
"""

from __future__ import annotations

import math

import numpy as np


def pixel_scale(optics: dict, image_width: int) -> float:
    """How much bigger (or smaller) things look at this resolution than at the
    reference width the config's pixel values are written for."""
    reference = float(optics.get("reference_width", 0) or image_width)
    return image_width / max(1.0, reference)


class PinholeModel:
    """Sphere size -> distance, pixel -> ray.  All parameters come from ``optics``."""

    def __init__(self, optics: dict, image_width: int, image_height: int):
        # The config gives its pixel values for a frame ``reference_width``
        # wide; a camera running at 320x240 sees everything half the size.
        self.pixel_scale = pixel_scale(optics, image_width)
        self.focal = max(1.0, float(optics["focal_px"]) * self.pixel_scale)
        self.cx = float(optics["cx"]) * self.pixel_scale if optics.get("cx", -1) >= 0 else image_width / 2.0
        self.cy = float(optics["cy"]) * self.pixel_scale if optics.get("cy", -1) >= 0 else image_height / 2.0
        self.sphere_radius = float(optics["sphere_radius_m"])
        self.k1 = float(optics.get("k1", 0.0))
        # The glow is a per-pixel effect - the blended pixels around the edge
        # of the sphere - so it is the same number of pixels at any resolution.
        self.radius_offset = float(optics.get("radius_offset_px", 0.0))

    def to_reference_px(self, value_px: float) -> float:
        """Convert a measurement in live-frame pixels to the config's reference width."""
        return value_px / self.pixel_scale

    def true_radius(self, measured_radius_px: float) -> float:
        """Remove the constant bias from a measured blob radius.

        A glowing sphere always measures slightly *bigger* than it is: the
        pixels at its edge are a blend of sphere and background, and enough of
        them pass the colour threshold to add roughly a pixel all the way
        round.  The amount depends on the camera and the LED brightness but not
        on where the sphere is, so it is one number - measure it once with the
        two-point distance calibration.
        """
        return measured_radius_px - self.radius_offset

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
        radius_px = self.true_radius(radius_px)
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
        return self.true_radius(radius_px) * math.sqrt(max(ratio, 0.0))

    def apparent_radius(self, distance_m: float) -> float:
        """How big the sphere should look at a given distance, before the bias."""
        span = distance_m ** 2 - self.sphere_radius ** 2
        if span <= 0:
            return float("inf")
        return self.focal * self.sphere_radius / math.sqrt(span)


def solve_focal_and_offset(samples: list[tuple[float, float]], sphere_radius_m: float) -> tuple[float, float]:
    """Fit ``measured_radius = focal * k(distance) + offset`` to two or more samples.

    Each sample is ``(measured_radius_px, distance_m)``.  Two distances that
    are far apart (say 0.6 m and 1.6 m) separate the focal length from the
    constant glow, which one distance alone cannot do.
    """
    if len(samples) < 2:
        raise ValueError("need at least two distances")
    k = np.array([sphere_radius_m / math.sqrt(max(1e-9, d ** 2 - sphere_radius_m ** 2)) for _, d in samples])
    radii = np.array([r for r, _ in samples])
    distances = [d for _, d in samples]
    if max(distances) / max(1e-6, min(distances)) < 1.15:
        raise ValueError("the two distances are too close together (use something like 0.6 m and 1.5 m)")
    design = np.column_stack([k, np.ones(len(samples))])
    (focal, offset), *_ = np.linalg.lstsq(design, radii, rcond=None)
    if not np.isfinite(focal) or focal <= 1.0:
        raise ValueError("that fit gives a nonsensical focal length; re-measure the distances")
    return float(focal), float(offset)


class WorldTransform:
    """Rigid transform camera -> world stored as a 3x3 rotation and a translation."""

    def __init__(self, world_cfg: dict):
        self.rotation = np.array(world_cfg.get("rotation", [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]), dtype=float)
        self.translation = np.array(world_cfg.get("translation", [0, 0, 0]), dtype=float)

    def to_world(self, cam_point: np.ndarray) -> np.ndarray:
        return self.rotation @ cam_point + self.translation

    def to_camera(self, world_point: np.ndarray) -> np.ndarray:
        return self.rotation.T @ (world_point - self.translation)

    def camera_pose(self) -> dict:
        """Where the camera sits in the calibrated playing space.

        The transform is world = rotation * camera + translation, so the lens
        itself (the origin of camera space) lands on ``translation``, and the
        camera's own axes are the columns of ``rotation``.  Camera y points
        down, hence the minus sign on ``up``.
        """
        return {
            "position": [round(float(v), 4) for v in self.translation],
            "right": [round(float(v), 4) for v in self.rotation[:, 0]],
            "up": [round(float(-v), 4) for v in self.rotation[:, 1]],
            "forward": [round(float(v), 4) for v in self.rotation[:, 2]],
        }

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
