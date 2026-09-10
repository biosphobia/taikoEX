#!/usr/bin/env python3
"""Record the 3D view in several simulated rooms, one clip per setup.

For each room this starts a tracker on the simulated camera, performs the
whole calibration a player would perform, sets both hands drumming, records
the 3D view with Godot's movie recorder, and measures the tracker's error
against the truth the simulator knows.

    python tools/record_setups.py --godot /path/to/godot --out footage/

Add --scene to pick particular rooms.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tracker"))

from taiko_tracker.simulation import SCENES        # noqa: E402

COMMAND_PORT = 47821


class Tracker:
    """A tracker process, driven over its UDP command port."""

    def __init__(self, scene: str, workdir: Path):
        self.config_path = workdir / f"tracker_{scene}.json"
        self.config_path.write_text(json.dumps({
            "camera": {"backend": "simulated", "fps": 60},
            "simulation": {"scene": scene},
            "hid": {"enabled": False},
            "debug": {"print_hits": False},
            "network": {"preview_enabled": True, "preview_fps": 20},
        }, indent=2))
        self.log = open(workdir / f"tracker_{scene}.log", "w")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tracker" / "run_tracker.py")],
            env={**os.environ, "TAIKO_TRACKER_CONFIG": str(self.config_path)},
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        self._sockets = threading.local()

    @property
    def sock(self) -> socket.socket:
        """One socket per thread.

        The drumming thread and the main thread both send commands, and a reply
        meant for one would otherwise be read by whichever happened to call
        recv first - which eventually leaves someone waiting for a reply that
        has already been thrown away.
        """
        existing = getattr(self._sockets, "sock", None)
        if existing is None:
            existing = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            existing.settimeout(3.0)
            existing.bind(("127.0.0.1", 0))
            self._sockets.sock = existing
        return existing

    def command(self, **message) -> dict:
        sock = self.sock
        sock.sendto(json.dumps(message).encode(), ("127.0.0.1", COMMAND_PORT))
        try:
            return json.loads(sock.recv(65535).decode())
        except socket.timeout:
            return {"ok": False, "error": "timeout"}

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.command(cmd="ping").get("ok"):
                return True
            time.sleep(0.3)
        return False

    def close(self) -> None:
        self.command(cmd="quit")
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()


def calibrate(tracker: Tracker) -> dict:
    """Background, then distance scale, then the world axes - in that order."""
    tracker.command(cmd="learn_background")
    time.sleep(2.0)
    background = tracker.command(cmd="background_result").get("result", {})
    pose = tracker.command(cmd="sim_truth")["camera"]
    lens, forward = np.array(pose["position"]), np.array(pose["forward"])
    tracker.command(cmd="calibrate_distance_reset")
    optics = {}
    for distance in (0.7, 1.5):
        tracker.command(cmd="sim_goto", controller=0, position=(lens + forward * distance).tolist())
        time.sleep(1.0)
        optics = tracker.command(cmd="calibrate_distance", controller=0, distance_m=distance)
        print(f"  {distance:.1f} m -> {optics.get('samples')}")
    for name, point in (("origin", [0, 0, 0]), ("right", [0.4, 0, 0]), ("forward", [0, 0, -0.4])):
        tracker.command(cmd="sim_goto", controller=0, position=point)
        time.sleep(0.9)
        tracker.command(cmd="world_capture", point=name, controller=0)
    tracker.command(cmd="sim_goto", controller=0, position=[-0.25, 0.25, 0.0])
    tracker.command(cmd="sim_goto", controller=1, position=[0.25, 0.25, 0.0])
    time.sleep(0.5)
    return {"background": background,
            "focal_px": optics.get("focal_px"), "radius_offset_px": optics.get("radius_offset_px"),
            "camera": pose}


stop_drumming = threading.Event()


def keep_drumming(tracker: Tracker, until: float, interval: float = 0.42) -> None:
    """Feed the simulator a steady stream of strokes for the whole recording."""
    pads = [pad["id"] for pad in tracker.command(cmd="get_pads").get("pads", [])] or ["don"]
    index = 0
    while time.time() < until and not stop_drumming.is_set():
        now = time.time()
        for step in range(4):
            tracker.command(cmd="sim_hit", at=now + 0.4 + step * interval,
                            pad=pads[(index + step) % len(pads)], controller=(index + step) % 2)
        index += 4
        time.sleep(interval * 4)


def record(godot: Path, out: Path, scene: str, seconds: float, workdir: Path, fast: bool = False) -> Path:
    """Run the 3D view under the movie recorder while the hands play."""
    settings = ROOT / "game" / "data" / "settings.json"
    settings.write_text(json.dumps({"tracker": {"auto_launch": False}}, indent=2))
    raw = workdir / f"pov_{scene}{'_fast' if fast else ''}.avi"
    title = f"{scene.replace('_', ' ')}: {SCENES[scene]['description']}"
    if fast:
        title += " - fastest camera mode, 320x240 at 187 fps"
    title = title.replace(" ", "~")
    command = [str(godot), "--path", str(ROOT / "game"), "--rendering-driver", "opengl3",
               "--write-movie", str(raw), "--fixed-fps", "30",
               "--log-file", str(workdir / f"godot_{scene}.log"),
               "res://tests/pov_demo.tscn", "--", f"++seconds={seconds}", f"++title={title}"]
    if shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"] + command
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=seconds * 20 + 120)
    return raw


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "footage")
    parser.add_argument("--scene", action="append")
    parser.add_argument("--seconds", type=float, default=34.0)
    parser.add_argument("--fast", action="store_true",
                        help="switch each tracker to its fastest camera mode (320x240 at 187 fps) first")
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/taikoex_setups"))
    args = parser.parse_args()

    args.workdir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    scenes = args.scene or list(SCENES)
    clips, summary = [], []

    for scene in scenes:
        print(f"=== {scene}: {SCENES[scene]['description']}")
        tracker = Tracker(scene, args.workdir)
        if not tracker.wait_until_ready():
            print("  tracker did not start")
            tracker.close()
            continue
        if args.fast:
            chosen = tracker.command(cmd="camera_fastest").get("chosen", {})
            print(f"  fastest mode: {chosen.get('width')}x{chosen.get('height')} at {chosen.get('fps_requested')} fps "
                  f"(measured {chosen.get('fps_measured')})")
        info = calibrate(tracker)
        print(f"  focal {info['focal_px']} px, glow {info['radius_offset_px']} px, "
              f"background masked {info['background'].get('covered_percent')}%")
        import threading

        stop_drumming.clear()
        stop_at = time.time() + args.seconds + 25
        drummer = threading.Thread(target=keep_drumming, args=(tracker, stop_at), daemon=True)
        drummer.start()
        raw = record(args.godot, args.out, scene, args.seconds, args.workdir, args.fast)
        stop_drumming.set()
        drummer.join(timeout=5)
        tracker.close()
        if not raw.exists():
            print("  no video produced")
            continue
        clip = args.out / f"setup-{scene.replace('_', '-')}{'-fast' if args.fast else ''}.mp4"
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(raw),
                        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip)], check=True)
        raw.unlink()
        clips.append(clip)
        summary.append({"scene": scene, **{k: v for k, v in info.items() if k != "camera"}})
        print(f"  wrote {clip}")

    if len(clips) > 1:
        listing = args.workdir / "clips.txt"
        listing.write_text("".join(f"file '{clip}'\n" for clip in clips))
        combined = args.out / ("taikoex-setups-fast.mp4" if args.fast else "taikoex-setups.mp4")
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listing), "-c", "copy", str(combined)], check=True)
        print("combined ->", combined)
    (args.out / ("setups-fast.json" if args.fast else "setups.json")).write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
