"""Camera modes: finding the fastest one, and reporting what was delivered."""
from taiko_tracker.camera import choose_fastest, measure_fps, nearest_pseye_rate, open_camera, probe_modes
from taiko_tracker.config import Config


def simulated_config(**camera):
    data = {"camera": {"backend": "simulated", "width": 640, "height": 480, "fps": 60},
            "simulation": {"virtual_clock": True}}
    data["camera"].update(camera)
    return Config(data)


def test_pseye_rates_round_to_what_the_sensor_can_do():
    assert nearest_pseye_rate(320, 240, 187) == 187
    assert nearest_pseye_rate(320, 240, 180) == 187
    assert nearest_pseye_rate(640, 480, 187) == 75
    assert nearest_pseye_rate(640, 480, 56) == 60
    assert nearest_pseye_rate(1280, 720, 30) == 30     # not a PS3 Eye mode: left alone


def test_choose_fastest_prefers_measured_rate_then_size():
    results = [
        {"width": 320, "height": 240, "fps_requested": 187, "fps_measured": 30.0},   # driver ignored the request
        {"width": 640, "height": 480, "fps_requested": 75, "fps_measured": 74.6},
        {"width": 320, "height": 240, "fps_requested": 75, "fps_measured": 75.2},
        {"width": 320, "height": 240, "fps_requested": 150, "error": "could not open", "fps_measured": 0.0},
    ]
    best = choose_fastest(results)
    assert best["fps_requested"] == 75 and best["width"] == 640   # same rate, bigger frame wins
    assert choose_fastest([{"error": "nope", "fps_measured": 0.0}]) is None


def test_measure_fps_counts_real_frames():
    camera = open_camera(simulated_config(width=320, height=240, fps=100))
    camera.virtual_clock = False      # measure against the wall clock, like the probe does
    try:
        measured = measure_fps(camera, seconds=0.25)
    finally:
        camera.close()
    assert 20 < measured <= 105


def test_probe_reports_every_mode_and_stops_at_the_first_delivered():
    config = simulated_config()
    modes = [[320, 240, 60], [320, 240, 30]]
    results = probe_modes(config, modes=modes, seconds=0.2, stop_when_delivered=True)
    assert results[0]["width"] == 320 and results[0]["fps_requested"] == 60
    assert results[0]["fps_reported"] == 60
    assert results[0]["fps_measured"] > 0
    if results[0]["delivered"]:
        assert len(results) == 1          # nothing slower is worth trying
    else:
        assert len(results) == 2
    # The probe must not have touched the caller's settings.
    assert config["camera"]["width"] == 640 and config["camera"]["fps"] == 60
    assert config["simulation"]["virtual_clock"] is True


def test_reported_mode_matches_the_request_for_the_simulator():
    camera = open_camera(simulated_config(width=320, height=240, fps=187))
    assert camera.mode() == {"width": 320, "height": 240, "fps": 187}
    camera.close()
