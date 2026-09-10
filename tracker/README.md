# TaikoEX tracker

Camera-based PS Move tracking.  Start it with:

```
python run_tracker.py                 # camera index 0
python run_tracker.py --camera 1 --show
python run_tracker.py --backend simulated --no-hid
python run_tracker.py --osu           # type keys for osu!taiko
python run_tracker.py --help
```

All settings live in `tracker_config.json` next to this file (created on first save;
every key is documented in `taiko_tracker/config.py`).  The game changes them through
the calibration screen, but a text editor works just as well.

Module map (each file has a docstring explaining it):

| file | what it does |
| --- | --- |
| `taiko_tracker/config.py` | defaults, load / save |
| `taiko_tracker/camera.py` | OpenCV / pseyepy / video / simulated camera backends |
| `taiko_tracker/vision.py` | HSV thresholding, blown-out core recovery, blob selection |
| `taiko_tracker/geometry.py` | pixel + radius -> 3D, world calibration |
| `taiko_tracker/pads.py` | virtual pads and stroke detection |
| `taiko_tracker/psmove_hid.py` | sphere LED colour and IMU over Bluetooth HID |
| `taiko_tracker/keysender.py` | keyboard output for osu! mode |
| `taiko_tracker/network.py` | UDP state / command / preview link to the game |
| `taiko_tracker/simulation.py` | fake camera + controllers for tests and demos |
| `taiko_tracker/tracker.py` | the main loop and every command the game can send |

Tests: `python -m pytest`.
