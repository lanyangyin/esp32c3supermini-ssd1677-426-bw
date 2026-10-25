# ssd1677.py — 极简版（1bpp，支持全刷/快刷/局刷）
import time
from micropython import const

_SW_RESET = const(0x12)
_DRIVER_OUTPUT_CONTROL = const(0x01)
_BORDER_WAVEFORM_CONTROL = const(0x3C)
_DATA_ENTRY_MODE = const(0x11)
_SET_RAM_X_WINDOW = const(0x44)
_SET_RAM_Y_WINDOW = const(0x45)
_SET_RAM_X_COUNTER = const(0x4E)
_SET_RAM_Y_COUNTER = const(0x4F)
_WRITE_RAM_BW = const(0x24)
_WRITE_RAM_RED = const(0x26)
_DISPLAY_UPDATE_CONTROL_2 = const(0x22)
_MASTER_ACTIVATION = const(0x20)
_DEEP_SLEEP = const(0x10)
_PARTIAL_IN = const(0x91)
_PARTIAL_OUT = const(0x90)

_UPDATE_FULL = const(0xF7)
_UPDATE_FAST = const(0xFF)


class SSD1677:
    def __init__(self, width, height, spi, dc, cs, rst, busy):
        self.width = width
        self.height = height
        self.spi = spi
        self.dc = dc
        self.cs = cs
        self.rst = rst
        self.busy = busy

        self.dc.init(self.dc.OUT, value=0)
        self.cs.init(self.cs.OUT, value=1)
        self.rst.init(self.rst.OUT, value=1)
        self.busy.init(self.busy.IN, pull=self.busy.PULL_UP)

        self._in_stream = False
        self.init_display()

    def _cmd(self, command, *data):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([command]))
        if data:
            self.dc(1)
            self.spi.write(bytearray(data))
        self.cs(1)

    def _busy_wait(self):
        t0 = time.ticks_ms()
        while self.busy.value():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise OSError("SSD1677: timed out")
            time.sleep_ms(20)
        time.sleep_ms(20)

    def _reset(self):
        self.rst(1)
        time.sleep_ms(20)
        self.rst(0)
        time.sleep_ms(2)
        self.rst(1)
        time.sleep_ms(20)

    def _set_window(self, x_start, y_start, x_end, y_end):
        self._cmd(_SET_RAM_X_WINDOW,
                  x_start & 0xFF, (x_start >> 8) & 0x03,
                  x_end & 0xFF, (x_end >> 8) & 0x03)
        self._cmd(_SET_RAM_Y_WINDOW,
                  y_start & 0xFF, (y_start >> 8) & 0xFF,
                  y_end & 0xFF, (y_end >> 8) & 0xFF)

    def _set_cursor(self, x, y):
        self._cmd(_SET_RAM_X_COUNTER, x & 0xFF, (x >> 8) & 0x03)
        self._cmd(_SET_RAM_Y_COUNTER, y & 0xFF, (y >> 8) & 0xFF)

    def init_display(self):
        self._reset()
        self._busy_wait()
        self._cmd(_SW_RESET)
        self._busy_wait()
        self._cmd(0x18, 0x80)
        self._cmd(0x0C, 0xAE, 0xC7, 0xC3, 0xC0, 0x80)
        h = self.height
        self._cmd(_DRIVER_OUTPUT_CONTROL, (h - 1) & 0xFF, ((h - 1) >> 8) & 0xFF, 0x02)
        self._cmd(_BORDER_WAVEFORM_CONTROL, 0x01)
        self._cmd(_DATA_ENTRY_MODE, 0x01)

        self._set_window(0, h - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self._busy_wait()

    def begin_frame(self):
        self._in_stream = True
        self._set_window(0, self.height - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([_WRITE_RAM_BW]))
        self.dc(1)

    def write_chunk(self, chunk):
        if not self._in_stream:
            raise OSError("Must call begin_frame() first")
        self.spi.write(chunk)

    def end_frame(self, mode='full'):
        """结束一帧并刷新。

        mode:
          'full'    → 0xF7，全刷，4.2s，永久显示，无残影
          'fast'    → 0xFF，快刷，0.9s，需 RED 基线，可能有轻微残影
          'partial' → 0x91+0xFF+0x90，局刷，0.9s（全屏窗口包裹 partial 命令）
        """
        if not self._in_stream:
            return
        self._in_stream = False
        self.cs(1)

        if mode == 'full':
            self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_FULL)
            self._cmd(_MASTER_ACTIVATION)
            self._busy_wait()
        elif mode == 'fast':
            self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_FAST)
            self._cmd(_MASTER_ACTIVATION)
            self._busy_wait()
        elif mode == 'partial':
            self._cmd(_PARTIAL_IN)
            self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_FAST)
            self._cmd(_MASTER_ACTIVATION)
            self._busy_wait()
            self._cmd(_PARTIAL_OUT)
        else:
            raise ValueError("mode must be 'full' / 'fast' / 'partial'")

    def sleep(self):
        self._cmd(_DEEP_SLEEP, 0x01)
        time.sleep_ms(2000)

    def clear(self, color=1, mode='full'):
        fill_val = 0xFF if color == 1 else 0x00
        row = bytearray([fill_val] * (self.width // 8))
        self.begin_frame()
        for _ in range(self.height):
            self.write_chunk(row)
        self.end_frame(mode)