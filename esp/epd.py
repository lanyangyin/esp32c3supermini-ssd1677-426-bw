# epd.py — SSD1677 墨水屏封装（最终修正版）
import framebuf
import ssd1677
from machine import Pin, SPI

SCK, MOSI, CS, DC, RST, BUSY = 18, 23, 4, 13, 14, 27
BLACK = 0
WHITE = 15

# 关键：用 SPI(2)，因为 VSPI 默认引脚正好是 18/23
spi = SPI(2)
spi.init(baudrate=10_000_000, polarity=0, phase=0)

# 所有 Pin 用位置参数，避免关键字兼容问题
pin_dc   = Pin(DC,   Pin.OUT, 0)
pin_cs   = Pin(CS,   Pin.OUT, 0)
pin_rst  = Pin(RST,  Pin.OUT, 0)
pin_busy = Pin(BUSY, Pin.IN,  Pin.PULL_UP)

# CS/RST 初始拉高（避免被通信误触发）
pin_cs.value(1)
pin_rst.value(1)

_display = ssd1677.SSD1677(800, 480, spi, pin_dc, pin_cs, pin_rst, pin_busy)


def init():
    _display.init_display()

def alloc_framebuffer():
    return _display.alloc_framebuffer()

def begin_frame():
    _display.begin_frame()

def write_chunk(chunk):
    _display.write_chunk(chunk)

def end_frame():
    _display.end_frame()

def show():
    _display.show()

def clear(c=WHITE):
    _display.clear(c)

def sleep():
    _display.sleep()

def fill(c=WHITE):
    _display.fill(c)

def rect(x, y, w, h, c=BLACK):
    _display.rect(x, y, w, h, c)

def text(s, x, y, color=BLACK, scale=1):
    _display.text(s, x, y, color)