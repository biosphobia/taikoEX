"""Press keyboard keys so other games (osu!taiko) can be played with the drums.

On Windows we use ``SendInput`` with hardware scan codes, which is what
osu! expects.  Elsewhere we fall back to pynput when it is installed.
"""

from __future__ import annotations

import ctypes
import heapq
import sys
import threading
import time

_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_EXTENDEDKEY = 0x0001
    MAPVK_VK_TO_VSC = 0

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Keys that are not printable characters.
    _SPECIAL_VK = {
        "space": 0x20, "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "backspace": 0x08,
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
        "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    }
    _EXTENDED = {0x25, 0x26, 0x27, 0x28}

    def _scan_code(key: str) -> tuple[int, bool]:
        key = key.lower()
        if key in _SPECIAL_VK:
            vk = _SPECIAL_VK[key]
        elif len(key) == 1:
            vk = _user32.VkKeyScanW(ord(key)) & 0xFF
        else:
            raise ValueError(f"Unknown key name '{key}'")
        return _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC), vk in _EXTENDED

    def _send(key: str, down: bool) -> None:
        scan, extended = _scan_code(key)
        flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.union.ki = KEYBDINPUT(0, scan, flags, 0, None)
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

else:
    try:
        from pynput.keyboard import Controller as _PynputController, Key as _PynputKey
        _pynput = _PynputController()
    except Exception:  # pragma: no cover - optional dependency / no display
        _pynput = None

    def _send(key: str, down: bool) -> None:
        if _pynput is None:
            return
        target = getattr(_PynputKey, key.lower(), key) if len(key) > 1 else key
        if down:
            _pynput.press(target)
        else:
            _pynput.release(target)


class KeySender:
    """Taps keys with a fixed hold time.  Releases happen on a background thread."""

    def __init__(self, hold_ms: float = 35.0):
        self.hold_s = hold_ms / 1000.0
        self._pending: list[tuple[float, str]] = []
        self._lock = threading.Condition()
        self._thread = threading.Thread(target=self._release_loop, daemon=True)
        self._thread.start()
        self.enabled = True

    def tap(self, key: str) -> None:
        if not self.enabled or not key:
            return
        try:
            _send(key, True)
        except Exception as exc:
            print(f"[keys] could not press '{key}': {exc}")
            return
        with self._lock:
            heapq.heappush(self._pending, (time.time() + self.hold_s, key))
            self._lock.notify()

    def _release_loop(self) -> None:
        while True:
            with self._lock:
                while not self._pending:
                    self._lock.wait()
                due, key = self._pending[0]
                wait = due - time.time()
                if wait > 0:
                    self._lock.wait(wait)
                    continue
                heapq.heappop(self._pending)
            try:
                _send(key, False)
            except Exception:
                pass
