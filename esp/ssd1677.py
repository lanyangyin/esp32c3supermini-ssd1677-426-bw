# ssd1677.py — 最终安全版（完全避开父类初始化冲突）
import time
import framebuf
from micropython import const
import gc

_SW_RESET = const(0x12)
_DRIVER_OUTPUT_CONTROL = const(0x01)
_BORDER_WAVEFORM_CONTROL = const(0x3C)
_DATA_ENTRY_MODE = const(0x11)
_SET_RAM_X_WINDOW = const(0x44)
_SET_RAM_Y_WINDOW = const(0x45)
_SET_RAM_X_COUNTER = const(0x4E)
_SET_RAM_Y_COUNTER = const(0x4F)
_WRITE_RAM_BW = const(0x24)
_DISPLAY_UPDATE_CONTROL_2 = const(0x22)
_MASTER_ACTIVATION = const(0x20)
_DEEP_SLEEP = const(0x10)
_UPDATE_MODE_FULL = const(0xF7)


# 注意：不再继承 framebuf.FrameBuffer，彻底断绝参数冲突！
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
        self.busy.init(self.busy.IN)

        self._dirty = None
        self._in_stream = False
        self._stream_y = 0
        self.buffer = None

        self.init_display()

    def alloc_framebuffer(self):
        """按需分配整屏缓存（用于 BIN 编辑等重绘图操作）"""
        if self.buffer is not None:
            return True
        try:
            gc.collect()
            self.buffer = bytearray((self.width // 2) * self.height)
            # 这里只是给外部提供一个缓冲，不绑定到 FrameBuffer 父类
            print("[SSD1677] framebuffer allocated (192000 bytes)")
            return True
        except MemoryError:
            print("[SSD1677] MemoryError: Cannot allocate framebuffer")
            self.buffer = None
            return False

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
        self._cmd(_SET_RAM_X_WINDOW, x_start & 0xFF, (x_start >> 8) & 0x03, x_end & 0xFF, (x_end >> 8) & 0x03)
        self._cmd(_SET_RAM_Y_WINDOW, y_start & 0xFF, (y_start >> 8) & 0xFF, y_end & 0xFF, (y_end >> 8) & 0xFF)

    def _set_cursor(self, x, y):
        self._cmd(_SET_RAM_X_COUNTER, x & 0xFF, (x >> 8) & 0x03)
        self._cmd(_SET_RAM_Y_COUNTER, y & 0xFF, (y >> 8) & 0xFF)

    def init_display(self, fast_prime=False):
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
        self._stream_y = 0
        self._set_window(0, self.height - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([_WRITE_RAM_BW]))
        self.dc(1)

    def write_chunk(self, chunk):
        """接收 4bpp（16 级灰度）数据，转成 SSD1677 需要的 2bpp（4 级灰度）后 SPI 发送。"""
        if not self._in_stream:
            raise OSError("Must call begin_frame() first")

        n = len(chunk)
        if n == 0:
            return

        # 4bpp -> 2bpp：每 2 字节输入（4 像素）→ 1 字节输出（4 像素，每 2 bit）
        if n % 2 != 0:
            chunk = bytes(chunk) + b'\x00'
            n += 1

        out = bytearray(n // 2)
        for i in range(0, n, 2):
            b0 = chunk[i]
            b1 = chunk[i + 1]
            q0 = ((b0 >> 4) & 0x0F) // 5  # 16级 → 4级 (0-3)
            q1 = (b0 & 0x0F) // 5
            q2 = ((b1 >> 4) & 0x0F) // 5
            q3 = (b1 & 0x0F) // 5
            out[i // 2] = (q0 << 6) | (q1 << 4) | (q2 << 2) | q3

        self.spi.write(out)

    def end_frame(self):
        self._in_stream = False
        self.cs(1)
        self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_MODE_FULL)
        self._cmd(_MASTER_ACTIVATION)
        self._busy_wait()

    def show(self):
        if self.buffer is None:
            raise OSError("Framebuffer not allocated. Use begin_frame() / write_chunk() instead.")
        self.begin_frame()
        chunk_size = 4000
        for i in range(0, len(self.buffer), chunk_size):
            self.write_chunk(self.buffer[i:i + chunk_size])
        self.end_frame()

    def sleep(self):
        self._cmd(_DEEP_SLEEP, 0x01)
        time.sleep_ms(2000)

    def clear(self, color=15):
        row = bytearray([(color << 4) | color] * 400)
        self.begin_frame()
        for _ in range(self.height):
            self.write_chunk(row)
        self.end_frame()