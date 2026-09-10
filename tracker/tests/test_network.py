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


def test_a_stuck_tracker_holding_the_port_is_killed():
    """A tracker wedged in a camera read never answers 'quit'; if the process
    on the port is recognisably a tracker it is killed and the port taken."""
    import subprocess
    import sys

    # A stand-in for a hung tracker: binds the port and ignores everything.
    # Its command line carries "run_tracker" so it is recognised as one.
    holder = subprocess.Popen([sys.executable, "-c",
                               "import socket, time, sys; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
                               "s.bind(('0.0.0.0', 47895)); print('bound', flush=True); time.sleep(60)",
                               "run_tracker.py"], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "bound"
        receiver = CommandReceiver(47895, takeover_s=3.0)
        receiver.close()
        assert holder.wait(timeout=5) != 0       # it was killed, not left running
    finally:
        if holder.poll() is None:
            holder.kill()


def test_a_stranger_on_the_port_is_left_alone():
    import subprocess
    import sys

    import pytest

    stranger = subprocess.Popen([sys.executable, "-c",
                                 "import socket, time; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
                                 "s.bind(('0.0.0.0', 47894)); print('bound', flush=True); time.sleep(60)"],
                                stdout=subprocess.PIPE, text=True)
    try:
        assert stranger.stdout.readline().strip() == "bound"
        with pytest.raises(RuntimeError, match="already in use"):
            CommandReceiver(47894, takeover_s=1.0)
        assert stranger.poll() is None           # still alive
    finally:
        stranger.kill()
