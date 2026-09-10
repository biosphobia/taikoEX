"""The main loop: camera -> detection -> 3D -> pads -> game / keyboard.

Run it with ``python run_tracker.py``.  Everything the game can change at
runtime goes through ``handle_command``; look there for the list of
commands (they are also described in docs/PROTOCOL.md).
"""

from __future__ import annotations

import sys
import time
import traceback
from collections import deque
from pathlib import Path

import numpy as np

from . import __version__
from .background import BackgroundLearner
from .camera import apply_orientation, choose_fastest, open_camera, probe_modes
from .config import DEFAULT_CONFIG, LED_PRESETS, Config
from .geometry import PinholeModel, WorldCalibration, WorldTransform, solve_focal_and_offset
from .imu import OrientationFilter, quat_rotate
from .keysender import KeySender
from .network import CommandReceiver, PreviewSender, StateSender
from .pads import LAYOUTS, HitDetector, Pad
from .psmove_hid import BUTTON_BITS, MoveManager
from .tracking_filter import RayKalman, measurement_covariance, radius_is_plausible, ray_aligned_rotation
from .vision import Detection, SphereDetector, draw_detections, sample_colour


class TrackedController:
    """Per-controller runtime state: position, orientation and their filters."""

    def __init__(self, cfg: dict, fusion_cfg: dict, imu_cfg: dict):
        self.cfg = cfg
        self.id = int(cfg["id"])
        self.visible = False
        self.pixel = (0.0, 0.0, 0.0)
        self.camera_pos = np.zeros(3)
        self.world_pos = np.zeros(3)          # filtered, used for hits and display
        self.raw_world_pos = np.zeros(3)      # straight from the camera, before filtering
        self.velocity = np.zeros(3)
        self.kalman = RayKalman(fusion_cfg)
        self.orientation = OrientationFilter(imu_cfg)
        self.last_seen = 0.0
        self.last_radius = 0.0
        self.recent_radii: deque[float] = deque(maxlen=60)   # for the distance calibration
        self.size_trusted = True
        self.untrusted_frames = 0
        self.last_imu_time = 0.0
        self.last_buttons = 0

    def measure(self, det: Detection, camera_pos: np.ndarray, raw_world: np.ndarray,
                covariance: np.ndarray, now: float, accel_world: np.ndarray | None) -> None:
        """Fold one camera measurement into the filter."""
        self.visible = True
        self.pixel = (det.x, det.y, det.radius)
        if det.completeness >= 0.85:
            # Only a whole sphere says anything about the distance; one with a
            # hand or a drum edge bitten out of it is left out of the average.
            self.recent_radii.append(float(det.radius))
        self.camera_pos = camera_pos
        self.raw_world_pos = raw_world
        gap = now - self.last_seen
        if gap > float(self.kalman.cfg.get("reset_after_s", 0.3)):
            self.kalman.reset()
        else:
            self.kalman.predict(max(1e-4, gap), accel_world)
        self.kalman.update(raw_world, covariance)
        self.world_pos = self.kalman.position.copy()
        self.velocity = self.kalman.velocity.copy()
        self.last_seen = now

    def steady_radius(self, tolerance: float = 0.1) -> float:
        """The sphere's radius averaged over the frames it has been held still.

        One frame's radius jitters by a fraction of a pixel, which on a sphere
        a few pixels across is several centimetres of distance.  The distance
        calibration is done with the controller held still, so take the median
        of the recent readings that agree with the latest one (within
        ``tolerance``); anything older belongs to wherever the hand was before.
        """
        radii = list(self.recent_radii)
        if not radii:
            raise RuntimeError("the sphere is partly hidden - move so the camera sees all of it, then try again")
        latest = radii[-1]
        steady = []
        for radius in reversed(radii):
            if abs(radius - latest) > tolerance * latest + 0.5:
                break
            steady.append(radius)
        return float(np.median(steady))

    def lost(self) -> None:
        self.visible = False
        self.recent_radii.clear()     # a radius from before the sphere was lost says nothing about now

    def update_orientation(self, accel_g, gyro_rad_s, now: float) -> None:
        dt = now - self.last_imu_time if self.last_imu_time > 0 else 1.0 / 60.0
        self.last_imu_time = now
        self.orientation.update(accel_g, gyro_rad_s, min(0.2, max(1e-4, dt)))

    def world_acceleration(self) -> np.ndarray | None:
        """Controller acceleration in world space, gravity removed (m/s^2)."""
        if not self.orientation.initialised or self.last_imu_time <= 0.0:
            return None
        reading = quat_rotate(self.orientation.q, self.orientation.last_accel_g)
        return (reading - np.array([0.0, 1.0, 0.0])) * 9.80665


