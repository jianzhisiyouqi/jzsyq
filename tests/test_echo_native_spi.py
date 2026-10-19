"""Host model/Python-binding tests, plus syntax parsing of both C branches.

The real C protocol self-test is compiled/run on PYNQ before installation.
Host has no C compiler, so these tests do not claim ARM hardware verification.
"""
import contextlib
import ctypes
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from pycparser import c_parser

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"software"))
import echo_native_spi as native
import st7796_lcd as driver
from test_st7796_lcd import Clock, GPIO, SPI, Overlay


class Function:
    def __init__(self,function):
        self.function=function
    def __call__(self,*args):
        return self.function(*args)


class Library:
    def __init__(self,spi):
        self.spi=spi
        self.chunk_sizes=[]
        self.code_override=None
        self.abi=native.ABI
        self.after_chunk=None
        self.incomplete=False
        self.echo_spi_abi=Function(lambda:self.abi)
        self.echo_spi_send=Function(self.send)
    def send(self,base,data,count,run,idle,timeout,chunk_timeout,report):
        self.chunk_sizes.append(count)
        if self.code_override is not None:
            return self.code_override
        spi=self.spi
        for offset in range(0,count,16):
            pending=spi.read(0x20)
            if pending & 0x3e23:
                return -2
            if pending:
                spi.write(0x20,pending)
            batch=bytes(data[offset:min(offset+16,count)])
            for value in batch:
                spi.write(0x68,value)
            spi.write(0x60,run)
            for poll in range(10):
                events=spi.read(0x20)
                if events&0x3e23:
                    return -2
                if events&4:
                    break
            else:
                return -3
            spi.write(0x60,idle)
            for value in batch:
                if spi.read(0x64)&1:
                    return -4
                spi.read(0x6c)
        report[0]=spi.read(0x64)
        report[1]=spi.read(0x20)
        report[2]=spi.read(0x60)
        report[3]=count-1 if self.incomplete else count
        if self.after_chunk:
            self.after_chunk()
        return 0


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.driver_clock=patch.object(driver,"time",Clock())
        self.driver_clock.start()
        self.overlay=Overlay()
        self.gpio=GPIO()
        self.spi=SPI(self.gpio)
        self.spi.array=np.zeros(32,dtype=np.uint32)
        self.spi.base_addr=0x41e00000
        self.lcd=driver.ST7796LCD(self.overlay,timeout=.002,
            mmio_factory=lambda address,size:{0x41e00000:self.spi,0x41200000:self.gpio}[address])
        self.lcd.initialize()
        self.spi.wire_transfers.clear()
        self.library=Library(self.spi)
        self.output=io.StringIO()
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(native.platform,"system",return_value="Linux"))
        self.stack.enter_context(patch.object(native.platform,"machine",return_value="armv7l"))
        self.build=self.stack.enter_context(patch.object(native,"_build_library",return_value=Path("mock.so")))
        self.stack.enter_context(patch.object(native.ctypes,"CDLL",return_value=self.library))
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
    def tearDown(self):
        self.stack.close()
        self.driver_clock.stop()
    def enable(self):
        return native.enable_native_spi(self.lcd)

    def test_install_and_restore_do_not_write_registers(self):
        original=self.lcd._send
        before=list(self.gpio.history)
        self.enable()
        self.assertEqual(self.gpio.history,before)
        self.assertEqual(self.spi.wire_transfers,[])
        self.assertEqual(self.library.chunk_sizes,[])
        self.assertTrue(native.restore_python_spi(self.lcd))
        self.assertEqual(self.lcd._send,original)
        self.assertEqual(self.gpio.history,before)
        self.assertFalse(native.restore_python_spi(self.lcd))

    def test_cs_held_across_native_chunks_and_byte_order_preserved(self):
        self.enable()
        cs_writes=[]
        original_write=self.spi.write
        def write(offset,value):
            if offset==0x70:
                cs_writes.append(value)
            original_write(offset,value)
        self.spi.write=write
        data=bytes(i%256 for i in range(4097))
        self.lcd._send(data,True)
        self.assertEqual(self.library.chunk_sizes,[4096,1])
        self.assertEqual(cs_writes,[0,1])
        self.assertEqual(b"".join(data for dc,data in self.spi.wire_transfers),data)
        self.assertTrue(all(dc for dc,data in self.spi.wire_transfers))
        self.assertFalse(self.spi.received)
        self.assertEqual(self.spi.max_fifo,16)

    def test_full_frame_chunk_size_and_existing_window_api(self):
        self.enable()
        self.lcd.fill(0x07e0)
        self.assertEqual(self.library.chunk_sizes[:5],[1,4,1,4,1])
        self.assertEqual(self.library.chunk_sizes[5:],[4096]*75)
        self.assertEqual(b"".join(data for dc,data in self.spi.wire_transfers[5:]),
                         b"\x07\xe0"*(480*320))
        self.assertEqual(self.lcd._echo_native_backend.pixel_and_command_bytes,307211)

    def test_timeout_resets_only_lcd(self):
        self.enable()
        self.spi.hang=True
        with self.assertRaises(TimeoutError):
            self.lcd._send(b"\xaa",True)
        self.assertEqual(self.gpio.value,0)
        self.assertEqual(self.spi.cs,1)
        self.assertFalse(self.spi.active)
        self.assertFalse(self.lcd._initialized)

    def test_rx_failure_resets_only_lcd(self):
        self.enable()
        self.spi.drop_rx=True
        with self.assertRaises(TimeoutError):
            self.lcd._send(b"\xaa",True)
        self.assertEqual(self.gpio.value,0)

    def test_hardware_error_is_not_silently_retried(self):
        self.enable()
        self.spi.inject_error=True
        with self.assertRaises(RuntimeError):
            self.lcd._send(b"\xaa",True)
        self.assertEqual(self.library.chunk_sizes,[1])
        self.assertFalse(self.lcd._initialized)

    def test_bad_abi_or_build_failure_keeps_python_backend(self):
        original=self.lcd._send
        self.library.abi=0
        with self.assertRaises(RuntimeError):
            self.enable()
        self.assertEqual(self.lcd._send,original)
        self.assertFalse(hasattr(self.lcd,"_echo_native_backend"))
        self.build.side_effect=RuntimeError("mock compiler error")
        with self.assertRaises(RuntimeError):
            self.enable()
        self.assertEqual(self.lcd._send,original)
        self.assertEqual(self.spi.wire_transfers,[])

    def test_wrong_dtype_or_base_is_rejected_before_build(self):
        self.spi.array=np.zeros(32,dtype=np.uint8)
        with self.assertRaises(ValueError):
            self.enable()
        self.spi.array=np.zeros(32,dtype=np.uint32)
        self.spi.base_addr=0x40010000
        with self.assertRaises(ValueError):
            self.enable()
        self.build.assert_not_called()

    def test_empty_payload_and_overlay_change_do_not_send(self):
        self.enable()
        self.lcd._send(b"",True)
        self.overlay.loaded=False
        with self.assertRaises(RuntimeError):
            self.lcd._send(b"\xaa",True)
        self.assertFalse(self.library.chunk_sizes)

    def test_overlay_replaced_between_chunks_avoids_old_ip_writes(self):
        self.enable()
        self.library.after_chunk=lambda:setattr(self.overlay,"loaded",False)
        before_gpio=list(self.gpio.history)
        with self.assertRaises(RuntimeError):
            self.lcd._send(b"\xaa"*4097,True)
        self.assertEqual(self.library.chunk_sizes,[4096])
        # Only the initial DC selection; no reset write into a different design.
        self.assertEqual(self.gpio.history,before_gpio+[self.lcd.RESET|self.lcd.DC])
        self.assertFalse(self.lcd._initialized)

    def test_incomplete_native_return_is_fatal(self):
        self.enable()
        self.library.incomplete=True
        with self.assertRaises(RuntimeError):
            self.lcd._send(b"\xaa",True)
        self.assertFalse(self.lcd._initialized)

    def test_interrupt_aborts_lcd_without_servo_mapping(self):
        self.enable()
        def interrupt(*args):
            raise KeyboardInterrupt()
        self.library.echo_spi_send.function=interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.lcd._send(b"\xaa",True)
        self.assertEqual(self.gpio.value,0)
        self.assertFalse(self.lcd._initialized)

    def test_idempotent_enable_does_not_rebuild(self):
        self.enable()
        self.enable()
        self.assertEqual(self.build.call_count,1)


