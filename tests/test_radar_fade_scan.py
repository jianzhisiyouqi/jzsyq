"""Desktop display tests; no physical servo or LCD access."""
import sys
import unittest
import contextlib
import io
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
from radar_display import radar_background, rgb565_bytes
from radar_fade_scan import FadingScanDisplay
from radar_slow_scan import SlowScanner


class Clock:
    now = 0.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class LCD:
    def __init__(self, frame):
        self.buffer = np.frombuffer(rgb565_bytes(frame), dtype=np.uint8).copy().reshape(320,480,2)
        self.fail = False
    def show_rgb565(self, data, x, y, width, height):
        if self.fail:
            raise RuntimeError("Injected SPI failure")
        self.buffer[y:y+height, x:x+width] = np.frombuffer(data, dtype=np.uint8).reshape(height,width,2)


class FadeTests(unittest.TestCase):
    def make_view(self):
        clock = Clock()
        frame = radar_background()
        lcd = LCD(frame)
        view = FadingScanDisplay(lcd, frame, fade_seconds=5, clock_fn=clock)
        return view, clock, lcd, frame

    def assert_synced(self, view, lcd, frame):
        expected = rgb565_bytes(view.render(view._last_distance))
        self.assertEqual(lcd.buffer.tobytes(), expected)
        self.assertEqual(rgb565_bytes(frame), expected)

    def test_real_time_fade_and_grid_restoration(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        brightest = frame.getpixel((240, 178))[1]
        clock.advance(2.5)
        view.refresh()
        self.assertLess(frame.getpixel((240, 178))[1], brightest)
        self.assert_synced(view, lcd, frame)
        clock.advance(2.5)
        view.refresh()
        self.assertEqual(view.points, {})
        self.assertEqual(frame.getpixel((240, 178)), view.base.getpixel((240, 178)))
        self.assert_synced(view, lcd, frame)

    def test_refresh_does_not_extend_lifetime(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        for step in range(1, 21):
            clock.now = step*.25
            view.refresh()
        self.assertFalse(view.points)
        self.assertFalse(view._seen_at)

    def test_revisited_angle_replaces_old_point_and_resets_age(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        clock.advance(4)
        view.update_sample(90, 30)
        self.assertEqual(view.points, {90: 30})
        clock.advance(1.1)
        view.refresh()
        self.assertIn(90, view.points)
        self.assert_synced(view, lcd, frame)
        clock.advance(4)
        view.refresh()
        self.assertFalse(view.points)

    def test_invalid_reading_clears_only_that_angle(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(89, 60)
        view.update_sample(90, 60)
        view.update_sample(90, None)
        self.assertNotIn(90, view.points)
        self.assertIn(89, view.points)
        view.update_sample(89, 121)
        self.assertFalse(view.points)
        self.assert_synced(view, lcd, frame)

    def test_long_pause_expires_history_on_resume(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(0, 120)
        clock.advance(20)
        view.update_sample(180, 120)
        self.assertEqual(view.points, {180: 120})
        self.assert_synced(view, lcd, frame)

    def test_no_changes_within_same_brightness_level(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        clock.advance(.01)
        self.assertEqual(view.refresh()["pixel_bytes"], 0)

    def test_tail_removes_all_dots_without_new_samples(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        view.fade_out(sleep_fn=clock.advance)
        self.assertFalse(view.points)
        self.assertLessEqual(clock.now, 5.25)
        self.assert_synced(view, lcd, frame)

    def test_transfer_failure_stays_invalid(self):
        view, clock, lcd, frame = self.make_view()
        view.update_sample(90, 60)
        clock.advance(3)
        lcd.fail = True
        with self.assertRaises(RuntimeError):
            view.refresh()
        self.assertFalse(view.valid)

    def test_bad_fade_duration(self):
        for seconds in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                FadingScanDisplay(None, radar_background(), fade_seconds=seconds)

    def test_full_scan_and_tail_issue_no_extra_servo_moves(self):
        from test_radar_slow_scan import GPIO, Overlay
        view, clock, lcd, frame = self.make_view()
        gpio = GPIO()
        def measure():
            clock.advance(.065)
            return 60.0
        scanner = SlowScanner(Overlay(), view, measure, low=0, high=180,
                              mmio_factory=lambda *args: gpio,
                              sleep_fn=clock.advance,
                              input_fn=lambda prompt: "正常")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        self.assertEqual(len(gpio.events), 360)
        self.assertEqual(gpio.command, 90)
        before = list(gpio.events)
        view.fade_out(sleep_fn=clock.advance)
        self.assertEqual(gpio.events, before)
        self.assertFalse(view.points)
        self.assert_synced(view, lcd, frame)


if __name__ == "__main__":
    unittest.main()
