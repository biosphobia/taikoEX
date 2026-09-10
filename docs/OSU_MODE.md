# osu! input mode

The virtual drums can drive osu!taiko - or any program that reads the keyboard.

1. Calibrate the camera and pads as usual.
2. Open **osu! input mode** from the main menu and press **Enable osu! mode**.  The
   default mapping is the osu!taiko default: left rim `D`, left face `F`, right face `J`,
   right rim `K`.  Change the keys on that screen or in `tracker_config.json` under
   `osu.keys`.  Key names are single characters or `space enter tab escape f1..f12
   left right up down shift ctrl alt`.
3. Alt-tab to osu!.  The game window can stay open in the background or you can close it
   and run the tracker alone: `taiko_tracker.exe --osu` (or
   `python run_tracker.py --osu`).

Keys are sent with hardware scan codes through `SendInput` on Windows, which is what
osu! expects.  Each key is held for `osu.key_hold_ms` (35 ms by default).

Set the *Latency compensation* in the Space tab to taste; osu! has its own universal
offset setting as well.
