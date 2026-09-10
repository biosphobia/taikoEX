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


# Beyond this axis ratio the blob is a streak, not a sphere with a tail, and
# the capsule model stops being a fair description.
MAX_ELONGATION = 6.0


@dataclass
class Detection:
    controller_id: int
    found: bool
    x: float = 0.0          # pixel x in the full (uncropped) frame
    y: float = 0.0
    radius: float = 0.0     # inscribed pixel radius of the blob
    area: float = 0.0       # blob area in pixels
    circularity: float = 0.0
    elongation: float = 1.0     # 1 = round, higher = smeared by motion blur
    completeness: float = 1.0   # 1 = whole sphere visible, lower = partly hidden


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


PIXEL_SETTINGS = ("bright_core_reach_px", "min_radius_px", "max_radius_px", "track_reach_px")


def scale_processing(processing: dict, scale: float) -> dict:
    """The ``processing`` settings converted from the reference width to the live frame.

    Everything measured in pixels - the crop, the mask polygons, the size
    limits, how far the bright core may reach and the blur - is written for a
    frame ``optics.reference_width`` wide, so that switching the camera to a
    smaller, faster mode does not silently change what counts as a sphere.
    """
    if abs(scale - 1.0) < 1e-6:
        return processing
    scaled = dict(processing)
    for key in PIXEL_SETTINGS:
        if key in scaled:
            scaled[key] = float(scaled[key]) * scale
    crop = processing.get("crop", {})
    if crop:
        scaled["crop"] = {k: int(round(float(v) * scale)) for k, v in crop.items()}
    scaled["mask_polygons"] = [[[x * scale, y * scale] for x, y in polygon]
                               for polygon in processing.get("mask_polygons", [])]
    blur = int(processing.get("blur", 0))
    if blur > 0:
        # A blur that scales below one pixel would do nothing; keep the
        # smallest kernel that still smooths sensor noise.
        scaled["blur"] = max(3, int(round(blur * scale)) | 1)
    return scaled


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
    reach = int(round(float(processing.get("bright_core_reach_px", 12))))
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


def fit_sphere_outline(contour, area: float) -> tuple[float, float, float, float, float, float]:
    """Measure a sphere blob.

    Returns ``(x, y, radius, enclosing_radius, elongation, completeness)``.

    The radius comes from the blob's **area**, corrected for how far the
    exposure smeared it.  A sphere sitting still is a disc of area pi*r^2; one
    moving during the exposure is a capsule, which for an axis ratio ``e`` has
    area ``(pi + 4(e - 1)) * r^2``.  Inverting that gives the radius the sphere
    would have had if it had held still.

    Area is the right thing to measure because it is a count of thousands of
    pixels: the error contributed by the blob's edge averages out to a small
    fraction of a pixel.  The alternatives are much worse - the enclosing
    circle grows with speed, and the largest circle that fits inside the blob
    is quantised to the pixel grid, which at a radius of ten pixels is a five
    per cent jump in distance.

    The centre is the blob's centroid, which for a smear is the position at
    the middle of the exposure - half a frame behind the shutter, and that
    constant lag is what ``hits.latency_compensation_ms`` exists for.

    ``completeness`` says how much of the sphere survived.  For an intact
    capsule the enclosing circle is exactly ``radius * elongation``; a blob
    that is bitten into by a hand or the drum edge loses area without losing
    its outline, so the ratio drops below one.  The tracker keeps such a
    measurement but trusts its distance less (see
    ``tracking_filter.measurement_covariance``).
    """
    (ex, ey), enclosing_radius = cv2.minEnclosingCircle(contour)
    # The smallest rotated rectangle around the blob gives the true extent of
    # the smear (a fitted ellipse matches second moments instead, which reads
    # a capsule as much rounder than it is).
    (rx, ry), (side_a, side_b), _ = cv2.minAreaRect(contour)
    long_side, short_side = max(side_a, side_b), min(side_a, side_b)
    elongation = 1.0
    if short_side > 1e-6:
        elongation = float(long_side / short_side)
    if len(contour) >= 5:
        # The rectangle can read a barely smeared blob as square; the fitted
        # ellipse notices small smears earlier.  Whichever sees more, wins.
        (_, _), (axis_a, axis_b), _ = cv2.fitEllipse(contour)
        if min(axis_a, axis_b) > 1e-6:
            elongation = max(elongation, float(max(axis_a, axis_b) / min(axis_a, axis_b)))
    elongation = max(1.0, min(elongation, MAX_ELONGATION))
    moments = cv2.moments(contour)
    if moments["m00"] > 1e-6:
        cx, cy = float(moments["m10"] / moments["m00"]), float(moments["m01"] / moments["m00"])
    else:
        cx, cy = float(rx), float(ry)
    radius = math.sqrt(max(area, 1.0) / capsule_area_factor(elongation))
    completeness = 1.0
    if enclosing_radius > 0.5:
        completeness = min(1.0, radius * elongation / enclosing_radius)
    return cx, cy, radius, float(enclosing_radius), elongation, completeness


