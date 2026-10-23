# main.py — EInkReader 入口

import time
import network
import epd
import reader
import web


def show_welcome():
    epd.fill(0)
    epd.text('EInkReader', 40, 40, 1, 4)
    epd.text('WiFi: EInkReader / reader1234', 40, 160, 1, 2)
    epd.text('Open: http://192.168.4.1', 40, 200, 1, 2)
    epd.show()


def main():
    show_welcome()

    # 启动 AP
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid='EInkReader', password='reader1234')
    print('[MAIN] AP: http://192.168.4.1')

    # 如果 /books 里有书，默认打开第一本
    books = reader.list_books()
    if books:
        book = reader.load_book(books[0])
        if book:
            reader.set_book(book)
            reader.render(full=True)
            print('[MAIN] 已打开:', books[0])

    web.start()


main()