# MicroPython SSD1677 e-paper display driver (SPI)
#
# For 800x480 monochrome panels such as the one used in the Xteink X4
# e-reader.
#
# Framebuffer convention: color 1 = black (ink), color 0 = white (background),
# matching typical framebuf usage elsewhere. This is the *opposite* of the
# panel's own RAM convention (a RAM bit of 1 renders white, 0 renders black),
# so the buffer is inverted on the way out whenever it's written to the panel.
#
# No rotation support: the framebuffer is always in the panel's native
# 800(w)x480(h) raster. On devices where the panel is mounted rotated
# relative to how it's held (e.g. a landscape panel used for portrait
# reading), rotating the drawn content is left to the caller for now.

import time
import framebuf
from micropython import const

_SW_RESET = const(0x12)
_TEMP_SENSOR_CONTROL = const(0x18)
_WRITE_TEMP_REG = const(0x1A)
_BOOSTER_SOFT_START = const(0x0C)
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

_BORDER_NORMAL = const(0x01)
_BORDER_PARTIAL = const(0x80)

_UPDATE_MODE_FULL = const(0xF7)
_UPDATE_MODE_FAST_PRIME = const(0x91)
_UPDATE_MODE_FAST = const(0xC7)
_UPDATE_MODE_PARTIAL = const(0xFF)

_BUSY_POLL_MS = const(20)
_BUSY_TIMEOUT_MS = const(15000)


