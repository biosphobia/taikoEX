"""A fake camera with fake controllers.

Used by the automated tests and for demo footage, and handy when you want
to work on the game without wiring up the real hardware.  The simulated
controllers live in *world* space, get projected through a virtual PS3 Eye
placed in front of the player, and are drawn as glowing spheres with an
over-exposed white centre - just like the real thing looks to the camera.

Controllers can be:

* parked at a position (``goto``)
* told to hit pads at given times (``schedule_hit``)
* told to play a whole TJA chart (``play_chart``)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .pads import Pad
from .tja import parse_tja_notes


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

    TOP = 0.18            # metres above the pad where a stroke starts
    DOWN_TIME = 0.08      # seconds from TOP down to the pad plane
    UP_TIME = 0.12        # seconds to come back up
    OVERSHOOT = 0.04      # how far below the pad plane the stroke goes

    def position_at(self, now: float, pads: dict[str, Pad]) -> np.ndarray:
        """Where the sphere is at time ``now``."""
        base = self.target if self.target is not None else self.rest_position
        # Find a stroke that is in progress.  The stroke moves at constant
        # speed so the sphere crosses the pad plane exactly at hit.time.
        speed = self.TOP / self.DOWN_TIME
        for hit in self.hits:
            pad = pads.get(hit.pad_id)
            if pad is None:
                continue
            start = hit.time - self.DOWN_TIME
            bottom_time = hit.time + self.OVERSHOOT / speed
            if start <= now <= bottom_time:
                height = self.TOP - speed * (now - start)
                return pad.center + pad.normal * height
            if bottom_time < now <= bottom_time + self.UP_TIME:
                s = (now - bottom_time) / self.UP_TIME
                height = -self.OVERSHOOT + (self.TOP + self.OVERSHOOT) * s
                return pad.center + pad.normal * height
        # Between strokes, drift towards the next pad so strokes are short.
        upcoming = [h for h in self.hits if h.time > now]
        if upcoming:
            pad = pads.get(upcoming[0].pad_id)
            if pad is not None:
                return pad.center + pad.normal * self.TOP
        return base

    def prune(self, now: float) -> None:
        self.hits = [h for h in self.hits if h.time + self.UP_TIME + 1.0 >= now]


class SimulatedCamera:
    """Camera backend that renders the virtual scene."""

    name = "simulated"

    def __init__(self, config):
        self.config = config
        cam_cfg = config["camera"]
        self.width, self.height = int(cam_cfg["width"]), int(cam_cfg["height"])
        self.fps = int(cam_cfg["fps"])
        self.virtual = VirtualCamera(
            position=np.array([0.15, 0.55, -1.25]),  # in front of the player, a bit up and to the side
            target=np.array([0.0, 0.05, 0.0]),
            focal_px=float(config["optics"]["focal_px"]),
            width=self.width, height=self.height,
            sphere_radius_m=float(config["optics"]["sphere_radius_m"]),
        )
        self.controllers: dict[int, VirtualController] = {}
        for index, ctrl in enumerate(config["controllers"]):
            rest = np.array([-0.2 if index == 0 else 0.2, 0.2, 0.0])
            self.controllers[int(ctrl["id"])] = VirtualController(int(ctrl["id"]), tuple(ctrl["led"]), rest, rest.copy())
        self.pads: dict[str, Pad] = {p["id"]: Pad.from_config(p) for p in config["pads"]}
        self.noise = 6
        self.distractor = True     # a static coloured blob to test masking
        self._next_frame = time.time()
        self.clock_offset = 0.0    # lets tests run faster than real time

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
        """Schedule every note of a chart.  Don notes alternate hands, big notes use both."""
        notes = parse_tja_notes(tja_text)
        ids = sorted(self.controllers)
        left, right = ids[0], ids[-1]
        toggle = False
        for note in notes:
            if note.big and len(ids) > 1:
                self.schedule_hit(audio_start_time + note.time, f"left_{note.kind}", left)
                self.schedule_hit(audio_start_time + note.time, f"right_{note.kind}", right)
            else:
                side, hand = ("right", right) if not toggle else ("left", left)
                toggle = not toggle
                self.schedule_hit(audio_start_time + note.time, f"{side}_{note.kind}", hand)
        return len(notes)

    def reload_pads(self) -> None:
        self.pads = {p["id"]: Pad.from_config(p) for p in self.config["pads"]}

    # ---- frame rendering -------------------------------------------------
    def render(self, now: float) -> np.ndarray:
        frame = np.full((self.height, self.width, 3), 28, dtype=np.uint8)
        if self.noise:
            frame = cv2.add(frame, np.random.randint(0, self.noise, frame.shape, dtype=np.uint8))
        if self.distractor:
            # A magenta-ish poster in the corner that the mask should remove.
            cv2.rectangle(frame, (self.width - 70, 20), (self.width - 20, 60), (120, 40, 130), -1)
        for ctrl in self.controllers.values():
            ctrl.prune(now)
            ctrl.position = ctrl.position_at(now, self.pads)
            projected = self.virtual.project(ctrl.position)
            if projected is None:
                continue
            u, v, r = projected
            bgr = tuple(int(c) for c in reversed(ctrl.colour_rgb))
            # Draw with 1/16 pixel precision (shift=4) so the sphere size is not quantised.
            centre = (int(round(u * 16)), int(round(v * 16)))
            cv2.circle(frame, centre, max(16, int(round(r * 16))), bgr, -1, cv2.LINE_AA, shift=4)
            # Over-exposed core: bright spheres wash out to white in the middle.
            cv2.circle(frame, centre, max(16, int(round(r * 0.55 * 16))), (255, 255, 255), -1, cv2.LINE_AA, shift=4)
        return frame

    def read(self):
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
