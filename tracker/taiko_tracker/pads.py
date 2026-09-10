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

    def side_for(self, controller_id: int) -> str:
        """A pad marked ``any`` takes its side from the hand that struck it,
        which is how a real taiko works: one drum, and the left and right
        halves are simply your left and right hands."""
        if self.side in ("left", "right"):
            return self.side
        return "left" if controller_id == 0 else "right"

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
    acceleration: np.ndarray | None = None
    accel_history: list = field(default_factory=list)   # recent (time, acceleration) samples
    approach_speed: float = 0.0      # how fast it was last heading at a pad
    armed: dict[str, bool] = field(default_factory=dict)
    hit_height: dict[str, float] = field(default_factory=dict)   # height read at the last hit
    peak_height: dict[str, float] = field(default_factory=dict)  # highest since the pad was armed
    last_hit_time: float = -1.0


class HitDetector:
    """Turns controller motion into pad hits.

    Two ways of deciding when a stroke happened, chosen with ``hits.mode``:

    ``stroke`` (the default)
        A hit is the *bottom of the stroke*: the moment the controller stops
        moving towards the pad and starts coming back.  Nothing is hit in the
        air, so this is the same thing your arm does over a real drum, and it
        is the moment a drummer hears.  It needs the velocity to change sign
        and the hand to be somewhere over the pad - it does not need the
        absolute distance from the camera to be right, which matters because
        that is the one number a camera measures badly.  A sphere the size of
        a PS Move at two metres gives a distance good to a few centimetres at
        best, while its *direction*, and therefore which pad the hand is over,
        is good to a few millimetres.

    ``plane``
        A hit is the moment the sphere crosses the pad's surface going down.
        Sharper, but only as accurate as the depth estimate, so it suits a
        close camera pointing straight at the player.

    In ``stroke`` mode the bottom is found from the controller's accelerometer
    when there is one - it feels the hand stop even when the camera cannot make
    the movement out - and from the motion itself otherwise, or when the stroke
    was too gentle to produce a clear spike.

    In every mode a pad has to be re-armed by lifting away from it before it can
    fire again, and each hand has a short cooldown.
    """

    HARD_HIT_SPEED = 3.0   # m/s that counts as strength 1.0
    MAX_REPORTED_SPEED = 8.0   # nothing faster than this is a hand

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

    def update(self, controller_id: int, position: np.ndarray, timestamp: float,
               velocity: np.ndarray | None = None,
               acceleration: np.ndarray | None = None) -> list[Hit]:
        """Feed one sample.

        ``velocity`` comes from the position filter when it is available;
        otherwise it is differenced here.  ``acceleration`` is the
        controller's own accelerometer reading rotated into world space with
        gravity removed - when it is present, and ``hits.use_accelerometer``
        is on, the *timing* of a hit comes from it instead of from the camera.
        """
        motion = self.motion.setdefault(controller_id, ControllerMotion())
        hits: list[Hit] = []
        previous_position, previous_time = motion.position, motion.time
        previous_velocity = motion.velocity.copy()
        max_gap = float(self.cfg.get("max_frame_gap_s", 0.25))
        dt = timestamp - previous_time
        fresh = previous_position is not None and 0 < dt <= max_gap

        if fresh:
            motion.velocity = velocity.copy() if velocity is not None else (position - previous_position) / dt
        elif velocity is not None:
            motion.velocity = velocity.copy()
        else:
            motion.velocity = np.zeros(3)

        motion.acceleration = None if acceleration is None else np.asarray(acceleration, dtype=float)
        if motion.acceleration is not None:
            motion.accel_history.append((timestamp, motion.acceleration))
            window = float(self.cfg.get("swing_window_s", 0.18))
            while len(motion.accel_history) > 3 and timestamp - motion.accel_history[0][0] > window:
                motion.accel_history.pop(0)

        if fresh:
            mode = str(self.cfg.get("mode", "stroke"))
            if mode == "plane":
                hits = self._plane_hits(motion, controller_id, previous_position, previous_time, position, timestamp)
            else:
                if motion.acceleration is not None and bool(self.cfg.get("use_accelerometer", True)):
                    hits = self._impact_hits(motion, controller_id, position, timestamp)
                if not hits:
                    # A soft stroke may stop too gently to show a clear spike.
                    # The turn-around in the motion still gives it away, so that
                    # is the fallback rather than losing the note.  Whichever
                    # fires first disarms the pad, so it cannot count twice.
                    hits = self._stroke_hits(motion, controller_id, previous_velocity, previous_time,
                                             position, timestamp)

        self._rearm(motion, position)
        # Remember the fastest approach seen since the pad was armed, so a hit
        # detected at the turn-around can report how hard it was struck.
        towards = max((-float(np.dot(motion.velocity, pad.normal)) for pad in self.pads), default=0.0)
        motion.approach_speed = max(towards, motion.approach_speed * 0.85)
        motion.position = position
        motion.time = timestamp
        return hits

    # ------------------------------------------------------------------
    def _rearm(self, motion: ControllerMotion, position: np.ndarray) -> None:
        """Re-arm a pad once the hand has lifted away from where it struck.

        The test is *relative* to the height read at the moment of the hit, not
        to the pad's surface.  The camera's idea of how high the hand is can be
        off by more than the lift itself - especially when the camera looks
        along the same line the hand moves - but the difference between two
        readings a moment apart is reliable.
        """
        rearm = float(self.cfg.get("rearm_height_m", 0.04))
        for pad in self.pads:
            height = pad.height_of(position)
            reference = motion.hit_height.get(pad.id, 0.0)
            if height > reference + rearm:
                motion.armed[pad.id] = True
            if motion.armed.get(pad.id, True):
                motion.peak_height[pad.id] = max(motion.peak_height.get(pad.id, height), height)

    def _candidate_pads(self, position: np.ndarray) -> list[tuple[float, Pad]]:
        """Pads the controller is over, nearest centre first."""
        reach = float(self.cfg.get("height_window_m", 0.15))
        found = []
        for pad in self.pads:
            if abs(pad.height_of(position)) > reach:
                continue
            distance = pad.in_plane_distance(position)
            if pad.inner_radius <= distance <= pad.radius:
                found.append((distance, pad))
        found.sort(key=lambda item: item[0])
        return found

    def _emit(self, motion, controller_id: int, pad: Pad, when: float, speed: float,
              position: np.ndarray, timestamp: float) -> list[Hit]:
        motion.armed[pad.id] = False
        motion.hit_height[pad.id] = pad.height_of(position)
        motion.peak_height.pop(pad.id, None)
        motion.last_hit_time = timestamp
        motion.approach_speed = 0.0
        # Forget the swing that produced this hit, so the next one needs a
        # fresh one.  Otherwise the hand slowing down again a moment later -
        # at the top of the lift, or setting off for the next pad - still has
        # this stroke's approach sitting in the window behind it.
        motion.accel_history.clear()
        speed = min(float(speed), self.MAX_REPORTED_SPEED)
        strength = max(0.0, min(1.0, speed / self.HARD_HIT_SPEED))
        return [Hit(pad.id, pad.kind, pad.side_for(controller_id), controller_id, when, speed, strength,
                    np.asarray(position).round(4).tolist())]

    # ------------------------------------------------------------------
    def _stroke_hits(self, motion, controller_id, previous_velocity, previous_time,
                     position, timestamp) -> list[Hit]:
        """Fire at the turning point of a downward stroke."""
        if timestamp - motion.last_hit_time < float(self.cfg.get("cooldown_s", 0.05)):
            return []
        min_speed = float(self.cfg.get("min_speed_mps", 0.5))
        latency = float(self.cfg.get("latency_compensation_ms", 0.0)) / 1000.0
        for _, pad in self._candidate_pads(position):
            if not motion.armed.get(pad.id, True):
                continue
            was = -float(np.dot(previous_velocity, pad.normal))    # speed towards the pad, last frame
            now = -float(np.dot(motion.velocity, pad.normal))      # and this frame
            # The hand has to have been driving at the pad and then actually
            # turned around.  Waiting for the sign to change rather than for the
            # speed to merely fall keeps a noisy frame mid-stroke from counting
            # as the bottom of it.
            if was < min_speed or now > 0.0:
                continue
            # And it has to have come down a real distance to get here.  Moving
            # from one pad to the next dips and levels off, which looks like a
            # tiny stroke; insisting on a proper descent tells them apart
            # without needing to know the true height of anything.
            descent = motion.peak_height.get(pad.id, pad.height_of(position)) - pad.height_of(position)
            if descent < float(self.cfg.get("min_descent_m", 0.06)):
                continue
            span = was - now
            share = was / span if span > 1e-6 else 1.0
            when = previous_time + (timestamp - previous_time) * min(1.0, share) - latency
            return self._emit(motion, controller_id, pad, when, was, position, timestamp)
        return []

    def _impact_hits(self, motion, controller_id, position, timestamp) -> list[Hit]:
        """Fire at the peak of the deceleration the controller feels at the bottom of a stroke.

        The accelerometer is inside the hand, so it feels the stroke stop
        whatever the camera can or cannot make out.  That matters most when the
        camera looks down the same line the hand travels - a camera on the
        floor, or up on a shelf - because then the one axis a camera measures
        badly is exactly the one the stroke moves along.  The camera still
        decides *which* pad, which is a sideways question it answers well.

        The moment used is the peak, not the first sample over the threshold:
        where the threshold is crossed depends on how big the stroke was, but
        the peak is the bottom of the stroke however hard it was played.  Three
        samples around the peak give its position to a fraction of a frame.

        A stop only counts if the hand was driving *towards* the pad shortly
        before it, which the same sensor says.  Without that check the hand
        stopping at the top of its lift, or setting off towards the next pad,
        reads as a second hit on the pad it has just left.
        """
        if timestamp - motion.last_hit_time < float(self.cfg.get("cooldown_s", 0.05)):
            return []
        history = motion.accel_history
        if len(history) < 3:
            return []
        threshold = float(self.cfg.get("accel_threshold_g", 2.5)) * 9.80665
        min_speed = float(self.cfg.get("min_speed_mps", 0.5))
        # The accelerometer path has its own delay, which is close to nothing:
        # the peak is found from the samples either side of it, so the moment
        # reported is the moment it happened, not the moment it was noticed.
        latency = float(self.cfg.get("latency_compensation_imu_ms", 0.0)) / 1000.0
        (time_a, accel_a), (time_b, accel_b), (time_c, accel_c) = history[-3:]
        swing = float(self.cfg.get("swing_threshold_g", 1.0)) * 9.80665
        for _, pad in self._candidate_pads(position):
            if not motion.armed.get(pad.id, True):
                continue
            first = float(np.dot(accel_a, pad.normal))
            peak = float(np.dot(accel_b, pad.normal))
            last = float(np.dot(accel_c, pad.normal))
            if peak < threshold or peak < first or peak < last:
                continue
            # Did the hand drive at this pad just before stopping?
            drove_in = min((float(np.dot(sample, pad.normal)) for _, sample in history[:-1]), default=0.0)
            if drove_in > -swing:
                continue
            # Vertex of the parabola through the three samples, in samples.
            curvature = first - 2.0 * peak + last
            offset = 0.5 * (first - last) / curvature if abs(curvature) > 1e-6 else 0.0
            offset = max(-1.0, min(1.0, offset))
            step = (time_c - time_a) / 2.0
            when = time_b + offset * step - latency
            hard = float(self.cfg.get("hard_hit_g", 6.0)) * 9.80665
            speed = max(min_speed, motion.approach_speed, self.HARD_HIT_SPEED * min(1.0, peak / hard))
            return self._emit(motion, controller_id, pad, when, speed, position, timestamp)
        return []

    def _plane_hits(self, motion, controller_id, previous_position, previous_time,
                    position, timestamp) -> list[Hit]:
        """Fire when the sphere passes down through the pad's surface."""
        if timestamp - motion.last_hit_time < float(self.cfg.get("cooldown_s", 0.05)):
            return []
        min_speed = float(self.cfg.get("min_speed_mps", 0.5))
        latency = float(self.cfg.get("latency_compensation_ms", 0.0)) / 1000.0
        candidates = []
        for pad in self.pads:
            if not motion.armed.get(pad.id, True):
                continue
            height_before = pad.height_of(previous_position)
            height_after = pad.height_of(position)
            if not (height_before > 0.0 >= height_after):
                continue
            share = height_before / (height_before - height_after)
            crossing = previous_position + (position - previous_position) * share
            if not pad.contains(crossing):
                continue
            speed = -float(np.dot(motion.velocity, pad.normal))
            if speed < min_speed:
                continue
            when = previous_time + (timestamp - previous_time) * share - latency
            candidates.append((pad.in_plane_distance(crossing), pad, crossing, when, speed))
        if not candidates:
            return []
        candidates.sort(key=lambda item: item[0])
        _, pad, crossing, when, speed = candidates[0]
        return self._emit(motion, controller_id, pad, when, speed, crossing, timestamp)


