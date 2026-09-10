"""PS Move HID report layouts, checked against what psmoveapi does."""
from taiko_tracker.psmove_hid import (LED_REPORT_ID, LED_REPORT_SIZE, PRODUCT_MOVE_ZCM1, PRODUCT_MOVE_ZCM2,
                                      decode_input_report, led_report, usable_on_this_platform)


def put16(data: bytearray, offset: int, value: int) -> None:
    value &= 0xFFFF
    data[offset] = value & 0xFF
    data[offset + 1] = value >> 8


def test_zcm1_report_averages_two_offset_frames():
    data = bytearray(49)
    data[1], data[2] = 0x08, 0x40          # start (bit 11) and cross (bit 6)
    data[5], data[12] = 200, 4
    for frame in (13, 19):                 # accelerometer, both half frames
        put16(data, frame, 0x8000 + 4300)
        put16(data, frame + 2, 0x8000 - 100)
        put16(data, frame + 4, 0x8000)
    for frame in (25, 31):                 # gyro, both half frames
        put16(data, frame, 0x8000 + 50)
        put16(data, frame + 2, 0x8000)
        put16(data, frame + 4, 0x8000 - 7)
    decoded = decode_input_report(data, PRODUCT_MOVE_ZCM1)
    assert decoded["buttons"] == (1 << 11) | (1 << 6)
    assert decoded["trigger"] == 200 and decoded["battery"] == 4
    assert decoded["accel"] == [4300, -100, 0]
    assert decoded["gyro"] == [50, 0, -7]


def test_zcm2_report_is_one_twos_complement_frame():
    data = bytearray(44)
    data[5], data[12] = 10, 5
    put16(data, 13, 4300)
    put16(data, 15, -100)
    put16(data, 25, 50)
    put16(data, 29, -7)
    decoded = decode_input_report(data, PRODUCT_MOVE_ZCM2)
    assert decoded["accel"] == [4300, -100, 0]
    assert decoded["gyro"] == [50, 0, -7]
    assert decoded["trigger"] == 10 and decoded["battery"] == 5


def test_led_report_is_exactly_what_windows_accepts():
    report = led_report(255, 0, 128, rumble=3)
    assert len(report) == LED_REPORT_SIZE == 9
    assert report[0] == LED_REPORT_ID == 0x06
    assert report[1] == 0 and report[2:5] == bytes([255, 0, 128]) and report[6] == 3
    assert led_report(1, 2, 3, report_id=2)[0] == 2


def test_only_the_first_hid_collection_is_used_on_windows(monkeypatch):
    import taiko_tracker.psmove_hid as module

    monkeypatch.setattr(module.sys, "platform", "win32")
    assert usable_on_this_platform(r"\\?\hid#{00001124}_vid&0002054c_pid&03d5&col01#9&1")
    assert not usable_on_this_platform(r"\\?\hid#{00001124}_vid&0002054c_pid&03d5&col02#9&1")
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert usable_on_this_platform("/dev/hidraw3")
