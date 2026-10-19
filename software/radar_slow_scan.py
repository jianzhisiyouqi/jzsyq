"""One bounded servo sweep for Echo's existing radar_lcd overlay.

Requires a known held starting command with unchanged FPGA configuration.
The GPIO readback is a command, NOT physical position feedback. No overlay
loading, clock changes, power switching, or automatic exception recentering.
"""

import math
import time
from PIL import ImageDraw
from radar_partial_refresh import FixedSensorDisplay


def _check_bounds(low, high):
    # Command limits, NOT a guarantee that this servo can reach both endpoints.
    if (type(low) is not int or type(high) is not int
            or not 0 <= low < 90 < high <= 180):
        raise ValueError("Use integer bounds: 0 <= low < 90 < high <= 180.")


class _ScanCancelled(Exception):
    pass


class SlowScanDisplay(FixedSensorDisplay):
    def __init__(self, lcd, current_frame, low=80, high=100):
        _check_bounds(low, high)
        super().__init__(lcd, current_frame)
        self.low, self.high = low, high
        self.command = 90
        self.points = {}

    def update_sample(self, command, distance_cm):
        if type(command) is not int or not self.low <= command <= self.high:
            raise ValueError(f"Scan command must be within {self.low}..{self.high}.")
        self.command = command
        if distance_cm is not None and math.isfinite(distance_cm) and 0 < distance_cm <= 120:
            self.points[command] = distance_cm
        else:
            # A failed/out-of-range new reading must not keep an old point at
            # that same command. Other directions retain their last readings.
            self.points.pop(command, None)
        return self.update(distance_cm)

    def render(self, distance_cm):
        frame = self.base.copy()
        draw = ImageDraw.Draw(frame)
        result = "TIMEOUT" if distance_cm is None else f"{distance_cm:.1f} cm"
        draw.rectangle((200, 2, 477, 39), fill=(2, 10, 7))
        draw.text((220, 8), result, font=self.large_font, fill=(60, 255, 140))
        draw.rectangle((10, 45, 215, 64), fill=(2, 10, 7))
        draw.text((13, 46), f"SLOW SCAN | CMD {self.command}",
                  font=self.small_font, fill=(113, 170, 144))

        # Command-based polar mapping; not calibrated physical-angle feedback.
        # 90 is up, smaller commands go right, matching the observed direction.
        for command, distance in self.points.items():
            angle = math.radians(command)
            radius = distance / 120 * 210
            x = 240 + round(radius * math.cos(angle))
            y = 283 - round(radius * math.sin(angle))
            color = (180, 255, 200) if command == self.command else (40, 190, 100)
            draw.ellipse((x-3, y-3, x+3, y+3), fill=color)

        draw.rectangle((10, 297, 468, 317), fill=(2, 10, 7))
        if distance_cm is None:
            footer = "NO ECHO | OTHER DOTS: PREVIOUS READINGS"
        elif not 0 < distance_cm <= 120:
            footer = "OUT OF RANGE | OTHER DOTS: PREVIOUS READINGS"
        else:
            footer = f"ONE PASS: 90 > {self.low} > {self.high} > 90"
        draw.text((12, 300), footer, font=self.small_font, fill=(113, 170, 144))
        return frame


