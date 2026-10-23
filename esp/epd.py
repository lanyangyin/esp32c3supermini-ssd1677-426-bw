# epd.py — 封装 micropython-ssd1677，提供全刷/局刷/清屏
import ssd1677
from machine import Pin, SPI

SCK = 18
MOSI = 23
CS = 4
DC = 13
RST = 14
BUSY = 27

spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
          sck=Pin(SCK), mosi=Pin(MOSI))

epd = ssd1677.SSD1677(
    800, 480, spi,
    dc=Pin(DC), cs=Pin(CS), rst=Pin(RST), busy=Pin(BUSY),
)

def clear():
    epd.fill(0)      # 0 = 白底
    epd.show()

def show_full():
    epd.show()       # 全刷，无残影

def show_partial():
    epd.show_partial()  # 局刷，快，但会累积残影

def text(s, x, y, color=1):
    epd.text(s, x, y, color)   # 1 = 黑字