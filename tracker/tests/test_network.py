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
