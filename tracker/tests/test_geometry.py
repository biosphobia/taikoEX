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
