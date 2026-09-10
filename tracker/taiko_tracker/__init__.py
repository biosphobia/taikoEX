"""taiko_tracker - camera based PS Move tracking for TaikoEX.

The package is split into small, single purpose modules so that every part
can be read and edited on its own:

    config.py       load / save / default settings (one JSON file)
    camera.py       camera backends (OpenCV, pseyepy, video file, simulation)
    vision.py       find the glowing spheres in a frame
    geometry.py     pixels -> 3D camera space -> 3D world space
    pads.py         virtual drum pads and hit detection
    psmove_hid.py   talk to the controllers over Bluetooth HID (LED colour, IMU)
    keysender.py    press keyboard keys for osu! mode
    network.py      UDP link to the Godot game (state, commands, preview video)
    simulation.py   fake camera + fake controllers used for tests and demos
    tracker.py      the main loop that wires everything together
"""

__version__ = "0.1.0"
