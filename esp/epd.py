# epd.py — SSD1677 墨水屏封装
import framebuf
import ssd1677
from machine import Pin, SPI

SCK, MOSI, CS, DC, RST, BUSY = 18, 23, 4, 13, 14, 27
BLACK = 0
WHITE = 15

spi = SPI(1, baudrate=10_000_000, polarity=0, phase=0, sck=Pin(SCK), mosi=Pin(MOSI))
_display = ssd1677.SSD1677(800, 480, spi, dc=Pin(DC), cs=Pin(CS), rst=Pin(RST), busy=Pin(BUSY))

def init():
    _display.init_display()

def alloc_framebuffer():
    """按需分配整屏缓存（用于 BIN 编辑等重绘图操作）"""
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

# --- 兼容旧接口（仅当 alloc_framebuffer() 成功后可用） ---
def fill(c=WHITE):
    _display.fill(c)

def rect(x, y, w, h, c=BLACK):
    _display.rect(x, y, w, h, c)

def text(s, x, y, color=BLACK, scale=1):
    _display.text(s, x, y, color)