"""UDP link between the tracker and the Godot game.

Three sockets, all on localhost by default:

* state    tracker -> game : one JSON object per camera frame
* commands game -> tracker : JSON ``{"cmd": ...}``; replies go back to the sender
* preview  tracker -> game : JPEG frames, split into numbered fragments

See docs/PROTOCOL.md for the message formats.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time

import cv2
import numpy as np

PREVIEW_MAGIC = b"TKPV"
PREVIEW_HEADER = struct.Struct("!4sHBB")   # magic, frame id, fragment index, fragment count
PREVIEW_CHUNK = 60000


class StateSender:
    def __init__(self, host: str, port: int):
        self.address = (host, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, payload: dict) -> None:
        try:
            self.sock.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), self.address)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


TRACKER_PROCESS_MARKERS = ("taiko_tracker", "run_tracker")


def port_holder(port: int) -> tuple[int, str]:
    """(pid, description) of the process bound to UDP ``port``, or (0, "")."""
    port = int(port)
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-ano", "-p", "udp"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[0].upper() == "UDP" and parts[1].endswith(f":{port}"):
                    pid = int(parts[-1])
                    listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                             capture_output=True, text=True, timeout=10).stdout
                    name = listing.split(",")[0].strip('" \r\n') if listing.strip() else ""
                    return pid, name
        else:
            out = subprocess.run(["lsof", "-t", "-i", f"udp:{port}"], capture_output=True, text=True, timeout=10).stdout
            for token in out.split():
                pid = int(token)
                return pid, process_description(pid)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return 0, ""


def process_description(pid: int) -> str:
    """The command line of a process (POSIX), read from /proc where there is one."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def kill_port_holder(port: int) -> str:
    """Kill the process on UDP ``port`` if it is a tracker; returns what was killed, or ""."""
    pid, description = port_holder(port)
    if not pid or pid == os.getpid():
        return ""
    if not any(marker in description.lower() for marker in TRACKER_PROCESS_MARKERS):
        return ""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"pid {pid}: {description}"


class CommandReceiver:
    """The tracker's command port.

    Deliberately *not* set to reuse the address: two trackers sharing one port
    would each receive some of the commands, and the calibration would quietly
    be applied to whichever one happened to get the packet.  If the port is
    taken, the holder is almost always a tracker left over from the last run
    (the game closed before it did), so it is asked to quit and the port is
    retried for a few seconds before giving up.
    """

    def __init__(self, port: int, host: str = "0.0.0.0", takeover_s: float = 4.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        deadline = time.time() + takeover_s
        asked = killed = False
        while True:
            try:
                self.sock.bind((host, int(port)))
                break
            except OSError as exc:
                now = time.time()
                if now >= deadline:
                    raise RuntimeError(
                        f"Port {port} is already in use and its owner did not quit - another tracker "
                        f"(or another program) is holding it. Stop it, or set network.command_port "
                        f"to something else.") from exc
                if not asked:
                    print(f"[network] port {port} is busy - asking the tracker holding it to quit")
                    self.ask_to_quit(port)
                    asked = True
                elif not killed and now >= deadline - takeover_s / 2:
                    # Half the time is up and it has not let go: a tracker
                    # stuck in a camera read never will.  Only a process that
                    # is recognisably a tracker is killed.
                    killed = True
                    victim = kill_port_holder(port)
                    if victim:
                        print(f"[network] killed the tracker holding port {port} ({victim})")
                time.sleep(0.25)
        self.sock.setblocking(False)

    @staticmethod
    def ask_to_quit(port: int) -> None:
        """Send the quit command to whatever tracker owns ``port``."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(json.dumps({"cmd": "quit"}).encode("utf-8"), ("127.0.0.1", int(port)))
        except OSError:
            pass

    def poll(self) -> list[tuple[dict, tuple]]:
        messages = []
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            try:
                message = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict):
                messages.append((message, addr))
        return messages

    def reply(self, addr: tuple, payload: dict) -> None:
        try:
            self.sock.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), addr)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


class PreviewSender:
    """Sends downscaled JPEG frames at a limited rate."""

    def __init__(self, host: str, port: int):
        self.address = (host, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.frame_id = 0
        self._last_send = 0.0

    def maybe_send(self, frame_bgr: np.ndarray, fps: float, width: int, quality: int) -> bool:
        now = time.time()
        if fps <= 0 or now - self._last_send < 1.0 / fps:
            return False
        self._last_send = now
        h, w = frame_bgr.shape[:2]
        if width > 0 and w > width:
            frame_bgr = cv2.resize(frame_bgr, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            return False
        data = encoded.tobytes()
        chunks = [data[i:i + PREVIEW_CHUNK] for i in range(0, len(data), PREVIEW_CHUNK)] or [b""]
        self.frame_id = (self.frame_id + 1) & 0xFFFF
        for index, chunk in enumerate(chunks):
            header = PREVIEW_HEADER.pack(PREVIEW_MAGIC, self.frame_id, index, len(chunks))
            try:
                self.sock.sendto(header + chunk, self.address)
            except OSError:
                return False
        return True

    def close(self) -> None:
        self.sock.close()


class PreviewReceiver:
    """Reassembles preview frames (used by tests and the debug viewer)."""

    def __init__(self, port: int, host: str = "0.0.0.0"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, int(port)))
        self.sock.setblocking(False)
        self._parts: dict[int, dict[int, bytes]] = {}

    def poll(self) -> np.ndarray | None:
        latest = None
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            if len(data) < PREVIEW_HEADER.size:
                continue
            magic, frame_id, index, count = PREVIEW_HEADER.unpack(data[:PREVIEW_HEADER.size])
            if magic != PREVIEW_MAGIC:
                continue
            parts = self._parts.setdefault(frame_id, {})
            parts[index] = data[PREVIEW_HEADER.size:]
            if len(parts) == count:
                payload = b"".join(parts[i] for i in range(count))
                del self._parts[frame_id]
                image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is not None:
                    latest = image
        if len(self._parts) > 8:   # drop stale incomplete frames
            self._parts.clear()
        return latest

    def close(self) -> None:
        self.sock.close()