def single_drum_layout(center: np.ndarray | None = None, face_radius: float = 0.22,
                       rim_width: float = 0.23, gap: float = 0.0) -> list[dict]:
    """One drum: a face in the middle for *don*, a rim around it for *ka*.

    This is the default because it is both the real instrument and the layout
    a camera can judge most reliably.  Which hand struck it decides left from
    right, so the only thing the tracker has to get right is how far the hand
    is from the middle of the drum - a sideways measurement, and the kind a
    camera is good at.  Four separate pads in a row need the hand placed to
    within a few centimetres in a straight line, which is asking more of the
    measurement than it can give at two metres.
    """
    c = np.zeros(3) if center is None else np.array(center, dtype=float)
    up = [0, 1, 0]
    return [
        {"id": "don", "name": "Drum face", "kind": "don", "side": "any",
         "center": c.round(4).tolist(), "normal": up, "radius": round(face_radius, 4), "inner_radius": 0.0},
        {"id": "ka", "name": "Drum rim", "kind": "ka", "side": "any",
         "center": c.round(4).tolist(), "normal": up,
         "radius": round(face_radius + gap + rim_width, 4), "inner_radius": round(face_radius + gap, 4)},
    ]


def taiko_layout(center: np.ndarray | None = None, face_spacing: float = 0.13, rim_spacing: float = 0.40,
                 radius: float = 0.12) -> list[dict]:
    """Four separate pads in a row, the way a taiko controller's buttons sit.

    Easier to aim at deliberately, but it needs a more accurate position than
    the single drum does, so keep the camera close if you use it.
    """
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


LAYOUTS = {"single_drum": single_drum_layout, "four_pads": taiko_layout}
