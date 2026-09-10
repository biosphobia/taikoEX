"""Settings for the tracker.

Everything lives in one JSON file (``tracker_config.json`` next to the
tracker by default).  ``DEFAULT_CONFIG`` below documents every key.  Missing
keys in the user's file are filled in from the defaults, so a config file
only needs to contain the values you actually changed.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Default settings.  Edit freely - every value here is meant to be tuned.
# --------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "camera": {
        # "opencv"    : any webcam / PS3 Eye with a UVC or CL-Eye style driver
        # "pseye"     : PS3 Eye through the pseyepy library (libusb driver)
        # "video"     : play back a video file (for testing)
        # "simulated" : synthetic camera with virtual controllers (for testing)
        "backend": "opencv",
        "index": 0,               # camera index for opencv / pseye
        "video_path": "",         # used by the "video" backend
        "width": 640,
        "height": 480,
        "fps": 60,
        "flip_horizontal": False,
        "flip_vertical": False,
        "rotate_degrees": 0,      # 0, 90, 180 or 270
        # Raw camera controls.  Values are passed straight to the backend.
        # For OpenCV these map to cv2.CAP_PROP_* properties; a value of null
        # leaves the driver default untouched.
        "controls": {
            "auto_exposure": 0,   # 0 = manual (OpenCV usually wants 0.25 / 1 on some drivers)
            "exposure": 40,
            "gain": 10,
            "brightness": None,
            "contrast": None,
            "saturation": None,
            "hue": None,
            "sharpness": None,
            "auto_white_balance": 0,
            "white_balance": None,
        },
    },
    "processing": {
        # Crop rectangle in full-frame pixels.  Width/height of 0 means "whole frame".
        "crop": {"x": 0, "y": 0, "w": 0, "h": 0},
        # Polygons (full-frame pixel coordinates) that are ignored, e.g. a
        # lamp or a TV in the background.  Each polygon is a list of [x, y].
        "mask_polygons": [],
        "blur": 3,                # gaussian blur kernel (odd number, 0 = off)
        "open_iterations": 1,     # morphology: remove speckles smaller than ~3 px
        "close_iterations": 2,    # morphology: bridge small gaps in the blob
        "radius_offset_px": 0.0,  # added to every measured radius (fine tune distance)
        "fill_holes": True,       # fill the blown-out white centre of a bright sphere
        "bright_core": True,      # also grab saturated white pixels touching the colour blob
        "bright_core_min_value": 235,
        "bright_core_max_saturation": 60,
        "bright_core_reach_px": 12,
        "min_radius_px": 3,
        "max_radius_px": 150,
        "min_circularity": 0.45,  # 1.0 = perfect circle; lower accepts partial occlusion
        "min_fill_ratio": 0.35,   # blob area / enclosing circle area (motion blur lowers it)
        "track_reach_px": 80,     # blobs this close to the last position are strongly preferred
    },
    "controllers": [
        {
            "id": 0,
            "name": "Left hand",
            "led": [255, 0, 255],          # colour to light the sphere with (magenta)
            "hsv_min": [135, 80, 100],     # OpenCV HSV: H 0..179, S 0..255, V 0..255
            "hsv_max": [175, 255, 255],
            "smoothing": 0.35,             # 0 = raw positions, 0.9 = very smooth (adds lag)
            "hid_serial": "",              # Bluetooth address to bind this slot to ("" = first free)
        },
        {
            "id": 1,
            "name": "Right hand",
            "led": [0, 255, 255],          # cyan
            "hsv_min": [80, 80, 100],
            "hsv_max": [110, 255, 255],
            "smoothing": 0.35,
            "hid_serial": "",
        },
    ],
    "optics": {
        # Focal length in pixels at the configured resolution.  PS3 Eye:
        # ~545 px at 640x480 with the narrow (red dot) lens setting,
        # ~420 px with the wide (blue dot) setting.  Calibrate to be exact.
        "focal_px": 545.0,
        "cx": -1,                          # principal point, -1 = image centre
        "cy": -1,
        "sphere_radius_m": 0.0225,         # PS Move sphere is 45 mm across
        "k1": 0.0,                         # radial distortion (optional)
    },
    "world": {
        # Camera space -> world space.  world = rotation * cam + translation.
        # Filled in by the "world calibration" step; identity-ish by default
        # (x = player's right, y = up, z = away from the camera).
        "calibrated": False,
        "rotation": [[-1, 0, 0], [0, -1, 0], [0, 0, 1]],
        "translation": [0.0, 0.0, 0.0],
    },
    "pads": [
        # Virtual drum pads.  Positions are metres in world space.
        # kind: "don" (drum face) or "ka" (rim).  side: "left" / "right".
        # A pad is a flat disc (inner_radius 0) or a ring (inner_radius > 0).
        {"id": "left_ka",   "name": "Left rim",   "kind": "ka",  "side": "left",
         "center": [-0.30, 0.0, 0.0], "normal": [0, 1, 0], "radius": 0.11, "inner_radius": 0.0},
        {"id": "left_don",  "name": "Left face",  "kind": "don", "side": "left",
         "center": [-0.10, 0.0, 0.0], "normal": [0, 1, 0], "radius": 0.11, "inner_radius": 0.0},
        {"id": "right_don", "name": "Right face", "kind": "don", "side": "right",
         "center": [0.10, 0.0, 0.0],  "normal": [0, 1, 0], "radius": 0.11, "inner_radius": 0.0},
        {"id": "right_ka",  "name": "Right rim",  "kind": "ka",  "side": "right",
         "center": [0.30, 0.0, 0.0],  "normal": [0, 1, 0], "radius": 0.11, "inner_radius": 0.0},
    ],
    "hits": {
        "min_speed_mps": 0.5,         # how fast the sphere must move through the pad
        "rearm_height_m": 0.04,       # rise this far above the pad before it can hit again
        "cooldown_s": 0.05,           # per-controller minimum time between hits
        "latency_compensation_ms": 25.0,   # camera + processing delay, subtracted from hit times
        "max_frame_gap_s": 0.25,      # if tracking is lost longer than this, forget velocity
        "use_accelerometer": False,   # confirm/time hits with the controller IMU (needs HID)
        "accel_threshold_g": 2.5,
    },
    "network": {
        "game_host": "127.0.0.1",
        "state_port": 47820,          # tracker -> game, one JSON packet per frame
        "command_port": 47821,        # game -> tracker, JSON commands
        "preview_port": 47822,        # tracker -> game, JPEG preview frames
        "preview_enabled": True,
        "preview_fps": 20,
        "preview_quality": 60,
        "preview_width": 640,
    },
    "osu": {
        # When enabled, hits are typed as keyboard keys (for osu!taiko)
        # instead of / in addition to being sent to the game.
        "enabled": False,
        "key_hold_ms": 35,
        "keys": {"left_ka": "d", "left_don": "f", "right_don": "j", "right_ka": "k"},
    },
    "hid": {
        "enabled": True,              # light the spheres and read the IMU over Bluetooth HID
        "led_brightness": 1.0,        # 0..1, lower if the camera blows out the colour
        "reconnect_interval_s": 3.0,
    },
    "debug": {
        "show_window": False,         # OpenCV window with the processed image
        "print_hits": True,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Return a copy of ``base`` with values from ``override`` applied recursively."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def default_config_path() -> Path:
    """The config file lives next to the tracker (works for source and PyInstaller builds)."""
    env = os.environ.get("TAIKO_TRACKER_CONFIG")
    if env:
        return Path(env)
    import sys

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "tracker_config.json"


class Config:
    """Thin wrapper around the settings dictionary with load/save helpers."""

    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.path = path or default_config_path()
        self.data = deep_merge(DEFAULT_CONFIG, data or {})

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                user_data = json.load(fh)
        else:
            user_data = {}
        return cls(user_data, path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
            fh.write("\n")

    # Convenience accessors -------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, dotted: str, default: Any = None) -> Any:
        """``cfg.get("camera.width")`` style lookup."""
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def apply_patch(self, patch: dict) -> None:
        """Merge a partial dictionary (e.g. received from the game) into the settings."""
        self.data = deep_merge(self.data, patch)
