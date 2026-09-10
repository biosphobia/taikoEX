"""Camera backends.

Every backend exposes the same tiny interface::

    cam = open_camera(config)
    ok, frame_bgr, timestamp = cam.read()
    cam.set_control("exposure", 30)
    cam.mode()          # {"width": 320, "height": 240, "fps": 187} as the driver reports it
    cam.close()

``timestamp`` is ``time.time()`` at the moment the frame was grabbed, so hit
times can be compared with the game's clock.

Frame rate matters more than resolution for drumming: a stroke lasts about a
tenth of a second, and every extra frame in it is another point on the curve
whose bottom is the hit.  ``probe_modes`` below tries the modes listed under
``camera.fast_modes`` and measures what each one *really* delivers, because
a driver will happily accept a request for 187 fps and then hand over 30.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover - cv2 is a hard requirement at runtime
    raise SystemExit("OpenCV is required: pip install opencv-python") from exc

# Frame rates the PS3 Eye sensor can actually run at.  Ask for anything else
# and the driver rounds to one of these.
PSEYE_RATES = {
    (640, 480): (2, 3, 5, 8, 10, 15, 20, 25, 30, 40, 50, 60, 75),
    (320, 240): (2, 3, 5, 7, 10, 12, 15, 17, 30, 37, 40, 50, 60, 75, 90, 100, 125, 137, 150, 187),
}


def nearest_pseye_rate(width: int, height: int, fps: int) -> int:
    """The PS3 Eye frame rate closest to what was asked for."""
    rates = PSEYE_RATES.get((int(width), int(height)))
    if not rates:
        return int(fps)
    return min(rates, key=lambda rate: abs(rate - fps))


class BaseCamera:
    """Interface shared by all backends."""

    name = "base"

    def __init__(self, width: int = 0, height: int = 0, fps: int = 0):
        self.requested = {"width": int(width), "height": int(height), "fps": int(fps)}

    def read(self) -> tuple[bool, np.ndarray | None, float]:
        raise NotImplementedError

    def set_control(self, control: str, value: Any) -> bool:
        """Change a camera setting live.  Returns True when the backend accepted it."""
        return False

    def get_controls(self) -> dict[str, Any]:
        """Current values of the controls this backend knows about."""
        return {}

    def mode(self) -> dict[str, Any]:
        """Resolution and frame rate as the driver reports them (what was
        requested, for backends that cannot say)."""
        return dict(self.requested)

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

    def __init__(self, index: int, width: int, height: int, fps: int, controls: dict[str, Any],
                 fourcc: str = ""):
        super().__init__(width, height, fps)
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
        # The pixel format has to go first: many webcams only offer their
        # fast modes compressed, and change the mode list when it is set.
        if fourcc and len(fourcc) == 4:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
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

    def mode(self) -> dict[str, Any]:
        # What the driver settled on, which is not always what was asked for.
        return {"width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.requested["width"]),
                "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.requested["height"]),
                "fps": round(float(self.cap.get(cv2.CAP_PROP_FPS) or self.requested["fps"]), 1)}

    def close(self) -> None:
        self.cap.release()


# --------------------------------------------------------------------------
# pseyepy backend: talks to the PS3 Eye directly through libusb.  Gives full
# control over exposure / gain and up to 187 fps at 320x240.
# --------------------------------------------------------------------------
class PSEyeCamera(BaseCamera):
    name = "pseye"

    def __init__(self, index: int, width: int, height: int, fps: int, controls: dict[str, Any]):
        super().__init__(width, height, fps)
        try:
            from pseyepy import Camera  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pseyepy is not installed (pip install pseyepy)") from exc
        small = width <= 320
        self.requested.update(width=320 if small else 640, height=240 if small else 480)
        self.rate = nearest_pseye_rate(self.requested["width"], self.requested["height"], fps)
        self.cam = Camera(
            index,
            fps=self.rate,
            resolution=Camera.RES_SMALL if small else Camera.RES_LARGE,
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

    def mode(self) -> dict[str, Any]:
        return {"width": self.requested["width"], "height": self.requested["height"], "fps": self.rate}

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
        super().__init__(int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), fps)
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
        return OpenCVCamera(int(cam_cfg["index"]), width, height, fps, controls, str(cam_cfg.get("fourcc", "")))
    if backend == "pseye":
        return PSEyeCamera(int(cam_cfg["index"]), width, height, fps, controls)
    if backend == "video":
        return VideoFileCamera(cam_cfg["video_path"], fps)
    if backend == "simulated":
        from .simulation import SimulatedCamera

        return SimulatedCamera(config)
    raise RuntimeError(f"Unknown camera backend '{backend}'")


# --------------------------------------------------------------------------
# Finding the fastest mode a camera really delivers.
# --------------------------------------------------------------------------
def measure_fps(camera: BaseCamera, seconds: float = 0.5, warm_up: int = 3) -> float:
    """Read frames for ``seconds`` and return how many per second arrived.

    The first few frames are skipped: drivers take a moment to switch modes
    and often hand back a stale frame or two immediately.
    """
    for _ in range(warm_up):
        camera.read()
    frames = 0
    start = time.perf_counter()
    deadline = start + seconds
    while True:
        ok, _, _ = camera.read()
        now = time.perf_counter()
        if ok:
            frames += 1
        if now >= deadline:
            break
    elapsed = max(1e-6, now - start)
    return frames / elapsed


def probe_modes(config, modes: list | None = None, seconds: float = 0.5,
                stop_when_delivered: bool = False,
                between: Callable[[], None] | None = None) -> list[dict[str, Any]]:
    """Open the camera in each mode of ``camera.fast_modes`` and measure it.

    Returns one entry per mode: what was asked for, what the driver claims,
    what was measured, and whether that counts as delivered
    (``camera.fast_mode_min_ratio`` of the request).  With
    ``stop_when_delivered`` the search ends at the first mode that delivers -
    the list is ordered fastest first, so nothing after it can beat it.
    ``between`` is called after each mode (the tracker uses it to keep the
    controllers' LEDs refreshed while the camera is busy).
    """
    cam_cfg = config["camera"]
    modes = modes if modes is not None else cam_cfg.get("fast_modes", [])
    min_ratio = float(cam_cfg.get("fast_mode_min_ratio", 0.9))
    results = []
    for width, height, fps in modes:
        trial = copy.deepcopy(config)
        trial["camera"].update(width=int(width), height=int(height), fps=int(fps))
        simulation = trial.get("simulation")
        if isinstance(simulation, dict):
            simulation["virtual_clock"] = False     # measure real time, even in tests
        entry: dict[str, Any] = {"width": int(width), "height": int(height), "fps_requested": int(fps)}
        try:
            camera = open_camera(trial)
        except Exception as exc:
            entry.update(error=str(exc), fps_reported=0.0, fps_measured=0.0, delivered=False)
            results.append(entry)
            continue
        try:
            reported = camera.mode()
            measured = measure_fps(camera, seconds)
        finally:
            camera.close()
        entry.update(fps_reported=float(reported.get("fps", 0.0)), fps_measured=round(measured, 1),
                     delivered=measured >= min_ratio * fps)
        results.append(entry)
        if between is not None:
            between()
        if stop_when_delivered and entry["delivered"]:
            break
    return results


def choose_fastest(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The probed mode with the highest measured frame rate.

    Ties go to the larger frame, and a mode that failed to open never wins.
    """
    usable = [r for r in results if not r.get("error") and r.get("fps_measured", 0) > 0]
    if not usable:
        return None
    return max(usable, key=lambda r: (round(r["fps_measured"]), r["width"] * r["height"]))
