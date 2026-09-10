"""Learn which parts of the room look like a controller, and mask them away.

Real rooms are full of things that match a glowing sphere: a lamp, a TV, a
magenta poster, sunlight on a wall.  Rather than making you draw polygons
around each one, the tracker can find them itself:

1. turn the controller LEDs off (over Bluetooth HID)
2. collect a few frames and threshold them with the *same* colour ranges the
   tracker uses for the spheres
3. anything that still matches must be part of the room - remember it
4. turn the LEDs back on

The learned mask is stored in the config as run-length encoded rows so it
survives a restart and can be inspected or edited by hand.
"""

from __future__ import annotations

import cv2
import numpy as np

from .vision import clean_mask, hsv_mask


def encode_mask(mask: np.ndarray) -> dict:
    """Run-length encode a binary mask: {"size": [w, h], "runs": [start, length, ...]}."""
    flat = (mask.reshape(-1) > 0).astype(np.uint8)
    if not flat.any():
        return {"size": [int(mask.shape[1]), int(mask.shape[0])], "runs": []}
    changes = np.flatnonzero(np.diff(np.concatenate([[0], flat, [0]])))
    starts, ends = changes[0::2], changes[1::2]
    runs: list[int] = []
    for start, end in zip(starts, ends):
        runs.extend((int(start), int(end - start)))
    return {"size": [int(mask.shape[1]), int(mask.shape[0])], "runs": runs}


def decode_mask(encoded: dict | list, shape: tuple[int, int]) -> np.ndarray | None:
    """Rebuild a mask, rescaling if the camera resolution changed since it was learned."""
    if not encoded or not isinstance(encoded, dict) or not encoded.get("runs"):
        return None
    width, height = (int(v) for v in encoded.get("size", [shape[1], shape[0]]))
    flat = np.zeros(width * height, dtype=np.uint8)
    runs = encoded["runs"]
    for index in range(0, len(runs) - 1, 2):
        start, length = int(runs[index]), int(runs[index + 1])
        flat[start:start + length] = 255
    mask = flat.reshape((height, width))
    if (height, width) != shape:
        mask = cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return mask


class BackgroundLearner:
    """Collects frames with the LEDs off and builds the "this is the room" mask."""

    def __init__(self, config):
        self.config = config
        self.active = False
        self.frames_left = 0
        self.accumulated: np.ndarray | None = None
        self.on_finished = None      # callable(dict) - set by the tracker

    def start(self, frames: int | None = None, fps: float = 60.0) -> int:
        """Begin learning.  Watches for ``background.learn_seconds`` of camera
        time (or an explicit number of ``frames``) so that a faster camera
        does not simply finish sooner and miss the slow flicker of a screen."""
        cfg = self.config["background"]
        seconds = float(cfg.get("learn_seconds", 0.5))
        self.frames_left = int(frames or max(5, round(seconds * max(1.0, fps))))
        self.accumulated = None
        self.active = True
        return self.frames_left

    def feed(self, frame_bgr: np.ndarray) -> bool:
        """Add one LED-off frame.  Returns True once the mask is finished."""
        if not self.active:
            return False
        from .geometry import pixel_scale
        from .vision import scale_processing

        scale = pixel_scale(self.config["optics"], frame_bgr.shape[1])
        processing = scale_processing(self.config["processing"], scale)
        blur = int(processing.get("blur", 0))
        if blur > 0:
            k = blur if blur % 2 == 1 else blur + 1
            frame_bgr = cv2.GaussianBlur(frame_bgr, (k, k), 0)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for controller in self.config["controllers"]:
            combined = cv2.bitwise_or(combined, hsv_mask(hsv, controller["hsv_min"], controller["hsv_max"]))
        # Also catch anything bright enough to be mistaken for a blown-out sphere.
        if processing.get("bright_core", True):
            combined = cv2.bitwise_or(combined, cv2.inRange(
                hsv,
                np.array([0, 0, int(processing.get("bright_core_min_value", 235))], dtype=np.uint8),
                np.array([179, int(processing.get("bright_core_max_saturation", 60)), 255], dtype=np.uint8)))
        self.accumulated = combined if self.accumulated is None else cv2.bitwise_or(self.accumulated, combined)
        self.frames_left -= 1
        if self.frames_left > 0:
            return False
        self.active = False
        self._finish(scale)
        return True

    def _finish(self, scale: float = 1.0) -> None:
        """Build the mask.  ``scale`` converts the reference-width sizes in the
        config to this frame's pixels."""
        cfg = self.config["background"]
        mask = self.accumulated if self.accumulated is not None else None
        if mask is None:
            return
        mask = clean_mask(mask, {"open_iterations": 1, "close_iterations": 1, "fill_holes": True})
        # Drop specks; they are sensor noise, not a lamp.
        min_area = float(cfg.get("min_area_px", 12)) * scale * scale
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cleaned = np.zeros_like(mask)
        regions = 0
        for contour in contours:
            if cv2.contourArea(contour) >= min_area:
                cv2.drawContours(cleaned, [contour], -1, 255, cv2.FILLED)
                regions += 1
        grow = int(round(float(cfg.get("dilate_px", 6)) * scale))
        if grow > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
            cleaned = cv2.dilate(cleaned, kernel)
        encoded = encode_mask(cleaned)
        self.config["background"]["mask"] = encoded
        covered = float((cleaned > 0).sum()) / cleaned.size * 100.0
        result = {"regions": regions, "covered_percent": round(covered, 2), "size": encoded["size"]}
        if self.on_finished is not None:
            self.on_finished(result)
