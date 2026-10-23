# main.py
import time
import epd
import web
import reader

def main():
    epd.clear()
    epd.text("EInk Reader", 20, 20, 1)
    epd.text("Upload a .txt via web", 20, 60, 1)
    epd.show_full()

    # 如果不需要 WiFi，可以跳过
    import network
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="EInkReader", password="reader1234")
    print("AP: http://192.168.4.1")

    web.start()

main()