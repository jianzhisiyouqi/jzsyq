"""Desktop-only tests: command path, no jump on failures, image bounds."""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
from radar_display import radar_background, rgb565_bytes
from radar_slow_scan import SlowScanner, SlowScanDisplay


class Overlay:
    ip_dict = {"axi_gpio_1": {"phys_addr": 0, "addr_range": 65536}}
    def is_loaded(self):
        return True


class GPIO:
    def __init__(self, command=90):
        self.command = command
        self.events = []
    def read(self, offset):
        assert offset == 8
        return self.command
    def write(self, offset, value):
        assert offset == 8
        assert abs(value-self.command) == 1
        self.command = value
        self.events.append(("write", value))


class Display:
    valid = True
    def __init__(self):
        self.points = {}
        self.samples = []
    def update_sample(self, command, distance):
        self.samples.append((command, distance))
        return {"seconds": 0}


class ScanTests(unittest.TestCase):
    def make_scanner(self, measure=lambda: 24.5, command=90, low=80, high=100,
                     input_fn=lambda prompt: "正常"):
        gpio = GPIO(command)
        display = Display()
        scanner = SlowScanner(Overlay(), display, measure,
                              mmio_factory=lambda *args: gpio,
                              sleep_fn=lambda seconds: gpio.events.append(("sleep", seconds)),
                              low=low, high=high, input_fn=input_fn)
        return scanner, gpio, display

    def test_one_pass(self):
        scanner, gpio, display = self.make_scanner()
        self.assertEqual(gpio.events, [])
        with contextlib.redirect_stdout(io.StringIO()):
            scanner.run()
        self.assertEqual(len(display.samples), 41)
        self.assertEqual(tuple(c for c, d in display.samples), scanner.PATH)
        self.assertEqual(len(gpio.events), 80)
        for write, sleep in zip(gpio.events[::2], gpio.events[1::2]):
            self.assertEqual(write[0], "write")
            self.assertEqual(sleep, ("sleep", .2))
        self.assertEqual(gpio.command, 90)

    def test_non90_refuses_without_writing(self):
        scanner, gpio, _ = self.make_scanner(command=89)
        with self.assertRaises(RuntimeError):
            scanner.run()
        self.assertEqual(gpio.events, [])

    def test_50_130_path(self):
        scanner, gpio, display = self.make_scanner(low=50, high=130)
        with contextlib.redirect_stdout(io.StringIO()):
            scanner.run()
        commands = [c for c, d in display.samples]
        self.assertEqual(len(commands), 161)
        self.assertEqual(commands[0:2], [90, 89])
        self.assertEqual(commands[40], 50)
        self.assertEqual(commands[120], 130)
        self.assertEqual(commands[-1], 90)
        self.assertTrue(all(abs(a-b) == 1 for a,b in zip(commands, commands[1:])))
        self.assertEqual(len(gpio.events), 320)
        self.assertTrue(all(e == ("sleep", .2) for e in gpio.events[1::2]))

    def test_wide_pass_interruption_no_recenter(self):
        calls = []
        def measure():
            calls.append(1)
            if len(calls) == 45:
                raise KeyboardInterrupt()
            return 24.5
        scanner, gpio, _ = self.make_scanner(measure, low=50, high=130)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                scanner.run()
        self.assertEqual(gpio.command, 54)
        self.assertEqual(scanner.last_command, 54)

    def test_reject_outside_authorized_range(self):
        for low, high in ((-1, 180), (0, 181), (90, 180), (0, 90)):
            with self.assertRaises(ValueError):
                self.make_scanner(low=low, high=high)

    def test_full_range_with_endpoint_gates(self):
        prompts = []
        scanner, gpio, display = self.make_scanner(
            low=0, high=180,
            input_fn=lambda prompt: prompts.append(gpio.command) or "正常")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        commands = [c for c, d in display.samples]
        self.assertEqual(len(commands), 361)
        self.assertEqual(commands[0:2], [90, 89])
        self.assertEqual(commands[90], 0)
        self.assertEqual(commands[270], 180)
        self.assertEqual(commands[-1], 90)
        self.assertTrue(all(abs(a-b) == 1 for a,b in zip(commands, commands[1:])))
        self.assertEqual(prompts, [4,3,2,1,0,176,177,178,179,180])
        self.assertEqual(len(gpio.events), 720)
        self.assertTrue(all(e == ("sleep", .2) for e in gpio.events[1::2]))

    def test_endpoint_cancellation_does_not_return(self):
        scanner, gpio, display = self.make_scanner(low=0, high=180,
                                                  input_fn=lambda prompt: "取消")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(scanner.run())
        self.assertEqual(gpio.command, 4)
        self.assertEqual(scanner.last_command, 4)
        self.assertEqual(display.samples[-1][0], 5)
        self.assertEqual(gpio.events[-2], ("write", 4))
        self.assertFalse(scanner.running)

    def test_measurement_failure_does_not_return(self):
        for error_type in (RuntimeError, KeyboardInterrupt):
            with self.subTest(error=error_type):
                calls = []
                def measure():
                    calls.append(1)
                    if len(calls) == 4:
                        raise error_type("Injected")
                    return 24.5
                scanner, gpio, _ = self.make_scanner(measure)
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(error_type):
                        scanner.run()
                self.assertEqual(gpio.command, 87)
                self.assertEqual(scanner.last_command, 87)
                self.assertFalse(scanner.running)
                self.assertEqual([v for kind, v in gpio.events if kind == "write"], [89, 88, 87])

    def test_display_framebuffer(self):
        import numpy as np
        class LCD:
            def __init__(self, frame):
                self.buffer = np.frombuffer(rgb565_bytes(frame), dtype=np.uint8).copy().reshape(320,480,2)
            def show_rgb565(self, data, x, y, width, height):
                self.buffer[y:y+height, x:x+width] = np.frombuffer(data, dtype=np.uint8).reshape(height,width,2)
        frame = radar_background()
        lcd = LCD(frame)
        view = SlowScanDisplay(lcd, frame, low=0, high=180)
        for command, distance in ((90, 24.5), (89, 30), (0, 120), (180, .1),
                                  (90, None), (89, 180), (90, 40)):
            view.update_sample(command, distance)
            self.assertEqual(lcd.buffer.tobytes(), rgb565_bytes(view.render(distance)))
        self.assertNotIn(89, view.points)
        self.assertEqual(view.points[90], 40)


if __name__ == "__main__":
    unittest.main()