class Tracker:
    def __init__(self, config: Config):
        self.config = config
        self.running = True
        self.camera = None
        self.camera_mode: dict = {}
        self.frame_size = (int(config["camera"]["width"]), int(config["camera"]["height"]))
        self.detector = SphereDetector(config)
        self.model = PinholeModel(config["optics"], *self.frame_size)
        self.world = WorldTransform(config["world"])
        self.world_calibration = WorldCalibration()
        self.controllers = {int(c["id"]): TrackedController(c, config["fusion"], config["imu"])
                            for c in config["controllers"]}
        self.hits = HitDetector([Pad.from_config(p) for p in config["pads"]], config["hits"])
        self.moves = MoveManager(config)
        self.background = BackgroundLearner(config)
        self.background.on_finished = self._on_background_learned
        self.background_result: dict = {}
        self.keys = KeySender(float(config["osu"]["key_hold_ms"]))
        net = config["network"]
        self.state_out = StateSender(net["game_host"], net["state_port"])
        self.commands = CommandReceiver(net["command_port"])
        self.preview = PreviewSender(net["game_host"], net["preview_port"])
        self.preview_mode = "camera"     # or "mask"
        self.fps = 0.0
        self._fps_clock = time.time()
        self._fps_frames = 0
        self.last_frame = None
        self.last_detections: list[Detection] = []
        self.recent_hits: list[dict] = []   # last few hits, for the game UI
        self.pads_revision = 0              # bumped whenever the pad layout changes
        self.distance_samples: list[tuple[float, float]] = []   # (radius_px, distance_m)
        self.camera_error = ""              # why the camera is not open, for the game to show
        self._camera_retry_at = 0.0
        self.try_open_camera()

    # ------------------------------------------------------------------
    def try_open_camera(self) -> bool:
        """Open the camera, or remember why it could not be opened.

        A camera that will not open is not the end of the tracker: the game
        still gets state packets carrying the error, so "not connected"
        comes with a reason (a driver that is not installed, another program
        holding the device, the wrong index), and the tracker keeps trying
        every few seconds.
        """
        try:
            self.open_camera()
        except Exception as exc:
            self.camera = None
            self.camera_error = str(exc)
            self._camera_retry_at = time.time() + float(self.config["camera"].get("retry_s", 3.0))
            print(f"[camera] could not open: {exc}")
            return False
        self.camera_error = ""
        return True

    def open_camera(self) -> None:
        if self.camera is not None:
            self.camera.close()
            self.camera = None
        self.camera = open_camera(self.config)
        self.camera_mode = self.camera.mode()
        new_size = (int(self.camera_mode["width"]), int(self.camera_mode["height"]))
        if new_size != self.frame_size:
            # Distance samples are radii in the old frame's pixels; they
            # cannot be mixed with samples taken at the new size.
            self.distance_samples = []
        self.frame_size = new_size
        self.model = PinholeModel(self.config["optics"], *self.frame_size)
        self.detector.invalidate()
        mode = self.camera_mode
        print(f"[camera] opened '{self.camera.name}' backend at {mode['width']}x{mode['height']}, "
              f"{mode['fps']} fps (asked for {self.config['camera']['width']}x{self.config['camera']['height']}, "
              f"{self.config['camera']['fps']} fps)")

    def apply_config_changes(self, patch: dict) -> None:
        """Push a partial config into the live objects."""
        self.config.apply_patch(patch)
        if "camera" in patch:
            self.try_open_camera()
        if "processing" in patch:
            self.detector.invalidate()
        if "optics" in patch:
            self.model = PinholeModel(self.config["optics"], *self.frame_size)
            self.detector.invalidate()      # the reference width may have changed
        if "world" in patch:
            self.world = WorldTransform(self.config["world"])
        if "pads" in patch or "hits" in patch:
            self.reload_pads()
        if "controllers" in patch:
            self.controllers = {int(c["id"]): TrackedController(c, self.config["fusion"], self.config["imu"])
                                for c in self.config["controllers"]}
        if "background" in patch:
            self.detector.invalidate()
        if "osu" in patch:
            self.keys.hold_s = float(self.config["osu"]["key_hold_ms"]) / 1000.0

    def reload_pads(self) -> None:
        self.pads_revision += 1
        self.hits.reload([Pad.from_config(p) for p in self.config["pads"]], self.config["hits"])
        if hasattr(self.camera, "reload_pads"):
            self.camera.reload_pads()

    # ------------------------------------------------------------------
    def process_frame(self, frame, timestamp: float) -> dict:
        frame = apply_orientation(frame, self.config["camera"])
        if frame.shape[1] != self.frame_size[0] or frame.shape[0] != self.frame_size[1]:
            self.frame_size = (frame.shape[1], frame.shape[0])
            self.model = PinholeModel(self.config["optics"], *self.frame_size)
        self.last_frame = frame
        # While the background learner is running the spheres are dark, so the
        # frame is evidence about the room rather than about the controllers.
        if self.background.active:
            if self.background.feed(frame):
                self.set_leds(True)
                self.detector.invalidate()
            self.last_detections = []
            return self.build_state(timestamp, [])

        detections = self.detector.detect(frame)
        self.last_detections = detections
        new_hits = []
        for det in detections:
            tracked = self.controllers.get(det.controller_id)
            if tracked is None:
                continue
            if not det.found:
                tracked.lost()
                continue
            fusion = self.config["fusion"]
            cam_pos = self.model.pixel_to_camera(det.x, det.y, det.radius)
            world_pos = self.world.to_world(cam_pos)
            # The accelerometer is used for two separate things - predicting
            # the motion, and timing the hit - each with its own switch.
            accel = tracked.world_acceleration()
            predict_accel = accel if fusion.get("use_imu_accel", True) else None
            if fusion.get("enabled", True):
                distance = float(np.linalg.norm(cam_pos))
                # A sphere cannot suddenly look half its size.  When it does,
                # keep the direction and let the filter hold the distance.
                tracked.size_trusted = radius_is_plausible(
                    tracked.last_radius, det.radius, distance,
                    max(1e-4, timestamp - tracked.last_seen), fusion)
                if tracked.size_trusted:
                    tracked.untrusted_frames = 0
                else:
                    tracked.untrusted_frames += 1
                    # Give up doubting eventually: if the sphere really has
                    # been this size for a while, it is not an obstruction.
                    if tracked.untrusted_frames > int(fusion.get("untrusted_frames_max", 20)):
                        tracked.size_trusted = True
                        tracked.untrusted_frames = 0
                rotation = ray_aligned_rotation(cam_pos, self.world.rotation)
                covariance = measurement_covariance(self.model, det.radius, distance, rotation,
                                                   fusion, det.completeness,
                                                   trust_size=tracked.size_trusted)
                if not tracked.size_trusted:
                    # Put the measurement back on the ray at the distance we
                    # already believe, so only its direction is used.
                    believed = self.world.to_camera(tracked.kalman.position) if tracked.kalman.initialised else cam_pos
                    scale = float(np.linalg.norm(believed)) / max(distance, 1e-6)
                    world_pos = self.world.to_world(cam_pos * scale)
                tracked.measure(det, cam_pos, world_pos, covariance, timestamp, predict_accel)
            else:
                tracked.size_trusted = True
                tracked.measure(det, cam_pos, world_pos, np.eye(3) * 1e-6, timestamp, None)
            # Only believe a new size once it has been accepted; otherwise the
            # obstructed reading would become the yardstick for the next frame
            # and everything after it would look perfectly reasonable.
            if tracked.size_trusted:
                tracked.last_radius = det.radius
            new_hits.extend(self.hits.update(det.controller_id, tracked.world_pos, timestamp,
                                             tracked.velocity, accel))
        for hit in new_hits:
            self.on_hit(hit)
        return self.build_state(timestamp, new_hits)

    def update_controllers_imu(self, timestamp: float) -> None:
        """Feed the IMU into each controller's orientation filter."""
        if not self.config["imu"].get("enabled", True):
            return
        scale = float(self.config["imu"].get("gyro_rad_per_unit", 0.001065))
        for tracked in self.controllers.values():
            sample = None
            move = self.moves.state_for(tracked.id)
            if move is not None and move.connected:
                sample = (move.accel_g, [v * scale for v in move.gyro_raw])
                if self.config["imu"].get("recenter_with_move_button", True):
                    pressed_now = bool(move.buttons & BUTTON_BITS["move"])
                    if pressed_now and not (tracked.last_buttons & BUTTON_BITS["move"]):
                        tracked.orientation.recenter_yaw()
                    tracked.last_buttons = move.buttons
            elif self.camera is not None and hasattr(self.camera, "imu_sample"):
                sample = self.camera.imu_sample(tracked.id)
            if sample is not None:
                tracked.update_orientation(sample[0], sample[1], timestamp)

    def set_leds(self, on: bool) -> None:
        """Turn every sphere on or off (used while learning the background)."""
        self.moves.set_all_leds(on)
        if self.camera is not None and hasattr(self.camera, "set_leds"):
            self.camera.set_leds(on)

    def _on_background_learned(self, result: dict) -> None:
        self.background_result = result
        self.detector.invalidate()
        print(f"[background] masked {result['regions']} region(s), {result['covered_percent']}% of the frame")

    def on_hit(self, hit) -> None:
        if self.config["osu"]["enabled"]:
            keys = self.config["osu"]["keys"]
            # Keys are named "<side>_<kind>"; with a single drum the side comes
            # from the hand, so the same pad types both d/f and j/k.
            key = keys.get(f"{hit.side}_{hit.kind}") or keys.get(hit.pad_id, "")
            self.keys.tap(key)
        if self.config["debug"]["print_hits"]:
            print(f"[hit] {hit.pad_id:10s} ctrl {hit.controller_id}  {hit.speed:4.2f} m/s")
        self.recent_hits.append(self.hit_to_dict(hit))
        del self.recent_hits[:-20]

    @staticmethod
    def hit_to_dict(hit) -> dict:
        return {"pad": hit.pad_id, "kind": hit.kind, "side": hit.side, "controller": hit.controller_id,
                "t": round(hit.time, 4), "speed": round(hit.speed, 3), "strength": round(hit.strength, 3),
                "pos": hit.position}

    def build_state(self, timestamp: float, new_hits) -> dict:
        controllers = []
        for tracked in self.controllers.values():
            move = self.moves.state_for(tracked.id)
            entry = {
                "id": tracked.id,
                "visible": tracked.visible,
                "px": [round(v, 1) for v in tracked.pixel],
                "cam": [round(float(v), 4) for v in tracked.camera_pos],
                "world": [round(float(v), 4) for v in tracked.world_pos],
                "raw": [round(float(v), 4) for v in tracked.raw_world_pos],
                "vel": [round(float(v), 3) for v in tracked.velocity],
                "quat": tracked.orientation.as_list(),
                "imu": bool(tracked.orientation.initialised),
                "led": [int(v) for v in tracked.cfg.get("led", [255, 255, 255])],
                "hid": move is not None and move.connected,
            }
            if move is not None:
                entry["buttons"] = move.buttons
                entry["trigger"] = move.trigger
                entry["battery"] = move.battery
                entry["accel"] = [round(a, 3) for a in move.accel_g]
            controllers.append(entry)
        state = {
            "type": "state",
            "t": round(timestamp, 4),
            "fps": round(self.fps, 1),                       # measured by the tracker
            "fps_requested": int(self.config["camera"]["fps"]),
            "fps_camera": self.camera_mode.get("fps", 0),     # what the driver claims
            "frame": list(self.frame_size),
            "controllers": controllers,
            "hits": [self.hit_to_dict(h) for h in new_hits],
            "world_calibrated": bool(self.config["world"].get("calibrated", False)),
            "world_points": sorted(self.world_calibration.points),
            "camera_pose": self.world.camera_pose(),
            "pads_revision": self.pads_revision,
            "osu": bool(self.config["osu"]["enabled"]),
            "hid_available": self.moves.available,
            "hid_status": self.moves.status,
            "camera_error": self.camera_error,
            "learning_background": self.background.active,
        }
        return state

    # ------------------------------------------------------------------
    def handle_command(self, msg: dict) -> dict:
        cmd = str(msg.get("cmd", ""))
        reply = {"reply": cmd, "ok": True}
        try:
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                reply.update(ok=False, error=f"unknown command '{cmd}'")
            else:
                result = handler(msg) or {}
                reply.update(result)
        except Exception as exc:
            reply.update(ok=False, error=str(exc))
        if msg.get("save"):
            self.config.save()
        return reply

    # -- generic -------------------------------------------------------
    def cmd_ping(self, msg):
        return {"version": __version__, "time": time.time()}

    def cmd_get_config(self, msg):
        return {"config": self.config.data}

    def cmd_get_defaults(self, msg):
        return {"config": DEFAULT_CONFIG}

    def cmd_set_config(self, msg):
        self.apply_config_changes(msg.get("patch", {}))
        return {"config": self.config.data}

    def cmd_save_config(self, msg):
        self.config.save()
        return {"path": str(self.config.path)}

    def cmd_reset_config(self, msg):
        """Back to DEFAULT_CONFIG, with every live object rebuilt from it."""
        self.config = Config({}, self.config.path)
        self.detector.config = self.config
        self.moves.config = self.config
        self.background.config = self.config
        self.reapply_config()
        return {"config": self.config.data}

    def reapply_config(self) -> None:
        """Rebuild everything that caches part of the config, from the config as it is now."""
        self.try_open_camera()
        self.detector.invalidate()
        self.model = PinholeModel(self.config["optics"], *self.frame_size)
        self.world = WorldTransform(self.config["world"])
        self.reload_pads()
        self.controllers = {int(c["id"]): TrackedController(c, self.config["fusion"], self.config["imu"])
                            for c in self.config["controllers"]}
        self.keys.hold_s = float(self.config["osu"]["key_hold_ms"]) / 1000.0

    def cmd_quit(self, msg):
        self.running = False

    # -- camera --------------------------------------------------------
    def cmd_set_camera_control(self, msg):
        control, value = msg["control"], msg["value"]
        ok = self.camera.set_control(control, value)
        self.config["camera"]["controls"][control] = value
        return {"applied": ok, "controls": self.camera.get_controls()}

    def cmd_get_camera_controls(self, msg):
        return {"controls": self.camera.get_controls()}

    def cmd_list_cameras(self, msg):
        import cv2

        found = []
        for index in range(int(msg.get("max_index", 8))):
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                found.append({"index": index, "width": cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                              "height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT)})
            cap.release()
        return {"cameras": found}

    def cmd_probe_camera_modes(self, msg):
        """Try every mode in camera.fast_modes and report what each delivers.

        The live camera is closed while the modes are measured (a device can
        only be open once) and reopened afterwards.
        """
        results = self._probe(msg, stop_when_delivered=False)
        return {"modes": results}

    def cmd_camera_fastest(self, msg):
        """Switch to the fastest mode the camera really delivers.

        The modes are tried quickest first and the search stops at the first
        one that measures at least camera.fast_mode_min_ratio of its request,
        so with a PS3 Eye on a good driver this takes about a second.
        """
        results = self._probe(msg, stop_when_delivered=True)
        best = choose_fastest(results)
        if best is None:
            raise RuntimeError("no camera mode could be opened: " + "; ".join(str(r.get("error", "")) for r in results))
        self.apply_config_changes({"camera": {"width": best["width"], "height": best["height"],
                                              "fps": best["fps_requested"]}})
        return {"chosen": best, "modes": results, "mode": self.camera_mode}

    def _probe(self, msg: dict, stop_when_delivered: bool) -> list[dict]:
        if self.camera is not None:
            self.camera.close()
            self.camera = None
        try:
            return probe_modes(self.config, modes=msg.get("modes"), seconds=float(msg.get("seconds", 0.5)),
                               stop_when_delivered=stop_when_delivered, between=self.moves.update)
        finally:
            self.open_camera()

    def cmd_set_preview(self, msg):
        net = self.config["network"]
        for key in ("preview_enabled", "preview_fps", "preview_width", "preview_quality"):
            if key in msg:
                net[key] = msg[key]
        if "mode" in msg:
            self.preview_mode = str(msg["mode"])
        return {"mode": self.preview_mode}

    # -- colours -------------------------------------------------------
    def cmd_sample_colour(self, msg):
        """Set a controller's HSV range from the pixels around (x, y)."""
        if self.last_frame is None:
            raise RuntimeError("no frame yet")
        controller = self._controller_cfg(int(msg.get("controller", 0)))
        h, w = self.last_frame.shape[:2]
        x = int(msg.get("x", w // 2))
        y = int(msg.get("y", h // 2))
        hsv_min, hsv_max = sample_colour(self.last_frame, x, y, int(msg.get("size", 12)),
                                         int(msg.get("hue_margin", 12)))
        controller["hsv_min"], controller["hsv_max"] = hsv_min, hsv_max
        return {"hsv_min": hsv_min, "hsv_max": hsv_max}

    def cmd_led_presets(self, msg):
        """Sphere colours that track well, each with its HSV range."""
        return {"presets": LED_PRESETS}

    def cmd_set_led(self, msg):
        controller = self._controller_cfg(int(msg.get("controller", 0)))
        controller["led"] = [int(v) for v in msg["rgb"]]
        return {"led": controller["led"]}

    def cmd_list_controllers(self, msg):
        return {"devices": self.moves.list_devices(), "status": self.moves.status,
                "connected": {slot: c.serial for slot, c in self.moves.controllers.items()}}

    # -- calibration ---------------------------------------------------
    def cmd_calibrate_distance(self, msg):
        """Add one sample to the two-point distance calibration.

        Hold the controller a measured distance from the lens and call this,
        then repeat at a clearly different distance.  With two samples the
        tracker can separate the focal length from the constant glow around
        the sphere, which one distance alone cannot do.
        """
        tracked = self._tracked(int(msg.get("controller", 0)))
        distance = float(msg["distance_m"])
        self.distance_samples = [s for s in self.distance_samples if abs(s[1] - distance) > 0.05]
        self.distance_samples.append((tracked.steady_radius(), distance))
        result = {"samples": [[round(r, 2), round(d, 3)] for r, d in self.distance_samples]}
        if len(self.distance_samples) >= 2:
            # Solved in the live frame's pixels (the glow is a per-pixel
            # effect); only the focal length is stored at the reference width.
            focal, offset = solve_focal_and_offset(self.distance_samples, float(self.config["optics"]["sphere_radius_m"]))
            self.config["optics"]["focal_px"] = round(self.model.to_reference_px(focal), 2)
            self.config["optics"]["radius_offset_px"] = round(offset, 3)
            self.model = PinholeModel(self.config["optics"], *self.frame_size)
            result.update(focal_px=self.config["optics"]["focal_px"],
                          radius_offset_px=self.config["optics"]["radius_offset_px"])
        return result

    def cmd_calibrate_distance_reset(self, msg):
        self.distance_samples = []

    def cmd_calibrate_focal(self, msg):
        """Hold a controller at a known distance from the lens and call this."""
        tracked = self._tracked(int(msg.get("controller", 0)))
        distance = float(msg["distance_m"])
        focal = self.model.focal_from_known_distance(tracked.pixel[2], distance)
        self.config["optics"]["focal_px"] = round(self.model.to_reference_px(focal), 2)
        self.model = PinholeModel(self.config["optics"], *self.frame_size)
        return {"focal_px": self.config["optics"]["focal_px"]}

    def cmd_world_capture(self, msg):
        """Capture one of the three world calibration points ("origin", "right", "forward")."""
        point = str(msg["point"])
        tracked = self._tracked(int(msg.get("controller", 0)))
        self.world_calibration.capture(point, tracked.camera_pos)
        result = {"captured": sorted(self.world_calibration.points)}
        if self.world_calibration.is_complete():
            self.world = self.world_calibration.solve()
            self.config["world"] = self.world.as_config()
            result["world"] = self.config["world"]
        return result

    def cmd_world_reset(self, msg):
        self.world_calibration = WorldCalibration()
        self.config["world"] = dict(DEFAULT_CONFIG["world"])
        self.world = WorldTransform(self.config["world"])

    # -- pads ----------------------------------------------------------
    def cmd_place_pad(self, msg):
        """Move a pad's centre to where the controller currently is."""
        tracked = self._tracked(int(msg.get("controller", 0)))
        pad = self._pad_cfg(str(msg["pad"]))
        pad["center"] = [round(float(v), 4) for v in tracked.world_pos]
        if msg.get("normal"):
            pad["normal"] = [float(v) for v in msg["normal"]]
        self.reload_pads()
        return {"pad": pad}

    def cmd_set_pad(self, msg):
        pad = self._pad_cfg(str(msg["pad"]))
        for key in ("center", "normal", "radius", "inner_radius", "name", "kind", "side"):
            if key in msg:
                pad[key] = msg[key]
        self.reload_pads()
        return {"pad": pad}

    def cmd_pad_layout_taiko(self, msg):
        """Reset the pads to a preset layout, centred on a point or a controller.

        ``style`` is "single_drum" (one face with a rim around it, the default)
        or "four_pads" (four targets in a row).
        """
        if "center" in msg:
            center = np.array(msg["center"], dtype=float)
        elif "controller" in msg:
            center = self._tracked(int(msg["controller"])).world_pos
        else:
            center = np.zeros(3)
        style = str(msg.get("style", "single_drum"))
        builder = LAYOUTS.get(style)
        if builder is None:
            raise KeyError(f"unknown pad layout '{style}' (try {', '.join(LAYOUTS)})")
        extra = {k: float(v) for k, v in msg.items()
                 if k in ("face_radius", "rim_width", "gap", "face_spacing", "rim_spacing", "radius")}
        self.config["pads"] = builder(center, **extra)
        self.reload_pads()
        return {"pads": self.config["pads"], "style": style}

    def cmd_nudge_pad(self, msg):
        """Move one pad by a delta in world metres, e.g. {"pad": "don", "delta": [0, 0.02, 0]}."""
        pad = self._pad_cfg(str(msg["pad"]))
        delta = np.array(msg.get("delta", [0, 0, 0]), dtype=float)
        pad["center"] = [round(float(v), 4) for v in np.array(pad["center"], dtype=float) + delta]
        self.reload_pads()
        return {"pad": pad}

    def cmd_nudge_pads(self, msg):
        """Move the whole drum by a delta, and optionally scale it about its centre."""
        delta = np.array(msg.get("delta", [0, 0, 0]), dtype=float)
        scale = float(msg.get("scale", 1.0))
        pads = self.config["pads"]
        centre = np.mean([np.array(p["center"], dtype=float) for p in pads], axis=0) if pads else np.zeros(3)
        for pad in pads:
            position = centre + (np.array(pad["center"], dtype=float) - centre) * scale + delta
            pad["center"] = [round(float(v), 4) for v in position]
            if scale != 1.0:
                pad["radius"] = round(float(pad.get("radius", 0.11)) * scale, 4)
        self.reload_pads()
        return {"pads": pads}

    def cmd_get_pads(self, msg):
        return {"pads": self.config["pads"], "revision": self.pads_revision}

    def cmd_get_recent_hits(self, msg):
        return {"hits": self.recent_hits}

    # -- background and IMU ---------------------------------------------
    def cmd_learn_background(self, msg):
        """Turn the spheres off, see what still looks like a sphere, mask it."""
        self.set_leds(False)
        frames = self.background.start(msg.get("frames"), fps=float(self.camera_mode.get("fps", 60)))
        return {"frames": frames}

    def cmd_clear_background(self, msg):
        self.config["background"]["mask"] = []
        self.background_result = {}
        self.detector.invalidate()

    def cmd_background_result(self, msg):
        return {"result": self.background_result, "learning": self.background.active}

    def cmd_recenter_orientation(self, msg):
        """Point the controller's heading at the camera (the MOVE button does this too)."""
        targets = [int(msg["controller"])] if "controller" in msg else list(self.controllers)
        for cid in targets:
            self._tracked_any(cid).orientation.recenter_yaw()
        return {"controllers": targets}

    def cmd_imu_calibrate_upright(self, msg):
        """Hold the controller upright, sphere up, and call this.

        Whatever the accelerometer reads now is the direction from the handle
        towards the sphere, so the 3D model lines up with the real controller.
        """
        cid = int(msg.get("controller", 0))
        tracked = self._tracked_any(cid)
        move = self.moves.state_for(cid)
        accel = np.array(move.accel_g if move is not None else tracked.orientation.last_accel_g, dtype=float)
        norm = float(np.linalg.norm(accel))
        if norm < 0.5:
            raise RuntimeError("no accelerometer reading from controller %d" % cid)
        axis = (accel / norm).round(4).tolist()
        self.config["imu"]["handle_axis"] = axis
        for other in self.controllers.values():
            other.orientation.handle_axis = np.array(axis, dtype=float)
            other.orientation.initialised = False
        return {"handle_axis": axis}

    # -- osu -----------------------------------------------------------
    def cmd_set_osu(self, msg):
        self.config["osu"]["enabled"] = bool(msg.get("enabled", True))
        if "keys" in msg:
            self.config["osu"]["keys"].update(msg["keys"])
        return {"osu": self.config["osu"]}

    # -- simulation ----------------------------------------------------
    def cmd_sim_scene(self, msg):
        """Switch the simulated room, e.g. {"scene": "living_room"}."""
        from .simulation import SCENES

        if "scene" in msg:
            self.config["simulation"]["scene"] = str(msg["scene"])
        for key in ("camera_position", "camera_target"):
            if key in msg:
                self.config["simulation"][key] = msg[key]
        self.open_camera()
        for tracked in self.controllers.values():
            tracked.kalman.reset()
        return {"scene": self.config["simulation"]["scene"],
                "description": SCENES.get(self.config["simulation"]["scene"], {}).get("description", ""),
                "camera": self._sim().camera_pose_world()}

    def cmd_sim_truth(self, msg):
        """True controller positions, for measuring the tracker's error."""
        return {"truth": self._sim().truth_positions(), "camera": self._sim().camera_pose_world()}

    def cmd_sim_goto(self, msg):
        self._sim().goto(int(msg.get("controller", 0)), msg["position"])

    def cmd_sim_hit(self, msg):
        sim = self._sim()
        at = float(msg.get("at", sim.clock() + 0.3))
        sim.schedule_hit(at, str(msg["pad"]), int(msg.get("controller", 0)))

    def cmd_sim_play_chart(self, msg):
        sim = self._sim()
        text = msg.get("tja") or Path(msg["path"]).read_text(encoding="utf-8", errors="replace")
        count = sim.play_chart(text, float(msg.get("start_time", time.time() + 1.0)))
        return {"notes": count}

    # -- helpers -------------------------------------------------------
    def _controller_cfg(self, cid: int) -> dict:
        for c in self.config["controllers"]:
            if int(c["id"]) == cid:
                return c
        raise KeyError(f"no controller with id {cid}")

    def _tracked_any(self, cid: int) -> TrackedController:
        tracked = self.controllers.get(cid)
        if tracked is None:
            raise KeyError(f"no controller with id {cid}")
        return tracked

    def _tracked(self, cid: int) -> TrackedController:
        tracked = self.controllers.get(cid)
        if tracked is None:
            raise KeyError(f"no controller with id {cid}")
        if not tracked.visible:
            raise RuntimeError(f"controller {cid} is not visible")
        return tracked

    def _pad_cfg(self, pad_id: str) -> dict:
        for p in self.config["pads"]:
            if p["id"] == pad_id:
                return p
        raise KeyError(f"no pad with id '{pad_id}'")

    def _sim(self):
        if self.camera is None or self.camera.name != "simulated":
            raise RuntimeError("camera backend is not 'simulated'")
        return self.camera

    # ------------------------------------------------------------------
    def send_preview(self) -> None:
        net = self.config["network"]
        if not net.get("preview_enabled", True) or self.last_frame is None:
            return
        if self.preview_mode == "mask":
            import cv2

            masks = list(self.detector.last_masks.values())
            image = cv2.cvtColor(masks[0] if masks else self.last_frame[:, :, 0], cv2.COLOR_GRAY2BGR)
            for mask in masks[1:]:
                image = cv2.bitwise_or(image, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))
        else:
            image = draw_detections(self.last_frame, self.last_detections, self.config)
        self.preview.maybe_send(image, float(net["preview_fps"]), int(net["preview_width"]), int(net["preview_quality"]))

    def show_debug_window(self) -> bool:
        import cv2

        if self.last_frame is None:
            return True
        image = draw_detections(self.last_frame, self.last_detections, self.config)
        cv2.putText(image, f"{self.fps:.0f} fps", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imshow("taiko tracker", image)
        return cv2.waitKey(1) != 27   # Esc closes

    def tick_fps(self) -> None:
        self._fps_frames += 1
        now = time.time()
        if now - self._fps_clock >= 1.0:
            self.fps = self._fps_frames / (now - self._fps_clock)
            self._fps_frames = 0
            self._fps_clock = now

    def step(self) -> None:
        """One iteration: commands, controllers, a frame."""
        for msg, addr in self.commands.poll():
            self.commands.reply(addr, self.handle_command(msg))
        self.moves.update()
        if self.camera is None:
            self.step_without_camera()
            return
        ok, frame, timestamp = self.camera.read()
        self.update_controllers_imu(timestamp)
        if not ok or frame is None:
            time.sleep(0.01)
            return
        state = self.process_frame(frame, timestamp)
        self.state_out.send(state)
        self.send_preview()
        self.tick_fps()
        if self.config["debug"]["show_window"] and not self.show_debug_window():
            self.running = False

    def step_without_camera(self) -> None:
        """Keep talking to the game and the controllers while the camera is missing."""
        now = time.time()
        if now >= self._camera_retry_at and self.try_open_camera():
            return
        self.update_controllers_imu(now)
        self.state_out.send(self.build_state(now, []))
        time.sleep(1.0 / 30.0)

    def run(self) -> None:
        print(f"taiko tracker {__version__} - Ctrl+C to stop")
        try:
            while self.running:
                self.step()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        self.running = False
        if self.camera is not None:
            self.camera.close()
        self.moves.close()
        # Release the UDP ports, so another tracker can start straight away.
        for link in (self.state_out, self.commands, self.preview):
            link.close()


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="TaikoEX PS Move tracker")
    parser.add_argument("--config", type=Path, help="path to tracker_config.json")
    parser.add_argument("--backend", help="camera backend: opencv, pseye, video, simulated")
    parser.add_argument("--camera", type=int, help="camera index")
    parser.add_argument("--video", help="video file to replay (sets backend=video)")
    parser.add_argument("--show", action="store_true", help="open a debug window")
    parser.add_argument("--osu", action="store_true", help="start with osu! key output enabled")
    parser.add_argument("--no-hid", action="store_true", help="do not talk to the controllers over Bluetooth")
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    log_path = config.path.with_name("tracker.log")
    log = start_logging(log_path)
    if args.backend:
        config["camera"]["backend"] = args.backend
    if args.camera is not None:
        config["camera"]["index"] = args.camera
    if args.video:
        config["camera"]["backend"] = "video"
        config["camera"]["video_path"] = args.video
    if args.show:
        config["debug"]["show_window"] = True
    if args.osu:
        config["osu"]["enabled"] = True
    if args.no_hid:
        config["hid"]["enabled"] = False

    try:
        Tracker(config).run()
    except Exception as exc:
        # The game shows the last lines of the log when the tracker is not
        # answering, so the reason must land there before the process ends.
        print(f"[tracker] fatal: {exc}")
        traceback.print_exc()
        if log is not None:
            log.flush()
        return 1
    return 0


class Tee:
    """Write everything printed to the console into the log file as well."""

    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file

    def write(self, text: str) -> None:
        for stream in (self.console, self.log_file):
            try:
                stream.write(text)
                stream.flush()
            except Exception:
                pass

    def flush(self) -> None:
        for stream in (self.console, self.log_file):
            try:
                stream.flush()
            except Exception:
                pass


def start_logging(path: Path):
    """Mirror stdout and stderr into ``tracker.log`` next to the config file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(path, "w", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)
    print(f"taiko tracker {__version__} - log at {path}")
    return log_file


if __name__ == "__main__":
    sys.exit(main())
