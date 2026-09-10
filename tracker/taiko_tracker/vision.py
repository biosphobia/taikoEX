"""Find the glowing PS Move spheres in a camera frame.

Pipeline for every controller colour:

1. crop + mask the frame (settings under ``processing``)
2. threshold in HSV between ``hsv_min`` and ``hsv_max``
3. optionally add the blown-out white core of a bright sphere
4. clean up with erode / dilate and fill holes
5. pick the most circular blob and fit the minimum enclosing circle

The result per controller is a ``Detection`` with the circle centre and
radius in *full frame* pixel coordinates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    controller_id: int
    found: bool
    x: float = 0.0          # pixel x in the full (uncropped) frame
    y: float = 0.0
    radius: float = 0.0     # pixel radius of the fitted circle
    area: float = 0.0       # blob area in pixels
    circularity: float = 0.0


def hsv_mask(hsv: np.ndarray, hsv_min, hsv_max) -> np.ndarray:
    """Threshold an HSV image.  Handles hue wrap-around (e.g. red: min 170, max 10)."""
    lo = np.array(hsv_min, dtype=np.uint8)
    hi = np.array(hsv_max, dtype=np.uint8)
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, lo, hi)
    # Hue wraps past 179 -> combine two ranges.
    mask_a = cv2.inRange(hsv, lo, np.array([179, hi[1], hi[2]], dtype=np.uint8))
    mask_b = cv2.inRange(hsv, np.array([0, lo[1], lo[2]], dtype=np.uint8), hi)
    return cv2.bitwise_or(mask_a, mask_b)


def build_region_mask(shape: tuple[int, int], processing: dict) -> np.ndarray | None:
    """Mask of pixels that may contain a controller (crop rectangle minus mask polygons).

    Returns ``None`` when nothing is cropped or masked, so the hot path can
    skip the extra bitwise operation.
    """
    height, width = shape
    crop = processing.get("crop", {})
    polygons = processing.get("mask_polygons", [])
    cw, ch = int(crop.get("w", 0)), int(crop.get("h", 0))
    if cw <= 0 and ch <= 0 and not polygons:
        return None
    mask = np.zeros((height, width), dtype=np.uint8)
    x0 = max(0, int(crop.get("x", 0)))
    y0 = max(0, int(crop.get("y", 0)))
    x1 = min(width, x0 + cw) if cw > 0 else width
    y1 = min(height, y0 + ch) if ch > 0 else height
    mask[y0:y1, x0:x1] = 255
    for polygon in polygons:
        if len(polygon) >= 3:
            pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [pts], 0)
    return mask


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill enclosed holes (the white centre of an over-exposed sphere)."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return mask
    filled = mask.copy()
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    return filled


def add_bright_core(mask: np.ndarray, hsv: np.ndarray, processing: dict) -> np.ndarray:
    """Add saturated-white pixels that sit right next to the coloured blob.

    A PS Move sphere at full brightness is white in the middle and only shows
    its colour around the edge.  Plain HSV thresholding then finds a ring, and
    the fitted circle jitters.  Here we take the white pixels that are within
    ``bright_core_reach_px`` of the colour mask and merge them in.
    """
    reach = int(processing.get("bright_core_reach_px", 12))
    if reach <= 0:
        return mask
    white = cv2.inRange(
        hsv,
        np.array([0, 0, int(processing.get("bright_core_min_value", 235))], dtype=np.uint8),
        np.array([179, int(processing.get("bright_core_max_saturation", 60)), 255], dtype=np.uint8),
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))
    near_colour = cv2.dilate(mask, kernel)
    core = cv2.bitwise_and(white, near_colour)
    return cv2.bitwise_or(mask, core)


def clean_mask(mask: np.ndarray, processing: dict) -> np.ndarray:
    """Remove speckles (opening) and bridge small gaps (closing).

    Both operations are symmetric so the blob keeps its size - growing the
    blob by even one pixel would throw the distance estimate off noticeably
    for a sphere that is only a handful of pixels across.
    """
    open_iterations = int(processing.get("open_iterations", 1))
    close_iterations = int(processing.get("close_iterations", 2))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    if open_iterations > 0:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=open_iterations)
    if close_iterations > 0:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=close_iterations)
    if processing.get("fill_holes", True):
        mask = fill_holes(mask)
    return mask


def fit_sphere_outline(contour, area: float) -> tuple[float, float, float, float]:
    """Return (x, y, radius, enclosing_radius) for a blob.

    The radius comes from the minor axis of a fitted ellipse: motion blur
    stretches the blob along the direction of movement, but its width stays
    the true sphere diameter.  Small blobs fall back to the enclosing circle.
    """
    (ex, ey), enclosing_radius = cv2.minEnclosingCircle(contour)
    if len(contour) >= 5:
        (cx, cy), (axis_a, axis_b), _ = cv2.fitEllipse(contour)
        radius = min(axis_a, axis_b) / 2.0
        if 0 < radius <= enclosing_radius * 1.05:
            return float(cx), float(cy), float(radius), float(enclosing_radius)
    return float(ex), float(ey), float(enclosing_radius), float(enclosing_radius)


def best_circle(mask: np.ndarray, processing: dict,
                expected: tuple[float, float, float] | None = None) -> tuple[float, float, float, float, float] | None:
    """Pick the blob that looks most like a sphere.  Returns (x, y, r, area, circularity).

    ``expected`` is the (x, y, r) of the previous detection; blobs close to it
    get a large bonus so a bigger distractor elsewhere cannot steal the track.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_r = float(processing.get("min_radius_px", 3))
    max_r = float(processing.get("max_radius_px", 150))
    min_circ = float(processing.get("min_circularity", 0.45))
    min_fill = float(processing.get("min_fill_ratio", 0.35))
    track_reach = float(processing.get("track_reach_px", 80))
    radius_offset = float(processing.get("radius_offset_px", 0.0))
    best = None
    best_score = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < math.pi * min_r * min_r:
            continue
        cx, cy, radius, enclosing_radius = fit_sphere_outline(contour, area)
        radius += radius_offset
        if radius < min_r or radius > max_r:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else 0.0
        fill_ratio = area / (math.pi * enclosing_radius * enclosing_radius)
        if circularity < min_circ or fill_ratio < min_fill:
            continue
        # Round, well filled blobs win; a square poster (circularity 0.78,
        # fill 0.64) needs several times the area of a sphere to outrank it.
        score = area * circularity * fill_ratio * fill_ratio
        if expected is not None:
            distance = math.hypot(cx - expected[0], cy - expected[1])
            if distance < track_reach + 2.0 * expected[2]:
                score *= 4.0
        if score > best_score:
            best_score = score
            best = (float(cx), float(cy), float(radius), float(area), float(circularity))
    return best


