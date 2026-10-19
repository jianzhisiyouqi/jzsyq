"""Desktop framebuffer checks; no FPGA or physical LCD access."""
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
from radar_display import radar_background, rgb565_bytes
from radar_partial_refresh import FixedSensorDisplay


class FakeLCD:
    def __init__(self, frame):
        self.buffer = np.frombuffer(rgb565_bytes(frame), dtype=np.uint8).copy().reshape(320, 480, 2)
        self.fail = False

    def show_rgb565(self, pixels, x, y, width, height):
        if self.fail:
            raise RuntimeError("Injected transfer failure")
        self.buffer[y:y+height, x:x+width] = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 2)


class PartialRefreshTests(unittest.TestCase):
    def test_motion_timeout_range_and_identical_frame(self):
        current = radar_background()
        lcd = FakeLCD(current)
        view = FixedSensorDisplay(lcd, current)
        for distance in (24.5, 24.5, 100.0, 0.1, 120.0, None, 180.0, -1.0, 60.0):
            with self.subTest(distance=distance):
                stats = view.update(distance)
                expected = rgb565_bytes(view.render(distance))
                self.assertEqual(lcd.buffer.tobytes(), expected)
                self.assertEqual(rgb565_bytes(current), expected)
                self.assertLess(stats["pixel_bytes"], 307200)
                print(distance, stats["pixel_bytes"], stats["rectangles"])
        self.assertEqual(view.update(60.0)["pixel_bytes"], 0)

    def test_previous_full_cell_frame(self):
        seed = radar_background()
        lcd = FakeLCD(seed)
        view = FixedSensorDisplay(lcd, seed)
        view.update(24.5)
        stats = view.update(25.0)
        self.assertLess(stats["pixel_bytes"], 10000)
        self.assertEqual(lcd.buffer.tobytes(), rgb565_bytes(view.render(25.0)))

    def test_failure_invalidates_shadow(self):
        seed = radar_background()
        lcd = FakeLCD(seed)
        view = FixedSensorDisplay(lcd, seed)
        lcd.fail = True
        with self.assertRaises(RuntimeError):
            view.update(24.5)
        self.assertFalse(view.valid)
        lcd.fail = False
        with self.assertRaises(RuntimeError):
            view.update(24.5)


if __name__ == "__main__":
    unittest.main()
