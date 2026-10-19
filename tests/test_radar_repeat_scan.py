"""Mock-only tests for the repeated path and inherited safety behavior."""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "software"))
from radar_repeat_scan import RepeatedScanner
from test_radar_slow_scan import GPIO, Overlay, Display


class CountClears(dict):
    def __init__(self):
        super().__init__()
        self.clears = 0
    def clear(self):
        self.clears += 1
        super().clear()


class RepeatTests(unittest.TestCase):
    def make_scanner(self, rounds=2, command=90, low=0, high=180,
                     measure=lambda: 60.0, confirm_endpoints=True,
                     start_command=90):
        gpio, display, prompts = GPIO(command), Display(), []
        display.points = CountClears()
        scanner = RepeatedScanner(
            Overlay(), display, measure, low=low, high=high, rounds=rounds,
            mmio_factory=lambda *args: gpio,
            sleep_fn=lambda seconds: gpio.events.append(("sleep", seconds)),
            input_fn=lambda prompt: prompts.append(gpio.command) or "正常",
            confirm_endpoints=confirm_endpoints, start_command=start_command)
        return scanner, gpio, display, prompts

    def test_two_round_path_and_history_continuity(self):
        scanner, gpio, display, prompts = self.make_scanner()
        self.assertFalse(gpio.events)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        commands = tuple(c for c, d in display.samples)
        self.assertEqual(len(commands), 721)
        self.assertEqual([commands[i] for i in (0,90,270,450,630,720)],
                         [90,0,180,0,180,90])
        self.assertTrue(all(abs(a-b)==1 for a,b in zip(commands,commands[1:])))
        self.assertEqual(len(gpio.events), 1440)
        self.assertTrue(all(e == ("sleep", .2) for e in gpio.events[1::2]))
        self.assertEqual(display.points.clears, 1)
        self.assertEqual(prompts, [4,3,2,1,0,176,177,178,179,180])
        self.assertEqual(gpio.command, 90)

    def test_bounded_rounds_and_asymmetric_ranges(self):
        for low, high in ((0,180),(50,130),(80,110)):
            for rounds in (1,2,3):
                scanner, gpio, _, _ = self.make_scanner(rounds=rounds, low=low, high=high)
                self.assertEqual(len(scanner.PATH), 2*rounds*(high-low)+1)
                self.assertEqual(scanner.PATH[0], 90)
                self.assertEqual(scanner.PATH[-1], 90)
                self.assertEqual(min(scanner.PATH), low)
                self.assertEqual(max(scanner.PATH), high)
                self.assertTrue(all(abs(a-b)==1 for a,b in zip(scanner.PATH, scanner.PATH[1:])))
                self.assertFalse(gpio.events)
        for rounds in (0,-1,4,2.5,True):
            with self.assertRaises(ValueError):
                self.make_scanner(rounds=rounds)

    def test_interrupt_during_second_return_does_not_recenter(self):
        count = []
        def measure():
            count.append(1)
            if len(count) == 400:
                raise KeyboardInterrupt()
            return 60.0
        scanner, gpio, _, _ = self.make_scanner(measure=measure)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                scanner.run()
        self.assertEqual(gpio.command, 51)
        self.assertEqual(scanner.last_command, 51)
        self.assertEqual(len(gpio.events), 798)
        self.assertFalse(scanner.running)

    def test_unknown_start_refuses_without_movement(self):
        scanner, gpio, _, _ = self.make_scanner(command=85)
        with self.assertRaises(RuntimeError):
            scanner.run()
        self.assertFalse(gpio.events)

    def test_no_confirmation_input_and_no_jump_from_held4(self):
        scanner, gpio, display, prompts = self.make_scanner(
            command=4, start_command=4, confirm_endpoints=False)
        def forbidden_input(prompt):
            raise AssertionError("No terminal input should occur")
        scanner.input = forbidden_input
        self.assertFalse(gpio.events)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        commands = [c for c,d in display.samples]
        self.assertEqual(commands[:6], [4,3,2,1,0,1])
        self.assertEqual(len(commands), 635)
        self.assertTrue(all(abs(a-b)==1 for a,b in zip(commands,commands[1:])))
        self.assertEqual(len(gpio.events), 1268)
        self.assertTrue(all(e == ("sleep", .2) for e in gpio.events[1::2]))
        self.assertEqual(gpio.command, 90)
        self.assertFalse(prompts)

    def test_start_changes_before_run_are_still_rejected(self):
        scanner, gpio, _, _ = self.make_scanner(
            command=4, start_command=4, confirm_endpoints=False)
        gpio.command = 5
        with self.assertRaises(RuntimeError):
            scanner.run()
        self.assertFalse(gpio.events)

    def test_disabled_confirmations_still_leave_last_command_on_failure(self):
        calls = []
        def measure():
            calls.append(1)
            if len(calls) == 4:
                raise KeyboardInterrupt()
            return 60.0
        scanner, gpio, _, _ = self.make_scanner(
            command=4, start_command=4, confirm_endpoints=False, measure=measure)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                scanner.run()
        self.assertEqual(gpio.command, 1)
        self.assertEqual(scanner.last_command, 1)
        self.assertEqual([v for kind,v in gpio.events if kind == "write"], [3,2,1])

    def test_explicit_start_and_flag_validation(self):
        for command in (-1,181,4.5,True):
            with self.assertRaises(ValueError):
                self.make_scanner(start_command=command)
        for flag in (None,"False",0,1):
            with self.assertRaises(ValueError):
                self.make_scanner(confirm_endpoints=flag)

    def test_fading_integration_and_stationary_tail(self):
        from test_radar_fade_scan import Clock, LCD
        from radar_display import radar_background, rgb565_bytes
        from radar_fade_scan import FadingScanDisplay
        clock, gpio = Clock(), GPIO()
        frame = radar_background()
        lcd = LCD(frame)
        view = FadingScanDisplay(lcd, frame, clock_fn=clock)
        def measure():
            clock.advance(.065)
            return 60.0
        scanner = RepeatedScanner(Overlay(), view, measure,
                                  mmio_factory=lambda *args: gpio,
                                  sleep_fn=clock.advance,
                                  input_fn=lambda prompt: "正常")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        self.assertEqual(len(gpio.events), 720)
        before = list(gpio.events)
        view.fade_out(sleep_fn=clock.advance)
        self.assertEqual(gpio.events, before)
        self.assertEqual(gpio.command, 90)
        self.assertFalse(view.points)
        self.assertEqual(lcd.buffer.tobytes(), rgb565_bytes(frame))


if __name__ == "__main__":
    unittest.main()
