"""Optional native SPI backend for the already initialized ST7796LCD object.

Installation compiles and runs a RAM-only C protocol self-test before binding.
It does not load FPGA configuration, initialize the LCD, touch servo GPIOs, or
map new addresses. Original Python backend can be restored without reloading
the FPGA. Any transfer failure uses the original LCD-only abort/reset routine.
"""

import ctypes
import hashlib
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import types

ABI = 0x00010001
CHUNK_BYTES = 4096


def _run_build_command(arguments, timeout=45):
    try:
        result = subprocess.run(arguments, check=True, capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Native SPI build/self-test failed (LCD unchanged):\n"
                           + (exc.stdout or "") + (exc.stderr or "")) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Native SPI build/self-test timed out; LCD unchanged.") from exc
    return result.stdout.strip()


def _build_library():
    source = Path(__file__).resolve().with_name("echo_native_spi.c")
    if not source.is_file():
        raise FileNotFoundError("Upload echo_native_spi.c beside echo_native_spi.py first.")
    compiler = shutil.which("gcc")
    if compiler is None:
        raise RuntimeError("gcc was not found. Original LCD driver is unchanged.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
    machine = platform.machine().lower()
    cache = source.parent / ".echo_native_spi" / f"{machine}_{digest}"
    cache.mkdir(parents=True, exist_ok=True)
    library, selftest = cache/"libecho_spi.so", cache/"spi_selftest"
    if not library.is_file() or not selftest.is_file():
        flags = ["-O2", "-std=c11", "-Wall", "-Wextra", "-Werror"]
        # Use the board compiler's native CPU/FPU/float-ABI defaults together.
        # Forcing only -march can conflict with an armhf toolchain's FPU flags.
        with tempfile.TemporaryDirectory(prefix="build_", dir=cache) as temporary:
            build = Path(temporary)
            print("正在编译不接触硬件的C自检程序……", flush=True)
            _run_build_command([compiler, *flags, "-DECHO_SPI_SELFTEST",
                                str(source), "-o", str(build/"spi_selftest"), "-lm", "-lrt"])
            print(_run_build_command([str(build/"spi_selftest")], timeout=5), flush=True)
            print("正在编译LCD发送加速库……", flush=True)
            _run_build_command([compiler, *flags, "-shared", "-fPIC", str(source),
                                "-o", str(build/"libecho_spi.so"), "-lm", "-lrt"])
            os.replace(build/"spi_selftest", selftest)
            os.replace(build/"libecho_spi.so", library)
    else:
        print(_run_build_command([str(selftest)], timeout=5), flush=True)
    return library


class _NativeBackend:
    def __init__(self, lcd, library_path, array):
        library = ctypes.CDLL(str(library_path))
        library.echo_spi_abi.argtypes = []
        library.echo_spi_abi.restype = ctypes.c_uint32
        if library.echo_spi_abi() != ABI:
            raise RuntimeError("Native SPI library ABI mismatch; original driver unchanged.")
        native_send = library.echo_spi_send
        native_send.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_double, ctypes.c_double,
                                ctypes.POINTER(ctypes.c_uint32)]
        native_send.restype = ctypes.c_int
        self.library = library
        self.library_path = str(library_path)
        self.native_send = native_send
        self.array = array  # Keep the actual MMIO mapping alive.
        self.address = int(array.ctypes.data)
        self.pointer = array.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
        self.original_send = lcd._send
        self.calls = 0
        self.pixel_and_command_bytes = 0


