"""Mock motion, sensor interval, and framebuffer checks; no board access."""
import contextlib
import io
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"software"))
from radar_fast_scan import CenteredFastScanner, CenteredFadingDisplay, IntervalRangefinder
from radar_display import radar_background, rgb565_bytes
from test_radar_slow_scan import GPIO, Overlay, Display
from test_radar_fade_scan import Clock, LCD


class SensorOverlay:
    ip_dict = {"axi_gpio_0":{"phys_addr":0,"addr_range":65536},
               "axi_gpio_1":{"phys_addr":1,"addr_range":65536}}
    loaded = True
    def is_loaded(self):
        return self.loaded


class SensorCtrl:
    def __init__(self,clock,terminal=2):
        self.clock = clock
        self.terminal = terminal
        self.triggers = []
        self.writes = []
        self.forced = None
    def write(self,offset,value):
        assert offset == 0 and value in (0,1)
        self.writes.append(value)
        if value == 1:
            self.triggers.append(self.clock())
    def read(self,offset):
        assert offset == 8
        if self.forced is not None:
            return self.forced
        if not self.triggers:
            return 2
        return 1 if self.clock()-self.triggers[-1] < .004 else self.terminal


class Count:
    reads = 0
    def read(self,offset):
        assert offset == 0
        self.reads += 1
        return 150000


