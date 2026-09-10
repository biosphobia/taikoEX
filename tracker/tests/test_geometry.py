import numpy as np

from taiko_tracker.config import Config
from taiko_tracker.geometry import PinholeModel, WorldCalibration
from taiko_tracker.simulation import SimulatedCamera


def test_pixel_to_camera_round_trip():
    cfg = Config({"camera": {"backend": "simulated"}})
    sim = SimulatedCamera(cfg.data)
    model = PinholeModel(cfg["optics"], 640, 480)
    for point in ([0.1, 0.05, 0.2], [-0.3, 0.3, -0.1], [0.0, 0.0, 0.0]):
        world = np.array(point)
        u, v, r = sim.virtual.project(world)
        recovered = model.pixel_to_camera(u, v, r)
        assert np.allclose(recovered, sim.virtual.world_to_camera(world), atol=1e-3)


def test_focal_calibration_inverts_distance():
    model = PinholeModel({"focal_px": 500, "cx": -1, "cy": -1, "sphere_radius_m": 0.0225}, 640, 480)
    distance = 1.3
    radius_px = model.focal * model.sphere_radius / np.sqrt(distance ** 2 - model.sphere_radius ** 2)
    assert abs(model.focal_from_known_distance(radius_px, distance) - 500) < 1e-6


def test_world_calibration_recovers_axes():
    cfg = Config({"camera": {"backend": "simulated"}})
    sim = SimulatedCamera(cfg.data)
    cal = WorldCalibration()
    cal.capture("origin", sim.virtual.world_to_camera(np.array([0.0, 0.0, 0.0])))
    cal.capture("right", sim.virtual.world_to_camera(np.array([0.4, 0.0, 0.0])))
    cal.capture("forward", sim.virtual.world_to_camera(np.array([0.0, 0.0, -0.4])))
    transform = cal.solve()
    for point in ([0.2, 0.1, -0.05], [-0.3, 0.25, 0.1]):
        world = np.array(point)
        assert np.allclose(transform.to_world(sim.virtual.world_to_camera(world)), world, atol=1e-6)


def test_pixel_values_scale_with_the_frame_width():
    """A calibration done at 640 wide must hold at 320: the same sphere at the
    same distance is half the radius, and the model must read the same metres."""
    from taiko_tracker.config import DEFAULT_CONFIG

    optics = dict(DEFAULT_CONFIG["optics"], focal_px=550.0, radius_offset_px=1.0)
    big = PinholeModel(optics, 640, 480)
    small = PinholeModel(optics, 320, 240)
    assert small.pixel_scale == 0.5 and big.pixel_scale == 1.0
    assert small.focal == big.focal / 2
    assert small.radius_offset == big.radius_offset      # the glow is per pixel, not per frame
    radius_big = big.apparent_radius(1.2) + 1.0
    radius_small = small.apparent_radius(1.2) + 1.0
    assert abs(big.distance_from_radius(radius_big) - 1.2) < 1e-6
    assert abs(small.distance_from_radius(radius_small) - 1.2) < 1e-6
    # A measurement in live pixels goes back to the config's reference width.
    assert small.to_reference_px(4.0) == 8.0
