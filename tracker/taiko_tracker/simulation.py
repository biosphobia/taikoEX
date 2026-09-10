"""A fake camera with fake controllers.

Used by the automated tests and for demo footage, and handy when you want
to work on the game without wiring up the real hardware.  The simulated
controllers live in *world* space, get projected through a virtual PS3 Eye
that can be placed anywhere (``simulation.camera_position`` / ``camera_target``
in the config), and are drawn as glowing spheres with an over-exposed white
centre - just like the real thing looks to the camera.

Scenes (``simulation.scene``) add real-world mess: lamps, a TV cycling
through colours, a bright window, skin-coloured blobs, a poster in a
controller colour, sensor noise, exposure drift, motion blur and an arm
that occludes the spheres now and then.  See ``SCENES`` below.

Controllers can be:

* parked at a position (``goto``)
* told to hit pads at given times (``schedule_hit``)
* told to play a whole TJA chart (``play_chart``)

They also produce synthetic IMU data (``imu_sample``) so the orientation
filter can be exercised without hardware.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .imu import quat_between, quat_conjugate, quat_multiply, quat_rotate
from .pads import Pad
from .tja import parse_tja_notes

# ---------------------------------------------------------------------------
# Scene presets.  Positions are metres in world space (x right, y up, z away
# from the camera); distractor rectangles are fractions of the frame.
# ---------------------------------------------------------------------------
SCENES: dict[str, dict] = {
    "clean": {
        "description": "camera 1.25 m in front, slightly above and to the side; one poster",
        "camera_position": [0.15, 0.55, -1.25], "camera_target": [0.0, 0.05, 0.0],
        "background": 28, "noise": 6, "exposure_drift": 0.0, "motion_blur": True, "occluder": False,
        "distractors": [{"kind": "rect", "rect": [0.89, 0.04, 0.08, 0.09], "colour": [130, 40, 120]}],
    },
    "living_room": {
        "description": "camera on the TV stand 1.6 m away; lamp, wooden shelf, a face, a magenta poster",
        "camera_position": [0.1, 0.35, -1.6], "camera_target": [0.0, 0.1, 0.0],
        "background": 38, "noise": 9, "exposure_drift": 0.05, "motion_blur": True, "occluder": True,
        "distractors": [
            {"kind": "rect", "rect": [0.0, 0.55, 1.0, 0.12], "colour": [40, 70, 120]},          # wooden shelf
            {"kind": "rect", "rect": [0.05, 0.1, 0.12, 0.2], "colour": [140, 50, 150]},         # poster
            {"kind": "lamp", "center": [0.82, 0.22], "radius": 0.05},
            {"kind": "skin", "center": [0.30, 0.30], "radius": 0.07},
        ],
    },
    "far_shelf": {
        "description": "camera on a high shelf 2.6 m away, 35 degrees to the side, TV playing, window glare",
        "camera_position": [1.5, 1.9, -2.2], "camera_target": [0.0, 0.0, 0.0],
        "background": 45, "noise": 10, "exposure_drift": 0.08, "motion_blur": True, "occluder": True,
        "distractors": [
            {"kind": "window", "rect": [0.0, 0.0, 0.28, 0.5]},
            {"kind": "tv", "rect": [0.62, 0.12, 0.3, 0.22]},
            {"kind": "rect", "rect": [0.35, 0.7, 0.4, 0.3], "colour": [60, 60, 65]},           # sofa
            {"kind": "skin", "center": [0.30, 0.62], "radius": 0.05},
        ],
    },
    "floor_low": {
        "description": "camera on the floor 0.9 m ahead looking up; ceiling lamp in frame; strong exposure drift",
        "camera_position": [-0.3, -0.8, -0.9], "camera_target": [0.0, 0.05, 0.0],
        "background": 32, "noise": 12, "exposure_drift": 0.15, "motion_blur": True, "occluder": True,
        "distractors": [
            {"kind": "lamp", "center": [0.5, 0.1], "radius": 0.09},
            {"kind": "rect", "rect": [0.0, 0.0, 1.0, 0.08], "colour": [90, 90, 90]},            # ceiling edge
            {"kind": "tv", "rect": [0.05, 0.3, 0.22, 0.18]},
        ],
    },
    "sunny": {
        "description": "bright daylight room, camera 1.4 m away and tilted, heavy noise, waving arm",
        "camera_position": [-0.6, 0.9, -1.3], "camera_target": [0.0, 0.0, 0.0],
        "background": 95, "noise": 18, "exposure_drift": 0.1, "motion_blur": True, "occluder": True,
        "distractors": [
            {"kind": "window", "rect": [0.6, 0.0, 0.4, 0.6]},
            {"kind": "rect", "rect": [0.0, 0.0, 0.3, 1.0], "colour": [120, 140, 150]},         # beige wall
            {"kind": "skin", "center": [0.22, 0.62], "radius": 0.08},
            {"kind": "rect", "rect": [0.3, 0.8, 0.5, 0.2], "colour": [150, 60, 170]},          # magenta blanket
        ],
    },
}


@dataclass
class VirtualCamera:
    """Pose of the simulated PS3 Eye in world space."""
    position: np.ndarray
    target: np.ndarray
    focal_px: float
    width: int
    height: int
    sphere_radius_m: float = 0.0225

    def __post_init__(self):
        forward = self.target - self.position
        forward /= np.linalg.norm(forward)
        up = np.array([0.0, 1.0, 0.0])
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        # Columns are the camera axes expressed in world coordinates.
        self.rotation_cam_to_world = np.column_stack([right, down, forward])

    def world_to_camera(self, point: np.ndarray) -> np.ndarray:
        return self.rotation_cam_to_world.T @ (point - self.position)

    def project(self, world_point: np.ndarray) -> tuple[float, float, float] | None:
        cam = self.world_to_camera(world_point)
        if cam[2] <= self.sphere_radius_m:
            return None
        u = self.focal_px * cam[0] / cam[2] + self.width / 2.0
        v = self.focal_px * cam[1] / cam[2] + self.height / 2.0
        distance = float(np.linalg.norm(cam))
        radius = self.focal_px * self.sphere_radius_m / math.sqrt(distance ** 2 - self.sphere_radius_m ** 2)
        return u, v, radius


@dataclass
class ScheduledHit:
    time: float
    pad_id: str
    controller_id: int


@dataclass
class VirtualController:
    id: int
    colour_rgb: tuple
    rest_position: np.ndarray
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))
    target: np.ndarray | None = None       # goto target
    hits: list[ScheduledHit] = field(default_factory=list)
    previous_position: np.ndarray | None = None
    previous_projection: tuple | None = None
    orientation: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    previous_orientation: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3))
    acceleration: np.ndarray = field(default_factory=lambda: np.zeros(3))

    TOP = 0.18            # metres above the pad where a stroke starts
    DOWN_TIME = 0.10      # seconds from the top of the stroke to the bottom
    UP_TIME = 0.14        # seconds to come back up
    OVERSHOOT = 0.04      # how far below the pad plane the stroke reaches

    @staticmethod
    def _smooth_step(phase: float) -> float:
        """0 -> 1 with zero slope at both ends (a raised cosine)."""
        return (1.0 - math.cos(math.pi * max(0.0, min(1.0, phase)))) / 2.0

    MIN_HALF_STROKE = 0.02   # a hand cannot go down or up faster than this
    MAX_TRAVEL_SPEED = 3.5   # m/s sideways between pads

    def stroke_shape(self, index: int, pads: dict[str, Pad] | None = None) -> tuple[float, float, float, float]:
        """``(down_time, up_time, top, overshoot)`` for the stroke at ``index``.

        Strokes shrink when the notes come fast, exactly as a drummer's do: at
        two hundred beats a minute nobody lifts a full hand's height between
        hits, and a hand that has to cross to the far rim lifts less still.
        Keeping the full stroke would make the simulated hand move at
        impossible speeds and hand the tracker an accelerometer trace no real
        arm could produce.
        """
        before = self._gap_share(index - 1, index, pads)
        after = self._gap_share(index, index + 1, pads)
        down = max(self.MIN_HALF_STROKE, min(self.DOWN_TIME, before))
        up = max(self.MIN_HALF_STROKE, min(self.UP_TIME, after))
        scale = down / self.DOWN_TIME
        return down, up, self.TOP * scale, self.OVERSHOOT * scale

    def _gap_share(self, first: int, second: int, pads: dict[str, Pad] | None) -> float:
        """How long each of the two strokes either side of a gap may take.

        The gap has to hold the end of one stroke, the trip to the next pad and
        the start of the next stroke, so the further apart the pads are, the
        less time is left for lifting.
        """
        if first < 0 or second >= len(self.hits):
            return 1e9
        gap = self.hits[second].time - self.hits[first].time
        distance = 0.0
        if pads is not None:
            from_pad = pads.get(self.hits[first].pad_id)
            to_pad = pads.get(self.hits[second].pad_id)
            if from_pad is not None and to_pad is not None:
                distance = float(np.linalg.norm(to_pad.center - from_pad.center))
        travel = min(gap * 0.5, distance / self.MAX_TRAVEL_SPEED)
        return max(self.MIN_HALF_STROKE, (gap - travel) / 2.0)

    def height_at(self, now: float, index: int, pads: dict[str, Pad] | None = None) -> float | None:
        """Height above the pad during stroke ``index``, which bottoms out at its hit time.

        Down and back up, both halves shaped as raised cosines.  The speed is
        zero at the top of the stroke and again at the bottom, with the hand
        slowing hardest right at the bottom - which is what the accelerometer
        feels as the hit, and what a drummer hears.
        """
        hit_time = self.hits[index].time
        down, up, top, overshoot = self.stroke_shape(index, pads)
        travel = top + overshoot
        if hit_time - down <= now <= hit_time:
            return top - travel * self._smooth_step((now - (hit_time - down)) / down)
        if hit_time < now <= hit_time + up:
            return -overshoot + travel * self._smooth_step((now - hit_time) / up)
        return None

    def position_at(self, now: float, pads: dict[str, Pad]) -> np.ndarray:
        """Where the sphere is at time ``now``.

        Either mid-stroke, or travelling between pads.  Nothing ever jumps: a
        jump would show up in the simulated accelerometer as a spike no real
        hand could produce, and the tracker would rightly be confused by it.
        """
        for index, hit in enumerate(self.hits):
            pad = pads.get(hit.pad_id)
            if pad is None:
                continue
            height = self.height_at(now, index, pads)
            if height is not None:
                return self.strike_point(pad) + pad.normal * height
        return self._travel_position(now, pads)

    def strike_point(self, pad: Pad) -> np.ndarray:
        """Where on the pad this hand aims.

        A disc is struck in the middle.  A ring - the rim of a taiko - has
        nothing in its middle, so the hand goes to the near side of it: the
        left hand to the left of the drum, the right hand to the right.
        """
        if pad.inner_radius <= 0.0:
            return pad.center
        sideways = np.cross(pad.normal, np.array([0.0, 0.0, 1.0]))
        if np.linalg.norm(sideways) < 1e-6:
            sideways = np.cross(pad.normal, np.array([1.0, 0.0, 0.0]))
        sideways /= np.linalg.norm(sideways)
        reach = (pad.inner_radius + pad.radius) / 2.0
        return pad.center + sideways * reach * (-1.0 if self.id == 0 else 1.0)

    def hover_over(self, pad: Pad, index: int, pads: dict[str, Pad]) -> np.ndarray:
        return self.strike_point(pad) + pad.normal * self.stroke_shape(index, pads)[2]

    def _travel_position(self, now: float, pads: dict[str, Pad]) -> np.ndarray:
        """Between strokes: glide from the pad just struck to the next one."""
        previous = [(i, h) for i, h in enumerate(self.hits) if h.time + self.stroke_shape(i, pads)[1] <= now]
        upcoming = [(i, h) for i, h in enumerate(self.hits) if h.time - self.stroke_shape(i, pads)[0] > now]
        from_index, from_hit = previous[-1] if previous else (None, None)
        to_index, to_hit = upcoming[0] if upcoming else (None, None)
        from_pad = pads.get(from_hit.pad_id) if from_hit else None
        to_pad = pads.get(to_hit.pad_id) if to_hit else None
        start = self.hover_over(from_pad, from_index, pads) if from_pad is not None else (
            self.target if self.target is not None else self.rest_position)
        if to_pad is None:
            return start
        end = self.hover_over(to_pad, to_index, pads)
        if from_pad is None:
            return end
        began = from_hit.time + self.stroke_shape(from_index, pads)[1]
        finish = to_hit.time - self.stroke_shape(to_index, pads)[0]
        if finish <= began:
            return end
        return start + (end - start) * self._smooth_step((now - began) / (finish - began))

    def motion_at(self, now: float, pads: dict[str, Pad], h: float = 1.0 / 480.0):
        """Position, velocity and acceleration, by central differences on the
        analytic trajectory - so they are the same however often they are asked
        for, unlike differencing whatever frames happen to arrive."""
        before = self.position_at(now - h, pads)
        here = self.position_at(now, pads)
        after = self.position_at(now + h, pads)
        velocity = (after - before) / (2.0 * h)
        acceleration = (after - 2.0 * here + before) / (h * h)
        return here, velocity, acceleration

    def prune(self, now: float) -> None:
        """Forget strokes long past, but keep the last one so the hand knows
        which pad it is hovering over."""
        played = [h for h in self.hits if h.time + self.UP_TIME <= now - 0.5]
        if len(played) > 1:
            self.hits = self.hits[len(played) - 1:]

    def update_orientation(self, dt: float) -> None:
        """A real drumstick leans into the stroke: tilt the handle towards the velocity."""
        self.previous_orientation = self.orientation.copy()
        up = np.array([0.0, 1.0, 0.0])
        speed = float(np.linalg.norm(self.velocity))
        if speed > 0.05:
            lean = min(math.radians(55.0), speed * math.radians(20.0))
            direction = self.velocity / speed
            target_dir = up * math.cos(lean) + direction * math.sin(lean)
            target_dir /= np.linalg.norm(target_dir)
        else:
            target_dir = up
        current_dir = quat_rotate(self.orientation, up)
        step = quat_between(current_dir, target_dir)
        # Ease towards the target so the gyro sees smooth rates.
        alpha = min(1.0, dt * 12.0)
        w, x, y, z = step
        angle = 2.0 * math.acos(max(-1.0, min(1.0, w)))
        axis = np.array([x, y, z])
        if np.linalg.norm(axis) > 1e-9 and angle > 1e-6:
            axis /= np.linalg.norm(axis)
            half = angle * alpha / 2.0
            eased = np.concatenate([[math.cos(half)], axis * math.sin(half)])
            self.orientation = quat_multiply(eased, self.orientation)
            self.orientation /= np.linalg.norm(self.orientation)

    def imu_sample(self, dt: float, noise: float = 0.01) -> tuple[list[float], list[float]]:
        """Accelerometer (g, sensor frame) and gyro (rad/s, sensor frame).

        A real accelerometer measures gravity's reaction *plus* whatever the
        hand is doing, so the stroke shows up in the reading - which is exactly
        what makes it useful to the position filter.
        """
        rng = np.random.default_rng()
        gravity_reaction = np.array([0.0, 1.0, 0.0])      # the accelerometer reads +1 g "up" at rest
        world_reading = gravity_reaction + self.acceleration / 9.80665
        accel = quat_rotate(quat_conjugate(self.orientation), world_reading) + rng.normal(0, noise, 3)
        delta = quat_multiply(quat_conjugate(self.previous_orientation), self.orientation)
        w = max(-1.0, min(1.0, float(delta[0])))
        angle = 2.0 * math.acos(w)
        axis = np.array(delta[1:])
        gyro = np.zeros(3)
        if np.linalg.norm(axis) > 1e-9 and dt > 0:
            gyro = axis / np.linalg.norm(axis) * angle / dt
        gyro = gyro + rng.normal(0, noise * 2.0, 3)
        return [float(v) for v in accel], [float(v) for v in gyro]


class SimulatedCamera:
    """Camera backend that renders the virtual scene."""

    name = "simulated"

    def __init__(self, config):
        self.config = config
        cam_cfg = config["camera"]
        sim_cfg = config.get("simulation", {}) if isinstance(config, dict) else config["simulation"]
        self.width, self.height = int(cam_cfg["width"]), int(cam_cfg["height"])
        self.fps = int(cam_cfg["fps"])
        scene_name = str(sim_cfg.get("scene", "clean"))
        self.scene = dict(SCENES.get(scene_name, SCENES["clean"]))
        # Explicit camera pose in the config overrides the scene's.
        if sim_cfg.get("camera_position"):
            self.scene["camera_position"] = sim_cfg["camera_position"]
        if sim_cfg.get("camera_target"):
            self.scene["camera_target"] = sim_cfg["camera_target"]
        self.virtual = VirtualCamera(
            position=np.array(self.scene["camera_position"], dtype=float),
            target=np.array(self.scene["camera_target"], dtype=float),
            focal_px=float(config["optics"]["focal_px"]),
            width=self.width, height=self.height,
            sphere_radius_m=float(config["optics"]["sphere_radius_m"]),
        )
        self.controllers: dict[int, VirtualController] = {}
        for index, ctrl in enumerate(config["controllers"]):
            rest = np.array([-0.2 if index == 0 else 0.2, 0.2, 0.0])
            self.controllers[int(ctrl["id"])] = VirtualController(int(ctrl["id"]), tuple(ctrl["led"]), rest, rest.copy())
        self.pads: dict[str, Pad] = {p["id"]: Pad.from_config(p) for p in config["pads"]}
        self.leds_on = True
        # A virtual clock makes a test run the same however busy the machine
        # is: frames advance by a fixed step instead of chasing real time.
        self.virtual_clock = bool(sim_cfg.get("virtual_clock", False))
        self._now = time.time()
        self._next_frame = self._now
        self._last_advance = self._now
        self._start = self._now
        self._background: np.ndarray | None = None
        self._rng = np.random.default_rng(7)
        self._skin_phase = self._rng.uniform(0, 6.28)

    # ---- commands from the tracker --------------------------------------
    def goto(self, controller_id: int, world_position) -> None:
        ctrl = self.controllers.get(controller_id)
        if ctrl is not None:
            ctrl.target = np.array(world_position, dtype=float)

    def schedule_hit(self, at_time: float, pad_id: str, controller_id: int) -> None:
        ctrl = self.controllers.get(controller_id)
        if ctrl is not None:
            ctrl.hits.append(ScheduledHit(at_time, pad_id, controller_id))
            ctrl.hits.sort(key=lambda h: h.time)

    def play_chart(self, tja_text: str, audio_start_time: float) -> int:
        """Schedule every note of a chart.  Hands alternate; big notes use both."""
        notes = parse_tja_notes(tja_text)
        ids = sorted(self.controllers)
        left, right = ids[0], ids[-1]
        toggle = False
        for note in notes:
            hands = [left, right] if (note.big and len(ids) > 1) else [right if not toggle else left]
            toggle = not toggle
            for hand in hands:
                pad = self._pad_for(note.kind, hand)
                if pad is not None:
                    self.schedule_hit(audio_start_time + note.time, pad, hand)
        return len(notes)

    def _pad_for(self, kind: str, controller_id: int) -> str | None:
        """The pad this hand would use for a don or a ka, whatever layout is loaded."""
        side = "left" if controller_id == 0 else "right"
        matching = [pad for pad in self.pads.values() if pad.kind == kind]
        for pad in matching:
            if pad.side == side:
                return pad.id
        return matching[0].id if matching else None

    def reload_pads(self) -> None:
        self.pads = {p["id"]: Pad.from_config(p) for p in self.config["pads"]}

    def set_leds(self, on: bool) -> None:
        self.leds_on = on

    def truth_positions(self) -> dict[int, list[float]]:
        return {cid: [round(float(v), 4) for v in c.position] for cid, c in self.controllers.items()}

    def imu_sample(self, controller_id: int):
        """A controller's IMU reading, sampled at the moment it is asked for.

        Real controllers report over Bluetooth at their own rate rather than
        the camera's, so the simulator steps the motion forward here too - the
        accelerometer must feel a stroke even when frames are slow.
        """
        ctrl = self.controllers.get(controller_id)
        if ctrl is None:
            return None
        self.advance(self.clock())
        return ctrl.imu_sample(1.0 / self.fps)

    def camera_pose_world(self) -> dict:
        r = self.virtual.rotation_cam_to_world
        return {"position": self.virtual.position.round(3).tolist(), "forward": r[:, 2].round(3).tolist(),
                "up": (-r[:, 1]).round(3).tolist()}

    # ---- frame rendering -------------------------------------------------
    def _static_background(self) -> np.ndarray:
        """The parts of the room that never move, rendered once.

        Lamps and windows are wide gaussian blurs; redrawing them every frame
        would cost more than the whole tracker does, and they do not change.
        """
        if self._background is not None:
            return self._background
        level = int(self.scene.get("background", 28))
        frame = np.full((self.height, self.width, 3), level, dtype=np.uint8)
        gradient = np.linspace(0, 18, self.height, dtype=np.uint8).reshape(-1, 1, 1)
        frame = cv2.add(frame, np.repeat(np.repeat(gradient, self.width, axis=1), 3, axis=2))
        h, w = self.height, self.width
        for item in self.scene.get("distractors", []):
            kind = item["kind"]
            if kind == "rect":
                x, y, rw, rh = item["rect"]
                cv2.rectangle(frame, (int(x * w), int(y * h)), (int((x + rw) * w), int((y + rh) * h)),
                              tuple(item["colour"]), -1)
            elif kind == "lamp":
                cx, cy = int(item["center"][0] * w), int(item["center"][1] * h)
                radius = int(item["radius"] * w)
                glow = np.zeros_like(frame)
                cv2.circle(glow, (cx, cy), radius * 2, (120, 140, 160), -1)
                glow = cv2.GaussianBlur(glow, (0, 0), radius * 0.8)
                frame = cv2.add(frame, glow)
                cv2.circle(frame, (cx, cy), radius, (255, 255, 255), -1, cv2.LINE_AA)
            elif kind == "window":
                x, y, rw, rh = item["rect"]
                glow = np.zeros_like(frame)
                cv2.rectangle(glow, (int(x * w), int(y * h)), (int((x + rw) * w), int((y + rh) * h)),
                              (255, 245, 225), -1)
                glow = cv2.GaussianBlur(glow, (0, 0), 12)
                frame = cv2.add(frame, glow)
        self._background = frame
        return frame

    def _draw_moving_distractors(self, frame: np.ndarray, t: float) -> None:
        """The things in the room that do change: a screen, and someone's face."""
        h, w = frame.shape[:2]
        for item in self.scene.get("distractors", []):
            if item["kind"] == "tv":
                x, y, rw, rh = item["rect"]
                hue = int((t * 25.0) % 180)
                value = 200 + int(50 * math.sin(t * 9.0))
                colour = cv2.cvtColor(np.uint8([[[hue, 200, value]]]), cv2.COLOR_HSV2BGR)[0, 0]
                cv2.rectangle(frame, (int(x * w), int(y * h)), (int((x + rw) * w), int((y + rh) * h)),
                              tuple(int(c) for c in colour), -1)
                if int(t * 2) % 7 == 0:      # a bright frame now and then, like a real film
                    cv2.rectangle(frame, (int(x * w) + 10, int(y * h) + 10),
                                  (int((x + rw) * w) - 10, int((y + rh) * h) - 10), (240, 250, 255), -1)
            elif item["kind"] == "skin":
                cx = int((item["center"][0] + 0.03 * math.sin(t * 0.7 + self._skin_phase)) * w)
                cy = int((item["center"][1] + 0.02 * math.sin(t * 0.5)) * h)
                cv2.ellipse(frame, (cx, cy), (int(item["radius"] * w), int(item["radius"] * 1.3 * w)),
                            0, 0, 360, (120, 160, 210), -1, cv2.LINE_AA)

    def _draw_occluder(self, frame: np.ndarray, t: float, sphere_px: list[tuple[float, float]]) -> None:
        """A dark arm that sweeps over the spheres every few seconds."""
        if not self.scene.get("occluder") or not sphere_px:
            return
        cycle = t % 6.0
        if cycle > 1.2:
            return
        u, v = sphere_px[int(t // 6) % len(sphere_px)]
        offset = (cycle / 1.2 - 0.5) * 120
        cv2.ellipse(frame, (int(u + offset), int(v + 10)), (28, 10), 20, 0, 360, (35, 30, 30), -1, cv2.LINE_AA)

    def advance(self, now: float) -> None:
        """Move the controllers to where they should be at ``now``.

        Kept separate from drawing so the motion can be sampled whenever it is
        needed - a real hand does not stop moving between exposures, and the
        accelerometer reports at its own rate, not the camera's.
        """
        dt = max(1e-4, now - self._last_advance)
        self._last_advance = now
        for ctrl in self.controllers.values():
            ctrl.prune(now)
            ctrl.previous_position = ctrl.position.copy()
            ctrl.position, ctrl.velocity, ctrl.acceleration = ctrl.motion_at(now, self.pads)
            ctrl.update_orientation(dt)

    def render(self, now: float) -> np.ndarray:
        t = now - self._start
        self.advance(now)
        frame = self._static_background().copy()
        self._draw_moving_distractors(frame, t)
        sphere_px = []
        for ctrl in self.controllers.values():
            projected = self.virtual.project(ctrl.position)
            previous = ctrl.previous_projection
            ctrl.previous_projection = projected
            if projected is None or not self.leds_on:
                continue
            u, v, r = projected
            sphere_px.append((u, v))
            bgr = tuple(int(c) for c in reversed(ctrl.colour_rgb))
            centre = (int(round(u * 16)), int(round(v * 16)))
            radius16 = max(16, int(round(r * 16)))
            if self.scene.get("motion_blur") and previous is not None:
                # Smear between the last and the current position (the exposure).
                prev_centre = (int(round(previous[0] * 16)), int(round(previous[1] * 16)))
                cv2.line(frame, prev_centre, centre, bgr, max(1, int(round(r * 2))), cv2.LINE_AA, shift=4)
            cv2.circle(frame, centre, radius16, bgr, -1, cv2.LINE_AA, shift=4)
            cv2.circle(frame, centre, max(16, int(round(r * 0.55 * 16))), (255, 255, 255), -1, cv2.LINE_AA, shift=4)
        self._draw_occluder(frame, t, sphere_px)
        noise = int(self.scene.get("noise", 0))
        if noise:
            frame = cv2.add(frame, self._rng.integers(0, noise, frame.shape, dtype=np.uint8))
        drift = float(self.scene.get("exposure_drift", 0.0))
        if drift:
            frame = cv2.convertScaleAbs(frame, alpha=1.0 + drift * math.sin(t * 0.9), beta=0)
        return frame

    def clock(self) -> float:
        """The camera's idea of the time.  Real, unless a virtual clock is on."""
        return self._now if self.virtual_clock else time.time()

    def read(self):
        if self.virtual_clock:
            self._now += 1.0 / self.fps
            return True, self.render(self._now), self._now
        now = time.time()
        if now < self._next_frame:
            time.sleep(self._next_frame - now)
            now = time.time()
        self._next_frame = max(self._next_frame, now) + 1.0 / self.fps
        return True, self.render(now), now

    def set_control(self, control, value) -> bool:
        return False

    def get_controls(self) -> dict:
        return {}

    def close(self) -> None:
        pass
