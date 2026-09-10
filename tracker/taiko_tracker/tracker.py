"""The main loop: camera -> detection -> 3D -> pads -> game / keyboard.

Run it with ``python run_tracker.py``.  Everything the game can change at
runtime goes through ``handle_command``; look there for the list of
commands (they are also described in docs/PROTOCOL.md).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from . import __version__
from .camera import apply_orientation, open_camera
from .config import DEFAULT_CONFIG, Config
from .geometry import PinholeModel, WorldCalibration, WorldTransform
from .keysender import KeySender
from .network import CommandReceiver, PreviewSender, StateSender
from .pads import HitDetector, Pad, taiko_layout
from .psmove_hid import MoveManager
from .vision import Detection, SphereDetector, draw_detections, sample_colour


class TrackedController:
    """Per-controller runtime state (smoothed position, visibility, ...)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.id = int(cfg["id"])
        self.visible = False
        self.pixel = (0.0, 0.0, 0.0)
        self.camera_pos = np.zeros(3)
        self.world_pos = np.zeros(3)          # smoothed, for display and pad placement
        self.raw_world_pos = np.zeros(3)      # unsmoothed, for hit timing (no lag)
        self.smoothed: np.ndarray | None = None
        self.last_seen = 0.0

    def update(self, det: Detection, camera_pos: np.ndarray | None, world_pos: np.ndarray | None, now: float) -> None:
        self.visible = det.found
        if not det.found:
            return
        self.pixel = (det.x, det.y, det.radius)
        self.camera_pos = camera_pos
        self.raw_world_pos = world_pos
        alpha = 1.0 - float(self.cfg.get("smoothing", 0.0))
        if self.smoothed is None or now - self.last_seen > 0.25:
            self.smoothed = world_pos.copy()
        else:
            self.smoothed = self.smoothed + (world_pos - self.smoothed) * max(0.05, alpha)
        self.world_pos = self.smoothed
        self.last_seen = now


