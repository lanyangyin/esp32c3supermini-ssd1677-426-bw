# main.py — 编排：WiFi → Web（暂时注释了开机模式）
import gc
import time
import os

gc.collect()
print('[MAIN] start')

# 0. 外部存储挂载已暂时注释
print('[MAIN] 外部存储挂载已暂时注释，仅使用 Flash 内部存储')
gc.collect()

# 1. 暂时注释掉 GPIO 检测和开机画面
mode = 0
print('[MAIN] 开机模式检测已注释，强制为 00 (mode=0)')

# ---------- 2. WiFi 初始化 ----------
try:
    import network
    import wifi_manager

    print('[MAIN] 连接 STA...')
    wifi_manager.auto_connect()

    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        for _ in range(10):
            if sta.isconnected():
                break
            time.sleep(0.5)

    if sta.isconnected():
        print('[MAIN] STA:', 'http://' + sta.ifconfig()[0])
    else:
        print('[MAIN] STA 未连接，仅 AP 模式')

    wifi_manager.start_ap()
    print('[MAIN] AP: http://192.168.4.1')
except KeyboardInterrupt:
    print('[MAIN] WiFi 初始化被 Ctrl+C 中断')
except Exception as e:
    print('[MAIN] WiFi 初始化失败:', e)
    try:
        import sys
        sys.print_exception(e)
    except Exception:
        pass

gc.collect()

# ---------- 3. Web 服务器 ----------
print('[MAIN] 启动 Web 服务器...')
try:
    from web_server import start_server
    start_server()
except KeyboardInterrupt:
    print('[MAIN] Web 服务器被 Ctrl+C 中断，回到 REPL')
except Exception as e:
    print('[MAIN] web_server 失败:', e)
    try:
        import sys
        sys.print_exception(e)
    except Exception:
        pass