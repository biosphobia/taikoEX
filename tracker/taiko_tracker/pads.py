"""Virtual drum pads and hit detection (the Aerodrums idea).

A pad is a flat disc (or ring) floating in world space.  A hit happens when
a controller's sphere passes *down* through the disc fast enough.  Because
the camera only gives us 60-ish samples per second, the exact crossing time
and point are interpolated between the last two positions, which keeps the
timing accurate to a few milliseconds.

After a hit the controller must rise back above the pad by
``rearm_height_m`` before the same pad can trigger again, which stops a
sphere hovering on the plane from spamming hits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Pad:
    id: str
    name: str
    kind: str                 # "don" or "ka"
    side: str                 # "left" or "right"
    center: np.ndarray
    normal: np.ndarray
    radius: float
    inner_radius: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict) -> "Pad":
        normal = np.array(cfg.get("normal", [0, 1, 0]), dtype=float)
        norm = np.linalg.norm(normal)
        normal = normal / norm if norm > 1e-9 else np.array([0.0, 1.0, 0.0])
        return cls(
            id=str(cfg["id"]),
            name=str(cfg.get("name", cfg["id"])),
            kind=str(cfg.get("kind", "don")),
            side=str(cfg.get("side", "left")),
            center=np.array(cfg.get("center", [0, 0, 0]), dtype=float),
            normal=normal,
            radius=float(cfg.get("radius", 0.1)),
            inner_radius=float(cfg.get("inner_radius", 0.0)),
        )

    def to_config(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind, "side": self.side,
            "center": self.center.round(4).tolist(), "normal": self.normal.round(4).tolist(),
            "radius": round(self.radius, 4), "inner_radius": round(self.inner_radius, 4),
        }

    def height_of(self, point: np.ndarray) -> float:
        """Signed distance above the pad plane (positive = above)."""
        return float(np.dot(point - self.center, self.normal))

    def in_plane_distance(self, point: np.ndarray) -> float:
        offset = point - self.center
        offset = offset - self.normal * np.dot(offset, self.normal)
        return float(np.linalg.norm(offset))

    def contains(self, point_on_plane: np.ndarray) -> bool:
        d = self.in_plane_distance(point_on_plane)
        return self.inner_radius <= d <= self.radius


@dataclass
class Hit:
    pad_id: str
    kind: str
    side: str
    controller_id: int
    time: float               # absolute time (time.time based) when the plane was crossed
    speed: float              # metres per second along the pad normal
    strength: float           # 0..1, speed relative to a "hard" hit
    position: list = field(default_factory=list)


@dataclass
class ControllerMotion:
    """Last known position/time of a controller plus per-pad arm state."""
    position: np.ndarray | None = None
    time: float = 0.0
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    armed: dict[str, bool] = field(default_factory=dict)
    last_hit_time: float = -1.0


class HitDetector:
    """Feeds controller positions through all pads and emits ``Hit`` objects."""

    HARD_HIT_SPEED = 3.0   # m/s that counts as strength 1.0

    def __init__(self, pads: list[Pad], hit_cfg: dict):
        self.pads = pads
        self.cfg = hit_cfg
        self.motion: dict[int, ControllerMotion] = {}

    def reload(self, pads: list[Pad], hit_cfg: dict) -> None:
        self.pads = pads
        self.cfg = hit_cfg
        for motion in self.motion.values():
            motion.armed.clear()

    def reset(self, controller_id: int) -> None:
        self.motion.pop(controller_id, None)

    def update(self, controller_id: int, position: np.ndarray, timestamp: float) -> list[Hit]:
        motion = self.motion.setdefault(controller_id, ControllerMotion())
        hits: list[Hit] = []
        prev_pos, prev_time = motion.position, motion.time
        max_gap = float(self.cfg.get("max_frame_gap_s", 0.25))

        if prev_pos is not None and 0 < timestamp - prev_time <= max_gap:
            dt = timestamp - prev_time
            motion.velocity = (position - prev_pos) / dt
            hits = self._check_pads(motion, controller_id, prev_pos, prev_time, position, timestamp)
        else:
            motion.velocity = np.zeros(3)

        # Re-arm pads the controller has clearly risen above.
        rearm = float(self.cfg.get("rearm_height_m", 0.04))
        for pad in self.pads:
            if pad.height_of(position) > rearm:
                motion.armed[pad.id] = True

        motion.position = position
        motion.time = timestamp
        return hits

    def _check_pads(self, motion, controller_id, prev_pos, prev_time, position, timestamp) -> list[Hit]:
        cooldown = float(self.cfg.get("cooldown_s", 0.05))
        if timestamp - motion.last_hit_time < cooldown:
            return []
        min_speed = float(self.cfg.get("min_speed_mps", 0.5))
        latency = float(self.cfg.get("latency_compensation_ms", 0.0)) / 1000.0

        candidates = []
        for pad in self.pads:
            if not motion.armed.get(pad.id, True):
                continue
            h_prev = pad.height_of(prev_pos)
            h_now = pad.height_of(position)
            if not (h_prev > 0.0 >= h_now):
                continue   # no downward crossing this frame
            s = h_prev / (h_prev - h_now)               # 0..1 along the segment
            cross_point = prev_pos + (position - prev_pos) * s
            if not pad.contains(cross_point):
                continue
            speed = -float(np.dot(motion.velocity, pad.normal))
            if speed < min_speed:
                continue
            cross_time = prev_time + (timestamp - prev_time) * s - latency
            candidates.append((pad.in_plane_distance(cross_point), pad, cross_point, cross_time, speed))

        if not candidates:
            return []
        # If pads overlap, the one whose centre is closest to the crossing wins.
        candidates.sort(key=lambda c: c[0])
        _, pad, cross_point, cross_time, speed = candidates[0]
        motion.armed[pad.id] = False
        motion.last_hit_time = timestamp
        strength = max(0.0, min(1.0, speed / self.HARD_HIT_SPEED))
        return [Hit(pad.id, pad.kind, pad.side, controller_id, cross_time, speed, strength,
                    cross_point.round(4).tolist())]


def taiko_layout(center: np.ndarray | None = None, face_spacing: float = 0.10, rim_spacing: float = 0.30,
                 radius: float = 0.11) -> list[dict]:
    """Preset that arranges four pads like one taiko drum seen from above."""
    c = np.zeros(3) if center is None else np.array(center, dtype=float)
    up = [0, 1, 0]
    return [
        {"id": "left_ka", "name": "Left rim", "kind": "ka", "side": "left",
         "center": (c + [-rim_spacing, 0, 0]).round(4).tolist(), "normal": up, "radius": radius, "inner_radius": 0.0},
        {"id": "left_don", "name": "Left face", "kind": "don", "side": "left",
         "center": (c + [-face_spacing, 0, 0]).round(4).tolist(), "normal": up, "radius": radius, "inner_radius": 0.0},
        {"id": "right_don", "name": "Right face", "kind": "don", "side": "right",
         "center": (c + [face_spacing, 0, 0]).round(4).tolist(), "normal": up, "radius": radius, "inner_radius": 0.0},
        {"id": "right_ka", "name": "Right rim", "kind": "ka", "side": "right",
         "center": (c + [rim_spacing, 0, 0]).round(4).tolist(), "normal": up, "radius": radius, "inner_radius": 0.0},
    ]
