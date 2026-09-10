import time

import numpy as np

from taiko_tracker.network import CommandReceiver, PreviewReceiver, PreviewSender, StateSender


def test_preview_round_trip():
    receiver = PreviewReceiver(47899)
    sender = PreviewSender("127.0.0.1", 47899)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :, 2] = 200
    assert sender.maybe_send(frame, fps=30, width=640, quality=95)
    image = None
    for _ in range(50):
        image = receiver.poll()
        if image is not None:
            break
        time.sleep(0.01)
    assert image is not None and image.shape == (480, 640, 3)
    assert image[240, 320, 2] > 180


def test_command_and_reply():
    receiver = CommandReceiver(47898)
    client = StateSender("127.0.0.1", 47898)
    client.send({"cmd": "ping"})
    messages = []
    for _ in range(50):
        messages = receiver.poll()
        if messages:
            break
        time.sleep(0.01)
    assert messages and messages[0][0]["cmd"] == "ping"


def test_command_port_is_taken_over_from_a_stale_tracker():
    """A tracker left running by a previous game session is asked to quit and
    gives its port up; the new one starts instead of dying with 'port in use'."""
    import threading

    stale = CommandReceiver(47897)
    quit_seen = threading.Event()

    def serve():
        while not quit_seen.is_set():
            for message, _ in stale.poll():
                if message.get("cmd") == "quit":
                    stale.close()
                    quit_seen.set()
            time.sleep(0.02)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    fresh = CommandReceiver(47897, takeover_s=3.0)
    assert quit_seen.is_set()
    fresh.close()
    thread.join(timeout=2)


def test_command_port_held_by_something_else_fails_loudly():
    import pytest

    other = CommandReceiver(47896)
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            CommandReceiver(47896, takeover_s=0.5)
    finally:
        other.close()
