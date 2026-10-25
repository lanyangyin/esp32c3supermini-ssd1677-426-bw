# epd.py — 极简流式封装（支持全刷/快刷/局刷）
import ssd1677
from machine import Pin, SPI

SCK, MOSI, CS, DC, RST, BUSY = 18, 23, 4, 13, 14, 27
BLACK = 0
WHITE = 1

spi = SPI(2)
spi.init(baudrate=4_000_000, polarity=0, phase=0)

pin_dc   = Pin(DC,   Pin.OUT, 0)
pin_cs   = Pin(CS,   Pin.OUT, 1)
pin_rst  = Pin(RST,  Pin.OUT, 1)
pin_busy = Pin(BUSY, Pin.IN,  Pin.PULL_UP)

_display = ssd1677.SSD1677(800, 480, spi, pin_dc, pin_cs, pin_rst, pin_busy)


def init():
    _display.init_display()


def begin_frame():
    _display.begin_frame()


def write_chunk(chunk):
    _display.write_chunk(chunk)


def end_frame(mode='full'):
    """结束一帧并刷新。

    mode:
      'full'    → 全刷 0xF7（4.2s，永久显示，无残影）
      'fast'    → 快刷 0xFF（0.9s，需 RED 基线，可能有轻微残影）
      'partial' → 局刷 0x91+0xFF+0x90（0.9s，全屏窗口包裹 partial 命令）
    """
    _display.end_frame(mode)


def clear(c=WHITE, mode='full'):
    _display.clear(c, mode)


def sleep():
    _display.sleep()


# ---------- 便捷封装（reader.py / 各处调用） ----------

def show(mode='full'):
    """兼容 reader.py 的 epd.show()"""
    _display.end_frame(mode)


def show_full():
    _display.end_frame('full')


def show_fast():
    _display.end_frame('fast')


def show_partial():
    _display.end_frame('partial')