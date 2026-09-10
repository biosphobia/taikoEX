# TaikoEX

A Taiko drum game for PC, played in the air with **PS Move controllers** tracked by a
**PS3 Eye camera** - no physical drum needed.  Think Aerodrums, but for Taiko.

* **Game** - Godot 4.5, plays `.tja` charts, judges hits, keeps score.
* **Tracker** - a small Python program that reads the camera, finds the glowing
  spheres, works out where they are in 3D and detects strokes through virtual drum pads.
* **osu! mode** - the same virtual drums can type keys for osu!taiko (or anything else).
* **Auto update** - every push to `main` builds a Windows release on GitHub; installed
  copies download and install it on start-up.

Everything a player might want to change (songs, skins, sounds, settings, calibration,
the tracker code itself) is a plain file you can open in a text editor.  See
[docs/CUSTOMIZING.md](docs/CUSTOMIZING.md).

## Quick start (Windows release)

1. Download `TaikoEX-windows-x64.zip` from the latest
   [release](../../releases/latest) and unzip it anywhere.
2. Pair your PS Move controllers over Bluetooth (pairing is done outside this project,
   e.g. with `psmove pair` from psmoveapi or with PSMoveService).  The tracker lights the
   spheres itself once they are connected.
3. Install a driver that exposes the PS3 Eye as a webcam (see *Hardware* below).
4. Run `TaikoEX.exe`.  The game starts the tracker (`tracker\taiko_tracker.exe`) for you.
5. Open **Camera & drum calibration** and follow [docs/CALIBRATION.md](docs/CALIBRATION.md).
6. **Play**.

Without a camera you can play on the keyboard: `D` `F` `J` `K` = left rim, left face,
right face, right rim.

## Quick start (from source)

```
pip install -r tracker/requirements.txt
python tracker/run_tracker.py --show          # camera index 0, debug window
```

Then open the `game/` folder in Godot 4.5 and press Play, or run
`godot --path game`.  The game finds `tracker/run_tracker.py` next to it and starts it
automatically when no tracker answers.

No hardware at hand?  `python tracker/run_tracker.py --backend simulated --no-hid`
starts a fake camera with two fake controllers so the whole game can be exercised.

## Hardware

**PS3 Eye on Windows.**  The camera needs a driver.  Two options:

* A driver that registers it as a normal webcam (CL-Eye, the "PS3 Eye Universal
  Driver", ...).  Use the default `opencv` backend and pick the camera index.
* The libusb / WinUSB driver (the one PSMoveService uses) together with the
  [pseyepy](https://github.com/bensondaled/pseyepy) library.  Set the backend to `pseye`.

Both give you exposure and gain control; lower them until the room is dark and only the
spheres are bright.  The narrow lens setting (red dot on the lens ring) gives more
pixels per sphere and therefore better depth accuracy.

**PS Move controllers.**  The tracker talks to them over Bluetooth HID to set the sphere
colour and read the buttons / accelerometer.  Both the PS3 (CECH-ZCM1) and the PS4
(CECH-ZCM2) models work.  If something else already lights the spheres (PSMoveService)
you can switch `hid.enabled` off in the tracker config and tracking still works from the
colour alone.

## How it works

```
 PS3 Eye ─► camera.py ─► vision.py ─► geometry.py ─► pads.py ─► network.py ─► Godot game
             (frames)    (find the    (pixels ->     (stroke     (UDP json)    (judge, score)
                          spheres)     3D metres)     through     ─► keysender.py ─► osu!
                                                      a pad)
```

1. **Colour tracking.**  Each controller gets its own LED colour (magenta and cyan by
   default) and HSV range.  A blown-out white centre is expected and merged back into the
   blob.  Crop and mask polygons remove lamps, TVs and posters.
2. **3D position.**  The sphere is 45 mm across, so its size in pixels gives its distance
   and the pixel position gives the direction.  A three point *world calibration* turns
   camera coordinates into "your right / up / back", whatever the camera angle.
3. **Virtual pads.**  Four flat discs (left rim, left face, right face, right rim) float in
   that space.  Moving a sphere down through a disc fast enough is a hit; the crossing time
   is interpolated between frames so timing is accurate to a few milliseconds.
4. **Game / osu!.**  Hits are sent to the game with their camera timestamp, or typed as
   keyboard keys when osu! mode is on.

Details: [docs/CALIBRATION.md](docs/CALIBRATION.md), [docs/PROTOCOL.md](docs/PROTOCOL.md),
[docs/OSU_MODE.md](docs/OSU_MODE.md), [docs/BUILDING.md](docs/BUILDING.md).

## Repository layout

```
game/                 Godot 4.5 project
  autoload/           settings, skin, tracker client, input, updater (singletons)
  scenes/             one .tscn per screen (the UI is built in the matching script)
  scripts/charts      TJA parser and chart model
  scripts/gameplay    lane drawing, judgement, scoring, results
  scripts/calibration camera / colour / space / pad calibration screen
  scripts/ui          menus and the osu! screen
  data/               USER CONTENT: songs, skins, sounds, settings.json
  tests/              headless smoke test and the demo driver
tracker/              Python tracker (see tracker/README.md)
tools/                asset generators and the tracker demo recorder
docs/                 documentation
.github/workflows     CI: tests, Windows build, GitHub release
footage/              demo videos
```

## Tests

```
cd tracker && python -m pytest          # vision, geometry, pads, network, end-to-end
godot --headless --path game res://tests/smoke.tscn
godot --path game res://tests/demo_driver.tscn -- ++sim ++song_seconds=30
```

The end-to-end tests run the complete tracker against the simulated camera: they
calibrate the world through the camera, hit pads with simulated strokes and check the
hit times.
