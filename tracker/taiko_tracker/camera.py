"""Camera backends.

Every backend exposes the same tiny interface::

    cam = open_camera(config)
    ok, frame_bgr, timestamp = cam.read()
    cam.set_control("exposure", 30)
    cam.close()

``timestamp`` is ``time.time()`` at the moment the frame was grabbed, so hit
times can be compared with the game's clock.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover - cv2 is a hard requirement at runtime
    raise SystemExit("OpenCV is required: pip install opencv-python") from exc


class BaseCamera:
    """Interface shared by all backends."""

    name = "base"

    def read(self) -> tuple[bool, np.ndarray | None, float]:
        raise NotImplementedError

    def set_control(self, control: str, value: Any) -> bool:
        """Change a camera setting live.  Returns True when the backend accepted it."""
        return False

    def get_controls(self) -> dict[str, Any]:
        """Current values of the controls this backend knows about."""
        return {}

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------
# OpenCV backend: works with any webcam driver, including PS3 Eye drivers
# that register as a normal camera (CL-Eye, PS3 Eye universal driver, ...).
# --------------------------------------------------------------------------
OPENCV_CONTROLS = {
    "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "gain": cv2.CAP_PROP_GAIN,
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "hue": cv2.CAP_PROP_HUE,
    "sharpness": cv2.CAP_PROP_SHARPNESS,
    "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
    "white_balance": cv2.CAP_PROP_WB_TEMPERATURE,
    "fps": cv2.CAP_PROP_FPS,
}


class OpenCVCamera(BaseCamera):
    name = "opencv"

    def __init__(self, index: int, width: int, height: int, fps: int, controls: dict[str, Any]):
        # CAP_DSHOW is the backend that exposes the most controls on Windows.
        backends = [cv2.CAP_DSHOW, cv2.CAP_ANY] if hasattr(cv2, "CAP_DSHOW") else [cv2.CAP_ANY]
        self.cap = None
        for backend in backends:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                self.cap = cap
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f"Could not open camera index {index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        for control, value in controls.items():
            if value is not None:
                self.set_control(control, value)

    def read(self):
        ok, frame = self.cap.read()
        return ok, frame, time.time()

    def set_control(self, control: str, value: Any) -> bool:
        prop = OPENCV_CONTROLS.get(control)
        if prop is None or value is None:
            return False
        return bool(self.cap.set(prop, float(value)))

    def get_controls(self) -> dict[str, Any]:
        return {name: self.cap.get(prop) for name, prop in OPENCV_CONTROLS.items()}

    def close(self) -> None:
        self.cap.release()


# --------------------------------------------------------------------------
# pseyepy backend: talks to the PS3 Eye directly through libusb.  Gives full
# control over exposure / gain and up to 187 fps at 320x240.
# --------------------------------------------------------------------------
class PSEyeCamera(BaseCamera):
    name = "pseye"

    def __init__(self, index: int, width: int, height: int, fps: int, controls: dict[str, Any]):
        try:
            from pseyepy import Camera  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pseyepy is not installed (pip install pseyepy)") from exc
        resolution = Camera.RES_SMALL if width <= 320 else Camera.RES_LARGE
        self.cam = Camera(
            index,
            fps=fps,
            resolution=resolution,
            colour=True,
            gain=int(controls.get("gain") or 20),
            exposure=int(controls.get("exposure") or 50),
            auto_gain=bool(controls.get("auto_exposure")),
            auto_whitebalance=bool(controls.get("auto_white_balance")),
        )

    def read(self):
        frame, _ = self.cam.read()
        # pseyepy returns RGB, the rest of the tracker works in BGR like OpenCV.
        return True, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), time.time()

    def set_control(self, control: str, value: Any) -> bool:
        mapping = {
            "exposure": "exposure",
            "gain": "gain",
            "auto_exposure": "auto_gain",
            "auto_white_balance": "auto_whitebalance",
            "hue": "hue",
            "brightness": "brightness",
            "contrast": "contrast",
            "sharpness": "sharpness",
        }
        attr = mapping.get(control)
        if attr is None or value is None:
            return False
        try:
            setattr(self.cam, attr, value)
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.cam.end()


# --------------------------------------------------------------------------
# Video file backend: replays a recording, useful when tuning thresholds.
# --------------------------------------------------------------------------
class VideoFileCamera(BaseCamera):
    name = "video"

    def __init__(self, path: str, fps: int):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file {path}")
        self.frame_time = 1.0 / max(1, fps)
        self.next_time = time.time()

    def read(self):
        ok, frame = self.cap.read()
        if not ok:  # loop forever
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        # Pace playback to the configured fps.
        now = time.time()
        if now < self.next_time:
            time.sleep(self.next_time - now)
        self.next_time = max(self.next_time, now) + self.frame_time
        return ok, frame, time.time()

    def close(self) -> None:
        self.cap.release()


def apply_orientation(frame: np.ndarray, camera_cfg: dict) -> np.ndarray:
    """Flip / rotate a frame according to the camera settings."""
    rotate = int(camera_cfg.get("rotate_degrees", 0)) % 360
    if rotate == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rotate == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rotate == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if camera_cfg.get("flip_horizontal"):
        frame = cv2.flip(frame, 1)
    if camera_cfg.get("flip_vertical"):
        frame = cv2.flip(frame, 0)
    return frame


def open_camera(config) -> BaseCamera:
    """Create the backend named in ``config["camera"]["backend"]``."""
    cam_cfg = config["camera"]
    backend = cam_cfg.get("backend", "opencv")
    width, height, fps = int(cam_cfg["width"]), int(cam_cfg["height"]), int(cam_cfg["fps"])
    controls = cam_cfg.get("controls", {})
    if backend == "opencv":
        return OpenCVCamera(int(cam_cfg["index"]), width, height, fps, controls)
    if backend == "pseye":
        return PSEyeCamera(int(cam_cfg["index"]), width, height, fps, controls)
    if backend == "video":
        return VideoFileCamera(cam_cfg["video_path"], fps)
    if backend == "simulated":
        from .simulation import SimulatedCamera

        return SimulatedCamera(config)
    raise RuntimeError(f"Unknown camera backend '{backend}'")
