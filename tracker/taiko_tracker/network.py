"""UDP link between the tracker and the Godot game.

Three sockets, all on localhost by default:

* state    tracker -> game : one JSON object per camera frame
* commands game -> tracker : JSON ``{"cmd": ...}``; replies go back to the sender
* preview  tracker -> game : JPEG frames, split into numbered fragments

See docs/PROTOCOL.md for the message formats.
"""

from __future__ import annotations

import json
import socket
import struct
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


class CommandReceiver:
    """The tracker's command port.

    Deliberately *not* set to reuse the address: if another tracker is already
    running, this must fail loudly.  Two trackers sharing one port would each
    receive some of the commands, and the calibration would quietly be applied
    to whichever one happened to get the packet.
    """

    def __init__(self, port: int, host: str = "0.0.0.0"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((host, int(port)))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is already in use - another tracker is probably running. "
                f"Stop it, or set network.command_port to something else.") from exc
        self.sock.setblocking(False)

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
