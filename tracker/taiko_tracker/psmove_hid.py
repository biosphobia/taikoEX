"""Talk to PS Move controllers over Bluetooth HID.

Pairing is done outside this project (psmove pair, PSMoveService, ...).
Once a controller is paired and connected it shows up as a HID device and
we can:

* set the sphere colour (the sphere stays dark until a host does this!)
* read buttons, trigger, battery level and the accelerometer / gyro

The report layout below is the one documented by the psmoveapi project.
"""

from __future__ import annotations

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

REPORT_SIZE = 49
REPORT_SET_LEDS = 0x02
LED_REFRESH_S = 3.0            # the controller turns the LED off if not refreshed for ~4-5 s

ACCEL_UNITS_PER_G = 4300.0     # raw accelerometer counts per 1 g (approximate, ZCM1)

# Button bits, in the 20 bit combined button word used by psmoveapi.
BUTTON_BITS = {
    "triangle": 1 << 4, "circle": 1 << 5, "cross": 1 << 6, "square": 1 << 7,
    "select": 1 << 8, "start": 1 << 11, "ps": 1 << 16, "move": 1 << 19, "t": 1 << 20,
}


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


def _decode_16(data: bytes, offset: int, twos_complement: bool) -> int:
    value = data[offset] | (data[offset + 1] << 8)
    if twos_complement:
        return value - 0x10000 if value & 0x8000 else value
    return value - 0x8000


class MoveController:
    """One connected controller."""

    def __init__(self, path: bytes, serial: str, product_id: int):
        self.device = hid.device()
        self.device.open_path(path)
        self.device.set_nonblocking(1)
        self.serial = serial
        self.product_id = product_id
        self.state = MoveState(serial=serial, connected=True)
        self.colour = (0, 0, 0)
        self.rumble = 0
        self._last_led_send = 0.0

    def set_led(self, r: int, g: int, b: int, rumble: int = 0, force: bool = False) -> None:
        colour = (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
        now = time.time()
        if not force and colour == self.colour and rumble == self.rumble and now - self._last_led_send < LED_REFRESH_S:
            return
        report = bytearray(REPORT_SIZE)
        report[0] = REPORT_SET_LEDS
        report[2], report[3], report[4] = colour
        report[6] = int(rumble) & 0xFF
        try:
            self.device.write(bytes(report))
            self.colour, self.rumble, self._last_led_send = colour, rumble, now
        except (OSError, ValueError):
            self.state.connected = False

    def poll(self) -> MoveState:
        """Drain pending input reports and return the newest state."""
        twos = self.product_id == PRODUCT_MOVE_ZCM2
        try:
            while True:
                data = self.device.read(REPORT_SIZE)
                if not data:
                    break
                if len(data) < 44:
                    continue
                buttons = (data[2] | (data[1] << 8) | ((data[3] & 0x01) << 16) | ((data[4] & 0xF0) << 13))
                self.state.buttons = buttons
                self.state.trigger = data[6]
                self.state.battery = data[12]
                # Two accelerometer frames per report; use the newer one (offset 19).
                ax = _decode_16(data, 19, twos) / ACCEL_UNITS_PER_G
                ay = _decode_16(data, 21, twos) / ACCEL_UNITS_PER_G
                az = _decode_16(data, 23, twos) / ACCEL_UNITS_PER_G
                self.state.accel_g = [ax, ay, az]
                self.state.gyro_raw = [_decode_16(data, 31, twos), _decode_16(data, 33, twos), _decode_16(data, 35, twos)]
                self.state.last_report_time = time.time()
        except (OSError, ValueError):
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
    """Keeps a controller connected for every slot in ``config["controllers"]``."""

    def __init__(self, config):
        self.config = config
        self.available = hid is not None
        self.controllers: dict[int, MoveController] = {}   # slot id -> controller
        self.leds_forced_off = False
        self._last_scan = 0.0

    def enabled(self) -> bool:
        return self.available and bool(self.config.get("hid.enabled", True))

    def list_devices(self) -> list[dict]:
        if not self.available:
            return []
        found = []
        for info in hid.enumerate(VENDOR_SONY, 0):
            if info["product_id"] in PRODUCTS:
                found.append({
                    "serial": info.get("serial_number") or "",
                    "path": info["path"].decode(errors="replace") if isinstance(info["path"], bytes) else str(info["path"]),
                    "product_id": info["product_id"],
                    "interface": info.get("interface_number", -1),
                })
        return found

    def update(self) -> None:
        """Reconnect missing controllers now and then, refresh LEDs, poll input."""
        if not self.enabled():
            return
        now = time.time()
        interval = float(self.config.get("hid.reconnect_interval_s", 3.0))
        for slot, controller in list(self.controllers.items()):
            if not controller.state.connected:
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
        used_serials = {c.serial for c in self.controllers.values()}
        devices = [d for d in self.list_devices() if d["serial"] not in used_serials]
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
                path = pick["path"].encode() if isinstance(pick["path"], str) else pick["path"]
                controller = MoveController(path, pick["serial"], pick["product_id"])
            except Exception as exc:  # device busy, permissions, ...
                print(f"[hid] could not open controller {pick['serial']}: {exc}")
                continue
            devices.remove(pick)
            self.controllers[slot] = controller
            print(f"[hid] slot {slot} -> controller {pick['serial'] or pick['path']}")

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