class SphereDetector:
    """Runs the pipeline above for every configured controller."""

    def __init__(self, config):
        self.config = config
        self._region_mask = None
        self._region_shape = None
        self.last_masks: dict[int, np.ndarray] = {}   # for the debug window / preview
        self.last_seen: dict[int, tuple[float, float, float]] = {}   # controller id -> (x, y, r)

    def region_mask_for(self, shape: tuple[int, int]) -> np.ndarray | None:
        if self._region_shape != shape:
            self._region_mask = build_region_mask(shape, self.config["processing"])
            self._region_shape = shape
        return self._region_mask

    def invalidate(self) -> None:
        """Call after the crop / mask settings changed."""
        self._region_shape = None

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        processing = self.config["processing"]
        blur = int(processing.get("blur", 0))
        if blur > 0:
            k = blur if blur % 2 == 1 else blur + 1
            frame_bgr = cv2.GaussianBlur(frame_bgr, (k, k), 0)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        region = self.region_mask_for(hsv.shape[:2])

        detections = []
        for controller in self.config["controllers"]:
            cid = int(controller["id"])
            mask = hsv_mask(hsv, controller["hsv_min"], controller["hsv_max"])
            if region is not None:
                mask = cv2.bitwise_and(mask, region)
            if processing.get("bright_core", True):
                mask = add_bright_core(mask, hsv, processing)
                if region is not None:
                    mask = cv2.bitwise_and(mask, region)
            mask = clean_mask(mask, processing)
            self.last_masks[cid] = mask
            circle = best_circle(mask, processing, self.last_seen.get(cid))
            if circle is None:
                self.last_seen.pop(cid, None)
                detections.append(Detection(cid, False))
            else:
                x, y, r, area, circ = circle
                self.last_seen[cid] = (x, y, r)
                detections.append(Detection(cid, True, x, y, r, area, circ))
        return detections


def sample_colour(frame_bgr: np.ndarray, x: int, y: int, size: int = 12,
                  hue_margin: int = 12, sat_margin: int = 60, val_margin: int = 80) -> tuple[list[int], list[int]]:
    """Look at a small square around (x, y) and return an HSV range that covers it.

    Used by the "auto colour" calibration button: hold the sphere in the
    box, click, done.  Only reasonably saturated pixels are considered so the
    blown-out white centre does not pull the range towards grey.
    """
    h, w = frame_bgr.shape[:2]
    x0, x1 = max(0, x - size), min(w, x + size)
    y0, y1 = max(0, y - size), min(h, y + size)
    patch = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    saturated = patch[patch[:, 1] > 40]
    if len(saturated) < 5:
        saturated = patch
    hues = saturated[:, 0].astype(int)
    # Circular mean of the hue so red (which wraps at 0/179) works too.
    angles = hues / 180.0 * 2.0 * math.pi
    mean_angle = math.atan2(np.sin(angles).mean(), np.cos(angles).mean())
    mean_hue = int(round((mean_angle / (2.0 * math.pi) * 180.0) % 180))
    sat = int(np.percentile(saturated[:, 1], 20))
    val = int(np.percentile(saturated[:, 2], 20))
    hsv_min = [(mean_hue - hue_margin) % 180, max(0, sat - sat_margin), max(0, val - val_margin)]
    hsv_max = [(mean_hue + hue_margin) % 180, 255, 255]
    return hsv_min, hsv_max


def draw_detections(frame_bgr: np.ndarray, detections: list[Detection], config) -> np.ndarray:
    """Overlay the detected circles, crop and masks on a copy of the frame."""
    out = frame_bgr.copy()
    processing = config["processing"]
    crop = processing.get("crop", {})
    if crop.get("w", 0) > 0 and crop.get("h", 0) > 0:
        cv2.rectangle(out, (int(crop["x"]), int(crop["y"])),
                      (int(crop["x"] + crop["w"]), int(crop["y"] + crop["h"])), (255, 255, 255), 1)
    for polygon in processing.get("mask_polygons", []):
        if len(polygon) >= 3:
            pts = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(out, [pts], True, (0, 0, 255), 1)
    for det in detections:
        controller = next((c for c in config["controllers"] if int(c["id"]) == det.controller_id), None)
        colour = tuple(int(v) for v in reversed(controller["led"])) if controller else (255, 255, 255)
        if det.found:
            cv2.circle(out, (int(det.x), int(det.y)), int(det.radius), colour, 2)
            cv2.drawMarker(out, (int(det.x), int(det.y)), colour, cv2.MARKER_CROSS, 10, 1)
            cv2.putText(out, f"{det.controller_id}", (int(det.x) + 8, int(det.y) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
    return out
