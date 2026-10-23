# main.py — EInkReader 入口（AP + STA，参考 E6 模式 10）
import time
import epd
import reader
import web
import wifi_manager


def show_connecting():
    epd.fill(0)
    epd.text('EInkReader', 40, 60, 1, 4)
    epd.text('Connecting WiFi...', 40, 180, 1, 2)
    epd.text('AP: EInkReader', 40, 230, 1, 2)
    epd.text('PASS: reader1234', 40, 270, 1, 2)
    epd.show()


def show_netinfo():
    """显示 AP + STA 网络信息（参考 E6 模式 10）"""
    ap_ssid, ap_pwd, ap_ip = wifi_manager.get_ap_info()
    sta_ssid, sta_ip = wifi_manager.get_sta_info()

    epd.fill(0)

    # 标题
    epd.text('EInkReader', 40, 20, 1, 3)
    epd.fill_rect(0, 70, 800, 1, 1)

    y = 85

    # AP
    epd.text('AP:   ' + ap_ssid, 40, y, 1, 2)
    y += 28
    epd.text('PASS: ' + ap_pwd, 40, y, 1, 2)
    y += 28
    epd.text('IP:   ' + ap_ip, 40, y, 1, 2)
    y += 28 + 12
    epd.fill_rect(0, y, 800, 1, 1)
    y += 12

    # STA
    epd.text('STA:  ' + sta_ssid, 40, y, 1, 2)
    y += 28
    epd.text('IP:   ' + sta_ip, 40, y, 1, 2)
    y += 28 + 12
    epd.fill_rect(0, y, 800, 1, 1)
    y += 16

    # 提示
    epd.text('Open in browser:', 40, y, 1, 2)
    y += 30
    epd.text('http://' + ap_ip, 40, y, 1, 3)

    epd.show()


def main():
    # 1. 提示正在连接
    show_connecting()

    # 2. 尝试连 STA
    print('[MAIN] 连接 STA...')
    sta_ok = False
    try:
        sta_ok = wifi_manager.auto_connect()
    except Exception as e:
        print('[MAIN] STA 失败:', e)

    if sta_ok:
        print('[MAIN] STA 已连接')
    else:
        print('[MAIN] STA 未连接，仅 AP')

    # 3. 启动 AP
    print('[MAIN] 启动 AP...')
    try:
        wifi_manager.start_ap()
    except Exception as e:
        print('[MAIN] AP 启动失败:', e)

    # 4. 显示网络信息
    show_netinfo()

    # 5. 有书就打开第一本
    books = reader.list_books()
    if books:
        book = reader.load_book(books[0])
        if book:
            reader.set_book(book)
            reader.render(full=True)
            print('[MAIN] 已打开:', books[0])

    # 6. 启动 Web
    web.start()


main()