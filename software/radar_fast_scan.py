"""Moderately faster scanning with the user's measured center command84.

Hardware command range stays 0..180. Only the center is calibrated. The display
keeps the original horizontal semicircle. Each side is normalized separately:
command0 -> display0, measured center -> display90, command180 -> display180.
These are schematic display coordinates, NOT calibrated physical angles away
from the measured center. No endpoint samples are clamped or discarded.
No overlay loads, clock changes, or power-on/default configuration changes.
"""

import math
import time
from radar_slow_scan import SlowScanner
from radar_fade_scan import FadingScanDisplay


class CenteredFadingDisplay(FadingScanDisplay):
    def __init__(self, lcd, current_frame, center_command=84, fade_seconds=5.0,
                 clock_fn=time.monotonic):
        if type(center_command) is not int or not 80 <= center_command <= 100:
            raise ValueError("center_command must be a measured command in 80..100.")
        super().__init__(lcd, current_frame, low=0, high=180,
                         fade_seconds=fade_seconds, clock_fn=clock_fn)
        self.center_command = center_command
        self.command = center_command

    def direction(self, command):
        if command <= self.center_command:
            return 90*command/self.center_command
        return 90 + 90*(command-self.center_command)/(180-self.center_command)

    def _point_position(self, command, distance):
        angle = math.radians(self.direction(command))
        radius = distance/120*210
        return (240 + round(radius*math.cos(angle)),
                283 - round(radius*math.sin(angle)))

    def _status_label(self):
        return f"REL~{self.direction(self.command):.0f} | CMD {self.command}"

    @classmethod
    def from_previous(cls, previous):
        """Replace display layout without resampling, resetting dot ages, or
        changing the servo. Caller must first wait until scanner.running=False.
        The existing LCD/current frame must still be initialized and valid.
        """
        if not previous.valid:
            raise RuntimeError("LCD state is invalid; restore initialization first.")
        view = cls(previous.lcd,previous.current,
                   center_command=previous.center_command,
                   fade_seconds=previous.fade_seconds,clock_fn=previous.clock)
        view.command = previous.command
        view.points = previous.points.copy()
        view._seen_at = previous._seen_at.copy()
        view._last_distance = previous._last_distance
        return view


class IntervalRangefinder:
    """Same HC-SR04 conversion, but wait only until a 65ms interval is met.

    Constructor performs no register writes. The first trigger also waits65ms
    because the previous notebook function's trigger timestamp is unknown.
    No servo channel writes: GPIO0 DATA0=start, DATA8=status; GPIO1 DATA0=count.
    """
    def __init__(self, overlay, mmio_factory=None, clock_fn=time.monotonic,
                 sleep_fn=time.sleep):
        self.overlay = overlay
        self.clock = clock_fn
        self.sleep = sleep_fn
        if not overlay.is_loaded():
            raise RuntimeError("Overlay is no longer loaded.")
        if mmio_factory is None:
            from pynq import MMIO
            mmio_factory = MMIO
        ctrl = overlay.ip_dict["axi_gpio_0"]
        count = overlay.ip_dict["axi_gpio_1"]
        self.ctrl = mmio_factory(ctrl["phys_addr"],ctrl["addr_range"])
        self.count = mmio_factory(count["phys_addr"],count["addr_range"])
        self._last_trigger = self.clock()

    def __call__(self):
        if not self.overlay.is_loaded():
            raise RuntimeError("Overlay changed; stop measuring.")
        self.ctrl.write(0,0)
        while self.clock()-self._last_trigger < .065:
            self.sleep(max(.0001,.065-(self.clock()-self._last_trigger)))
        if not self.overlay.is_loaded():
            raise RuntimeError("Overlay changed while waiting; stop measuring.")
        if self.ctrl.read(8) & 1:
            raise RuntimeError("HC-SR04 is still busy; no new trigger sent.")
        try:
            self.ctrl.write(0,1)
            # Timestamp AFTER the write: never underestimate trigger interval.
            self._last_trigger = self.clock()
            self.sleep(.001)
        finally:
            self.ctrl.write(0,0)  # HC start only; never the servo command.
        deadline = self.clock()+.12
        status = 0
        while self.clock() < deadline:
            if not self.overlay.is_loaded():
                raise RuntimeError("Overlay changed during measurement.")
            status = self.ctrl.read(8)&7
            if status == 2:
                return self.count.read(0)*34300.0/(2*125_000_000)
            if status == 4:
                return None
            self.sleep(.001)
        raise TimeoutError(f"HC-SR04 completion timeout; status={status:03b}.")


class CenteredFastScanner(SlowScanner):
    """Finite 1..3 rounds, command84 reference; optional sparse measurements.

    Starts only if the register still equals the user-confirmed held center.
    Does not attempt startup recovery and does not automatically recenter on
    errors. Endpoint input is disabled only for the already verified range.
    """
    def __init__(self, overlay, display, measure, center_command=84, rounds=2,
                 settle_seconds=.1, mmio_factory=None, sleep_fn=time.sleep,
                 sample_stride=1, move_seconds=.03):
        if type(center_command) is not int or not 80 <= center_command <= 100:
            raise ValueError("center_command must be in 80..100.")
        if type(rounds) is not int or not 1 <= rounds <= 3:
            raise ValueError("rounds must be a finite integer in 1..3.")
        if getattr(display,"center_command",None) != center_command:
            raise ValueError("Motion and display center commands must match.")
        super().__init__(overlay,display,measure,low=0,high=180,
                         mmio_factory=mmio_factory,sleep_fn=sleep_fn,
                         confirm_endpoints=False,settle_seconds=settle_seconds,
                         sample_stride=sample_stride,move_seconds=move_seconds)
        self.start_command = center_command
        path = list(range(center_command,-1,-1))
        for index in range(rounds):
            path.extend(range(1,181))
            path.extend(range(179,-1 if index < rounds-1 else center_command-1,-1))
        self.PATH = tuple(path)
