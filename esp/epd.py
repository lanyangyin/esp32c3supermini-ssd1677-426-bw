# epd.py — 极简流式封装
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

def end_frame():
    _display.end_frame()

def clear(c=WHITE):
    _display.clear(c)

def sleep():
    _display.sleep()