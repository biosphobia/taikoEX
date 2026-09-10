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
{"type": "state", "t": 1789015405.88, "fps": 60.0, "frame": [640, 480],
 "controllers": [
   {"id": 0, "visible": true, "px": [301.2, 240.5, 9.4],
    "cam": [0.02, -0.11, 1.32], "world": [-0.12, 0.05, 0.01],
    "hid": true, "buttons": 0, "trigger": 0, "battery": 4, "accel": [0.0, 1.0, 0.0]}],
 "hits": [
   {"pad": "left_don", "kind": "don", "side": "left", "controller": 0,
    "t": 1789015405.87, "speed": 2.1, "strength": 0.7, "pos": [-0.1, 0.0, 0.0]}],
 "world_calibrated": true, "world_points": [], "osu": false, "hid_available": true}
```

`t` values are Unix time (`time.time()` / `Time.get_unix_time_from_system()`), so the
game can convert a hit's timestamp to song time regardless of transport delay.
`px` is pixel x, y and radius in the full camera frame; `cam` is metres in camera
space (x right, y down, z forward); `world` is metres in the calibrated space (x right,
y up, z away from the camera).

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
| `set_preview` | `preview_enabled`, `preview_fps`, `preview_width`, `preview_quality`, `mode` (`camera` / `mask`) | |
| `sample_colour` | `controller`, `x`, `y`, `size`, `hue_margin` | learn the HSV range from the pixels around (x, y) |
| `set_led` | `controller`, `rgb` | |
| `list_controllers` | | HID devices |
| `calibrate_focal` | `controller`, `distance_m` | focal length from a known distance |
| `world_capture` | `point` (`origin` / `right` / `forward`), `controller` | world calibration; solved after the third point |
| `world_reset` | | |
| `place_pad` | `pad`, `controller`, optional `normal` | move a pad to the controller |
| `set_pad` | `pad`, any of `center normal radius inner_radius name kind side` | |
| `pad_layout_taiko` | `center` or `controller`, `face_spacing`, `rim_spacing`, `radius` | the preset layout |
| `get_recent_hits` | | last 20 hits |
| `set_osu` | `enabled`, `keys` | osu! key output |
| `sim_goto` / `sim_hit` / `sim_play_chart` | see `tracker.py` | drive the simulated camera |
| `quit` | | |

## Preview frames

Each packet: `"TKPV"` + frame id (uint16, big endian) + fragment index (uint8) +
fragment count (uint8) + up to 60000 bytes of JPEG.  Reassemble all fragments of a
frame id, then decode the JPEG.
