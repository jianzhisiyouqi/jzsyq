"""Mock tests only; no physical servo access."""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
from servo_center_trim import trim_center


class Overlay:
    ip_dict = {"axi_gpio_1": {"phys_addr": 0, "addr_range": 65536}}
    loaded = True
    def is_loaded(self):
        return self.loaded


class GPIO:
    def __init__(self, command=90):
        self.command = command
        self.events = []
    def read(self, offset):
        assert offset == 8
        return self.command
    def write(self, offset, value):
        assert offset == 8 and abs(value-self.command) == 1
        self.command = value
        self.events.append(("write", value))


class TrimTests(unittest.TestCase):
    def run_trim(self, actions, gpio=None, overlay=None, expected_start=90):
        gpio = gpio or GPIO()
        overlay = overlay or Overlay()
        iterator = iter(actions)
        with contextlib.redirect_stdout(io.StringIO()):
            result = trim_center(overlay, mmio_factory=lambda *args: gpio,
                                 input_fn=lambda prompt: next(iterator),
                                 sleep_fn=lambda seconds: gpio.events.append(("sleep", seconds)),
                                 expected_start=expected_start)
        return result, gpio

    def test_save_manual_steps_and_dwell(self):
        result, gpio = self.run_trim(["开始微调", "+", "+", "-", "save"])
        self.assertEqual(result, 91)
        self.assertEqual(gpio.command, 91)
        self.assertEqual(gpio.events, [("write",91),("sleep",.2),
                                       ("write",92),("sleep",.2),
                                       ("write",91),("sleep",.2)])

    def test_cancel_and_unknown_input_do_not_move(self):
        result, gpio = self.run_trim(["取消"])
        self.assertIsNone(result)
        self.assertFalse(gpio.events)
        result, gpio = self.run_trim(["开始微调", "95", "u", "q"])
        self.assertIsNone(result)
        self.assertFalse(gpio.events)

    def test_guard_unknown_start_without_writes(self):
        gpio = GPIO(95)
        with self.assertRaises(RuntimeError):
            self.run_trim([], gpio=gpio)
        self.assertFalse(gpio.events)

    def test_continue_from_86_without_return_to90(self):
        result, gpio = self.run_trim(["开始微调", "-", "+", "+", "save"],
                                     gpio=GPIO(86), expected_start=86)
        self.assertEqual(result, 87)
        self.assertEqual([v for kind,v in gpio.events if kind == "write"], [85,86,87])

    def test_changed_start_is_rejected_without_writes(self):
        gpio = GPIO(90)
        with self.assertRaises(RuntimeError):
            self.run_trim([], gpio=gpio, expected_start=86)
        self.assertFalse(gpio.events)

    def test_expected_start_validation(self):
        for expected in (79,101,86.0,True):
            gpio = GPIO(86)
            with self.assertRaises(ValueError):
                self.run_trim([], gpio=gpio, expected_start=expected)
            self.assertFalse(gpio.events)

    def test_both_boundaries_and_no_automatic_return(self):
        result, gpio = self.run_trim(["开始微调"] + ["+"]*12 + ["-"]*22 + ["q"])
        self.assertIsNone(result)
        self.assertEqual(gpio.command, 80)
        writes = [value for kind,value in gpio.events if kind == "write"]
        self.assertEqual(len(writes), 30)
        self.assertEqual(min(writes), 80)
        self.assertEqual(max(writes), 100)

    def test_interrupt_does_not_recenter(self):
        gpio = GPIO()
        actions = iter(["开始微调", "+"])
        def read_input(prompt):
            try:
                return next(actions)
            except StopIteration:
                raise KeyboardInterrupt()
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                trim_center(Overlay(), mmio_factory=lambda *args: gpio,
                            input_fn=read_input, sleep_fn=lambda seconds: None)
        self.assertEqual(gpio.events, [("write",91)])

    def test_external_command_change_before_write_is_rejected(self):
        gpio = GPIO()
        calls = []
        def read_input(prompt):
            calls.append(1)
            if len(calls) == 1:
                return "开始微调"
            gpio.command = 92
            return "+"
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                trim_center(Overlay(), mmio_factory=lambda *args: gpio,
                            input_fn=read_input, sleep_fn=lambda seconds: None)
        self.assertFalse(gpio.events)

    def test_replaced_overlay_after_input_is_rejected(self):
        gpio, overlay = GPIO(), Overlay()
        calls = []
        def read_input(prompt):
            calls.append(1)
            if len(calls) == 1:
                return "开始微调"
            overlay.loaded = False
            return "+"
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                trim_center(overlay, mmio_factory=lambda *args: gpio,
                            input_fn=read_input, sleep_fn=lambda seconds: None)
        self.assertFalse(gpio.events)


if __name__ == "__main__":
    unittest.main()
