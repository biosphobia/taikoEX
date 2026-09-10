import cv2
import numpy as np

from taiko_tracker.config import Config
from taiko_tracker.vision import SphereDetector, sample_colour


def synthetic_frame(centre=(300, 200), radius=25, bgr=(255, 0, 255), core=True):
    frame = np.full((480, 640, 3), 30, dtype=np.uint8)
    cv2.circle(frame, centre, radius, bgr, -1, cv2.LINE_AA)
    if core:
        cv2.circle(frame, centre, int(radius * 0.6), (255, 255, 255), -1, cv2.LINE_AA)
    return frame


def test_detects_sphere_with_blown_out_core():
    cfg = Config()
    det = SphereDetector(cfg).detect(synthetic_frame())
    magenta = det[0]
    assert magenta.found
    assert abs(magenta.x - 300) < 1.5 and abs(magenta.y - 200) < 1.5
    assert abs(magenta.radius - 25) < 2.5
    assert not det[1].found     # nothing cyan in the frame


def test_mask_polygon_hides_distractor():
    frame = synthetic_frame()
    cv2.rectangle(frame, (550, 20), (630, 100), (255, 0, 255), -1)   # magenta poster
    cfg = Config({"processing": {"mask_polygons": [[[540, 10], [640, 10], [640, 110], [540, 110]]]}})
    det = SphereDetector(cfg).detect(frame)[0]
    assert det.found and abs(det.x - 300) < 2


def test_crop_limits_search():
    cfg = Config({"processing": {"crop": {"x": 0, "y": 0, "w": 200, "h": 480}}})
    det = SphereDetector(cfg).detect(synthetic_frame())[0]
    assert not det.found


def test_sample_colour_gives_range_containing_sphere():
    frame = synthetic_frame(bgr=(0, 200, 255))   # orange-ish
    hsv_min, hsv_max = sample_colour(frame, 300, 200, size=20)
    cfg = Config({"controllers": [{"id": 0, "name": "x", "led": [255, 200, 0], "hsv_min": hsv_min, "hsv_max": hsv_max}]})
    det = SphereDetector(cfg).detect(frame)[0]
    assert det.found and abs(det.radius - 25) < 3


def half_size(frame):
    return cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)


def test_detection_settings_follow_the_resolution():
    """The same scene at 320x240 must be found with the same config: every
    pixel setting is written for the reference width and scaled to the frame."""
    cfg = Config()
    det = SphereDetector(cfg).detect(half_size(synthetic_frame()))[0]
    assert det.found
    assert abs(det.x - 150) < 1.5 and abs(det.y - 100) < 1.5
    assert abs(det.radius - 12.5) < 1.5


def test_crop_and_mask_scale_with_the_resolution():
    frame = synthetic_frame()
    cv2.rectangle(frame, (550, 20), (630, 100), (255, 0, 255), -1)   # magenta poster, in 640-wide pixels
    cfg = Config({"processing": {"mask_polygons": [[[540, 10], [640, 10], [640, 110], [540, 110]]]}})
    det = SphereDetector(cfg).detect(half_size(frame))[0]
    assert det.found and abs(det.x - 150) < 2      # the poster is masked at 320 wide too
    cropped = Config({"processing": {"crop": {"x": 0, "y": 0, "w": 200, "h": 480}}})
    assert not SphereDetector(cropped).detect(half_size(synthetic_frame()))[0].found
