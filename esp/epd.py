# epd.py — 封装 micropython-ssd1677，自带 scale 放大
import ssd1677
from machine import Pin, SPI
from framebuf import FrameBuffer, MONO_HLSB

SCK = 18
MOSI = 23
CS = 4
DC = 13
RST = 14
BUSY = 27

spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
          sck=Pin(SCK), mosi=Pin(MOSI))

_display = ssd1677.SSD1677(
    800, 480, spi,
    dc=Pin(DC), cs=Pin(CS), rst=Pin(RST), busy=Pin(BUSY),
)

buffer = _display.buffer


# ---------- 放大字模缓存 ----------

_scaled_fonts = {}


class _ScaledFont:
    """把 framebuf 内置 8x8 字模位图放大 scale 倍，缓存结果"""

    def __init__(self, scale):
        self.scale = scale
        self.w = 8 * scale
        self.h = 8 * scale
        self.bytes_per_row = (self.w + 7) // 8
        self.buf_size = self.bytes_per_row * self.h
        self.cache = {}

    def glyph(self, ch):
        if ch in self.cache:
            return self.cache[ch]

        # 用 8x8 framebuf 渲染单字符
        tmp = bytearray(8)
        tmp_fb = FrameBuffer(tmp, 8, 8, MONO_HLSB)
        tmp_fb.fill(0)
        try:
            tmp_fb.text(ch, 0, 0, 1)
        except Exception:
            self.cache[ch] = None
            return None

        # 逐像素放大
        out = bytearray(self.buf_size)
        for py in range(8):
            for px in range(8):
                if tmp_fb.pixel(px, py):
                    for dy in range(self.scale):
                        ny = py * self.scale + dy
                        row_off = ny * self.bytes_per_row
                        for dx in range(self.scale):
                            nx = px * self.scale + dx
                            out[row_off + (nx >> 3)] |= 0x80 >> (nx & 7)

        fb = FrameBuffer(out, self.w, self.h, MONO_HLSB)
        self.cache[ch] = fb
        return fb


def _get_scaled_font(scale):
    font = _scaled_fonts.get(scale)
    if font is None:
        font = _ScaledFont(scale)
        _scaled_fonts[scale] = font
    return font


# ---------- 公共 API ----------

def fill(c):
    _display.fill(c)


def show():
    _display.show()


def show_full():
    _display.show()


def show_partial():
    _display.show_partial()


def clear():
    fill(0)
    show()


def text(s, x, y, color=1, scale=1):
    """color: 0=白, 1=黑；scale: 1/2/3/4"""
    if scale <= 1:
        _display.text(s, x, y, color)
        return

    font = _get_scaled_font(scale)
    cx = x
    for ch in s:
        glyph = font.glyph(ch)
        if glyph is not None:
            _display.blit(glyph, cx, y, 0)
        cx += font.w


def fill_rect(x, y, w, h, c):
    _display.fill_rect(x, y, w, h, c)


def rect(x, y, w, h, c):
    _display.rect(x, y, w, h, c)