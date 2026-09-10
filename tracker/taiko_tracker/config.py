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
        # Resolution and frame rate to ask the driver for.  The PS3 Eye does
        # up to 75 fps at 640x480 and up to 187 fps at 320x240; press
        # "Fastest mode" in the calibration screen (the camera_fastest
        # command) to try the modes in `fast_modes` and keep the quickest one
        # the driver really delivers.
        "width": 640,
        "height": 480,
        "fps": 60,
        # Pixel format to request from an OpenCV driver, e.g. "MJPG" - many
        # ordinary webcams only reach their top frame rate compressed.  Leave
        # empty for the PS3 Eye, which streams raw frames.
        "fourcc": "",
        # Modes tried by "Fastest mode", quickest first: [width, height, fps].
        "fast_modes": [[320, 240, 187], [320, 240, 150], [320, 240, 125], [320, 240, 100],
                       [320, 240, 75], [640, 480, 75], [640, 480, 60], [640, 480, 30]],
        # A mode counts as delivered when it measures at least this share of
        # the requested rate.
        "fast_mode_min_ratio": 0.9,
        "retry_s": 3.0,           # how often to retry a camera that would not open
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
        # Every pixel measurement in this section, and every "_px" setting
        # under "processing", is given for a frame `reference_width` pixels
        # wide and scaled to whatever resolution the camera is running at.
        # So a calibration done at 640x480 still holds after switching to
        # 320x240 for speed, and the crop, the mask and the size limits move
        # with it.
        "reference_width": 640,
        # Focal length in pixels at the reference width.  PS3 Eye:
        # ~545 px at 640x480 with the narrow (red dot) lens setting,
        # ~420 px with the wide (blue dot) setting.  Calibrate to be exact.
        "focal_px": 545.0,
        "cx": -1,                          # principal point, -1 = image centre
        "cy": -1,
        "sphere_radius_m": 0.0225,         # PS Move sphere is 45 mm across
        "k1": 0.0,                         # radial distortion (optional)
        # A glowing sphere measures about a pixel bigger than it is (blended
        # edge pixels).  The two-point distance calibration measures this.
        # It is a per-pixel effect, so unlike the focal length it is the same
        # number of pixels at every resolution and is not scaled.
        "radius_offset_px": 0.0,
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
        # kind: "don" (face) or "ka" (rim).  side: "left", "right" or "any"
        # ("any" means the hand that hit it decides, which is how a real drum
        # works).  A pad is a flat disc, or a ring when inner_radius > 0.
        {"id": "don", "name": "Drum face", "kind": "don", "side": "any",
         "center": [0.0, 0.0, 0.0], "normal": [0, 1, 0], "radius": 0.22, "inner_radius": 0.0},
        {"id": "ka", "name": "Drum rim", "kind": "ka", "side": "any",
         "center": [0.0, 0.0, 0.0], "normal": [0, 1, 0], "radius": 0.45, "inner_radius": 0.22},
    ],
    "fusion": {
        # Smoothing that knows a camera measures direction well and distance
        # badly (see taiko_tracker/tracking_filter.py).
        "enabled": True,
        "pixel_noise_px": 0.35,           # how much the blob centre jitters
        "radius_noise_px": 0.45,          # how much the blob radius jitters
        "process_accel_mps2": 40.0,       # how hard a hand can accelerate (about 4 g)
        "process_accel_with_imu_mps2": 12.0,   # lower, because the IMU supplies the acceleration
        "use_imu_accel": True,            # feed controller acceleration into the prediction
        "reset_after_s": 0.3,             # start fresh if tracking was lost this long
        "max_speed_mps": 6.0,             # fastest a hand can plausibly move
        "unmeasured_depth_m": 5.0,        # depth uncertainty when the size cannot be trusted
        "untrusted_frames_max": 20,       # stop doubting the size after this many frames
        "completeness_full": 0.8,         # a blob at least this whole is trusted for distance
        "occluded_noise_max": 8.0,        # how much a hidden sphere's distance noise may grow
        "outlier_sigma": 6.0,             # a measurement this far outside the prediction is suspect
        "outlier_frames": 3,              # after this many suspect frames in a row, restart there
    },
    "hits": {
        # "stroke": a hit is the bottom of the stroke, where the hand turns
        #           around.  Robust, because it does not depend on the camera's
        #           distance estimate.  This is the default.
        # "plane":  a hit is the sphere crossing the pad's surface.  Sharper,
        #           but needs an accurate distance, so use it with a close
        #           camera pointing straight at you.
        "mode": "stroke",
        "height_window_m": 0.25,      # how far above/below a pad still counts as "over" it
        "min_speed_mps": 0.5,         # how fast the sphere must move towards the pad
        "min_descent_m": 0.06,        # and how far it must come down to count as a stroke
        "rearm_height_m": 0.04,       # rise this far above the pad before it can hit again
        "cooldown_s": 0.05,           # per-controller minimum time between hits
        "latency_compensation_ms": 25.0,      # camera + processing delay, subtracted from camera-timed hits
        "latency_compensation_imu_ms": 15.0,  # the same for accelerometer-timed hits, which barely lag
        "max_frame_gap_s": 0.25,      # if tracking is lost longer than this, forget velocity
        # When the controller's accelerometer is available, take the *timing*
        # of a hit from it: it feels the stroke stop even when the camera is
        # looking straight down the line the hand travels.  The camera still
        # decides which pad was hit.
        "use_accelerometer": True,
        "accel_threshold_g": 2.0,     # deceleration that counts as a strike
        "hard_hit_g": 6.0,            # deceleration that counts as a full-strength strike
        "swing_window_s": 0.18,       # how far back to look for the swing towards the pad
        "swing_threshold_g": 1.0,     # how hard the hand must have driven at the pad first
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
        # HID report that sets the sphere colour.  6 is what psmoveapi uses
        # and works for both controller models; older documentation says 2.
        "led_report_id": 6,
    },
    "imu": {
        # Orientation from the controller's accelerometer + gyroscope.
        "enabled": True,
        "accel_gain": 1.0,            # how hard gravity pulls the estimate straight (0 = gyro only)
        "auto_bias": True,            # learn the gyro's zero offset while the controller rests
        "bias_samples": 120,
        "still_gyro_rad_s": 0.15,
        "handle_axis": [0, 1, 0],     # sensor-frame direction from the handle towards the sphere
        "gyro_rad_per_unit": 0.001065,  # raw gyro counts -> rad/s (about 61 counts per deg/s)
        "recenter_with_move_button": True,   # pressing MOVE re-points the heading at the camera
    },
    "background": {
        # Automatic mask learning: the tracker turns the spheres off for a
        # moment, looks at what still matches each controller's colour and
        # masks those areas away (lamps, TVs, posters, sunlit walls).
        "learn_seconds": 0.5,         # how long to watch with the LEDs off (a flickering screen needs a moment)
        "dilate_px": 6,               # grow each masked area by this much
        "min_area_px": 12,            # ignore specks smaller than this
        "auto_relearn_s": 0.0,        # >0: relearn automatically every N seconds
        "mask": [],                   # run-length encoded learned mask (written by the tracker)
    },
    "simulation": {
        # Only used by the "simulated" camera backend.
        "scene": "clean",             # clean, living_room, far_shelf, floor_low, sunny
        "camera_position": [],        # override the scene's camera pose if you want
        "camera_target": [],
        # The virtual lens, in pixels at 640 wide.  Kept apart from
        # optics.focal_px on purpose: that is what the calibration *finds*,
        # this is the truth it is measured against.
        "focal_px": 545.0,
        "imu_noise": 0.01,
        # Step time by one frame per read instead of following the wall clock.
        # Used by the tests, so that a busy machine cannot change the result.
        "virtual_clock": False,
    },
    "debug": {
        "show_window": False,         # OpenCV window with the processed image
        "print_hits": True,
    },
}

