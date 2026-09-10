"""Talk to PS Move controllers over Bluetooth HID.

Pairing is done outside this project (psmove pair, PSMoveService, ...).
Once a controller is paired and connected it shows up as a HID device and
we can:

* set the sphere colour (the sphere stays dark until a host does this!)
* read buttons, trigger, battery level and the accelerometer / gyro

The report layouts below follow the psmoveapi project, which is the
reference for this hardware.  Two things about Windows are worth knowing,
because both make a controller look "paired but not connected":

* Windows lists every PS Move three times, once per HID collection.  Only
  the ``&col01#`` entry carries the input reports and accepts the LED
  report; the other two open fine and then do nothing.
* Only one program can hold a controller.  While PSMoveService (or its
  config tool) is running, opening the controller fails with an access
  error - close it once the pairing is done.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

try:
    import hid  # pip install hidapi
except ImportError:  # pragma: no cover
    hid = None

VENDOR_SONY = 0x054C
PRODUCT_MOVE_ZCM1 = 0x03D5     # original PS3 Move
PRODUCT_MOVE_ZCM2 = 0x0C5E     # PS4 era Move (CECH-ZCM2)
PRODUCTS = (PRODUCT_MOVE_ZCM1, PRODUCT_MOVE_ZCM2)

INPUT_REPORT_SIZE = 49         # ZCM1 reports are 49 bytes, ZCM2 reports 44
LED_REPORT_ID = 0x06           # psmoveapi's PSMove_Req_SetLEDs
LED_REPORT_SIZE = 9            # id, zero, r, g, b, rumble2, rumble, padding - exactly this long
LED_REFRESH_S = 3.0            # the controller turns the LED off if not refreshed for ~4-5 s

ACCEL_UNITS_PER_G = 4300.0     # raw accelerometer counts per 1 g (approximate, ZCM1)

# Button bits, in the 20 bit combined button word used by psmoveapi.
BUTTON_BITS = {
    "triangle": 1 << 4, "circle": 1 << 5, "cross": 1 << 6, "square": 1 << 7,
    "select": 1 << 8, "start": 1 << 11, "ps": 1 << 16, "move": 1 << 19, "t": 1 << 20,
}

# Byte offsets in the input report (psmoveapi's PSMove_Data_Input_Common).
OFFSET_TRIGGER = 5
OFFSET_BATTERY = 12
OFFSET_ACCEL_FRAME1 = 13       # x, y, z as 16 bit pairs
OFFSET_ACCEL_FRAME2 = 19
OFFSET_GYRO_FRAME1 = 25
OFFSET_GYRO_FRAME2 = 31


@dataclass
class MoveState:
    serial: str = ""
    connected: bool = False
    buttons: int = 0
    trigger: int = 0
    battery: int = 0
    accel_g: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    gyro_raw: list = field(default_factory=lambda: [0, 0, 0])
    last_report_time: float = 0.0

    def pressed(self, name: str) -> bool:
        return bool(self.buttons & BUTTON_BITS[name])


def _raw_16(data, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def decode_triplet(data, offset: int, model: int) -> list[int]:
    """Three 16-bit sensor values starting at ``offset``, as psmoveapi reads them.

    The original controller (ZCM1) sends two half-frames per report and
    stores each value with a 0x8000 offset; the two are averaged.  The PS4
    era controller (ZCM2) sends one frame as plain two's complement.
    """
    values = []
    for axis in range(3):
        at = offset + 2 * axis
        if model == PRODUCT_MOVE_ZCM2:
            value = _raw_16(data, at)
            values.append(value - 0x10000 if value & 0x8000 else value)
        else:
            frame1 = _raw_16(data, at)
            frame2 = _raw_16(data, at + 6)
            values.append((frame1 + frame2) // 2 - 0x8000)
    return values


def decode_input_report(data, model: int) -> dict:
    """Buttons, trigger, battery, accelerometer and gyro from one input report."""
    buttons = data[2] | (data[1] << 8) | ((data[3] & 0x01) << 16) | ((data[4] & 0xF0) << 13)
    return {
        "buttons": buttons,
        "trigger": data[OFFSET_TRIGGER],
        "battery": data[OFFSET_BATTERY],
        "accel": decode_triplet(data, OFFSET_ACCEL_FRAME1, model),
        "gyro": decode_triplet(data, OFFSET_GYRO_FRAME1, model),
    }


def led_report(r: int, g: int, b: int, rumble: int = 0, report_id: int = LED_REPORT_ID) -> bytes:
    """The output report that sets the sphere colour.

    Exactly ``LED_REPORT_SIZE`` bytes: Windows refuses a write whose length
    does not match the report the device declares.
    """
    report = bytearray(LED_REPORT_SIZE)
    report[0] = int(report_id) & 0xFF
    report[2], report[3], report[4] = int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF
    report[6] = int(rumble) & 0xFF
    return bytes(report)


def usable_on_this_platform(path: str) -> bool:
    """Whether a HID entry is the one that actually talks to the controller."""
    if sys.platform == "win32":
        return "&col01#" in path.lower()
    return True


class MoveController:
    """One connected controller."""

    def __init__(self, path: bytes, serial: str, product_id: int, led_report_id: int = LED_REPORT_ID):
        self.device = hid.device()
        self.device.open_path(path)
        self.device.set_nonblocking(1)
        self.path = path.decode(errors="replace") if isinstance(path, bytes) else str(path)
        self.serial = serial
        self.product_id = product_id
        self.led_report_id = led_report_id
        self.state = MoveState(serial=serial, connected=True)
        self.colour = (0, 0, 0)
        self.rumble = 0
        self._last_led_send = 0.0
        self.last_error = ""

    def set_led(self, r: int, g: int, b: int, rumble: int = 0, force: bool = False) -> None:
        colour = (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
        now = time.time()
        if not force and colour == self.colour and rumble == self.rumble and now - self._last_led_send < LED_REFRESH_S:
            return
        try:
            self.device.write(led_report(*colour, rumble, self.led_report_id))
            self.colour, self.rumble, self._last_led_send = colour, rumble, now
        except (OSError, ValueError) as exc:
            self.last_error = f"LED write failed: {exc}"
            self.state.connected = False

    def poll(self) -> MoveState:
        """Drain pending input reports and return the newest state."""
        try:
            while True:
                data = self.device.read(INPUT_REPORT_SIZE)
                if not data:
                    break
                if len(data) < 44:
                    continue
                decoded = decode_input_report(data, self.product_id)
                self.state.buttons = decoded["buttons"]
                self.state.trigger = decoded["trigger"]
                self.state.battery = decoded["battery"]
                self.state.accel_g = [v / ACCEL_UNITS_PER_G for v in decoded["accel"]]
                self.state.gyro_raw = decoded["gyro"]
                self.state.last_report_time = time.time()
        except (OSError, ValueError) as exc:
            self.last_error = f"read failed: {exc}"
            self.state.connected = False
        return self.state

    def close(self) -> None:
        try:
            self.set_led(0, 0, 0, force=True)
            self.device.close()
        except Exception:
            pass
        self.state.connected = False


class MoveManager:
    """Keeps a controller connected for every slot in ``config["controllers"]``.

    ``status`` is a one-line explanation of the current situation - "2
    connected", "no PS Move found", "found 2, open failed: ..." - meant to be
    shown in the game, so that "not connected" always comes with a reason.
    """

    def __init__(self, config):
        self.config = config
        self.available = hid is not None
        self.controllers: dict[int, MoveController] = {}   # slot id -> controller
        self.leds_forced_off = False
        self._last_scan = 0.0
        self.last_error = ""
        self.status = "hidapi is not installed (pip install hidapi)" if not self.available else "not scanned yet"

    def enabled(self) -> bool:
        return self.available and bool(self.config.get("hid.enabled", True))

    def list_devices(self) -> list[dict]:
        """Every PS Move Windows / Linux / macOS knows about, usable or not."""
        if not self.available:
            return []
        found = []
        for info in hid.enumerate(VENDOR_SONY, 0):
            if info["product_id"] not in PRODUCTS:
                continue
            path = info["path"].decode(errors="replace") if isinstance(info["path"], bytes) else str(info["path"])
            found.append({
                "serial": info.get("serial_number") or "",
                "path": path,
                "product_id": info["product_id"],
                "model": "ZCM2" if info["product_id"] == PRODUCT_MOVE_ZCM2 else "ZCM1",
                "interface": info.get("interface_number", -1),
                "usable": usable_on_this_platform(path),
            })
        return found

    def update(self) -> None:
        """Reconnect missing controllers now and then, refresh LEDs, poll input."""
        if not self.enabled():
            if self.available:
                self.status = "switched off (hid.enabled)"
            return
        now = time.time()
        interval = float(self.config.get("hid.reconnect_interval_s", 3.0))
        for slot, controller in list(self.controllers.items()):
            if not controller.state.connected:
                self.last_error = controller.last_error or "connection lost"
                controller.close()
                del self.controllers[slot]
        if now - self._last_scan > interval:
            self._last_scan = now
            self._connect_missing()
        brightness = 0.0 if self.leds_forced_off else float(self.config.get("hid.led_brightness", 1.0))
        for slot_cfg in self.config["controllers"]:
            controller = self.controllers.get(int(slot_cfg["id"]))
            if controller is None:
                continue
            r, g, b = (int(round(c * brightness)) for c in slot_cfg["led"])
            controller.set_led(r, g, b)
            controller.poll()

    def _connect_missing(self) -> None:
        used_paths = {c.path for c in self.controllers.values()}
        all_devices = self.list_devices()
        devices = [d for d in all_devices if d["usable"] and d["path"] not in used_paths]
        led_report_id = int(self.config.get("hid.led_report_id", LED_REPORT_ID))
        open_error = ""
        for slot_cfg in self.config["controllers"]:
            slot = int(slot_cfg["id"])
            if slot in self.controllers:
                continue
            wanted = str(slot_cfg.get("hid_serial", "")).strip()
            pick = None
            for dev in devices:
                if wanted and dev["serial"] != wanted:
                    continue
                pick = dev
                break
            if pick is None:
                continue
            try:
                controller = MoveController(pick["path"].encode(), pick["serial"], pick["product_id"], led_report_id)
            except Exception as exc:  # device busy, permissions, ...
                open_error = str(exc)
                print(f"[hid] could not open controller {pick['serial'] or pick['path']}: {exc}")
                devices.remove(pick)
                continue
            devices.remove(pick)
            self.controllers[slot] = controller
            print(f"[hid] slot {slot} -> {pick['model']} controller {pick['serial'] or pick['path']}")
        self._update_status(all_devices, open_error)

    def _update_status(self, all_devices: list[dict], open_error: str) -> None:
        wanted = len(self.config["controllers"])
        connected = len(self.controllers)
        usable = sum(1 for d in all_devices if d["usable"])
        if connected >= wanted:
            self.status = f"{connected} connected"
        elif open_error:
            self.last_error = open_error
            self.status = (f"{connected} of {wanted} connected; found {usable} but could not open one: {open_error} "
                           f"- is PSMoveService (or another program) still holding it?")
        elif not all_devices:
            self.status = (f"{connected} of {wanted} connected; no PS Move found over Bluetooth HID "
                           f"- pair it, press its PS button, and close PSMoveService")
        elif usable == 0:
            self.status = (f"{connected} of {wanted} connected; {len(all_devices)} HID entries but none is the "
                           f"'&col01#' collection this needs")
        else:
            self.status = f"{connected} of {wanted} connected; found {usable} usable device(s)"

    def set_all_leds(self, on: bool) -> None:
        """Force every sphere on or off.  Used while learning the background."""
        self.leds_forced_off = not on
        brightness = float(self.config.get("hid.led_brightness", 1.0)) if on else 0.0
        for slot_cfg in self.config["controllers"]:
            controller = self.controllers.get(int(slot_cfg["id"]))
            if controller is None:
                continue
            r, g, b = (int(round(c * brightness)) for c in slot_cfg["led"])
            controller.set_led(r, g, b, force=True)

    def state_for(self, slot: int) -> MoveState | None:
        controller = self.controllers.get(slot)
        return controller.state if controller else None

    def close(self) -> None:
        for controller in self.controllers.values():
            controller.close()
        self.controllers.clear()