def capsule_circularity(elongation: float) -> float:
    """How round a smeared disc looks: 1.0 unsmeared, lower as it stretches."""
    e = max(1.0, elongation)
    perimeter = 2.0 * math.pi + 4.0 * (e - 1.0)
    return 4.0 * math.pi * capsule_area_factor(e) / (perimeter * perimeter)


def capsule_fill_ratio(elongation: float) -> float:
    """Area of a smeared disc over the area of the circle drawn around it."""
    e = max(1.0, elongation)
    return capsule_area_factor(e) / (math.pi * e * e)


def capsule_area_factor(elongation: float) -> float:
    """Area of a smeared disc divided by its radius squared.

    A disc of radius r dragged a distance L covers ``pi*r^2 + 2*r*L``.  Its
    fitted ellipse has minor axis ``2r`` and major axis ``L + 2r``, so the axis
    ratio is ``e = (L + 2r) / 2r`` and ``L = 2r(e - 1)``, giving the factor
    below.  At ``e = 1`` it is pi, the plain disc.
    """
    return math.pi + 4.0 * (max(1.0, elongation) - 1.0)


def best_circle(mask: np.ndarray, processing: dict,
                expected: tuple[float, float, float] | None = None) -> tuple | None:
    """Pick the blob that looks most like a sphere.

    Returns ``(x, y, radius, area, circularity, elongation, completeness)``.

    ``expected`` is the (x, y, r) of the previous detection; blobs close to it
    get a large bonus so a bigger distractor elsewhere cannot steal the track.

    The roundness and fullness a blob has to reach are measured *against the
    shape it should have been*.  A sphere smeared across the frame during the
    exposure is a long capsule, and judging it against a circle would throw
    away every fast movement - which is precisely when the tracking is needed.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_r = float(processing.get("min_radius_px", 3))
    max_r = float(processing.get("max_radius_px", 150))
    min_circ = float(processing.get("min_circularity", 0.45))
    min_fill = float(processing.get("min_fill_ratio", 0.35))
    track_reach = float(processing.get("track_reach_px", 80))
    best = None
    best_score = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < math.pi * min_r * min_r:
            continue
        cx, cy, radius, enclosing_radius, elongation, completeness = fit_sphere_outline(contour, area)
        if radius < min_r or radius > max_r:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0 else 0.0
        fill_ratio = area / (math.pi * enclosing_radius * enclosing_radius)
        roundness = circularity / capsule_circularity(elongation)
        fullness = fill_ratio / capsule_fill_ratio(elongation)
        if roundness < min_circ or fullness < min_fill:
            continue
        # Scoring stays absolute even though acceptance does not: among the
        # blobs that could be a sphere, the roundest and fullest is the most
        # likely one, and a long smear is a worse bet than a clean disc.
        score = area * circularity * fill_ratio * fill_ratio
        if expected is not None:
            distance = math.hypot(cx - expected[0], cy - expected[1])
            if distance < track_reach + 2.0 * expected[2]:
                score *= 4.0
        if score > best_score:
            best_score = score
            best = (float(cx), float(cy), float(radius), float(area), float(circularity),
                    float(elongation), float(completeness))
    return best


class SphereDetector:
    """Runs the pipeline above for every configured controller."""

    def __init__(self, config):
        self.config = config
        self._region_mask = None
        self._region_shape = None
        self._processing: dict = {}
        self._processing_shape = None
        self.last_masks: dict[int, np.ndarray] = {}   # for the debug window / preview
        self.last_seen: dict[int, tuple[float, float, float]] = {}   # controller id -> (x, y, r)

    def processing_for(self, shape: tuple[int, int]) -> dict:
        """The processing settings scaled to this frame size (cached per size)."""
        if self._processing_shape != shape:
            from .geometry import pixel_scale

            scale = pixel_scale(self.config["optics"], shape[1])
            self._processing = scale_processing(self.config["processing"], scale)
            self._processing_shape = shape
        return self._processing

    def region_mask_for(self, shape: tuple[int, int]) -> np.ndarray | None:
        """Pixels the tracker is allowed to search: the crop, minus the drawn
        mask polygons, minus anything the background learner found."""
        if self._region_shape != shape:
            region = build_region_mask(shape, self.processing_for(shape))
            learned = self._learned_mask(shape)
            if learned is not None:
                if region is None:
                    region = np.full(shape, 255, dtype=np.uint8)
                region = cv2.bitwise_and(region, cv2.bitwise_not(learned))
            self._region_mask = region
            self._region_shape = shape
        return self._region_mask

    def _learned_mask(self, shape: tuple[int, int]) -> np.ndarray | None:
        from .background import decode_mask   # imported here to keep the module import order simple

        try:
            encoded = self.config["background"].get("mask")
        except (KeyError, TypeError):
            return None
        return decode_mask(encoded, shape)

    def invalidate(self) -> None:
        """Call after the crop / mask / processing settings changed."""
        self._region_shape = None
        self._processing_shape = None

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        processing = self.processing_for(frame_bgr.shape[:2])
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
                x, y, r, area, circ, elongation, completeness = circle
                self.last_seen[cid] = (x, y, r)
                detections.append(Detection(cid, True, x, y, r, area, circ, elongation, completeness))
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
    from .geometry import pixel_scale

    processing = scale_processing(config["processing"], pixel_scale(config["optics"], out.shape[1]))
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