# Sphere colours that track well, with the HSV range that catches each one
# (OpenCV hue runs 0..179).  The calibration screen offers these as presets;
# "Sample colour" then fine-tunes the range for your camera and lighting.
# Two hands need two colours that are far apart in hue: magenta + cyan is the
# safest pair, green + magenta and blue + yellow also work.  White is only
# usable in a dark room, because every bright thing matches it.
LED_PRESETS: dict[str, dict[str, Any]] = {
    "magenta": {"led": [255, 0, 255], "hsv_min": [135, 80, 100], "hsv_max": [175, 255, 255]},
    "cyan":    {"led": [0, 255, 255], "hsv_min": [80, 80, 100], "hsv_max": [110, 255, 255]},
    "green":   {"led": [0, 255, 0], "hsv_min": [45, 80, 100], "hsv_max": [80, 255, 255]},
    "blue":    {"led": [0, 60, 255], "hsv_min": [105, 80, 100], "hsv_max": [130, 255, 255]},
    "yellow":  {"led": [255, 255, 0], "hsv_min": [18, 80, 100], "hsv_max": [40, 255, 255]},
    "orange":  {"led": [255, 100, 0], "hsv_min": [5, 100, 100], "hsv_max": [20, 255, 255]},
    "red":     {"led": [255, 0, 0], "hsv_min": [168, 100, 100], "hsv_max": [6, 255, 255]},
    "white":   {"led": [255, 255, 255], "hsv_min": [0, 0, 200], "hsv_max": [179, 60, 255]},
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
        # A config without controllers or pads cannot track anything; treat
        # an empty list as "use the defaults" rather than as a choice.
        for key in ("controllers", "pads"):
            if not self.data.get(key):
                self.data[key] = copy.deepcopy(DEFAULT_CONFIG[key])

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
