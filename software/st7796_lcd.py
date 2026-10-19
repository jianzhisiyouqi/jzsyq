"""ST7796 / 35002 LCD driver for this project's radar_lcd overlay.

Requires axi_quad_spi_0: Standard SPI, 8-bit words, one slave, FIFO depth 16.
Requires axi_gpio_2: three outputs: bit0 DC, bit1 RESET, bit2 backlight.
Uses only those two IPs. Does not load an overlay or control the servo.

Initialization follows the supplied STM32F1_ST7796_IPS_4SPI_CTP Demo_V1
HARDWARE/LCD/LCD.c, including C1=06, C5=18 and its complete gamma tables.
SPI mode 3 matches that example's idle-high, rising-edge sampling waveform.
This is first-hardware-test software, not a claim of verified panel operation.

AXI Quad SPI registers and completion semantics: AMD PG153, SPICR, SPISR,
and IPISR. IPISR is toggle-on-write. SPISR.TX_EMPTY alone is NOT completion;
IPISR.DTR_EMPTY (bit2) is used before stopping SCK or releasing CS.
"""

import math
import time


class ST7796LCD:
    """Bind to an already loaded overlay; keep MB102 off for LCD testing."""

    DC = 0x01
    RESET = 0x02
    BACKLIGHT = 0x04

    DGIER = 0x1C
    IPISR = 0x20
    IPIER = 0x28
    SRR = 0x40
    CR = 0x60
    SR = 0x64
    DTR = 0x68
    DRR = 0x6C
    SSR = 0x70

    INHIBIT = 0x100
    FIFO_RESET = 0x60
    IRQ_DONE = 0x04
    IRQ_ERRORS = 0x3E23
    FIFO_DEPTH = 16

    # Manufacturer's FOUR-WIRE SPI example, one coherent initialization profile.
    _PANEL_REGISTERS = (
        (0xF0, b"\xC3"),
        (0xF0, b"\x96"),
        (0x36, b"\x48"),
        (0x3A, b"\x55"),
        (0xB4, b"\x01"),
        (0xB7, b"\xC6"),
        (0xE8, bytes((0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33))),
        (0xC1, b"\x06"),
        (0xC2, b"\xA7"),
        (0xC5, b"\x18"),
        (0xE0, bytes((0xF0, 0x09, 0x0B, 0x06, 0x04, 0x15, 0x2F,
                      0x54, 0x42, 0x3C, 0x17, 0x14, 0x18, 0x1B))),
        (0xE1, bytes((0xF0, 0x09, 0x0B, 0x06, 0x04, 0x03, 0x2D,
                      0x43, 0x42, 0x3B, 0x16, 0x14, 0x17, 0x1B))),
        (0xF0, b"\x3C"),
        (0xF0, b"\x69"),
    )

    def __init__(self, overlay, spi_mode=3, timeout=0.5, mmio_factory=None):
        if spi_mode not in (0, 3):
            raise ValueError("This driver supports rising-edge modes 0 and 3.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite.")
        self.overlay = overlay
        self.timeout = float(timeout)
        self.width, self.height = 480, 320
        self._initialized = False
        self._gpio_state = 0
        self._check_loaded()

        spi_info = overlay.ip_dict["axi_quad_spi_0"]
        gpio_info = overlay.ip_dict["axi_gpio_2"]
        self._check_parameter(spi_info, "C_SPI_MODE", 0)  # Standard, not CPOL/CPHA.
        self._check_parameter(spi_info, "C_FIFO_DEPTH", 16)
        self._check_parameter(spi_info, "C_NUM_SS_BITS", 1)
        self._check_parameter(spi_info, "C_NUM_TRANSFER_BITS", 8)
        self._check_parameter(spi_info, "C_USE_STARTUP", 0)
        self._check_parameter(gpio_info, "C_GPIO_WIDTH", 3)
        self._check_parameter(gpio_info, "C_ALL_OUTPUTS", 1)
        self._check_parameter(gpio_info, "C_IS_DUAL", 0)

        if mmio_factory is None:
            from pynq import MMIO
            mmio_factory = MMIO
        self.spi = mmio_factory(spi_info["phys_addr"], spi_info["addr_range"])
        self.gpio = mmio_factory(gpio_info["phys_addr"], gpio_info["addr_range"])
        # SPE, master, manual SS; MSB first. Mode3 additionally sets CPOL/CPHA.
        self._cr_run = 0x86 | (0x18 if spi_mode == 3 else 0)
        self._cr_idle = self._cr_run | self.INHIBIT
        self._gpio(0)  # LCD reset asserted and backlight off; no servo access.
        try:
            self._configure_spi()
        except BaseException:
            self._abort()
            raise

    @staticmethod
    def _check_parameter(info, name, expected):
        value = info.get("parameters", {}).get(name)
        if value is not None:
            try:
                actual = int(str(value), 0)
            except ValueError:
                actual = int(str(value), 10)
            if actual != expected:
                raise ValueError(f"Overlay parameter {name}={actual}; expected {expected}.")

    def _check_loaded(self):
        if not self.overlay.is_loaded():
            raise RuntimeError("The supplied overlay is no longer loaded.")

    def _gpio(self, value):
        value &= 7
        self.gpio.write(0, value)
        self._gpio_state = value  # Output shadow; not GPIO readback or position feedback.

    def _clear_events(self):
        pending = self.spi.read(self.IPISR)
        if pending:
            self.spi.write(self.IPISR, pending)  # Clear observed ones only (TOW).

    def _configure_spi(self):
        self.spi.write(self.SRR, 0x0A)
        time.sleep(0.001)
        self.spi.write(self.DGIER, 0)
        self.spi.write(self.IPIER, 0)
        self.spi.write(self.CR, self._cr_idle | self.FIFO_RESET)
        self.spi.write(self.SSR, 1)
        self.spi.write(self.CR, self._cr_idle)
        self._clear_events()
        if self.spi.read(self.CR) & 0x3FF != self._cr_idle:
            raise RuntimeError("SPI control-register readback mismatch.")
        if self.spi.read(self.SR) & 0x05 != 0x05:
            raise RuntimeError("SPI RX/TX FIFOs are not empty after reset.")

    def _error(self, stage):
        return (f"{stage}: SPI SR=0x{self.spi.read(self.SR):08X}, "
                f"IPISR=0x{self.spi.read(self.IPISR):08X}, "
                f"CR=0x{self.spi.read(self.CR):08X}")

    def _abort(self):
        # An interrupted SPI byte may be invalid, so force LCD reset/backlight off.
        # Touches only LCD IPs. In particular, never move/recenter a servo here.
        self._initialized = False
        try:
            self._gpio(0)
        except Exception:
            pass
        try:
            self.spi.write(self.CR, self._cr_idle | self.FIFO_RESET)
            self.spi.write(self.SSR, 1)
            self.spi.write(self.CR, self._cr_idle)
            self._clear_events()
        except Exception:
            pass

    def _send(self, data, dc):
        """Keep CS active across <=16-byte batches; consume every received byte."""
        payload = memoryview(data).cast("B")
        if not payload:
            return
        self._check_loaded()
        self._gpio((self._gpio_state & ~self.DC) | (self.DC if dc else 0))
        self.spi.write(self.SSR, 0)
        transfer_deadline = time.monotonic() + 60.0
        try:
            for offset in range(0, len(payload), self.FIFO_DEPTH):
                batch = payload[offset:offset + self.FIFO_DEPTH]
                if time.monotonic() >= transfer_deadline:
                    raise TimeoutError(self._error("Whole LCD transfer timed out"))
                self._clear_events()
                for value in batch:
                    self.spi.write(self.DTR, int(value))
                self.spi.write(self.CR, self._cr_run)
                deadline = min(transfer_deadline, time.monotonic() + self.timeout)
                while True:
                    events = self.spi.read(self.IPISR)
                    if events & self.IRQ_ERRORS:
                        raise RuntimeError(self._error("SPI transfer error"))
                    if events & self.IRQ_DONE:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(self._error("SPI completion timed out"))
                # IRQ_DONE means the final byte has left the pipeline. TX_EMPTY alone
                # could assert earlier and must not be used to terminate a transfer.
                self.spi.write(self.CR, self._cr_idle)
                received = 0
                while received < len(batch):
                    status = self.spi.read(self.SR)
                    if status & 0x10:
                        raise RuntimeError(self._error("SPI mode fault"))
                    if not status & 0x01:
                        self.spi.read(self.DRR)
                        received += 1
                    elif time.monotonic() >= deadline:
                        raise TimeoutError(self._error("SPI receive drain timed out"))
                if self.spi.read(self.IPISR) & self.IRQ_ERRORS:
                    raise RuntimeError(self._error("SPI FIFO error"))
            self.spi.write(self.SSR, 1)
        except BaseException:
            self._abort()
            raise

    def _command(self, command, parameters=b""):
        self._send(bytes((command,)), dc=False)
        if parameters:
            self._send(parameters, dc=True)

    def initialize(self):
        """Reset and initialize, selecting landscape 480x320; leave backlight off."""
        self._check_loaded()
        self._initialized = False
        try:
            self._gpio(0)
            self._configure_spi()
            self._gpio(self.RESET)
            time.sleep(0.001)
            self._gpio(0)
            time.sleep(0.010)
            self._gpio(self.RESET)
            time.sleep(0.120)
            time.sleep(0.120)  # Additional delay in manufacturer's example.
            self._command(0x11)
            time.sleep(0.120)
            for command, parameters in self._PANEL_REGISTERS:
                self._command(command, parameters)
            self._command(0x21)
            time.sleep(0.120)
            self._command(0x29)
            time.sleep(0.050)
            self._command(0x36, b"\x28")  # Vendor direction3: MV + BGR.
            self._initialized = True
        except BaseException:
            self._abort()
            raise

    def backlight(self, enabled=True):
        self._check_loaded()
        value = self._gpio_state & ~self.BACKLIGHT
        if enabled:
            value |= self.BACKLIGHT
        self._gpio(value)

    def _require_initialized(self):
        self._check_loaded()
        if not self._initialized:
            raise RuntimeError("Call lcd.initialize() before writing pixels.")

    def show_rgb565(self, pixels, x=0, y=0, width=None, height=None):
        """Write row-major, HIGH-BYTE-FIRST RGB565 bytes into a rectangular window."""
        self._require_initialized()
        width = self.width if width is None else width
        height = self.height if height is None else height
        if not all(isinstance(v, int) for v in (x, y, width, height)):
            raise ValueError("Window coordinates and dimensions must be integers.")
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("Invalid pixel window.")
        if x + width > self.width or y + height > self.height:
            raise ValueError("Pixel window exceeds 480x320 screen bounds.")
        data = memoryview(pixels).cast("B")
        if len(data) != width * height * 2:
            raise ValueError("RGB565 data length must equal width * height * 2.")
        right, bottom = x + width - 1, y + height - 1
        self._command(0x2A, bytes((x >> 8, x & 255, right >> 8, right & 255)))
        self._command(0x2B, bytes((y >> 8, y & 255, bottom >> 8, bottom & 255)))
        self._command(0x2C)
        self._send(data, dc=True)

    def fill_rect(self, x, y, width, height, color):
        if not isinstance(color, int) or not 0 <= color <= 0xFFFF:
            raise ValueError("color must be a 16-bit RGB565 integer.")
        if not isinstance(width, int) or not isinstance(height, int):
            raise ValueError("Rectangle dimensions must be integers.")
        if width <= 0 or height <= 0 or width > self.width or height > self.height:
            raise ValueError("Invalid rectangle dimensions.")
        pixel = bytes((color >> 8, color & 255))
        self.show_rgb565(pixel * (width * height), x, y, width, height)

    def fill(self, color):
        self.fill_rect(0, 0, self.width, self.height, color)

    def close(self):
        """Disable this LCD backlight and assert its reset. Does not stop PWM."""
        self._check_loaded()
        self._abort()
