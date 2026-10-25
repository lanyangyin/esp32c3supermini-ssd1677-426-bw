# ssd1677.py — 极简版（支持全刷/快刷/局刷，支持多矩形局刷）

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
        self._bytes_written = 0
        self._frame_bytes = (width * height) // 8
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
        self.rst(1);
        time.sleep_ms(20)
        self.rst(0);
        time.sleep_ms(2)
        self.rst(1);
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
        time.sleep_ms(10)
        self._cmd(_SW_RESET)
        self._busy_wait()

        self._cmd(0x18, 0x80)
        self._cmd(0x0C, 0xAE, 0xC7, 0xC3, 0xC0, 0x80)
        h = self.height
        self._cmd(_DRIVER_OUTPUT_CONTROL, (h - 1) & 0xFF, ((h - 1) >> 8) & 0xFF, 0x02)
        self._cmd(_BORDER_WAVEFORM_CONTROL, 0x01)
        self._cmd(_DATA_ENTRY_MODE, 0x01)

        self._cmd(0x46, 0xF7)
        self._cmd(0x47, 0xF7)

        self._set_window(0, h - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self._busy_wait()

    def begin_frame(self):
        """全屏刷新准备"""
        self._in_stream = True
        self._bytes_written = 0
        self._frame_bytes = (self.width * self.height) // 8
        self._set_window(0, self.height - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([_WRITE_RAM_BW]))
        self.dc(1)

    def write_chunk(self, chunk):
        if not self._in_stream:
            raise OSError("Must call begin_frame() first")
        if self._bytes_written + len(chunk) > self._frame_bytes:
            raise ValueError("frame overflow")
        self.spi.write(chunk)
        self._bytes_written += len(chunk)

    def end_frame(self, mode='full'):
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

    # ================== 多矩形局刷支持 ==================

    def start_partial_multi(self):
        """多矩形局刷：初始化局部刷新模式"""
        self._cmd(_PARTIAL_IN)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([_WRITE_RAM_BW]))
        self.dc(1)
        self._in_stream = True

    def write_partial_rect(self, x0, y0, x1, y1, data):
        """写入一个矩形窗口的数据（不触发刷新）"""
        # 强制 X 对齐到 8 的倍数
        x0 = x0 & ~7
        x1 = (x1 | 7)
        if x1 >= self.width: x1 = self.width - 1
        if y0 < 0: y0 = 0
        if y1 >= self.height: y1 = self.height - 1

        # 设置窗口
        self.cs(1)
        self._set_window(x0, y1, x1, y0)
        self._set_cursor(x0, y0)

        # 重新开启数据流
        self.cs(0)
        self.dc(1)
        self.spi.write(data)
        self.cs(1)

    def end_partial_multi(self):
        """所有矩形写完后，统一触发刷新"""
        if not self._in_stream:
            return
        self._in_stream = False
        self.cs(1)

        self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_FAST)
        self._cmd(_MASTER_ACTIVATION)
        self._busy_wait()
        self._cmd(_PARTIAL_OUT)

    def sleep(self):
        self._cmd(_DEEP_SLEEP, 0x01)
        time.sleep_ms(2000)