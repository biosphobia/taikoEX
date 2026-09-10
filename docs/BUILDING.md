# Building and updating

## Continuous builds

`.github/workflows/build.yml` runs on every push to `main`:

1. **test** (Ubuntu) - tracker unit tests and the Godot headless smoke test.
2. **build-windows** (Windows) - exports the game with Godot 4.5, freezes the tracker
   with PyInstaller, copies `data/` and the docs, zips everything into
   `TaikoEX-windows-x64.zip` and publishes a GitHub release tagged
   `v<VERSION>-build.<run number>`.

Bump the `VERSION` file for a new version line; the build number always increases.

## Auto update

`game/autoload/updater.gd` runs at start-up in installed builds:

1. `GET https://api.github.com/repos/<update.repo>/releases/latest`
2. compare the build number in the tag with `version.txt` inside the exe
3. download the zip to `user://update/`, unpack it with Godot's `ZIPReader`
4. write `apply_update.bat`, which waits for the game to exit, `robocopy`s the new
   files over the install folder (keeping `settings.json` and `tracker_config.json`),
   and starts the game again.

The main menu shows the status and a *Restart to install update* button.  Turn
automatic installation off in Settings if you prefer to be asked.

## Building by hand

```
# game
godot --headless --path game --import
mkdir build
godot --headless --path game --export-release "Windows Desktop" ../build/TaikoEX.exe

# tracker
pip install -r tracker/requirements.txt pyinstaller
pyinstaller --onedir --name taiko_tracker --collect-all hid --collect-all cv2 tracker/run_tracker.py
```

Copy `game/data` next to the exe as `data` and the PyInstaller output as `tracker`.
The game looks for `tracker\taiko_tracker.exe` there and starts it automatically.