class Tracker:
    def __init__(self, config: Config):
        self.config = config
        self.running = True
        self.camera = None
        self.frame_size = (int(config["camera"]["width"]), int(config["camera"]["height"]))
        self.detector = SphereDetector(config)
        self.model = PinholeModel(config["optics"], *self.frame_size)
        self.world = WorldTransform(config["world"])
        self.world_calibration = WorldCalibration()
        self.controllers = {int(c["id"]): TrackedController(c) for c in config["controllers"]}
        self.hits = HitDetector([Pad.from_config(p) for p in config["pads"]], config["hits"])
        self.moves = MoveManager(config)
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
        self.open_camera()

    # ------------------------------------------------------------------
    def open_camera(self) -> None:
        if self.camera is not None:
            self.camera.close()
        self.camera = open_camera(self.config)
        self.frame_size = (int(self.config["camera"]["width"]), int(self.config["camera"]["height"]))
        self.model = PinholeModel(self.config["optics"], *self.frame_size)
        self.detector.invalidate()
        print(f"[camera] opened '{self.camera.name}' backend")

    def apply_config_changes(self, patch: dict) -> None:
        """Push a partial config into the live objects."""
        self.config.apply_patch(patch)
        if "camera" in patch:
            try:
                self.open_camera()
            except Exception as exc:
                print(f"[camera] reopen failed: {exc}")
        if "processing" in patch:
            self.detector.invalidate()
        if "optics" in patch:
            self.model = PinholeModel(self.config["optics"], *self.frame_size)
        if "world" in patch:
            self.world = WorldTransform(self.config["world"])
        if "pads" in patch or "hits" in patch:
            self.reload_pads()
        if "controllers" in patch:
            self.controllers = {int(c["id"]): TrackedController(c) for c in self.config["controllers"]}
        if "osu" in patch:
            self.keys.hold_s = float(self.config["osu"]["key_hold_ms"]) / 1000.0

    def reload_pads(self) -> None:
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
        detections = self.detector.detect(frame)
        self.last_detections = detections
        new_hits = []
        for det in detections:
            tracked = self.controllers.get(det.controller_id)
            if tracked is None:
                continue
            if det.found:
                cam_pos = self.model.pixel_to_camera(det.x, det.y, det.radius)
                world_pos = self.world.to_world(cam_pos)
                tracked.update(det, cam_pos, world_pos, timestamp)
                new_hits.extend(self.hits.update(det.controller_id, tracked.raw_world_pos, timestamp))
            else:
                tracked.update(det, None, None, timestamp)
        for hit in new_hits:
            self.on_hit(hit)
        return self.build_state(timestamp, new_hits)

    def on_hit(self, hit) -> None:
        if self.config["osu"]["enabled"]:
            key = self.config["osu"]["keys"].get(hit.pad_id, "")
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
                "hid": move is not None and move.connected,
            }
            if move is not None:
                entry["buttons"] = move.buttons
                entry["trigger"] = move.trigger
                entry["battery"] = move.battery
                entry["accel"] = [round(a, 3) for a in move.accel_g]
            controllers.append(entry)
        return {
            "type": "state",
            "t": round(timestamp, 4),
            "fps": round(self.fps, 1),
            "frame": list(self.frame_size),
            "controllers": controllers,
            "hits": [self.hit_to_dict(h) for h in new_hits],
            "world_calibrated": bool(self.config["world"].get("calibrated", False)),
            "world_points": sorted(self.world_calibration.points),
            "osu": bool(self.config["osu"]["enabled"]),
            "hid_available": self.moves.available,
        }

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
        self.config = Config({}, self.config.path)
        self.detector.config = self.config
        self.moves.config = self.config
        self.apply_config_changes({"camera": {}, "processing": {}, "optics": {}, "world": {}, "pads": [], "controllers": []})
        self.apply_config_changes({"pads": DEFAULT_CONFIG["pads"]})
        return {"config": self.config.data}

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

    def cmd_set_led(self, msg):
        controller = self._controller_cfg(int(msg.get("controller", 0)))
        controller["led"] = [int(v) for v in msg["rgb"]]
        return {"led": controller["led"]}

    def cmd_list_controllers(self, msg):
        return {"devices": self.moves.list_devices(),
                "connected": {slot: c.serial for slot, c in self.moves.controllers.items()}}

    # -- calibration ---------------------------------------------------
    def cmd_calibrate_focal(self, msg):
        """Hold a controller at a known distance from the lens and call this."""
        tracked = self._tracked(int(msg.get("controller", 0)))
        distance = float(msg["distance_m"])
        focal = self.model.focal_from_known_distance(tracked.pixel[2], distance)
        self.config["optics"]["focal_px"] = round(focal, 2)
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
        """Reset the pads to the standard taiko layout, centred on a point or controller."""
        if "center" in msg:
            center = np.array(msg["center"], dtype=float)
        elif "controller" in msg:
            center = self._tracked(int(msg["controller"])).world_pos
        else:
            center = np.zeros(3)
        self.config["pads"] = taiko_layout(center, float(msg.get("face_spacing", 0.10)),
                                           float(msg.get("rim_spacing", 0.30)), float(msg.get("radius", 0.11)))
        self.reload_pads()
        return {"pads": self.config["pads"]}

    def cmd_get_recent_hits(self, msg):
        return {"hits": self.recent_hits}

    # -- osu -----------------------------------------------------------
    def cmd_set_osu(self, msg):
        self.config["osu"]["enabled"] = bool(msg.get("enabled", True))
        if "keys" in msg:
            self.config["osu"]["keys"].update(msg["keys"])
        return {"osu": self.config["osu"]}

    # -- simulation ----------------------------------------------------
    def cmd_sim_goto(self, msg):
        self._sim().goto(int(msg.get("controller", 0)), msg["position"])

    def cmd_sim_hit(self, msg):
        sim = self._sim()
        at = float(msg.get("at", time.time() + 0.3))
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
        ok, frame, timestamp = self.camera.read()
        if not ok or frame is None:
            time.sleep(0.01)
            return
        state = self.process_frame(frame, timestamp)
        self.state_out.send(state)
        self.send_preview()
        self.tick_fps()
        if self.config["debug"]["show_window"] and not self.show_debug_window():
            self.running = False

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

    Tracker(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