def _validate_lcd(lcd):
    import numpy as np
    lcd._require_initialized()  # Metadata/software checks only, no register writes.
    if platform.system() != "Linux" or platform.machine().lower() not in ("armv7l", "aarch64"):
        raise RuntimeError("Enable this backend only on the local ARM Linux PYNQ board.")
    if sys.byteorder != "little":
        raise RuntimeError("This backend expects little-endian ARM Linux.")
    if not math.isfinite(lcd.timeout) or lcd.timeout <= 0:
        raise ValueError("LCD timeout must be positive and finite.")
    expected = {"IPISR":0x20,"CR":0x60,"SR":0x64,"DTR":0x68,"DRR":0x6c,
                "SSR":0x70,"FIFO_DEPTH":16,"IRQ_DONE":4,"IRQ_ERRORS":0x3e23}
    if any(getattr(lcd,name,None) != value for name,value in expected.items()):
        raise ValueError("Unsupported LCD driver register map or FIFO settings.")
    if lcd._cr_run not in (0x86,0x9e) or lcd._cr_idle != (lcd._cr_run|0x100):
        raise ValueError("Unsupported SPI control mode.")
    info = lcd.overlay.ip_dict["axi_quad_spi_0"]
    for name,value in (("C_SPI_MODE",0),("C_FIFO_DEPTH",16),
                       ("C_NUM_TRANSFER_BITS",8),("C_NUM_SS_BITS",1),("C_USE_STARTUP",0)):
        lcd._check_parameter(info,name,value)
    if hasattr(lcd.spi,"base_addr") and int(lcd.spi.base_addr) != int(info["phys_addr"]):
        raise ValueError("SPI MMIO base does not match overlay metadata.")
    array = lcd.spi.array
    if (not isinstance(array,np.ndarray) or array.dtype != np.dtype("uint32")
            or not array.dtype.isnative or array.ndim != 1
            or not array.flags.c_contiguous or not array.flags.writeable
            or array.nbytes < 0x74 or int(array.ctypes.data)%4):
        raise ValueError("SPI MMIO must be an aligned, writable, contiguous uint32 array.")
    return array


def _native_send(lcd, data, dc):
    payload = memoryview(data).cast("B")
    if not payload:
        return
    if len(payload) > 480*320*2:
        raise ValueError("Payload exceeds a full 480x320 RGB565 frame.")
    lcd._check_loaded()
    backend = lcd._echo_native_backend
    if int(lcd.spi.array.ctypes.data) != backend.address:
        raise RuntimeError("SPI MMIO mapping changed. Stop and restore the driver.")
    # Copy into an owned C buffer; no Python callbacks or per-byte MMIO calls.
    buffer = (ctypes.c_uint8*len(payload)).from_buffer_copy(payload)
    deadline = time.monotonic()+60.0
    report = (ctypes.c_uint32*4)()
    try:
        lcd._gpio((lcd._gpio_state & ~lcd.DC) | (lcd.DC if dc else 0))
        lcd.spi.write(lcd.SSR,0)
        for offset in range(0,len(payload),CHUNK_BYTES):
            lcd._check_loaded()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Whole native LCD transfer timed out.")
            count = min(CHUNK_BYTES,len(payload)-offset)
            code = backend.native_send(
                backend.pointer,
                ctypes.cast(ctypes.byref(buffer,offset),ctypes.POINTER(ctypes.c_uint8)),
                count,lcd._cr_run,lcd._cr_idle,lcd.timeout,min(2.0,remaining),report)
            if code != 0:
                message = (f"Native SPI error {code}: SR=0x{report[0]:08X}, "
                           f"IPISR=0x{report[1]:08X}, CR=0x{report[2]:08X}, "
                           f"completed chunk bytes={report[3]}")
                if code in (-3,-4,-7):
                    raise TimeoutError(message)
                raise RuntimeError(message)
            if report[3] != count:
                raise RuntimeError("Native SPI returned an incomplete byte count.")
        lcd.spi.write(lcd.SSR,1)
        backend.calls += 1
        backend.pixel_and_command_bytes += len(payload)
    except BaseException:
        # Partial command/pixel writes may be invalid. Reset ONLY this LCD;
        # never fall back mid-transfer, reload the FPGA, or move the servo.
        try:
            still_owned = lcd.overlay.is_loaded()
        except BaseException:
            still_owned = False
        if still_owned:
            lcd._abort()
        else:
            # Do not write old IP addresses into a newly replaced design.
            lcd._initialized = False
        raise


def enable_native_spi(lcd):
    """No hardware writes until a later LCD command/image call. Stop scans first."""
    array = _validate_lcd(lcd)
    existing = getattr(lcd,"_echo_native_backend",None)
    if existing is not None:
        print("LCD原生发送加速已启用。",flush=True)
        return existing.library_path
    path = _build_library()
    backend = _NativeBackend(lcd,path,array)
    # Change the existing instance only AFTER build, RAM self-test and ABI checks.
    lcd._echo_native_backend = backend
    lcd._send = types.MethodType(_native_send,lcd)
    print("LCD原生发送加速已启用；没有重新初始化屏幕或改变舵机指令。",flush=True)
    return backend.library_path


def restore_python_spi(lcd):
    """Restore the original transport without register writes or reinitializing.

    This does not repair an LCD that was reset after an interrupted transfer.
    """
    backend = getattr(lcd,"_echo_native_backend",None)
    if backend is None:
        return False
    lcd._send = backend.original_send
    del lcd._echo_native_backend
    return True
