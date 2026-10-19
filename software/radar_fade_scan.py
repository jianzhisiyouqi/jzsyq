"""Time-based fading target dots, using the existing bounded servo scanner.

Only the display changes. No servo writes, overlay loading, clock changes, or
angle offset compensation. Dots are past measurements at command-scale rays,
not physical angle feedback or live occupancy of every displayed direction.
"""

import math
import time
from PIL import Image, ImageDraw
from radar_slow_scan import SlowScanDisplay


class FadingScanDisplay(SlowScanDisplay):
    """Fade each real sample over fade_seconds; expire it and restore the grid.

    Rendering occurs on each measurement/update, not in a background thread.
    While waiting for a terminal input there are no LCD updates. When updates
    resume, real elapsed time still determines expiry. fade_out() finishes the
    animation after a normally completed scan, without any further servo moves.
    """

    def __init__(self, lcd, current_frame, low=0, high=180,
                 fade_seconds=5.0, clock_fn=time.monotonic):
        if not math.isfinite(fade_seconds) or fade_seconds <= 0:
            raise ValueError("fade_seconds must be positive and finite.")
        super().__init__(lcd, current_frame, low=low, high=high)
        self.fade_seconds = float(fade_seconds)
        self.clock = clock_fn
        self._seen_at = {}
        self._last_distance = None

    def update_sample(self, command, distance_cm):
        if type(command) is not int or not self.low <= command <= self.high:
            raise ValueError(f"Scan command must be within {self.low}..{self.high}.")
        if distance_cm is not None and math.isfinite(distance_cm) and 0 < distance_cm <= 120:
            self._seen_at[command] = self.clock()
        else:
            self._seen_at.pop(command, None)
        self._last_distance = distance_cm
        return super().update_sample(command, distance_cm)

    def _prune(self, now):
        for command in list(self._seen_at):
            if (command not in self.points
                    or now-self._seen_at[command] >= self.fade_seconds):
                self._seen_at.pop(command, None)
                self.points.pop(command, None)

    def _point_position(self, command, distance):
        angle = math.radians(command)
        radius = distance/120*210
        return (240 + round(radius*math.cos(angle)),
                283 - round(radius*math.sin(angle)))

    def _status_label(self):
        return f"FADE {self.fade_seconds:g}s | CMD {self.command}"

    def render(self, distance_cm):
        now = self.clock()
        self._prune(now)
        frame = self.base.copy()
        draw = ImageDraw.Draw(frame)
        result = "TIMEOUT" if distance_cm is None else f"{distance_cm:.1f} cm"
        draw.rectangle((200, 2, 477, 39), fill=(2, 10, 7))
        draw.text((220, 8), result, font=self.large_font, fill=(60, 255, 140))
        draw.rectangle((10, 45, 230, 64), fill=(2, 10, 7))
        draw.text((13, 46), self._status_label(),
                  font=self.small_font, fill=(113, 170, 144))

        # Quantized alpha avoids resending every dot for tiny color changes.
        # Composite over the actual grid, so fading never erases its lines.
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        dots = ImageDraw.Draw(layer)
        for command in sorted(self.points, key=self._seen_at.__getitem__):
            age = max(0.0, now-self._seen_at[command])
            level = max(1, min(8, math.ceil((1-age/self.fade_seconds)*8)))
            alpha = round(255*level/8)
            x, y = self._point_position(command, self.points[command])
            dots.ellipse((x-3, y-3, x+3, y+3), fill=(90, 255, 150, alpha))
        frame = Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((10, 297, 468, 317), fill=(2, 10, 7))
        status = ("NO ECHO" if distance_cm is None else
                  "OUT OF RANGE" if not 0 < distance_cm <= 120 else "LAST SAMPLE")
        draw.text((12, 300), f"{status} | DOTS: RECENT HISTORY",
                  font=self.small_font, fill=(113, 170, 144))
        return frame

    def refresh(self):
        """Age dots without creating a new sample or changing the servo."""
        return self.update(self._last_distance)

    def fade_out(self, interval=0.25, sleep_fn=time.sleep):
        """Only call after normal completion; no exception-return-to-center."""
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval must be positive and finite.")
        while self.points:
            self.refresh()
            if self.points:
                sleep_fn(interval)