class SlowScanner:
    """One finite path; configurable settling and optional sparse sampling.

    With sample_stride=1, all existing motion timing is unchanged. With larger
    strides, every command is still issued in unit steps, separated by at least
    move_seconds. Measured targets get a total final-command settling delay of
    at least settle_seconds before triggering the sensor. Endpoints and final
    position are always measured; skipped positions never get fabricated dots.

    Constructing this object only maps registers; it issues no servo commands.
    Start run() only after external power is on and the assembly is stable at
    the physically confirmed 90 reference. Never use it to recover unknown pose.
    Endpoint confirmations are enabled by default for compatibility. They may
    be explicitly disabled after the user has verified the installed range.
    Position consistency checks and unit-step moves remain active either way.
    """

    PATH = tuple(range(90, 79, -1)) + tuple(range(81, 101)) + tuple(range(99, 89, -1))

    def __init__(self, overlay, display, measure, mmio_factory=None,
                 sleep_fn=time.sleep, low=80, high=100, input_fn=input,
                 confirm_endpoints=True, settle_seconds=0.2,
                 sample_stride=1, move_seconds=0.03):
        _check_bounds(low, high)
        if not math.isfinite(settle_seconds) or not 0.1 <= settle_seconds <= 0.5:
            raise ValueError("settle_seconds must be within 0.1..0.5 seconds.")
        if type(confirm_endpoints) is not bool:
            raise ValueError("confirm_endpoints must be True or False.")
        if type(sample_stride) is not int or not 1 <= sample_stride <= 5:
            raise ValueError("sample_stride must be an integer in 1..5.")
        if not math.isfinite(move_seconds) or not 0.03 <= move_seconds <= 0.1:
            raise ValueError("move_seconds must be within 0.03..0.1 seconds.")
        if (getattr(display, "low", low), getattr(display, "high", high)) != (low, high):
            raise ValueError("Display and servo scan bounds must match.")
        self.PATH = (tuple(range(90, low-1, -1))
                     + tuple(range(low+1, high+1))
                     + tuple(range(high-1, 89, -1)))
        self.overlay = overlay
        self.display = display
        self.measure = measure
        self.sleep = sleep_fn
        self.settle_seconds = float(settle_seconds)
        self.sample_stride = sample_stride
        self.move_seconds = float(move_seconds)
        self.input = input_fn
        self.confirm_endpoints = confirm_endpoints
        self.start_command = 90
        if mmio_factory is None:
            from pynq import MMIO
            mmio_factory = MMIO
        info = overlay.ip_dict["axi_gpio_1"]
        self.gpio = mmio_factory(info["phys_addr"], info["addr_range"])
        self.last_command = None
        self.running = False
        self.samples_taken = 0

    def read_command(self):
        if not self.overlay.is_loaded():
            raise RuntimeError("Overlay changed; do not continue from the old position record.")
        return self.gpio.read(0x08) & 0xFF

    def run(self):
        if self.running:
            raise RuntimeError("A scan is already running.")
        if not self.display.valid:
            raise RuntimeError("Restore LCD initialization and a matching full frame first.")
        if self.read_command() != self.start_command:
            raise RuntimeError(f"Start command is not {self.start_command}. "
                               "No servo write has been issued.")
        self.last_command = self.start_command
        self.running = True
        self.samples_taken = 0
        self.display.points.clear()
        confirmed_edges = set()
        moves_since_sample = 0
        required_samples = {min(self.PATH),max(self.PATH),self.PATH[-1]}
        try:
            for index, command in enumerate(self.PATH, 1):
                if self.read_command() != self.last_command:
                    raise RuntimeError("Servo command changed outside this scan; stopped.")
                moved = command != self.last_command
                if moved:
                    if abs(command-self.last_command) != 1:
                        raise RuntimeError("Refusing a non-unit servo move.")
                    # No DATA0 writes here: only GPIO1 channel2 servo command.
                    self.gpio.write(0x08, command)
                    self.last_command = command
                    moves_since_sample += 1
                    move_wait = (self.settle_seconds if self.sample_stride == 1
                                 else self.move_seconds)
                    self.sleep(move_wait)
                if (self.confirm_endpoints and (command < 5 or command > 175)
                        and command not in confirmed_edges):
                    print(f"当前指令 {command}：这是尚未验证的端部位置。", flush=True)
                    print("若卡住、持续异常响声或牵拉，立即关闭MB102；取消输入不会关闭PWM。",
                          flush=True)
                    answer = self.input("确认实际运动正常，输入“正常”继续；其他输入结束：").strip()
                    if answer != "正常":
                        raise _ScanCancelled()
                    confirmed_edges.add(command)
                should_sample = (index == 1 or moves_since_sample >= self.sample_stride
                                 or command in required_samples)
                if not should_sample:
                    continue
                if moved and self.sample_stride > 1:
                    remaining_settle = self.settle_seconds-self.move_seconds
                    if remaining_settle > 0:
                        self.sleep(remaining_settle)
                # A human confirmation may have paused this loop for a while.
                if self.read_command() != command:
                    raise RuntimeError("Servo command changed while waiting; stopped.")
                distance = self.measure()
                # Detect replaced overlay/changed command before displaying a
                # result as belonging to this commanded direction.
                if self.read_command() != command:
                    raise RuntimeError("Servo command changed during measurement; stopped.")
                stats = self.display.update_sample(command, distance)
                moves_since_sample = 0
                self.samples_taken += 1
                result = "TIMEOUT" if distance is None else f"{distance:.1f} cm"
                print(f"{index}/{len(self.PATH)} | SAMPLE {self.samples_taken} | "
                      f"CMD {command} | {result} | "
                      f"LCD {stats['seconds']:.3f}s", flush=True)
            print(f"Scan complete. Last command {self.last_command}; no further movement.",
                  flush=True)
            return True
        except _ScanCancelled:
            print(f"已取消，最后发送指令 {self.last_command}，不自动回中。"
                  "FPGA仍可能输出PWM；如果机械异常，请关闭MB102。", flush=True)
            return False
        except BaseException:
            print(f"Stopped. Last software-issued command: {self.last_command}. "
                  "No return-to-center command sent. FPGA PWM may still be active.",
                  flush=True)
            raise
        finally:
            # Bookkeeping ONLY. In particular, no servo writes or recenter here.
            self.running = False
