"""Bounded repeated sweeps, reusing the tested SlowScanner motion loop.

Only builds a longer adjacent-command path. No new register-writing logic,
power control, changed settling time, angle compensation, or exception cleanup.
One invocation is limited to 1..3 rounds. It normally starts at command 90.
An explicit known-held start_command allows starting after an input-cancelled
scan, ONLY with unchanged power/configuration and no manually moved servo.
Register readback alone cannot establish physical pose after a power cycle.
"""

import time
from radar_slow_scan import SlowScanner


class RepeatedScanner(SlowScanner):
    """For rounds=2: 90 -> low -> high -> low -> high -> 90.

    The inherited run() clears display history only at the beginning of the
    entire invocation. Endpoint observations remain required on their first
    encounter in that invocation unless confirm_endpoints=False is explicitly
    selected for the already physically verified range. It never jumps back
    to low between rounds. A non-90 start first steps toward low, without
    attempting to infer which leg of a previous invocation was interrupted.
    """

    def __init__(self, overlay, display, measure, low=0, high=180, rounds=2,
                 mmio_factory=None, sleep_fn=time.sleep, input_fn=input,
                 confirm_endpoints=True, start_command=90):
        if type(rounds) is not int or not 1 <= rounds <= 3:
            raise ValueError("Use a finite integer number of rounds: 1..3.")
        super().__init__(overlay, display, measure, mmio_factory=mmio_factory,
                         sleep_fn=sleep_fn, low=low, high=high, input_fn=input_fn,
                         confirm_endpoints=confirm_endpoints)
        if type(start_command) is not int or not low <= start_command <= high:
            raise ValueError("start_command must be an integer within the scan bounds.")
        self.start_command = start_command
        path = list(range(start_command, low-1, -1))
        for index in range(rounds):
            path.extend(range(low+1, high+1))
            if index < rounds-1:
                path.extend(range(high-1, low-1, -1))
            else:
                path.extend(range(high-1, 89, -1))
        self.PATH = tuple(path)
        self.rounds = rounds
