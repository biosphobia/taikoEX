import numpy as np

from taiko_tracker.imu import OrientationFilter


def test_upright_then_tilt_forward():
    f = OrientationFilter({"handle_axis": [0, 1, 0]})
    for _ in range(50):
        f.update([0, 1, 0], [0, 0, 0], 0.01)
    assert np.allclose(f.handle_direction_world(), [0, 1, 0], atol=1e-3)
    # Rotate 45 degrees about the sensor x axis over one second; gravity in
    # the sensor frame follows the rotation.  With the default heading the
    # sensor's +z faces the camera, so the handle ends up leaning towards it (-z).
    for i in range(100):
        angle = 0.7854 * (i + 1) / 100.0
        f.update([0, np.cos(angle), -np.sin(angle)], [0.7854, 0, 0], 0.01)
    assert np.allclose(f.handle_direction_world(), [0, 0.7071, -0.7071], atol=0.05)


def test_gyro_bias_is_learned_when_still():
    f = OrientationFilter({"handle_axis": [0, 1, 0], "bias_samples": 20})
    for _ in range(40):
        f.update([0, 1, 0], [0.05, -0.02, 0.01], 0.01)
    assert np.allclose(f.gyro_bias, [0.05, -0.02, 0.01], atol=1e-6)


def test_recenter_yaw_faces_camera():
    f = OrientationFilter({"handle_axis": [0, 1, 0]})
    for _ in range(50):
        f.update([0, 0.7071, -0.7071], [0, 0, 0], 0.01)   # tilted 45 deg, some heading
    f.recenter_yaw()
    handle = f.handle_direction_world()
    # Tilted handle now leans towards the camera (-z), no sideways component.
    assert handle[2] < -0.5 and abs(handle[0]) < 0.05
