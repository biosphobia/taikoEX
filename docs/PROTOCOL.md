# Tracker <-> game protocol

Three UDP sockets on localhost (ports in `network` of the tracker config and
`tracker` of the game settings):

| port  | direction       | content |
| ----- | --------------- | ------- |
| 47820 | tracker -> game | one JSON *state* packet per camera frame |
| 47821 | game -> tracker | JSON commands; the reply goes back to the sender |
| 47822 | tracker -> game | JPEG preview frames (fragmented) |

Any program can use this: the tracker does not care whether Godot is listening.

## State packet

```json
{"type": "state", "t": 1789015405.88, "fps": 186.2, "fps_requested": 187, "fps_camera": 187,
 "frame": [320, 240],
 "controllers": [
   {"id": 0, "visible": true, "px": [150.6, 120.2, 4.7], "led": [255, 0, 255],
    "cam": [0.02, -0.11, 1.32], "world": [-0.12, 0.05, 0.01],
    "raw": [-0.12, 0.05, 0.04], "vel": [0.0, -1.8, 0.0],
    "quat": [0.998, 0.06, 0.0, 0.01], "imu": true,
    "hid": true, "buttons": 0, "trigger": 0, "battery": 4, "accel": [0.0, 1.0, 0.0]}],
 "hits": [
   {"pad": "don", "kind": "don", "side": "left", "controller": 0,
    "t": 1789015405.87, "speed": 2.1, "strength": 0.7, "pos": [-0.1, 0.0, 0.0]}],
 "camera_pose": {"position": [0.15, 0.55, -1.25], "right": [1, 0, 0],
                 "up": [0, 0.9, 0.4], "forward": [0, -0.4, 0.9]},
 "pads_revision": 3, "world_calibrated": true, "world_points": [],
 "learning_background": false, "osu": false, "hid_available": true}
```

`t` values are Unix time (`time.time()` / `Time.get_unix_time_from_system()`), so the
game can convert a hit's timestamp to song time regardless of transport delay.

| field | meaning |
| --- | --- |
| `fps` | frames per second the tracker is actually getting through |
| `fps_requested` / `fps_camera` | the rate asked for in the config, and the rate the driver claims to run at |
| `frame` | the live frame size (the driver's answer, not the request) |
| `px` | pixel x, y and radius in the full camera frame |
| `led` | the sphere's LED colour, so a client can draw the controller in it |
| `cam` | metres in camera space (x right, y down, z forward) |
| `world` | metres in the calibrated space (x right, y up, z away from the camera), filtered |
| `raw` | the same position straight from the camera, before filtering |
| `vel` | metres per second, from the filter |
| `quat` | orientation as (w, x, y, z), sensor to world |
| `imu` | whether the orientation estimate has settled |
| `camera_pose` | where the camera itself is, in the calibrated space |
| `pads_revision` | bumped whenever the pad layout changes, so a client knows to refetch |

`side` on a hit is the hand that struck, not a property of the pad: a pad marked
`"side": "any"` (the single-drum default) takes its side from the controller.

## Commands

Send `{"cmd": "<name>", ...}`.  Add `"save": true` to write the config afterwards.
The reply is `{"reply": "<name>", "ok": true, ...}` or `{"ok": false, "error": "..."}`.

| command | arguments | effect |
| --- | --- | --- |
| `ping` | | version and time |
| `get_config` / `get_defaults` | | full config |
| `set_config` | `patch` | merge a partial config; camera / pads / optics are re-applied live |
| `save_config` / `reset_config` | | |
| `set_camera_control` | `control`, `value` | exposure, gain, ... live |
| `get_camera_controls` | | |
| `list_cameras` | `max_index` | probe OpenCV camera indices |
| `probe_camera_modes` | `modes` (optional, `[[w, h, fps], ...]`), `seconds` | open every mode in `camera.fast_modes` and measure the frame rate each one really delivers |
| `camera_fastest` | `modes`, `seconds` | the same, quickest first, stopping at the first mode that delivers; switches to it |
| `led_presets` | | sphere colours that track well, each with its HSV range |
| `set_preview` | `preview_enabled`, `preview_fps`, `preview_width`, `preview_quality`, `mode` (`camera` / `mask`) | |
| `sample_colour` | `controller`, `x`, `y`, `size`, `hue_margin` | learn the HSV range from the pixels around (x, y) |
| `set_led` | `controller`, `rgb` | |
| `list_controllers` | | HID devices |
| `calibrate_distance` | `controller`, `distance_m` | add one sample to the two-point distance calibration; solves for focal length and sphere glow once two distances are in |
| `calibrate_distance_reset` | | throw the samples away and start again |
| `calibrate_focal` | `controller`, `distance_m` | one-shot focal length only, ignoring the glow (kept for quick tests) |
| `world_capture` | `point` (`origin` / `right` / `forward`), `controller` | world calibration; solved after the third point |
| `world_reset` | | |
| `place_pad` | `pad`, `controller`, optional `normal` | move a pad to the controller |
| `set_pad` | `pad`, any of `center normal radius inner_radius name kind side` | |
| `pad_layout_taiko` | `style` (`single_drum` or `four_pads`), `center` or `controller`, plus that style's sizes | rebuild the pads from a preset |
| `nudge_pad` | `pad`, `delta` | move one pad by a vector in world metres |
| `nudge_pads` | `delta`, `scale` | move or resize the whole drum |
| `get_pads` | | the pads and the current revision |
| `learn_background` | `frames` (optional; default `background.learn_seconds` of camera time) | turn the LEDs off, find what in the room looks like a controller, mask it |
| `clear_background` | | forget the learned mask |
| `background_result` | | what the last learning run found |
| `recenter_orientation` | `controller` (optional) | point the heading at the camera, as the MOVE button does |
| `imu_calibrate_upright` | `controller` | learn which way the controller's handle points, from the accelerometer |
| `get_recent_hits` | | last 20 hits |
| `set_osu` | `enabled`, `keys` | osu! key output |
| `sim_goto` / `sim_hit` / `sim_play_chart` | see `tracker.py` | drive the simulated camera |
| `sim_scene` | `scene`, `camera_position`, `camera_target` | switch the simulated room |
| `sim_truth` | | the true controller positions and camera pose, for measuring error |
| `quit` | | |

## Preview frames

Each packet: `"TKPV"` + frame id (uint16, big endian) + fragment index (uint8) +
fragment count (uint8) + up to 60000 bytes of JPEG.  Reassemble all fragments of a
frame id, then decode the JPEG.
