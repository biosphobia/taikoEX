# Customizing

The rule of the project: everything is a plain file, nothing is hidden in a binary.
The `data` folder next to the game (or `game/data` in the source tree) holds all
user content and is never packed into the executable.

```
data/
  settings.json         game settings (created when you change something in Settings)
  songs/<name>/         one folder per song: chart.tja + audio (ogg / mp3 / wav)
  skins/<name>/         skin.json + optional PNG files
  sounds/               don.wav, ka.wav, balloon_pop.wav
  models/               drum.glb and controller.glb for the 3D view (optional)
tracker/
  tracker_config.json   camera, colours, calibration, pads, osu keys
```

## Songs

Drop a folder with a `.tja` chart and its audio file into `data/songs`.  Any depth of
sub-folders works.  The `WAVE:` line in the chart names the audio file; if it is missing
the game looks for an audio file with the chart's name.

Supported TJA features: `TITLE SUBTITLE BPM WAVE OFFSET DEMOSTART SONGVOL COURSE LEVEL
BALLOON`, several courses per file, `#BPMCHANGE #MEASURE #SCROLL #DELAY #GOGOSTART
#GOGOEND #BARLINEOFF #BARLINEON`, branches (the master branch is played), notes
`1 2 3 4 5 6 7 8 9`.

`tools/make_demo_assets.py` shows how the bundled demo song and chart were generated.

## Skins

Copy `data/skins/default` to a new folder and set `"skin": "myskin"` in
`settings.json`.  `skin.json` holds every colour and size used by the game.  Any of
these PNG files placed in the skin folder replaces the built-in drawing:

`don.png ka.png don_big.png ka_big.png roll_head.png roll_body.png balloon.png lane.png
background.png drum.png`

Hit sounds are `data/sounds/don.wav`, `ka.wav` and `balloon_pop.wav` (ogg also works).

## Game settings

`data/settings.json` only needs the keys you change; the full list with comments is
`DEFAULTS` in `game/autoload/settings.gd`.  Highlights:

* `keys` - keyboard mapping.
* `judgement` - timing windows in ms.
* `scoring` - points per judgement, gauge clear ratio.
* `audio.offset_ms` - shift judgement if the audio lags.
* `gameplay.note_speed`, `gameplay.autoplay`.
* `tracker.*` - ports, auto-launch, extra input offset for tracker hits.
* `update.*` - GitHub repository to update from, automatic install on/off.

## Tracker settings

`tracker/tracker_config.json` - every key is documented in
`tracker/taiko_tracker/config.py`.  The calibration screen edits the same file live.
The sections worth knowing about:

* `pads` - the drum itself.  Each entry is a disc or a ring with a centre, a
  normal, an outer radius and an inner radius, so you can build any shape out of
  them: a single drum with a rim (the default), four pads in a row, angled pads,
  three drums side by side.  `"side": "any"` means the hand that strikes decides
  left from right.  See [TRACKING.md](TRACKING.md#telling-don-from-ka).
* `hits` - what counts as a hit and when it happened.
* `fusion` - the position filter, which knows a camera measures direction well
  and distance badly.
* `imu` - the controller's gyro and accelerometer.
* `background` - the learned mask of things in the room that look like a sphere.
* `simulation` - only used by the `simulated` camera backend: which of the five
  test rooms to render, and where to put the virtual camera.

## Models for the 3D view

The 3D view draws the drum and the controllers procedurally, but it will use
your own models instead if you drop them in:

* `data/models/drum.glb` replaces the drum body
* `data/models/controller.glb` replaces the PS Move

Model the controller with its handle along +Y and the sphere at the top, which
is how the tracker describes it, and it will tip and roll with the real one.

The built-in controller is painted in its LED colour - the sphere glows with
it, a band under the sphere carries it, and the handle is tinted with it
(`HANDLE_TINT` in `game/scripts/pov/controller_model.gd` sets how strongly).
Change the colour in the calibration screen's Colours tab and the model
follows.  A custom `controller.glb` keeps its own materials.

## Code

* Game screens build their UI in code with the helpers in `game/scripts/ui/ui_kit.gd`;
  change a helper to change the look everywhere.
* Gameplay rules (judgement, big notes, rolls, balloons, gauge) are all in
  `game/scripts/gameplay/gameplay.gd`; drawing is in `lane.gd`.
* The tracker is split into single-purpose modules; `tracker/README.md` has the map.
  New commands for the game are plain methods called `cmd_<name>` in `tracker.py`.
