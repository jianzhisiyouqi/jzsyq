"""Notebook sequencing tests with fake hardware, never a PYNQ connection."""
import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np  # Import before patch.dict(sys.modules) snapshots it.

OUTPUTS = Path(__file__).resolve().parents[1] / "software"
sys.path.insert(0, str(OUTPUTS))
import st7796_lcd
import radar_fast_scan
import echo_native_spi


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((OUTPUTS / "radar_stable_demo.ipynb").read_text(encoding="utf-8"))
        self.cells = ["".join(c["source"]) for c in self.doc["cells"] if c["cell_type"] == "code"]
        self.events = []
        self.command = 90
        self.mode = "normal"
        test = self

        class Overlay:
            ip_dict = {name: {"phys_addr": index, "addr_range": 65536}
                       for index, name in enumerate(("axi_gpio_0", "axi_gpio_1", "axi_gpio_2", "axi_quad_spi_0"))}
            def __init__(self, path):
                test.events.append(("load", path))
                test.command = 90
            def is_loaded(self):
                return True

        class GPIO:
            def __init__(self, address, length):
                test.assertEqual(address, 1)
            def write(self, offset, value):
                test.assertEqual(offset, 8)
                test.events.append(("write", value))
                test.command = value
            def read(self, offset):
                test.assertEqual(offset, 8)
                return test.command

        class LCD:
            def __init__(self, overlay):
                test.events.append(("lcd",))
            def initialize(self):
                test.events.append(("initialize",))
            def show_rgb565(self, data):
                test.assertEqual(len(data), 480*320*2)
                test.events.append(("image",))
            def backlight(self, enabled):
                test.events.append(("backlight", enabled))

        class Display:
            def __init__(self, lcd, frame, center_command, fade_seconds):
                self.lcd = lcd
                test.assertEqual(center_command, 84)
                test.assertEqual(fade_seconds, 5)
            def fade_out(self):
                test.events.append(("fade",))

        class Scanner:
            def __init__(self, ol, display, measure, **options):
                test.assertEqual(options, dict(center_command=84, rounds=2, sample_stride=3,
                                              move_seconds=.03, settle_seconds=.10))
                self.display = display
                self.running = False
                self.samples_taken = 0
            def read_command(self):
                return test.command
            def run(self):
                test.events.append(("scan",))
                if test.command != 84:
                    raise RuntimeError("Start command mismatch")
                if test.mode == "interrupt":
                    test.command = 70
                    raise KeyboardInterrupt()
                if test.mode == "error":
                    test.command = 70
                    raise RuntimeError("mock transfer error")
                self.samples_taken = 241
                return True

        fake_pynq = types.ModuleType("pynq")
        fake_pynq.Overlay = Overlay
        fake_pynq.MMIO = GPIO
        fake_pynq.Clocks = types.SimpleNamespace(fclk0_mhz=125.0)
        self.fake_pynq = fake_pynq
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.dict(sys.modules, {"pynq": fake_pynq}))
        self.stack.enter_context(patch.object(st7796_lcd, "ST7796LCD", LCD))
        self.stack.enter_context(patch.object(radar_fast_scan, "CenteredFadingDisplay", Display))
        self.stack.enter_context(patch.object(radar_fast_scan, "CenteredFastScanner", Scanner))
        self.stack.enter_context(patch.object(radar_fast_scan, "IntervalRangefinder", lambda ol: object()))
        self.stack.enter_context(patch.object(echo_native_spi, "enable_native_spi",
                                             lambda lcd: setattr(lcd, "_echo_native_backend", object())))
        self.stack.enter_context(patch("time.sleep", lambda duration: None))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.namespace = {}

    def tearDown(self):
        self.stack.close()

    def run_cell(self, index, answer=""):
        # Only substitute the board folder in test code. The delivered notebook
        # keeps its real PYNQ absolute path and uses no desktop path.
        source = self.cells[index].replace("/home/xilinx/jupyter_notebooks/radar", OUTPUTS.as_posix())
        with patch("builtins.input", return_value=answer):
            exec(compile(source, f"notebook_cell_{index+1}", "exec"), self.namespace)

    def prepare(self):
        self.run_cell(0)
        self.run_cell(1, "MB102已关闭")
        self.run_cell(2)

    def test_format_and_python_syntax(self):
        self.assertEqual(self.doc["nbformat"], 4)
        self.assertEqual(len(self.cells), 5)
        for cell in self.doc["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                ast.parse("".join(cell["source"]))

    def test_check_only_does_not_access_hardware(self):
        self.run_cell(0)
        self.assertEqual(self.events, [])

    def test_cancel_before_overlay_means_no_hardware_access(self):
        self.run_cell(0)
        with self.assertRaises(RuntimeError):
            self.run_cell(1, "cancel")
        self.assertEqual(self.events, [])
        self.assertFalse(self.namespace["_prepared"])

    def test_cold_start_and_repeat_uses_same_scanner_without_reload(self):
        self.prepare()
        self.assertEqual([e for e in self.events if e[0] == "write"], [("write", 84)])
        self.assertNotIn(("scan",), self.events)
        self.run_cell(3, "上电稳定")
        self.run_cell(4)
        self.run_cell(4)
        self.assertEqual(sum(e[0] == "load" for e in self.events), 1)
        self.assertEqual(self.events.count(("scan",)), 2)
        self.assertEqual(self.events.count(("fade",)), 2)

    def test_scan_before_power_confirmation_refused(self):
        self.prepare()
        with self.assertRaises(RuntimeError):
            self.run_cell(4)
        self.assertNotIn(("scan",), self.events)

    def test_lcd_reinitialize_after_power_confirmation_refused(self):
        self.prepare()
        self.run_cell(3, "上电稳定")
        count = self.events.count(("initialize",))
        with self.assertRaises(RuntimeError):
            self.run_cell(2)
        self.assertEqual(self.events.count(("initialize",)), count)

    def test_bad_clock_stops_before_center_write(self):
        self.run_cell(0)
        self.fake_pynq.Clocks.fclk0_mhz = 100
        with self.assertRaises(RuntimeError):
            self.run_cell(1, "MB102已关闭")
        self.assertFalse(any(e[0] == "write" for e in self.events))
        self.assertFalse(self.namespace["_prepared"])

    def test_interrupt_and_error_never_recenter_or_fade_or_allow_retry(self):
        self.prepare()
        for mode, exception in (("interrupt", KeyboardInterrupt), ("error", RuntimeError)):
            self.command = 84
            self.run_cell(3, "上电稳定")
            self.mode = mode
            previous = list(self.events)
            with self.assertRaises(exception):
                self.run_cell(4)
            self.assertEqual(self.command, 70)
            self.assertFalse(self.namespace["_power_ready"])
            self.assertEqual(self.events[len(previous):], [("scan",)])
            with self.assertRaises(RuntimeError):
                self.run_cell(4)


if __name__ == "__main__":
    unittest.main()