def select_c_branch(source,selftest):
    active=True
    stack=[]
    lines=[]
    for line in source.splitlines():
        token=line.strip()
        if token.startswith("#ifdef ECHO_SPI_SELFTEST"):
            stack.append((active,selftest))
            active=active and selftest
        elif token.startswith("#if defined(__arm__)"):
            stack.append((active,False))
            active=False
        elif token=="#else":
            parent,condition=stack[-1]
            active=parent and not condition
        elif token=="#endif":
            active=stack.pop()[0]
        elif token.startswith("#"):
            continue
        elif active:
            lines.append(line)
    return "\n".join(lines)


class CSourceSyntaxTests(unittest.TestCase):
    def test_standard_c_syntax_in_production_and_ram_model(self):
        import re
        source=(Path(native.__file__).with_name("echo_native_spi.c")).read_text(encoding="utf-8")
        source=re.sub(r"/\*.*?\*/","",source,flags=re.S)
        typedefs=("typedef unsigned int uint32_t; typedef unsigned char uint8_t; "
                  "typedef unsigned long size_t; struct timespec {long tv_sec; long tv_nsec;};\n")
        for selftest in (False,True):
            tree=c_parser.CParser().parse(typedefs+select_c_branch(source,selftest))
            functions={node.decl.name for node in tree.ext if type(node).__name__=="FuncDef"}
            self.assertIn("echo_spi_send",functions)
            self.assertIn("echo_spi_abi",functions)
            if selftest:
                self.assertIn("main",functions)


if __name__=="__main__":
    unittest.main()