class SSD1677(framebuf.FrameBuffer):
    def __init__(self, width, height, spi, dc, cs, rst, busy):
        if width % 8:
            raise ValueError("width must be a multiple of 8")

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
        self._fast_primed = False

        self.buffer = bytearray(width // 8 * height)
        super().__init__(self.buffer, width, height, framebuf.MONO_HLSB)

        self.init_display()

    # -- low-level helpers ---------------------------------------------

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
            if time.ticks_diff(time.ticks_ms(), t0) > _BUSY_TIMEOUT_MS:
                raise OSError("SSD1677: timed out waiting for busy")
            time.sleep_ms(_BUSY_POLL_MS)
        time.sleep_ms(_BUSY_POLL_MS)

    def _reset(self):
        self.rst(1)
        time.sleep_ms(20)
        self.rst(0)
        time.sleep_ms(2)
        self.rst(1)
        time.sleep_ms(20)

    def _set_window(self, x_start, y_start, x_end, y_end):
        self._cmd(
            _SET_RAM_X_WINDOW,
            x_start & 0xFF,
            (x_start >> 8) & 0x03,
            x_end & 0xFF,
            (x_end >> 8) & 0x03,
        )
        self._cmd(
            _SET_RAM_Y_WINDOW,
            y_start & 0xFF,
            (y_start >> 8) & 0xFF,
            y_end & 0xFF,
            (y_end >> 8) & 0xFF,
        )

    def _set_cursor(self, x, y):
        self._cmd(_SET_RAM_X_COUNTER, x & 0xFF, (x >> 8) & 0x03)
        self._cmd(_SET_RAM_Y_COUNTER, y & 0xFF, (y >> 8) & 0xFF)

    def _write_ram(self, x0, y0, x1, y1):
        """Write buffer[x0:x1+1, y0:y1+1] (inclusive, pixel coords) to the
        panel's B/W RAM, inverting polarity on the way out. x0/x1 must
        already be byte-aligned (x0 % 8 == 0, (x1 + 1) % 8 == 0)."""
        stride = self.width // 8
        x0_byte = x0 // 8
        x1_byte = x1 // 8
        row_bytes = x1_byte - x0_byte + 1

        self._set_window(x0, y1, x1, y0)
        self._set_cursor(x0, y0)

        mv = memoryview(self.buffer)
        self.cs(0)
        self.dc(0)
        self.spi.write(bytearray([_WRITE_RAM_BW]))
        self.dc(1)

        if x0_byte == 0 and row_bytes == stride:
            # Contiguous across rows - stream it in one go.
            chunk = bytearray(512)
            start = y0 * stride
            end = (y1 + 1) * stride
            for offset in range(start, end, len(chunk)):
                piece = mv[offset : min(offset + len(chunk), end)]
                for i, b in enumerate(piece):
                    chunk[i] = b ^ 0xFF
                self.spi.write(memoryview(chunk)[: len(piece)])
        else:
            chunk = bytearray(row_bytes)
            for y in range(y0, y1 + 1):
                row_start = y * stride + x0_byte
                row = mv[row_start : row_start + row_bytes]
                for i, b in enumerate(row):
                    chunk[i] = b ^ 0xFF
                self.spi.write(chunk)

        self.cs(1)

    def _mark_dirty(self, x0, y0, x1, y1):
        x0 = max(0, min(x0, self.width - 1))
        x1 = max(0, min(x1, self.width - 1))
        y0 = max(0, min(y0, self.height - 1))
        y1 = max(0, min(y1, self.height - 1))
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        if self._dirty is None:
            self._dirty = [x0, y0, x1, y1]
        else:
            self._dirty[0] = min(self._dirty[0], x0)
            self._dirty[1] = min(self._dirty[1], y0)
            self._dirty[2] = max(self._dirty[2], x1)
            self._dirty[3] = max(self._dirty[3], y1)

    # -- framebuf overrides, for automatic dirty-region tracking --------

    def mark_dirty(self, x0, y0, x1, y1):
        """Manually mark a region (inclusive pixel coords) as needing a
        partial refresh. Useful for drawing done via direct buffer
        manipulation rather than the usual framebuf drawing methods (which
        are all tracked automatically)."""
        self._mark_dirty(x0, y0, x1, y1)

    def pixel(self, x, y, *args):
        self._mark_dirty(x, y, x, y)
        return super().pixel(x, y, *args)

    def hline(self, x, y, w, c):
        self._mark_dirty(x, y, x + w - 1, y)
        super().hline(x, y, w, c)

    def vline(self, x, y, h, c):
        self._mark_dirty(x, y, x, y + h - 1)
        super().vline(x, y, h, c)

    def line(self, x1, y1, x2, y2, c):
        self._mark_dirty(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        super().line(x1, y1, x2, y2, c)

    def rect(self, x, y, w, h, c, *args):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        super().rect(x, y, w, h, c, *args)

    def ellipse(self, x, y, xr, yr, c, *args):
        self._mark_dirty(x - xr, y - yr, x + xr, y + yr)
        super().ellipse(x, y, xr, yr, c, *args)

    def poly(self, x, y, coords, c, *args):
        n = len(coords)
        if n >= 2:
            min_x = max_x = coords[0]
            min_y = max_y = coords[1]
            for i in range(2, n - 1, 2):
                px, py = coords[i], coords[i + 1]
                if px < min_x:
                    min_x = px
                elif px > max_x:
                    max_x = px
                if py < min_y:
                    min_y = py
                elif py > max_y:
                    max_y = py
            self._mark_dirty(x + min_x, y + min_y, x + max_x, y + max_y)
        super().poly(x, y, coords, c, *args)

    def fill_rect(self, x, y, w, h, c):
        self._mark_dirty(x, y, x + w - 1, y + h - 1)
        super().fill_rect(x, y, w, h, c)

    def fill(self, c):
        self._mark_dirty(0, 0, self.width - 1, self.height - 1)
        super().fill(c)

    def scroll(self, dx, dy):
        self._mark_dirty(0, 0, self.width - 1, self.height - 1)
        super().scroll(dx, dy)

    def text(self, s, x, y, c=1, *args):
        scale = args[0] if args else 1
        self._mark_dirty(x, y, x + 8 * len(s) * scale - 1, y + 8 * scale - 1)
        super().text(s, x, y, c, *args)

    def blit(self, fbuf, x, y, *args):
        w = getattr(fbuf, "width", None)
        h = getattr(fbuf, "height", None)
        if w is None or h is None:
            self._mark_dirty(0, 0, self.width - 1, self.height - 1)
        else:
            self._mark_dirty(x, y, x + w - 1, y + h - 1)
        super().blit(fbuf, x, y, *args)

    # -- display init and refresh ----------------------------------------

    def init_display(self, fast_prime=False):
        """Run the panel's full reset/init sequence.

        With fast_prime=True, this also primes the quicker "fast" refresh
        waveform (needed by show_fast()) - this is what the vendor
        reference calls init_Fast(), and it's a complete standalone init in
        its own right, not a small add-on to the normal init. Any full or
        partial refresh (or sleep()) reloads/drops that primed waveform, so
        show_fast() re-primes (via a full re-init) whenever needed.
        """
        self._reset()
        self._busy_wait()

        self._cmd(_SW_RESET)
        self._busy_wait()

        self._cmd(_TEMP_SENSOR_CONTROL, 0x80)
        self._cmd(_BOOSTER_SOFT_START, 0xAE, 0xC7, 0xC3, 0xC0, 0x80)

        h = self.height
        self._cmd(_DRIVER_OUTPUT_CONTROL, (h - 1) & 0xFF, ((h - 1) >> 8) & 0xFF, 0x02)
        self._cmd(_BORDER_WAVEFORM_CONTROL, _BORDER_NORMAL)
        self._cmd(_DATA_ENTRY_MODE, 0x01)  # X increment, Y decrement

        self._set_window(0, h - 1, self.width - 1, 0)
        self._set_cursor(0, 0)
        self._busy_wait()

        if fast_prime:
            self._cmd(_WRITE_TEMP_REG, 0x5A)
            self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_MODE_FAST_PRIME)
            self._cmd(_MASTER_ACTIVATION)
            self._busy_wait()
        self._fast_primed = fast_prime

    def show(self):
        """Push the framebuffer to the display and do a full refresh.

        Full refresh is the slowest but most reliable mode (no ghosting),
        and is the one to reach for after using show_fast()/show_partial()
        a few times, to clear any accumulated ghosting.
        """
        self._cmd(_BORDER_WAVEFORM_CONTROL, _BORDER_NORMAL)
        self._write_ram(0, 0, self.width - 1, self.height - 1)
        self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_MODE_FULL)
        self._cmd(_MASTER_ACTIVATION)
        self._busy_wait()
        self._dirty = None
        self._fast_primed = False  # full refresh reloads the LUT

    def show_fast(self):
        """Push the framebuffer and do a full-screen refresh using the
        panel's alternate "fast" waveform (0xC7) instead of show()'s
        default (0xF7).

        On the Xteink X4 panel this has been measured to take about the
        same time as show() (~1.6s either way, even once primed) - the
        expected speedup didn't materialize on this particular panel's OTP
        waveform table, so show_partial() is the more reliable way to get a
        snappier update. show_fast() is kept since it may behave
        differently (faster, and/or with different ghosting/flash
        characteristics) on other SSD1677 panels - measure before relying
        on it for speed.

        The first call after a show()/show_partial()/sleep() re-primes the
        fast waveform, which involves a full re-init and is as slow as
        show() itself; subsequent back-to-back show_fast() calls reuse that
        priming.
        """
        if not self._fast_primed:
            self.init_display(fast_prime=True)

        self._write_ram(0, 0, self.width - 1, self.height - 1)
        self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_MODE_FAST)
        self._cmd(_MASTER_ACTIVATION)
        self._busy_wait()
        self._dirty = None

    def show_partial(self):
        """Refresh only the region changed since the last show*() call, for
        the lowest latency and least flicker. Does nothing if nothing has
        changed. Ghosting accumulates faster than with show_fast() since
        only part of the screen is refreshed each time - fall back to
        show() occasionally to clear it.
        """
        if self._dirty is None:
            return

        x0, y0, x1, y1 = self._dirty
        x0 = (x0 // 8) * 8
        x1 = min(self.width - 1, ((x1 // 8) + 1) * 8 - 1)

        self._cmd(_BORDER_WAVEFORM_CONTROL, _BORDER_PARTIAL)
        self._write_ram(x0, y0, x1, y1)
        self._cmd(_DISPLAY_UPDATE_CONTROL_2, _UPDATE_MODE_PARTIAL)
        self._cmd(_MASTER_ACTIVATION)
        self._busy_wait()
        self._dirty = None
        self._fast_primed = False  # partial refresh reloads the LUT

    def sleep(self):
        """Put the panel into deep sleep. A hardware reset is needed to wake it."""
        self._cmd(_DEEP_SLEEP, 0x01)
        time.sleep_ms(2000)
        self._fast_primed = False  # deep sleep powers the LUT down