class FastTests(unittest.TestCase):
    def make_rangefinder(self,terminal=2):
        clock, overlay = Clock(), SensorOverlay()
        ctrl, count = SensorCtrl(clock,terminal), Count()
        rf = IntervalRangefinder(overlay,
            mmio_factory=lambda address,size: ctrl if address==0 else count,
            clock_fn=clock,sleep_fn=clock.advance)
        return rf,clock,ctrl,count

    def test_sensor_spacing_and_no_redundant65ms_wait(self):
        rf,clock,ctrl,count = self.make_rangefinder()
        self.assertFalse(ctrl.writes)
        self.assertAlmostEqual(rf(),20.58)
        self.assertGreaterEqual(ctrl.triggers[0],.065)
        self.assertAlmostEqual(rf(),20.58)
        self.assertGreaterEqual(ctrl.triggers[1]-ctrl.triggers[0],.065)
        clock.advance(.1)
        before = clock()
        rf()
        self.assertEqual(ctrl.triggers[-1],before)
        self.assertLess(clock()-before,.01)
        self.assertEqual(ctrl.writes[-1],0)

    def test_sensor_timeout_never_reads_stale_count(self):
        rf,clock,ctrl,count = self.make_rangefinder(terminal=4)
        self.assertIsNone(rf())
        self.assertEqual(count.reads,0)

    def test_host_timeout_is_bounded(self):
        rf,clock,ctrl,count = self.make_rangefinder(terminal=0)
        with self.assertRaises(TimeoutError):
            rf()
        self.assertLess(clock(),.2)
        self.assertEqual(count.reads,0)

    def test_busy_does_not_retrigger(self):
        rf,clock,ctrl,count = self.make_rangefinder()
        ctrl.forced = 1
        with self.assertRaises(RuntimeError):
            rf()
        self.assertFalse(ctrl.triggers)

    def test_unit_moves_center84_and100ms_wait(self):
        gpio, display = GPIO(84), Display()
        display.center_command = 84
        scanner = CenteredFastScanner(Overlay(),display,lambda:60.0,
            mmio_factory=lambda *args:gpio,
            sleep_fn=lambda seconds:gpio.events.append(("sleep",seconds)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        commands = [c for c,d in display.samples]
        self.assertEqual(len(commands),721)
        self.assertEqual([commands[i] for i in (0,84,264,444,624,720)],
                         [84,0,180,0,180,84])
        self.assertTrue(all(abs(a-b)==1 for a,b in zip(commands,commands[1:])))
        self.assertTrue(all(e==("sleep",.1) for e in gpio.events[1::2]))
        self.assertEqual(len(gpio.events),1440)
        self.assertEqual(gpio.command,84)

    def test_old90_start_rejected_without_move(self):
        gpio, display = GPIO(90), Display()
        display.center_command = 84
        scanner = CenteredFastScanner(Overlay(),display,lambda:60.0,
                                      mmio_factory=lambda *args:gpio)
        with self.assertRaises(RuntimeError):
            scanner.run()
        self.assertFalse(gpio.events)

    def test_interrupt_does_not_move_back_to_either_center(self):
        gpio, display, calls = GPIO(84), Display(), []
        display.center_command = 84
        def measure():
            calls.append(1)
            if len(calls)==4:
                raise KeyboardInterrupt()
            return 60.0
        scanner = CenteredFastScanner(Overlay(),display,measure,
            mmio_factory=lambda *args:gpio,sleep_fn=lambda seconds:None)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                scanner.run()
        self.assertEqual(gpio.command,81)
        self.assertEqual([v for kind,v in gpio.events if kind=="write"],[83,82,81])

    def test_center_and_endpoints_are_inside_plot_and_restore_grid(self):
        frame = radar_background()
        lcd, clock = LCD(frame), Clock()
        display = CenteredFadingDisplay(lcd,frame,clock_fn=clock)
        self.assertEqual(display.base.tobytes(),radar_background().tobytes())
        self.assertEqual(display.direction(0),0)
        self.assertEqual(display.direction(84),90)
        self.assertEqual(display.direction(180),180)
        self.assertEqual(display._point_position(84,120),(240,73))
        self.assertEqual(display._point_position(0,120),(450,283))
        self.assertEqual(display._point_position(180,120),(30,283))
        for command in range(181):
            x,y = display._point_position(command,120)
            self.assertTrue(3<=x<=476 and 65<=y<=293)
        for command,distance in ((84,60),(0,120),(180,120),(84,None)):
            display.update_sample(command,distance)
            self.assertEqual(lcd.buffer.tobytes(),rgb565_bytes(frame))
        clock.advance(6)
        display.refresh()
        self.assertFalse(display.points)
        self.assertEqual(lcd.buffer.tobytes(),rgb565_bytes(display.render(None)))

    def test_layout_replacement_keeps_sample_age_and_does_not_resample(self):
        frame, clock = radar_background(), Clock()
        lcd = LCD(frame)
        previous = CenteredFadingDisplay(lcd,frame,clock_fn=clock)
        previous.update_sample(84,60)
        clock.advance(2)
        replacement = CenteredFadingDisplay.from_previous(previous)
        self.assertEqual(replacement._seen_at,previous._seen_at)
        self.assertEqual(replacement._last_distance,60)
        self.assertIs(replacement.current,frame)
        replacement.refresh()
        self.assertEqual(lcd.buffer.tobytes(),rgb565_bytes(frame))
        clock.advance(3)
        replacement.refresh()
        self.assertFalse(replacement.points)
        previous.valid = False
        with self.assertRaises(RuntimeError):
            CenteredFadingDisplay.from_previous(previous)

    def test_settle_validation(self):
        display = Display()
        display.center_command = 84
        for seconds in (0,.05,-1,.6,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                CenteredFastScanner(Overlay(),display,lambda:None,
                    settle_seconds=seconds,mmio_factory=lambda *args:GPIO(84))

    def test_integrated_sensor_motion_display_and_tail(self):
        class CombinedGPIO(GPIO):
            def read(self,offset):
                return 150000 if offset==0 else super().read(offset)
        clock, overlay = Clock(), SensorOverlay()
        ctrl, gpio = SensorCtrl(clock), CombinedGPIO(84)
        def factory(address,size):
            return ctrl if address==0 else gpio
        frame = radar_background()
        lcd = LCD(frame)
        display = CenteredFadingDisplay(lcd,frame,clock_fn=clock)
        rf = IntervalRangefinder(overlay,mmio_factory=factory,
                                clock_fn=clock,sleep_fn=clock.advance)
        scanner = CenteredFastScanner(overlay,display,rf,rounds=1,
                                       mmio_factory=factory,sleep_fn=clock.advance)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        self.assertEqual(len(ctrl.triggers),361)
        self.assertTrue(all(b-a >= .065 for a,b in zip(ctrl.triggers,ctrl.triggers[1:])))
        self.assertEqual(len(gpio.events),360)
        self.assertEqual(gpio.command,84)
        before = list(gpio.events)
        display.fade_out(sleep_fn=clock.advance)
        self.assertEqual(gpio.events,before)
        self.assertEqual(lcd.buffer.tobytes(),rgb565_bytes(frame))
        self.assertFalse(display.points)

    def test_stride3_keeps_unit_writes_and_final_target_settling(self):
        clock = Clock()
        class TimedGPIO(GPIO):
            def __init__(self):
                super().__init__(84)
                self.write_times = []
            def write(self,offset,value):
                super().write(offset,value)
                self.write_times.append(clock())
        gpio, display, observed = TimedGPIO(), Display(), []
        display.center_command = 84
        def measure():
            if gpio.write_times:
                self.assertGreaterEqual(clock()-gpio.write_times[-1],.1-1e-10)
            observed.append(gpio.command)
            return 60.0
        scanner = CenteredFastScanner(Overlay(),display,measure,
            mmio_factory=lambda *args:gpio,sleep_fn=clock.advance,
            sample_stride=3,move_seconds=.03,settle_seconds=.1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        self.assertEqual(observed,list(scanner.PATH[::3]))
        self.assertEqual(scanner.samples_taken,241)
        writes = [v for kind,v in gpio.events if kind=="write"]
        self.assertEqual(writes,list(scanner.PATH[1:]))
        self.assertEqual(gpio.command,84)
        self.assertEqual(display.samples,[(c,60.0) for c in observed])

    def test_stride3_endpoints_and_center_with_non_multiple3_centers(self):
        for center in (80,83,86,100):
            gpio, display = GPIO(center), Display()
            display.center_command = center
            scanner = CenteredFastScanner(Overlay(),display,lambda:60,
                center_command=center,rounds=1,sample_stride=3,
                mmio_factory=lambda *args:gpio,sleep_fn=lambda seconds:None)
            with contextlib.redirect_stdout(io.StringIO()):
                scanner.run()
            measured = [c for c,d in display.samples]
            self.assertEqual(measured[0],center)
            self.assertEqual(measured[-1],center)
            self.assertIn(0,measured)
            self.assertIn(180,measured)
            self.assertTrue(all(abs(a-b)<=3 for a,b in zip(measured,measured[1:])))
            self.assertEqual(gpio.command,center)

    def test_stride3_interruption_never_returns_to_center(self):
        gpio, display, count = GPIO(84), Display(), []
        display.center_command = 84
        def measure():
            count.append(1)
            if len(count)==4:
                raise KeyboardInterrupt()
            return 60
        scanner = CenteredFastScanner(Overlay(),display,measure,sample_stride=3,
            mmio_factory=lambda *args:gpio,sleep_fn=lambda seconds:None)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                scanner.run()
        self.assertEqual(gpio.command,75)
        self.assertEqual(scanner.last_command,75)
        self.assertEqual([v for kind,v in gpio.events if kind=="write"],list(range(83,74,-1)))

    def test_stride3_integrated_sensor_spacing_and_framebuffer(self):
        class CombinedGPIO(GPIO):
            def read(self,offset):
                return 150000 if offset==0 else super().read(offset)
        clock, overlay = Clock(), SensorOverlay()
        ctrl, gpio = SensorCtrl(clock), CombinedGPIO(84)
        def factory(address,size):
            return ctrl if address==0 else gpio
        frame = radar_background()
        lcd = LCD(frame)
        display = CenteredFadingDisplay(lcd,frame,clock_fn=clock)
        rf = IntervalRangefinder(overlay,mmio_factory=factory,
                                clock_fn=clock,sleep_fn=clock.advance)
        scanner = CenteredFastScanner(overlay,display,rf,rounds=1,sample_stride=3,
                                       mmio_factory=factory,sleep_fn=clock.advance)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(scanner.run())
        self.assertEqual(len(ctrl.triggers),121)
        self.assertTrue(all(b-a>=.065 for a,b in zip(ctrl.triggers,ctrl.triggers[1:])))
        self.assertEqual(len(gpio.events),360)
        self.assertEqual(gpio.command,84)
        self.assertEqual(lcd.buffer.tobytes(),rgb565_bytes(frame))

    def test_stride_parameters_are_bounded(self):
        display = Display()
        display.center_command = 84
        for stride in (0,-1,6,True,3.0):
            with self.assertRaises(ValueError):
                CenteredFastScanner(Overlay(),display,lambda:60,sample_stride=stride,
                                     mmio_factory=lambda *args:GPIO(84))
        for seconds in (0,.02,.11,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                CenteredFastScanner(Overlay(),display,lambda:60,move_seconds=seconds,
                                     mmio_factory=lambda *args:GPIO(84))


if __name__ == "__main__":
    unittest.main()
