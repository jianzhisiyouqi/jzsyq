"""Behavioral checks for SPI timing, FIFO boundaries, and safe LCD-only aborts.

The model deliberately reports TX_EMPTY before wire completion, while delaying
IPISR.DTR_EMPTY. This detects the common premature-inhibit/CS-release bug.
These are desktop model tests, not panel or FPGA hardware verification.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
import st7796_lcd as driver


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 0.0001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Overlay:
    loaded = True
    ip_dict = {
        "axi_quad_spi_0": {"phys_addr": 0x41E00000, "addr_range": 65536},
        "axi_gpio_2": {"phys_addr": 0x41200000, "addr_range": 65536},
    }

    def is_loaded(self):
        return self.loaded


class GPIO:
    def __init__(self):
        self.value = 0
        self.history = []

    def write(self, offset, value):
        assert offset == 0
        assert 0 <= value <= 7
        self.value = value
        self.history.append(value)


class SPI:
    def __init__(self, gpio):
        self.gpio = gpio
        self.control = 0
        self.events = 0
        self.cs = 1
        self.pending = []
        self.received = []
        self.active = False
        self.polls = 0
        self.wire_transfers = []
        self.max_fifo = 0
        self.hang = False
        self.drop_rx = False
        self.inject_error = False

    def write(self, offset, value):
        if offset == 0x40:
            assert value == 0x0A
            self.pending.clear()
            self.received.clear()
            self.events = 0
            self.active = False
        elif offset == 0x60:
            if self.active and value & 0x100 and not value & 0x60:
                raise AssertionError("Transfer inhibited before wire completion")
            if value & 0x60:
                self.pending.clear()
                self.received.clear()
                self.active = False
            self.control = value & ~0x60
            if not value & 0x100 and self.pending:
                assert not self.events & 4, "Old completion event was not cleared"
                assert self.cs == 0
                self.active = True
                self.polls = 0
        elif offset == 0x68:
            assert self.control & 0x100, "Expected staged FIFO filling"
            assert len(self.pending) < 16
            self.pending.append(value)
            self.max_fifo = max(self.max_fifo, len(self.pending))
        elif offset == 0x70:
            if value == 1 and self.active:
                raise AssertionError("CS released before wire completion")
            self.cs = value
        elif offset == 0x20:
            assert value & ~self.events == 0, "TOW write set unobserved event bits"
            self.events ^= value
        else:
            assert offset in (0x1C, 0x28)
            assert value == 0

    def read(self, offset):
        if offset == 0x60:
            return self.control
        if offset == 0x64:
            # TX_EMPTY is intentionally high even before the final byte finishes.
            return 0x24 | (0 if self.received else 1)
        if offset == 0x6C:
            return self.received.pop(0)
        assert offset == 0x20
        if self.active:
            if self.inject_error:
                self.events |= 1
            self.polls += 1
            if self.polls >= 3 and not self.hang and not self.inject_error:
                self.active = False
                self.wire_transfers.append((bool(self.gpio.value & 1),
                                            bytes(self.pending)))
                if not self.drop_rx:
                    self.received.extend([0] * len(self.pending))
                self.pending.clear()
                assert len(self.received) <= 16, "RX FIFO overflow"
                self.events |= 4
                if len(self.received) == 16:
                    self.events |= 0x10
        return self.events


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.real_time = driver.time
        driver.time = Clock()
        self.gpio = GPIO()
        self.spi = SPI(self.gpio)
        self.mapped_addresses = []
        self.overlay = Overlay()

        def factory(address, length):
            self.mapped_addresses.append(address)
            self.assertEqual(length, 65536)
            return {0x41E00000: self.spi, 0x41200000: self.gpio}[address]

        self.lcd = driver.ST7796LCD(self.overlay, timeout=0.002, mmio_factory=factory)

    def tearDown(self):
        driver.time = self.real_time

    def test_initialization_and_lcd_only_mapping(self):
        self.lcd.initialize()
        self.assertTrue(self.lcd._initialized)
        self.assertEqual(set(self.mapped_addresses), {0x41E00000, 0x41200000})
        self.assertTrue(self.gpio.value & 2)
        self.assertFalse(self.gpio.value & 4)
        self.assertEqual(self.spi.control & 0x18, 0x18)
        transfers = self.spi.wire_transfers
        self.assertEqual(transfers[0], (False, b"\x11"))
        self.assertEqual(transfers[-2:], [(False, b"\x36"), (True, b"\x28")])
        for command, parameter in ((0xC1, b"\x06"), (0xC5, b"\x18")):
            index = transfers.index((False, bytes((command,))))
            self.assertEqual(transfers[index + 1], (True, parameter))

    def test_pixel_window_byte_order_and_fifo_boundaries(self):
        self.lcd.initialize()
        self.spi.wire_transfers.clear()
        self.lcd.fill_rect(7, 9, 17, 1, 0xF800)
        transfers = self.spi.wire_transfers
        self.assertEqual(transfers[:5], [
            (False, b"\x2A"), (True, b"\x00\x07\x00\x17"),
            (False, b"\x2B"), (True, b"\x00\x09\x00\x09"),
            (False, b"\x2C"),
        ])
        self.assertEqual([len(payload) for _, payload in transfers[5:]], [16, 16, 2])
        self.assertEqual(b"".join(payload for _, payload in transfers[5:]), b"\xF8\x00" * 17)
        self.assertTrue(all(dc for dc, _ in transfers[5:]))
        self.assertEqual(self.spi.cs, 1)
        self.assertEqual(self.spi.max_fifo, 16)
        self.assertFalse(self.spi.received)

    def test_hung_transfer_times_out_and_resets_only_lcd(self):
        self.spi.hang = True
        with self.assertRaisesRegex(TimeoutError, "completion timed out"):
            self.lcd.initialize()
        self.assertEqual(self.gpio.value, 0)
        self.assertEqual(self.spi.cs, 1)
        self.assertFalse(self.spi.active)
        self.assertFalse(self.lcd._initialized)

    def test_receive_failure_is_bounded(self):
        self.spi.drop_rx = True
        with self.assertRaisesRegex(TimeoutError, "receive drain timed out"):
            self.lcd.initialize()
        self.assertEqual(self.gpio.value, 0)

    def test_mode_fault_is_reported(self):
        self.spi.inject_error = True
        with self.assertRaisesRegex(RuntimeError, "SPI transfer error"):
            self.lcd.initialize()
        self.assertEqual(self.gpio.value, 0)
        self.assertEqual(self.spi.cs, 1)

    def test_bounds_and_overlay_changes_rejected(self):
        self.lcd.initialize()
        before = len(self.spi.wire_transfers)
        with self.assertRaises(ValueError):
            self.lcd.show_rgb565(b"\x00\x00", x=480, y=0, width=1, height=1)
        with self.assertRaises(ValueError):
            self.lcd.show_rgb565(b"\x00", width=1, height=1)
        self.assertEqual(len(self.spi.wire_transfers), before)
        self.overlay.loaded = False
        with self.assertRaisesRegex(RuntimeError, "no longer loaded"):
            self.lcd.fill(0)

    def test_full_frame_batches_do_not_overflow_receive_fifo(self):
        self.lcd.initialize()
        self.spi.wire_transfers.clear()
        self.lcd.fill(0x07E0)
        transfers = self.spi.wire_transfers[5:]
        self.assertEqual(sum(len(data) for _, data in transfers), 480 * 320 * 2)
        self.assertTrue(all(data == b"\x07\xE0" * 8 for _, data in transfers))
        self.assertFalse(self.spi.received)


if __name__ == "__main__":
    unittest.main(verbosity=2)
